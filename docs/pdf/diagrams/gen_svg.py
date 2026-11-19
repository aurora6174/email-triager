"""Generates the hand-laid-out diagrams (sequence, idempotency timelines, defense layers).
Graphviz handles the rest. Run: python3 gen_svg.py"""

from html import escape
from pathlib import Path

HERE = Path(__file__).parent
FONT = "Noto Sans, DejaVu Sans, sans-serif"
INK, MUTED, LINE = "#0F172A", "#475569", "#94A3B8"
N8N, SVC, LLM, OUT, ERR, WARN = "#EA4B71", "#2563EB", "#7C3AED", "#059669", "#DC2626", "#D97706"
TINT = {N8N: "#FFF1F3", SVC: "#EFF6FF", LLM: "#F5F3FF", OUT: "#ECFDF5", ERR: "#FEE2E2",
        WARN: "#FEF3C7", MUTED: "#F1F5F9", LINE: "#F8FAFC"}


def svg(w, h, body):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'font-family="{FONT}">\n<defs>'
        + "".join(
            f'<marker id="a{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
            for c in (INK, MUTED, ERR, OUT, N8N, SVC, LLM, WARN)
        )
        + "</defs>\n" + "\n".join(body) + "\n</svg>\n"
    )


def text(x, y, s, size=12, color=INK, anchor="middle", weight="normal", style="normal"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" text-anchor="{anchor}" '
            f'font-weight="{weight}" font-style="{style}">{escape(s)}</text>')


def box(x, y, w, h, color, label=None, size=12, rx=8, fill=None, weight="normal", dash=False):
    d = ' stroke-dasharray="5 4"' if dash else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill or TINT.get(color, "white")}" '
           f'stroke="{color}" stroke-width="1.4"{d}/>']
    if label:
        lines = label.split("\n")
        y0 = y + h / 2 - (len(lines) - 1) * (size + 3) / 2 + size * 0.35
        for i, ln in enumerate(lines):
            out.append(text(x + w / 2, y0 + i * (size + 3), ln, size, INK, weight=weight))
    return "\n".join(out)


def arrow(x1, y1, x2, y2, color=INK, dash=False, width=1.3):
    d = ' stroke-dasharray="6 4"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{d} '
            f'marker-end="url(#a{color[1:]})"/>')


# --------------------------------------------------------------------------- sequence
def sequence():
    actors = [("Sender", MUTED), ("n8n", N8N), ("Classifier", SVC), ("SQLite", SVC),
              ("LLM", LLM), ("Sheets", OUT), ("Slack", OUT)]
    W, gap, top = 900, 124, 20
    xs = [70 + i * gap for i in range(len(actors))]
    msgs = [
        # (from, to, label, dashed, color, note)
        (0, 1, "POST /webhook/email-triage", False, INK),
        (1, 0, "202 Accepted (immediately)", True, MUTED),
        (1, 2, "POST /v1/classify", False, INK),
        (2, 3, "claim message_id → processing", False, INK),
        (2, 4, "system prompt + <email> + JSON schema", False, INK),
        (4, 2, "{category, priority, confidence, …}", True, LLM),
        ("self", 2, "validate · review rules · route", False, SVC),
        (2, 3, "save → classified (+ lease)", False, INK),
        (2, 1, "200 {route, notify, needs_review, …}", True, SVC),
        (1, 5, "append row to route's tab", False, INK),
        (1, 2, "POST /v1/messages/{id}/ack", False, INK),
        (2, 3, "status → routed", False, INK),
        (1, 6, "alert (only if urgent / high)", False, OUT),
    ]
    step, y0 = 38, top + 62
    H = y0 + (len(msgs) - 1) * step + 34
    body = []
    for (name, c), x in zip(actors, xs):
        body.append(f'<line x1="{x}" y1="{top + 34}" x2="{x}" y2="{H - 10}" stroke="{LINE}" stroke-dasharray="4 4"/>')
        body.append(box(x - 52, top, 104, 34, c, name, 12.5, weight="600"))
    # background bands for phases
    phases = [(0, 1, "accept"), (2, 8, "classify"), (9, 12, "deliver + ack + notify")]
    for a, b, lab in phases:
        ya, yb = y0 + a * step - 22, y0 + b * step + 14
        body.insert(0, f'<rect x="6" y="{ya}" width="{W - 12}" height="{yb - ya}" rx="6" fill="#F8FAFC"/>')
        body.insert(1, text(12, ya + 13, lab.upper(), 9.5, MUTED, "start", "600"))
    for i, (f, t, lab, dash, c) in enumerate(msgs):
        y = y0 + i * step
        if f == "self":
            x = xs[t]
            body.append(f'<path d="M{x},{y - 8} h34 v16 h-32" fill="none" stroke="{c}" stroke-width="1.3" marker-end="url(#a{c[1:]})"/>')
            body.append(text(x + 42, y + 4, lab, 11.5, c, "start", "600"))
            continue
        x1, x2 = xs[f], xs[t]
        sgn = 1 if x2 > x1 else -1
        body.append(arrow(x1 + 4 * sgn, y, x2 - 4 * sgn, y, c if c != INK else INK, dash))
        body.append(text((x1 + x2) / 2, y - 6, lab, 11, INK if c == INK else c))
    return svg(W, H, body)


