#!/bin/bash
# Monthly export. Reads the published files, writes OUT/<YYYY-MM>/, diffs against the latest earlier month in OUT.
# OUT=/path [MONTH=YYYY-MM] ./run_monthly.sh [extra export.py flags]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${OUT:-$here/out}"
month="${MONTH:-$(date +%Y-%m)}"
prev="$(ls -1d "$out"/20??-?? 2>/dev/null | grep -v "/$month\$" | sort | tail -1 || true)"
args=(--out "$out" --month "$month")
[ -n "$prev" ] && args+=(--prev "$prev")
python3 "$here/export.py" "${args[@]}" "$@"
