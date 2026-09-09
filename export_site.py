"""
Liquidity Radar — static site export.

Turns `deals` + `deal_members` into the JSON documented in
docs/data-contract.md, so the website is a static site with no server:

    site/public/data/deals.json       feed slice — trimmed fields, newest first
    site/public/data/deals/<id>.json  one full Deal record each

    python export_site.py                 # export, default 90-day feed window
    python export_site.py --dry           # report what WOULD be written
    python export_site.py --days 0        # no window: every deal in the feed

Imports only `db`, `sizing` and `textutil` — never `classify` or `filters`,
which pull in the Anthropic SDK. A data-export step must not need the LLM SDK
installed to run. `sizing` is fine: it costs one 0.10s parse of the exchange
CSVs at import and drags in nothing else.

Two things here are easy to get wrong and both would publish a false number:

1. `size_band` is overloaded. On a `band` row it IS the estimate; on a
   `stated` row it is only a derived bucket of a figure we already know
   exactly. Reading it uniformly turns a fact into a guess, so it is read
   ONLY when size_source is 'band'.
2. `amount_raw` exists on rows with no amount, and is then a SHARE COUNT
   ("1.66 crore shares — no price stated"), not money. It is exported for
   provenance display, never as the amount.
"""
import argparse
import json
import os
import sqlite3
import sys

import db
import sizing
import textutil

# Vite serves site/public/* at the site root, so this lands at /data/*.
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "site", "public", "data")

# Feed retention. 90 days at ~18 deals/day is ~1,600 rows; the trimmed feed
# entry is small enough that this stays well under a megabyte. Per-deal files
# are always written for every deal, so nothing is lost from the window — an
# older deal stays reachable by its own URL.
DEFAULT_FEED_DAYS = 90

# A Google News link is an opaque redirect token, not a publisher URL: the
# real article is unrecoverable from it (see filters.py). 77% of source links
# are these, so the site must be able to say so rather than promising a
# publisher page it cannot deliver.
_OPAQUE_HOST = "news.google.com"

# The gated promoter/contact layer must never reach the public export. Checked
# as KEYS, not substrings of values — "promoters/selling shareholders" is a
# legitimate seller string and must not trip this.
FORBIDDEN_KEYS = {
    "contact", "contacts", "personal", "email", "irEmail", "mobile", "phone",
    "boardLine", "registeredOffice", "promoters", "promoter", "stakePct",
    "pledgedPct",
}

# Fields carried in the feed slice. Everything else lives in the per-deal file.
FEED_FIELDS = (
    "id", "company", "dealType", "confidence", "confirmed", "amountCr",
    "amountRaw", "sizeSource", "sizeBand", "oneLine", "seller", "individuals",
    "listed", "ticker", "primaryOutlet", "sourceCount", "createdAt", "updatedAt",
)


def _listing(company):
    """
    (listed, ticker) via the same NSE/BSE master-list resolution sizing.py
    uses for market caps.

    A False here means "no unique match in the exchange master lists", which
    is *usually* genuinely unlisted (Rentomojo, Zetwerk) but also catches a
    listed company whose name is ambiguous — sizing drops ambiguous keys
    rather than guessing. The site's Listing filter inherits that caveat.
    """
    try:
        match = sizing.resolve_company(company or "")
    except Exception:  # noqa: BLE001
        return False, None
    return (True, match["ticker"]) if match else (False, None)


def _individuals(raw):
    """deals.individuals is a JSON list; tolerate anything else as empty."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _size(row):
    """
    Resolve (amount_cr, size_source, size_band) into the contract's shape.

    Legacy rows pre-date the size_source column. Those that carry an amount
    were stated figures — verified across all 65 of them on 2026-09-08: none
    has a computed formula in amount_raw — so they are reported as 'stated'
    rather than as an unexplained null.
    """
    amount = row["amount_cr"]
    source = (row["size_source"] or "").strip() or None
    band = (row["size_band"] or "").strip() or None
    if band == "UNKNOWN":                     # a literal string, not a null
        band = None

    if source is None and amount is not None:
        source = "stated"

    # Only a band row's band is an estimate. Anywhere else it is a derived
    # bucket of a known figure and must not be shown as a range.
    if source != "band":
        band = None

    return amount, source, band


def _sources(deal_id, row, path):
    """
    Every article clustered into this deal, outlet name resolved.

    18% of deals have no deal_members rows at all, so the deal's own url is
    synthesised into a single Source rather than leaving Coverage empty.
    """
    out = []
    for member in db.members_for_deal(deal_id, path=path):
        outlet, title = textutil.split_publisher(member["title"])
        out.append({
            "outlet": outlet or row["source"] or "Unknown",
            "title": title,
            "url": member["url"] or "",
            "resolvable": _OPAQUE_HOST not in (member["url"] or ""),
            # When this article joined the cluster. The site's update list is
            # "coverage that arrived after the first article", so it needs the
            # attach time, not the deal's creation time.
            "attachedAt": member.get("attached_at"),
        })
    if out:
        return out

    url = row["url"] or ""
    if not url:
        return []
    # No clustered article: the outlet is whatever the pipeline recorded, and
    # there is no headline to show — one_line is a summary, not a headline,
    # so the title is left empty rather than passing a summary off as one.
    return [{
        "outlet": row["source"] or "Unknown",
        "title": "",
        "url": url,
        "resolvable": _OPAQUE_HOST not in url,
        "attachedAt": row["created_at"],
    }]


def build_deal(row, path):
    """One row of `deals` (plus its members) as the contract's Deal object."""
    amount, size_source, size_band = _size(row)
    company, _ = textutil.clean_company(row["company"])
    sources = _sources(row["id"], row, path)
    listed, ticker = _listing(company)

    return {
        "id": row["id"],
        "company": company,
        "dealType": row["deal_type"] or "unknown",
        "confidence": row["confidence"] or "medium",
        "confirmed": bool(row["confirmed"]),
        "amountCr": amount,
        # Provenance text for display beside the figure. NOT an amount: on a
        # mcap_plausible row this reads "1.66 crore shares — no price stated".
        "amountRaw": row["amount_raw"] or None,
        "sizeSource": size_source,
        "sizeBand": size_band,
        "oneLine": row["one_line"] or "",
        "seller": (row["seller"] or "").strip() or None,
        # Legacy column, never written since 2026-08-21 — populated on old
        # rows only. Exported so the UI can show it where it exists.
        "buyer": (row["buyer"] or "").strip() or None,
        "individuals": _individuals(row["individuals"]),
        "listed": listed,
        "ticker": ticker,
        # Not extracted by the pipeline yet; present so the shape is stable.
        "advisers": [],
        "sources": sources,
        "primaryUrl": row["url"] or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"] or row["created_at"],
    }