# --------------------------------------------------------------------------- idempotency
def idempotency():
    W = 900
    body = []
    scenarios = [
        ("A · Redelivery after success",
         [("Run 1", [("classify", SVC, "LLM ✓"), ("deliver", OUT, "Sheets ✓"), ("ack", OUT, "routed")]),
          ("Run 2 (copy, later)", [("classify", MUTED, "duplicate: routed"), ("stop", MUTED, "nothing posted")])],
         "1 LLM call · delivered once"),
        ("B · Failure between classify and deliver",
         [("Run 1", [("classify", SVC, "LLM ✓"), ("deliver", ERR, "Sheets ✗"), ("dead letter", ERR, "+ Slack alert")]),
          ("Run 2 (retry, after lease)", [("classify", SVC, "duplicate: classified"), ("deliver", OUT, "Sheets ✓"), ("ack", OUT, "routed")])],
         "1 LLM call · delivered once · failure visible"),
        ("C · Two copies a few seconds apart",
         [("Run 1", [("classify", SVC, "LLM ✓ · lease starts"), ("deliver", OUT, "Sheets ✓"), ("ack", OUT, "routed")]),
          ("Run 2 (copy, +3 s)", [("classify", WARN, "409 in progress"), ("retry +5 s", WARN, "duplicate: delivering"), ("stop", MUTED, "nothing posted")])],
         "1 LLM call · delivered once · no race"),
    ]
    y = 14
    bw, bh, gap = 150, 44, 26
    for title, runs, outcome in scenarios:
        body.append(text(14, y + 14, title, 14, INK, "start", "700"))
        body.append(text(W - 14, y + 14, outcome, 11.5, OUT, "end", "600"))
        y += 26
        for run, steps in runs:
            body.append(text(14, y + bh / 2 + 4, run, 11.5, MUTED, "start", "600"))
            x = 190
            for i, (lab, c, sub) in enumerate(steps):
                body.append(box(x, y, bw, bh, c, None))
                body.append(text(x + bw / 2, y + 18, lab, 12, INK, weight="600"))
                body.append(text(x + bw / 2, y + 34, sub, 10.5, MUTED))
                if i < len(steps) - 1:
                    body.append(arrow(x + bw + 2, y + bh / 2, x + bw + gap - 2, y + bh / 2, MUTED))
                x += bw + gap
            y += bh + 12
        y += 18
        body.append(f'<line x1="14" y1="{y - 14}" x2="{W - 14}" y2="{y - 14}" stroke="#E2E8F0"/>')
    return svg(W, y - 10, body)


# --------------------------------------------------------------------------- defense layers
def defense():
    W, H = 900, 330
    layers = [
        ("1  Prompt: the email is data inside <email> tags; instructions in it are ignored", "#F5F3FF", LLM),
        ("2  Escaping: a </email> in the email can't close the data block", "#EDE9FE", LLM),
        ("3  Structured output: only enum labels and short text can come back", "#DBEAFE", SVC),
        ("4  No tools: the model can't act; code decides routes (D4)", "#D1FAE5", OUT),
        ("5  Heuristic: suspicious phrasing forces human review", "#FEF3C7", WARN),
    ]
    body = [text(W / 2, 22, "Untrusted email  →  must pass every layer before anything happens", 13, INK, weight="600")]
    x, y, w, h = 30, 40, W - 60, H - 60
    for i, (lab, fill, c) in enumerate(layers):
        body.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{c}" stroke-width="1.4"/>')
        body.append(text(x + 16, y + 22, lab, 12, INK, "start", "600"))
        x, y, w, h = x + 26, y + 34, w - 52, h - 44
    body.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="white" stroke="{INK}" stroke-width="1.4"/>')
    body.append(text(x + w / 2, y + h / 2 + 5, "Worst case of a successful injection: a wrong label → still bounded by routing code", 12, INK, weight="600"))
    return svg(W, H, body)


if __name__ == "__main__":
    for name, fn in [("sequence", sequence), ("idempotency", idempotency), ("defense", defense)]:
        (HERE / f"{name}.svg").write_text(fn())
    print("ok")
