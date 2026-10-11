#!/usr/bin/env python3
"""Monthly export of Mot Dang place records, split by licence, with a changelog.

Reads the PUBLISHED files (https://motdang.net/data/places.json and
https://motdang.net/api/v1/index.json) or local copies of them. It does not read
the build tree and does not run a build.

Each record is put in one of three tiers by the licences of its sources, and
each tier is written to its own files, so one file carries one licence:

    odbl        a source is OpenStreetMap, or a field was derived from OSM lines
                -> the file is ODbL 1.0, © OpenStreetMap contributors
    open        every source is Mot Dang's own collection, Overture
                (CDLA-Permissive-2.0), the OCHA admin boundaries (CC BY-IGO), or a
                Thai government register that states an open licence
                -> compilation CC BY 4.0, each source keeps its own licence
    unverified  at least one source states no licence, records none, or is a
                third-party page or a logged-in scrape (OSM may be present too)
                -> facts and source links only; no licence is offered for the
                record as a whole; an OSM part of it stays ODbL (see `licences`)

    python3 export.py --out OUT [--month 2026-11] [--prev OUT/2026-10]
                      [--sample 2000] [--source PATH_OR_URL] [--index PATH_OR_URL]

Output, in OUT/<month>/:
    places-<tier>.ndjson.gz   one record per line
    places-<tier>.geojson.gz  pinned records only
    places-<tier>.parquet     needs duckdb (python -m pip install duckdb); skipped without it
    manifest.json             counts, input checksums, dropped fields
    LICENCES.txt              what each tier is and what each source needs
    changelog-<month>.json / .txt   when --prev is given

Same input gives the same bytes. Python 3.9, standard library, plus duckdb for Parquet.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from collections import Counter, defaultdict
from datetime import date

SOURCE_URL = "https://motdang.net/data/places.json"
INDEX_URL = "https://motdang.net/api/v1/index.json"
SITE = "https://motdang.net/"
UA = "motdang-open/0.1 (+https://motdang.net/api/)"
ATTRIBUTION_SELF = "มดแดง Mot Dang · motdang.net"

# ---------------------------------------------------------------------------
# Licence labels. One place to change them.
# ---------------------------------------------------------------------------
TIER_LICENCE = {
    "odbl": "ODbL-1.0",
    "open": "CC-BY-4.0 (compilation); each source keeps its own licence",
    "unverified": "none offered",
}
TIER_ORDER = ["odbl", "open", "unverified"]

# Source classes -> (licence label, tier it forces at minimum)
OSM = ("ODbL-1.0", "odbl")
OVERTURE = ("CDLA-Permissive-2.0", "open")
COD_AB = ("CC-BY-IGO-3.0", "open")
OGD_TH = ("TH-Open-Data-Common", "open")
CC_BY = ("CC-BY", "open")
OWN = ("CC-BY-4.0 (Mot Dang)", "open")
UNSTATED = ("licence-not-stated-by-publisher", "unverified")
UNRECORDED = ("licence-not-recorded", "unverified")
THIRD_PARTY = ("third-party-page-no-licence", "unverified")
SCRAPE = ("logged-in-browser-read", "unverified")

# Source types that are Mot Dang's own collection or facts read off the place itself.
OWN_TYPES = {"field", "curated", "researched", "own-site", "official-site", "official"}
THIRD_PARTY_TYPES = {"directory", "web", "forum", "news", "social", "paper", "map", "wiki",
                     "shop", "search-index"}

# Fields not exported. Reason beside each.
DROP_TOP = {
    "tagVia": "build bookkeeping",
    "provenance": "build bookkeeping (per-field source of derived values)",
    "enrichedFields": "build bookkeeping",
    "enrichedScope": "build bookkeeping",
    "photo": "photos carry their own credit and licence and are not part of this export",
    "gallery": "same as photo",
    "photoExempt": "build bookkeeping",
    "email": "owner-supplied contact; the terms say listings are not a marketing list",
    "blurb_th": "authored prose; not part of the structured export",
    "blurb_en": "authored prose; not part of the structured export",
    "history": "authored prose, some paraphrased from Wikipedia; not part of the structured export",
    "_pricesVerified": "build bookkeeping",
    "(email address inside other text)": "email address removed from a sign, address line, card text or name",
    "featured": "site layout flag",
    "landmark": "site layout flag",
}
DROP_ATTR = {
    "email": "owner-supplied contact",
    "summary": "Wikipedia extract, CC BY-SA 4.0: share-alike text, kept out of every tier",
    "desc": "free text copied from a third-party directory",
    "note": "internal build note",
    "photoRefExternal": "link to a third-party photo",
}
DROP_ATTR_PREFIX = ("_", "photo")


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------
def fetch(src, workdir):
    """Return (local path, info). A URL is downloaded to workdir."""
    if not src.startswith(("http://", "https://")):
        return src, {"source": os.path.basename(src), "bytes": os.path.getsize(src)}
    req = urllib.request.Request(src, headers={"User-Agent": UA})
    path = os.path.join(workdir, hashlib.sha1(src.encode()).hexdigest()[:12] + ".json")
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        etag = r.headers.get("ETag")
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return path, {"source": src, "etag": etag, "bytes": os.path.getsize(path)}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Licence classification
# ---------------------------------------------------------------------------
def classify_source(s):
    """One source entry -> (licence label, tier)."""
    t = s.get("type")
    via = s.get("via") or ""
    lic = (s.get("licence") or "").strip()
    if t == "osm":
        return OSM
    if t == "derived":
        if via == "river-line":
            return OSM
        if via == "admin-polygon":
            return COD_AB
        return OWN
    if t == "directory" and via == "overture":
        return OVERTURE
    if t in ("register", "opendata"):
        low = lic.lower()
        if lic.startswith("Open Data Common"):
            return OGD_TH
        if low.startswith("cc-by"):
            return CC_BY
        if low == "odbl":
            return OSM
        if "not specified" in low or "not stated" in low or "ไม่ระบุ" in lic:
            return UNSTATED
        return UNRECORDED
    if t == "own-facebook":
        return SCRAPE
    if t in OWN_TYPES:
        return OWN
    if t in THIRD_PARTY_TYPES:
        return THIRD_PARTY
    return UNRECORDED


def attribution_for(s, label):
    t = s.get("type")
    if label == "ODbL-1.0":
        return "Contains information from OpenStreetMap, © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"
    if label == "CDLA-Permissive-2.0":
        ds = s.get("dataset")
        rel = s.get("release")
        return "Overture Maps Foundation places%s%s, CDLA-Permissive-2.0" % (
            " (%s)" % ds if ds else "", " release %s" % rel if rel else "")
    if label == "CC-BY-IGO-3.0":
        return "COD-AB Thailand, OCHA / Royal Thai Survey Department, CC BY-IGO"
    if s.get("credit") and t in ("register", "opendata"):
        return s["credit"]
    return None


def tier_of(labels_tiers):
    """unverified outranks odbl outranks open: one source without a stated licence keeps
    the whole record out of the licensed files."""
    tiers = {t for _, t in labels_tiers}
    for t in ("unverified", "odbl", "open"):
        if t in tiers:
            return t
    return "unverified"


# ---------------------------------------------------------------------------
# Record -> export row
# ---------------------------------------------------------------------------
def clean_attrs(attrs, dropped):
    out = {}
    for k, v in (attrs or {}).items():
        if k in DROP_ATTR or k.startswith(DROP_ATTR_PREFIX):
            dropped["attrs." + k] += 1
            continue
        out[k] = v
    return out


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")


def scrub_emails(o, hits):
    """Remove email addresses that sit inside other text (a sign, an address line, a card)."""
    if isinstance(o, str):
        n = EMAIL_RE.sub("", o)
        if n != o:
            hits.append(1)
            return re.sub(r"\s{2,}", " ", n).strip()
        return o
    if isinstance(o, list):
        return [scrub_emails(v, hits) for v in o]
    if isinstance(o, dict):
        return {k: scrub_emails(v, hits) for k, v in o.items()}
    return o


def wikipedia_url(attrs):
    s = (attrs or {}).get("summary")
    if isinstance(s, dict) and s.get("url"):
        return s["url"]
    return None


RIVER_ATTRS = ("riverName", "riverM", "riverside")


def build_row(r, slugs, dropped, strip_river=False):
    srcs = r.get("sources") or []
    if strip_river:
        srcs = [s for s in srcs if s.get("via") != "river-line"]
    classes = [classify_source(s) for s in srcs]
    if not strip_river and any(k in (r.get("attrs") or {}) for k in RIVER_ATTRS):
        # riverName / riverM are computed from OSM waterway lines (importers/harvest_rivers.py)
        classes.append(OSM)
    tier = tier_of(classes)
    labels = sorted({l for l, _ in classes})
    attribution = []
    for s, (label, _) in zip(srcs + [{}] * (len(classes) - len(srcs)), classes):
        a = attribution_for(s, label)
        if a and a not in attribution:
            attribution.append(a)
    attribution.append(ATTRIBUTION_SELF)

    for k in DROP_TOP:
        if r.get(k):
            dropped[k] += 1

    slug = slugs.get(r["id"])
    prov = r.get("province")
    row = {
        "id": r["id"],
        "province": prov,
        "name": r.get("name"),
        "name_th": r.get("nameTh"),
        "name_en": r.get("nameEn"),
        "categories": r.get("cat") or [],
        "subcategories": r.get("sub") or [],
        "lat": r.get("lat"),
        "lng": r.get("lng"),
        "geo_precision": r.get("geoPrecision"),
        "address": r.get("address"),
        "phone": r.get("phone"),
        "website": r.get("website"),
        "hours": r.get("hours"),
        "tags": r.get("tags") or [],
        "confidence": r.get("confidence"),
        "updated_at": r.get("updatedAt"),
        "url": "%s%s/p/%s.html" % (SITE, prov, slug) if slug else None,
        "wikipedia_url": wikipedia_url(r.get("attrs")),
        "licence_tier": tier,
        "licences": labels,
        "attribution": attribution,
        "sources": [
            {k: s[k] for k in ("type", "via", "ref", "licence", "credit", "dataset",
                               "release", "edition", "fetched") if s.get(k) not in (None, "")}
            for s in srcs
        ],
        "attrs": clean_attrs(r.get("attrs"), dropped),
    }
    if strip_river:
        for k in RIVER_ATTRS:
            if k in row["attrs"]:
                del row["attrs"][k]
                dropped["attrs." + k] += 1
    hits = []
    row = scrub_emails(row, hits)
    if hits:
        dropped["(email address inside other text)"] += 1
    return row


def geojson_feature(row):
    props = {k: v for k, v in row.items() if k not in ("lat", "lng")}
    return {
        "type": "Feature",
        "id": row["id"],
        "geometry": {"type": "Point", "coordinates": [row["lng"], row["lat"]]},
        "properties": props,
    }


# ---------------------------------------------------------------------------
# Writers (deterministic gzip: mtime 0)
# ---------------------------------------------------------------------------
def gz_open(path):
    raw = open(path, "wb")
    return io.TextIOWrapper(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9),
                            encoding="utf-8", newline="\n"), raw


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def write_tier(outdir, tier, rows):
    paths = {}
    p = os.path.join(outdir, "places-%s.ndjson.gz" % tier)
    f, raw = gz_open(p)
    for row in rows:
        f.write(dumps(row) + "\n")
    f.close(); raw.close()
    paths["ndjson"] = p

    p = os.path.join(outdir, "places-%s.geojson.gz" % tier)
    f, raw = gz_open(p)
    f.write('{"type":"FeatureCollection","name":"motdang-%s","features":[\n' % tier)
    first = True
    pinned = 0
    for row in rows:
        if row["lat"] is None or row["lng"] is None:
            continue
        f.write(("" if first else ",\n") + dumps(geojson_feature(row)))
        first = False
        pinned += 1
    f.write("\n]}\n")
    f.close(); raw.close()
    paths["geojson"] = p
    paths["pinned"] = pinned
    return paths


PARQUET_COLUMNS = {
    "id": "VARCHAR", "province": "VARCHAR", "name": "VARCHAR", "name_th": "VARCHAR",
    "name_en": "VARCHAR", "categories": "VARCHAR[]", "subcategories": "VARCHAR[]",
    "lat": "DOUBLE", "lng": "DOUBLE", "geo_precision": "VARCHAR", "address": "VARCHAR",
    "phone": "VARCHAR", "website": "VARCHAR", "hours": "VARCHAR", "tags": "VARCHAR[]",
    "confidence": "VARCHAR", "updated_at": "VARCHAR", "url": "VARCHAR",
    "wikipedia_url": "VARCHAR", "licence_tier": "VARCHAR", "licences": "VARCHAR[]",
    "attribution": "VARCHAR[]", "sources": "JSON", "attrs": "JSON",
}


def write_parquet(outdir, tier, nd_path):
    try:
        import duckdb
    except ImportError:
        return None
    out = os.path.join(outdir, "places-%s.parquet" % tier)
    if os.path.exists(out):
        os.remove(out)
    cols = ", ".join("'%s': '%s'" % kv for kv in PARQUET_COLUMNS.items())
    con = duckdb.connect()
    try:
        con.execute("SET threads=2")
        con.execute(
            "COPY (SELECT * FROM read_json('%s', format='newline_delimited', "
            "compression='gzip', columns={%s}) ORDER BY id) TO '%s' "
            "(FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000)" % (nd_path, cols, out))
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------
def comparable(row):
    """The row without the dates that move on every walk."""
    r = dict(row)
    r["sources"] = [{k: v for k, v in s.items() if k != "fetched"} for s in row.get("sources", [])]
    return r


def load_prev(prev_dir):
    prev = {}
    for tier in TIER_ORDER:
        p = os.path.join(prev_dir, "places-%s.ndjson.gz" % tier)
        if not os.path.exists(p):
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                prev[row["id"]] = row
    return prev


def changelog(prev, cur_rows, from_label, to_label):
    cur = {r["id"]: r for r in cur_rows}
    added = sorted(set(cur) - set(prev))
    removed = sorted(set(prev) - set(cur))
    changed = []
    field_counts = Counter()
    tier_moves = Counter()
    for i in sorted(set(cur) & set(prev)):
        a, b = comparable(prev[i]), comparable(cur[i])
        if a == b:
            continue
        fields = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        changed.append({"id": i, "fields": fields})
        field_counts.update(fields)
        if a.get("licence_tier") != b.get("licence_tier"):
            tier_moves["%s->%s" % (a.get("licence_tier"), b.get("licence_tier"))] += 1
    return {
        "from": from_label, "to": to_label,
        "counts": {"previous": len(prev), "current": len(cur), "added": len(added),
                   "removed": len(removed), "changed": len(changed)},
        "fieldsChanged": dict(field_counts.most_common()),
        "tierMoves": dict(tier_moves),
        "note": "A change in sources[].fetched alone is not counted.",
        "added": added, "removed": removed, "changed": changed,
    }


def changelog_text(c, sample=15):
    n = c["counts"]
    out = [
        "Mot Dang open data: changes %s to %s" % (c["from"], c["to"]),
        "",
        "Records %d -> %d" % (n["previous"], n["current"]),
        "Added %d" % n["added"],
        "Removed %d" % n["removed"],
        "Changed %d" % n["changed"],
        "",
    ]
    if c["fieldsChanged"]:
        out.append("Fields changed, by number of records")
        for k, v in list(c["fieldsChanged"].items())[:15]:
            out.append("  %-16s %d" % (k, v))
        out.append("")
    if c["tierMoves"]:
        out.append("Licence tier moves")
        for k, v in c["tierMoves"].items():
            out.append("  %-24s %d" % (k, v))
        out.append("")
    if c["added"]:
        out.append("Added, first %d: %s" % (min(sample, len(c["added"])), ", ".join(c["added"][:sample])))
    if c["removed"]:
        out.append("Removed, first %d: %s" % (min(sample, len(c["removed"])), ", ".join(c["removed"][:sample])))
    out.append("")
    out.append("The full lists are in the JSON file beside this one.")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# LICENCES.txt
# ---------------------------------------------------------------------------
def licences_text(month, counts):
    return """Mot Dang open data, %(month)s

