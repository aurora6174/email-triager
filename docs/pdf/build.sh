#!/bin/sh
# Rebuilds docs/DESIGN.pdf from design.html and the diagram sources.
# Needs: graphviz (dot), python3, chromium. Fonts: Noto Sans (falls back to DejaVu).
set -eu
cd "$(dirname "$0")"
for f in diagrams/*.dot; do dot -Tsvg "$f" -o "${f%.dot}.svg"; done
python3 diagrams/gen_svg.py
chromium --headless --disable-gpu --no-pdf-header-footer --allow-file-access-from-files \
  --print-to-pdf=../DESIGN.pdf "file://$PWD/design.html" 2>/dev/null
echo "wrote docs/DESIGN.pdf"
