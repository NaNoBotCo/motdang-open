Mot Dang monthly export

Reads the published https://motdang.net/data/places.json (every field, about 204 MB) and https://motdang.net/api/v1/index.json (id to page slug, about 75 MB). It does not read the build tree and does not run a build.

  python3 export.py --out OUT --month 2026-11 --prev OUT/2026-10
  OUT=/path ./run_monthly.sh        month from the clock, previous month found in OUT

Python 3.9, standard library. Parquet needs duckdb (python3 -m pip install duckdb) and is skipped without it. Options: --source and --index take a path or a URL, --sample N keeps about N records, --no-parquet, --strip-river.

Output in OUT/<month>/
  places-odbl.*        26,740 records with an OpenStreetMap source or an OSM-derived field; ODbL 1.0
  places-open.*        records whose sources are all Mot Dang's own, Overture, OCHA boundaries or a Thai register that states an open licence; compilation CC BY 4.0, each source under its own licence
  places-unverified.*  records with a source that states no licence, records none, or is a third-party page; no licence offered
  each as .ndjson.gz, .geojson.gz (pinned records) and .parquet
  manifest.json        counts, input sizes and checksums, dropped fields with record counts
  LICENCES.txt         what each file is and what each source needs
  changelog-<month>.json and .txt   added, removed and changed records against --prev; a change in sources[].fetched alone is not counted

Same input gives the same bytes.

How a record gets its file
  A source entry maps to a licence: type osm and the river fields (riverName, riverM, riverside, computed from OSM waterway lines) are ODbL 1.0. Overture rows are CDLA-Permissive-2.0. The admin-polygon fields (amphoe, tambon) are COD-AB, CC BY-IGO. A register or open-data row is Thai Open Data Common or CC BY when its licence field says so, "not stated" when the publisher gave none, "not recorded" when the row has no licence field. Field, curated, researched and own-site rows are Mot Dang's own. Directory rows without Overture, web, news, forum, social and paper rows are third-party pages with no licence. Facebook rows read through a logged-in browser are marked as such.
  One unverified source puts the record in places-unverified. Otherwise one OSM source puts it in places-odbl. Otherwise places-open.

Left out of every file: photos, email addresses (25,547 records carry one), Wikipedia extracts in attrs.summary (140 records, CC BY-SA 4.0), attrs.desc (2,531 records, text copied from a third-party directory), attrs.note, authored prose (history, blurbs), build bookkeeping. manifest.json counts each.

Run on 2026-10-10 data (89,047 records, 23 seconds)
  odbl 26,740 · open 55,776 · unverified 6,531
  ndjson.gz 16.2 MB · geojson.gz 15.7 MB · parquet 14.2 MB · 46 MB all together
  With --strip-river: odbl 16,527 · open 65,989 · unverified 6,531

Monthly release
  release_monthly.sh runs the export in a scratch directory, attaches the odbl and open files, the manifest, LICENCES.txt and the changelog to a GitHub release tagged with the month (gh release create), then deletes the scratch. places-unverified.* stays on the machine that ran it. The three ndjson files of the last month are kept in ~/.local/state/motdang-open/prev for the changelog. Needs gh signed in and under 3 GB free.
  install_launchd.sh writes ~/Library/LaunchAgents/com.nanobotco.motdang-open.plist (1st of the month, 05:00; a Mac that is asleep runs it on wake) and loads it. Log: ~/Library/Logs/motdang-open.log.
  Email addresses inside other text (a sign, an address line, a card, a name) are removed in every file; manifest.json counts the records.
