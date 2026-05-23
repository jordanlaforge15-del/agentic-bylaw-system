#!/usr/bin/env bash
# Validate every ```mermaid``` fenced block in a markdown file.
#
# Usage: ./scripts/validate_tdd_mermaid.sh docs/TDD.md
#
# Extracts each fenced mermaid block to a temp file, runs mermaid-cli
# (via npx) to render it to SVG, and fails fast on the first syntax
# error. Exits 0 if every block renders.

set -euo pipefail

INPUT="${1:-docs/TDD.md}"

if [[ ! -f "$INPUT" ]]; then
  echo "validate_tdd_mermaid: $INPUT not found" >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Split the markdown on ```mermaid ... ``` blocks. The awk script writes
# one file per block to $tmp_dir, named block-NN.mmd. We then invoke
# mmdc once per file. Errors print the failing block + the mmdc diagnostic
# and exit non-zero.
awk -v outdir="$tmp_dir" '
  BEGIN { in_block=0; idx=0; out="" }
  /^```mermaid[[:space:]]*$/ {
    in_block=1; idx++;
    out=sprintf("%s/block-%02d.mmd", outdir, idx);
    next
  }
  /^```[[:space:]]*$/ {
    if (in_block) { in_block=0; out=""; next }
  }
  { if (in_block) { print > out } }
' "$INPUT"

count=$(ls "$tmp_dir"/block-*.mmd 2>/dev/null | wc -l | tr -d ' ')
if [[ "$count" -eq 0 ]]; then
  echo "validate_tdd_mermaid: no mermaid blocks found in $INPUT" >&2
  exit 1
fi

echo "validate_tdd_mermaid: found $count mermaid block(s) in $INPUT"

fail=0
for f in "$tmp_dir"/block-*.mmd; do
  name="$(basename "$f")"
  echo "  -> rendering $name"
  if ! npx -y @mermaid-js/mermaid-cli -i "$f" -o "$tmp_dir/${name%.mmd}.svg" >"$tmp_dir/${name%.mmd}.log" 2>&1; then
    echo "validate_tdd_mermaid: $name FAILED" >&2
    echo "----- block contents -----" >&2
    cat "$f" >&2
    echo "----- mmdc output -----" >&2
    cat "$tmp_dir/${name%.mmd}.log" >&2
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi

echo "validate_tdd_mermaid: all $count block(s) valid"
