#!/bin/bash
# Monthly release. Runs the export in a scratch directory, attaches the odbl and open files,
# the manifest, LICENCES.txt and the changelog to a GitHub release tagged YYYY-MM, then deletes the scratch.
# places-unverified.* stays on this machine. Last month's three ndjson files are kept in
# $STATE/prev for the changelog.
# REPO=NaNoBotCo/motdang-open  STATE=~/.local/state/motdang-open  MONTH=2026-11 ./release_monthly.sh
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
here="$(cd "$(dirname "$0")" && pwd)"
repo="${REPO:-NaNoBotCo/motdang-open}"
state="${STATE:-$HOME/.local/state/motdang-open}"
month="${MONTH:-$(date +%Y-%m)}"
scratch="$(mktemp -d "${TMPDIR:-/private/tmp}/motdang-open.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT

if gh release view "$month" --repo "$repo" >/dev/null 2>&1; then
  echo "release $month exists"; exit 0
fi
free_kb="$(df -k "$scratch" | awk 'NR==2{print $4}')"
if [ "$free_kb" -lt 3000000 ]; then
  echo "under 3 GB free; no run" >&2; exit 1
fi

if [ -d "$state/prev" ]; then
  prev_label="$(cat "$state/prev/LABEL" 2>/dev/null || echo previous)"
  if [ "$prev_label" != "$month" ]; then
    mkdir -p "$scratch/$prev_label"
    cp "$state"/prev/places-*.ndjson.gz "$scratch/$prev_label/"
  fi
fi

OUT="$scratch" MONTH="$month" "$here/run_monthly.sh" >/dev/null
d="$scratch/$month"

assets=("$d"/places-odbl.* "$d"/places-open.* "$d/manifest.json" "$d/LICENCES.txt")
for f in "$d"/changelog-"$month".json "$d"/changelog-"$month".txt; do [ -f "$f" ] && assets+=("$f"); done

python3 - "$d/manifest.json" "$month" > "$scratch/notes.txt" <<'PY'
import json, sys
m = json.load(open(sys.argv[1])); month = sys.argv[2]
c = {k: v["records"] for k, v in m["tiers"].items()}
print("Mot Dang open data, %s" % month)
print()
print("places-odbl: %d records, Open Database Licence 1.0, credit OpenStreetMap contributors." % c["odbl"])
print("places-open: %d records, CC BY 4.0 for the compilation, each source under its own licence." % c["open"])
print("Each in NDJSON (gzip), GeoJSON (gzip) and Parquet. LICENCES.txt says what each file needs. manifest.json has counts, input checksums and the fields left out.")
print("Source: https://motdang.net/data/places.json and https://motdang.net/api/v1/index.json. Site terms: https://motdang.net/terms.html")
PY

gh release create "$month" "${assets[@]}" --repo "$repo" --title "Mot Dang open data $month" --notes-file "$scratch/notes.txt"

rm -rf "$state/prev"; mkdir -p "$state/prev"
cp "$d"/places-*.ndjson.gz "$state/prev/"
echo "$month" > "$state/prev/LABEL"
echo "released $month"