def feed_entry(deal):
    """The trimmed projection the feed list renders from."""
    entry = {k: deal[k] for k in FEED_FIELDS if k in deal}
    entry["primaryOutlet"] = deal["sources"][0]["outlet"] if deal["sources"] else None
    entry["sourceCount"] = len(deal["sources"])
    # Coverage that arrived after the first article, for the row's inline
    # update list. Carried in the feed rather than fetched on expand so the
    # interaction is instant; it is the headline and attach time only, not
    # the whole Source.
    entry["updates"] = [
        {"attachedAt": s["attachedAt"], "title": s["title"]}
        for s in deal["sources"][1:] if s["title"]
    ]
    return entry


def assert_public(payload):
    """Fail loudly if a gated field ever reaches the public export."""
    def walk(node, trail="$"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in FORBIDDEN_KEYS:
                    raise SystemExit(
                        f"[export] REFUSING TO WRITE: gated field {key!r} at {trail}"
                    )
                walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{trail}[{i}]")
    walk(payload)


def load_deals(path):
    """Every deal, newest first. The feed window is applied later, not here."""
    conn = db._conn(path)
    rows = conn.execute("SELECT * FROM deals ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def write_json(target, payload):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        # sort_keys + a fixed separator keeps the output byte-stable, so a run
        # that changes nothing produces no git diff. radar.db already strains
        # this repo's size limits; the export must not add churn of its own.
        json.dump(payload, handle, ensure_ascii=False, indent=1,
                  sort_keys=True, separators=(",", ": "))
        handle.write("\n")


def run(path, out_dir, days, dry):
    rows = load_deals(path)
    if not rows:
        print("[export] no deals in the database — nothing to write")
        return 0

    deals = [build_deal(r, path) for r in rows]

    if days > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        feed_deals = [d for d in deals if (d["createdAt"] or "") >= cutoff]
    else:
        feed_deals = deals

    feed = {
        # Derived from the data, not the wall clock: a run that changes
        # nothing must not rewrite this file.
        "generatedAt": max(d["updatedAt"] or "" for d in deals),
        "dealCount": len(feed_deals),
        "totalDeals": len(deals),
        "feedWindowDays": days or None,
        "deals": [feed_entry(d) for d in feed_deals],
    }

    assert_public(feed)
    for deal in deals:
        assert_public(deal)

    sized = sum(1 for d in deals if d["amountCr"] is not None)
    named = sum(1 for d in deals if d["individuals"])
    nosrc = sum(1 for d in deals if not d["sources"])
    opaque = sum(1 for d in deals for s in d["sources"] if not s["resolvable"])
    links = sum(len(d["sources"]) for d in deals)

    print(f"[export] {len(deals)} deals -> feed {len(feed_deals)}"
          f" (window: {days or 'all'} days)")
    print(f"[export] with amount {sized} ({100*sized//len(deals)}%) | "
          f"named individual {named} ({100*named//len(deals)}%) | "
          f"no sources {nosrc}")
    print(f"[export] {links} source links, {opaque} opaque "
          f"({100*opaque//links if links else 0}%)")

    if dry:
        print("[export] --dry: nothing written")
        return 0

    write_json(os.path.join(out_dir, "deals.json"), feed)
    for deal in deals:
        write_json(os.path.join(out_dir, "deals", f"{deal['id']}.json"), deal)
    print(f"[export] wrote deals.json + {len(deals)} per-deal files to {out_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Export deals for the static site.")
    parser.add_argument("--db", default=db.DB_PATH, help="path to radar.db")
    parser.add_argument("--out", default=OUT_DIR, help="output directory")
    parser.add_argument("--days", type=int, default=DEFAULT_FEED_DAYS,
                        help="feed window in days; 0 for every deal")
    parser.add_argument("--dry", action="store_true",
                        help="report what would be written, write nothing")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[export] no database at {args.db}")
        return 1
    try:
        return run(args.db, args.out, args.days, args.dry)
    except sqlite3.Error as exc:
        print(f"[export] database error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
