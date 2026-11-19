"""Idempotency store and audit log, on SQLite.

Each message moves through: (absent) -> processing -> classified -> routed.

* ``processing`` is a short-lived claim so two concurrent deliveries of the same message
  (an n8n retry racing the original) can't both call the LLM and both route it.
* ``classified`` means the answer is stored. Handing a result to n8n also starts a short
  *delivery lease*: while it runs, another copy of the message is told ``delivering`` and
  n8n stops it, so a concurrent duplicate can't deliver the email a second time. Once the
  lease has expired without an ack, the earlier run evidently failed (its error is in the
  dead-letter queue); a redelivery then gets the stored answer back, with no second LLM
  call, and n8n finishes the delivery.
* ``routed`` is set by n8n's ack after the destinations succeeded; a redelivery after
  that is reported as a duplicate and n8n stops.

SQLite is enough for one classifier replica. For several replicas, the same state machine
moves to Postgres (INSERT ... ON CONFLICT plus a row lock), as in the booking API.
"""

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id   TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('processing', 'classified', 'routed')),
    result_json  TEXT,
    claimed_at   REAL NOT NULL,
    lease_until  REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL
);
"""


class ClaimOutcome(Enum):
    NEW = "new"  # caller owns the claim and must call complete() or release()
    EXISTING = "existing"  # already classified or routed; row holds the stored result
    DELIVERING = "delivering"  # classified and another run holds the delivery lease
    IN_PROGRESS = "in_progress"  # another request holds a live claim
    CONFLICT = "conflict"  # same message_id, different content


@dataclass
class StoredMessage:
    message_id: str
    content_hash: str
    status: str
    result_json: str | None


class Store:
    def __init__(self, path: str, claim_ttl_seconds: int, lease_seconds: int = 120):
        self.path = path
        self.claim_ttl = claim_ttl_seconds
        self.lease = lease_seconds
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN IMMEDIATE ourselves, which takes the write
        # lock up front so the read-then-write in claim() is atomic across threads.
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def claim(
        self, message_id: str, content_hash: str
    ) -> tuple[ClaimOutcome, StoredMessage | None]:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO messages"
                    " (message_id, content_hash, status, claimed_at, updated_at)"
                    " VALUES (?, ?, 'processing', ?, ?)",
                    (message_id, content_hash, now, now),
                )
                conn.execute("COMMIT")
                return ClaimOutcome.NEW, None

            stored = StoredMessage(
                row["message_id"], row["content_hash"], row["status"], row["result_json"]
            )
            if row["content_hash"] != content_hash:
                conn.execute("COMMIT")
                return ClaimOutcome.CONFLICT, stored
            if row["status"] == "processing":
                if now - row["claimed_at"] < self.claim_ttl:
                    conn.execute("COMMIT")
                    return ClaimOutcome.IN_PROGRESS, stored
                # The previous holder died mid-call. Take the claim over.
                conn.execute(
                    "UPDATE messages SET claimed_at = ?, updated_at = ? WHERE message_id = ?",
                    (now, now, message_id),
                )
                conn.execute("COMMIT")
                return ClaimOutcome.NEW, None
            if row["status"] == "classified":
                if now < row["lease_until"]:
                    conn.execute("COMMIT")
                    return ClaimOutcome.DELIVERING, stored
                # The earlier delivery attempt is over without an ack: this caller takes
                # over delivery under a fresh lease.
                conn.execute(
                    "UPDATE messages SET lease_until = ?, updated_at = ? WHERE message_id = ?",
                    (now + self.lease, now, message_id),
                )
            conn.execute("COMMIT")
            return ClaimOutcome.EXISTING, stored
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def complete(self, message_id: str, result_json: str) -> None:
        """Store the result; the caller now holds the delivery lease."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE messages SET status = 'classified', result_json = ?, lease_until = ?,"
                " updated_at = ? WHERE message_id = ? AND status = 'processing'",
                (result_json, now + self.lease, now, message_id),
            )

    def release(self, message_id: str) -> None:
        """Drop a claim after a failure so the next delivery can try again."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM messages WHERE message_id = ? AND status = 'processing'",
                (message_id,),
            )

    def mark_routed(self, message_id: str) -> str | None:
        """Returns the status before the call, or None if the message is unknown.
        Idempotent: acking an already-routed message is a no-op."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            if row["status"] == "classified":
                conn.execute(
                    "UPDATE messages SET status = 'routed', updated_at = ? WHERE message_id = ?",
                    (time.time(), message_id),
                )
            conn.execute("COMMIT")
            return row["status"]
        finally:
            conn.close()

    def get(self, message_id: str) -> StoredMessage | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None:
            return None
        return StoredMessage(
            row["message_id"], row["content_hash"], row["status"], row["result_json"]
        )