Each record sits in one file by the licences of its sources. The `licences`, `attribution` and `sources` fields on every record say which source gave what.

places-odbl.*  (%(odbl)d records)
  Contains a source that is OpenStreetMap, or a field derived from OpenStreetMap lines, and no source without a stated licence.
  Licence: Open Database Licence 1.0, https://opendatacommons.org/licenses/odbl/1-0/
  Credit: Contains information from OpenStreetMap, © OpenStreetMap contributors.
  A database you publish that is built from these records is under ODbL too.
  The records also carry fields from other sources (Overture, Thai government registers, Mot Dang's own collection); each record's `attribution` lists their credits.

places-open.*  (%(open)d records)
  Every source is Mot Dang's own collection, Overture, the OCHA admin boundaries, or a Thai government register that states an open licence. No OpenStreetMap.
  Compilation (selection, categories, labels, fields collected by Mot Dang): CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ , credit: %(self)s
  Overture places: CDLA-Permissive-2.0, https://cdla.dev/permissive-2-0/ , credit Overture Maps Foundation.
  Admin boundaries (amphoe, tambon): COD-AB Thailand, OCHA / Royal Thai Survey Department, CC BY-IGO.
  Thai government registers: the licence named in the record, credit the publisher named in `credit`.

places-unverified.*  (%(unverified)d records)
  At least one source states no licence, records none, or is a third-party page or a read made through a logged-in browser. No licence is offered for these records as a whole. They carry facts and the link to each source. Ask the source before you reuse them.
  A record here that also has an OpenStreetMap source lists ODbL-1.0 in `licences`; that part stays under ODbL.

Not in the export: photos (each carries its own credit and licence on its page), email addresses, Wikipedia extracts (CC BY-SA 4.0), free text copied from third-party pages, build bookkeeping. manifest.json counts each dropped field.

Site terms: https://motdang.net/terms.html
""" % dict(month=month, self=ATTRIBUTION_SELF, **counts)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="parent directory; files go in OUT/<month>/")
    ap.add_argument("--month", default=date.today().strftime("%Y-%m"))
    ap.add_argument("--prev", help="a previous export directory, for the changelog")
    ap.add_argument("--source", default=SOURCE_URL, help="places.json, path or URL")
    ap.add_argument("--index", default=INDEX_URL, help="api/v1/index.json (id -> page slug), path or URL; 'none' skips")
    ap.add_argument("--sample", type=int, default=0, help="keep about this many records (every k-th by id)")
    ap.add_argument("--no-parquet", action="store_true")
    ap.add_argument("--strip-river", action="store_true",
                    help="drop the river fields, which are derived from OSM lines; their records can then leave the ODbL tier")
    args = ap.parse_args()

    outdir = os.path.join(args.out, args.month)
    os.makedirs(outdir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="motdang-open-") as work:
        log("reading", args.source)
        src_path, src_info = fetch(args.source, work)
        src_info["sha256"] = sha256(src_path)
        with open(src_path, encoding="utf-8") as f:
            records = json.load(f)

        slugs = {}
        idx_info = None
        if args.index != "none":
            log("reading", args.index)
            idx_path, idx_info = fetch(args.index, work)
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
            idx_info["generated"] = idx.get("generated")
            slugs = {p["id"]: p["slug"] for p in idx["places"] if p.get("slug")}
            del idx

    records.sort(key=lambda r: r["id"])
    if args.sample and args.sample < len(records):
        k = max(1, len(records) // args.sample)
        records = records[::k]
    log("records", len(records))

    dropped = Counter()
    rows = [build_row(r, slugs, dropped, args.strip_river) for r in records]
    del records

    by_tier = defaultdict(list)
    for row in rows:
        by_tier[row["licence_tier"]].append(row)

    files = {}
    counts = {}
    for tier in TIER_ORDER:
        tier_rows = by_tier.get(tier, [])
        counts[tier] = len(tier_rows)
        w = write_tier(outdir, tier, tier_rows)
        entry = {"records": len(tier_rows), "pinned": w["pinned"], "licence": TIER_LICENCE[tier],
                 "ndjson": os.path.basename(w["ndjson"]), "geojson": os.path.basename(w["geojson"])}
        if not args.no_parquet and tier_rows:
            pq = write_parquet(outdir, tier, w["ndjson"])
            if pq:
                entry["parquet"] = os.path.basename(pq)
        files[tier] = entry
        log(tier, len(tier_rows))

    licence_counts = Counter()
    pushed_by_river = 0
    for row in rows:
        licence_counts.update(row["licences"])
    for row in rows:
        if row["licence_tier"] == "odbl" and not any(s["type"] == "osm" for s in row["sources"]):
            pushed_by_river += 1

    with open(os.path.join(outdir, "LICENCES.txt"), "w", encoding="utf-8") as f:
        f.write(licences_text(args.month, counts))

    manifest = {
        "month": args.month,
        "records": len(rows),
        "sample": args.sample or None,
        "stripRiver": args.strip_river,
        "input": {"places": src_info, "index": idx_info},
        "tiers": files,
        "recordsPerLicenceLabel": dict(licence_counts.most_common()),
        "odblOnlyBecauseOfDerivedOsmFields": pushed_by_river,
        "recordsWithoutPageUrl": sum(1 for r in rows if not r["url"]),
        "dropped": {k: {"records": v, "why": (DROP_TOP.get(k) or DROP_ATTR.get(k.split(".", 1)[-1]) or
                                              ("derived from OSM waterway lines" if k.split(".", 1)[-1] in RIVER_ATTRS
                                               else "attrs prefix rule"))}
                    for k, v in sorted(dropped.items())},
        "licenceNote": "See LICENCES.txt. Tier labels are set in export.py (TIER_LICENCE).",
    }
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    if args.prev:
        log("changelog against", args.prev)
        prev = load_prev(args.prev)
        c = changelog(prev, rows, os.path.basename(os.path.normpath(args.prev)), args.month)
        with open(os.path.join(outdir, "changelog-%s.json" % args.month), "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
            f.write("\n")
        with open(os.path.join(outdir, "changelog-%s.txt" % args.month), "w", encoding="utf-8") as f:
            f.write(changelog_text(c))

    sizes = {n: os.path.getsize(os.path.join(outdir, n)) for n in sorted(os.listdir(outdir))}
    print(json.dumps({"out": outdir, "counts": counts, "bytes": sizes}, indent=1))


if __name__ == "__main__":
    main()
