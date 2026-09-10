"""
Liquidity Radar — stage 3. Read the article, name the individual.

    python enrich.py --dry           # show what it would do, touch nothing
    python enrich.py --limit 20
    python enrich.py --stats

86% of deals name no individual, and naming individuals is the entire product.
Stage 2 only ever sees a headline and 400 characters of description, so a name
sitting in paragraph four is invisible to it. This reads the article.

## Everything here is free

  1. Resolve the Google News token to a publisher url  (gnews.py, Google's own
     endpoint, no key)
  2. Fetch the article                                 (plain requests)
  3. Extract                                           (Gemini Flash-Lite,
                                                        free tier, via llm.py)

Measured 2026-09-10 on deals that previously had NO usable link at all:
token resolution 20/20, fetch 19/20, extraction 19/20.

## Why it targets by deal type

Individuals are not spread evenly, and the spread is the whole design:

    block deal        37.5% name someone   (80 deals)
    promoter sale     27.8%                (36)
    open offer        15.6%                (32)
    strategic buyout   8.3%                (109)
    ipo-ofs            7.7%                (91)
    drhp filing        5.1%                (175)  <- biggest bucket, worst yield

A DRHP article names nobody because nobody is personally being paid; that is
the reality, not an extraction failure. Running the chain untargeted returned
individuals on 10% of deals; running it on block deals and promoter sales
returned 20% — same code, same prompt, twice the yield. So PRIORITY orders the
queue and DRHP filings go last.

## What it will not do

It never overwrites a value the pipeline already has. An enriched figure is a
model reading prose; a stated figure came from the alert path and may be
exchange-confirmed. Enrichment only ever FILLS A HOLE — see _merge. Every
attempt is recorded in `deal_enrichment` whether or not it found anything, so
a deal is read once rather than re-fetched every run.
"""
import argparse
import html
import json
import re
import sys
import time

import requests

import config
import db
import gnews
import llm

# Highest-yield first. Anything not listed is enriched after these, and
# "drhp filing" is pinned last on purpose (5.1% yield, 175 deals — it would
# eat the whole budget for almost nothing).
PRIORITY = ["block deal", "promoter sale", "open offer", "pe secondary",
            "strategic buyout", "other", "unknown", "pe primary", "ipo-ofs",
            "drhp filing"]

MAX_ATTEMPTS = 2          # give up on a deal that has failed this many times
ARTICLE_CHARS = 6000      # ~1,500 tokens; enough for the body, cheap to send
FETCH_TIMEOUT = 25

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "individuals": {"type": "ARRAY", "items": {"type": "STRING"}},
        "amount_cr": {"type": "NUMBER", "nullable": True},
        "amount_raw": {"type": "STRING", "nullable": True},
        "seller": {"type": "STRING", "nullable": True},
        "buyer": {"type": "STRING", "nullable": True},
        "advisers": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["individuals", "amount_cr", "amount_raw", "seller", "buyer", "advisers"],
}

PROMPT = """You are reading one Indian M&A / stake-sale / IPO article for a \
private banker who prospects individuals about to receive large sums.

Extract ONLY what the article states. Never infer, never use prior knowledge.

individuals — named people who PERSONALLY receive money: promoters, founders,
  family shareholders selling their own holding. NOT executives merely quoted,
  NOT journalists, NOT analysts, NOT company officers who are not selling, NOT
  people named only as buyers' representatives. An empty list is the correct
  and common answer.
seller — the party RECEIVING the money. buyer — the party PAYING.
amount_cr — the DEAL value in crore INR. Not revenue, not valuation, not market
  cap, not a fund size, not a share price. A valuation alone means null.
amount_raw — the exact phrase the figure came from, quoted from the article,
  plus your conversion if you made one. The site shows this next to the number
  so a reader can check it; a paraphrase is useless there.
advisers — banks or law firms named as advising on this transaction.

Unstated means null, or an empty list."""

_session = None


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": gnews.UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "en-IN,en;q=0.9",
        })
    return _session


def article_text(url):
    """Readable text from an article url. Raises on anything unusable."""
    r = _sess().get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    if "pdf" in (r.headers.get("content-type") or "").lower():
        # Prospectuses and exchange PDFs need a parser we do not have here.
        raise ValueError("pdf")
    body = re.sub(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>",
                  " ", r.text)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", html.unescape(body)).strip()
    if len(body) < 400:
        raise ValueError(f"too short ({len(body)} chars)")
    return body[:ARTICLE_CHARS]


def extract(headline, body):
    text = llm.complete(
        system=PROMPT,
        user=f"Headline: {headline}\n\nARTICLE:\n{body}",
        max_tokens=1024,
        schema=SCHEMA,
        stage="enrich",
    )
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def _clean_names(names):
    """Drop obvious non-people before anything reaches the database."""
    out = []
    for n in names or []:
        n = " ".join(str(n).split())
        if not n or len(n) > 60 or len(n.split()) < 2:
            continue              # single tokens are almost always a company
        if re.search(r"\b(Ltd|Limited|Pvt|Inc|LLP|Fund|Capital|Partners|Trust|"
                     r"Holdings|Ventures|Group|Bank|PLC)\b", n, re.I):
            continue
        out.append(n)
    return out


def _merge(deal, found):
    """Fields to write. ONLY fills holes — never overwrites what we have.

    A stated amount reached the deal through the alert path and may be
    exchange-confirmed; an enriched one is a model reading prose. Where both
    exist, the existing value wins, always.
    """
    fields = {}
    names = _clean_names(found.get("individuals"))
    existing = json.loads(deal["individuals"] or "[]")
    if names and not existing:
        fields["individuals"] = names
    if found.get("amount_cr") is not None and deal["amount_cr"] is None:
        fields["amount_cr"] = float(found["amount_cr"])
        # The site renders amount_raw beside the figure so a reader can check
        # it, so the model's quoted phrase goes in rather than a label. The
        # suffix marks where it came from without hiding the evidence.
        raw = (found.get("amount_raw") or "").strip()
        fields["amount_raw"] = (f"{raw[:160]} (from article)" if raw
                                else "stated in article text")
        # Same class as stage 2's "stated": a figure the article asserts, as
        # opposed to one computed from stake x market cap.
        fields["size_source"] = "stated"
    for key in ("seller", "buyer"):
        val = " ".join((found.get(key) or "").split())
        if val and not (deal[key] or "").strip():
            # Articles often list several counterparties; the column holds one
            # line, so keep it readable rather than truncating mid-name.
            if len(val) > 120:
                val = val[:117].rsplit(",", 1)[0] + " +others"
            fields[key] = val
    return fields


def candidates(limit, path=db.DB_PATH):
    """Unenriched deals with no named individual, highest-yield type first."""
    conn = db._conn(path)
    rows = [dict(r) for r in conn.execute(
        "SELECT d.* FROM deals d "
        "LEFT JOIN deal_enrichment e ON e.deal_id = d.id "
        "WHERE (d.individuals IS NULL OR d.individuals IN ('[]','','null')) "
        "  AND (e.deal_id IS NULL OR e.attempts < ?) "
        "  AND (e.status IS NULL OR e.status != 'ok') "
        "ORDER BY d.id DESC", (MAX_ATTEMPTS,))]
    conn.close()
    rank = {t: i for i, t in enumerate(PRIORITY)}
    rows.sort(key=lambda d: (rank.get((d["deal_type"] or "").lower(), len(PRIORITY)), -d["id"]))
    return rows[:limit] if limit else rows


def enrich_one(deal, dry=False, path=None):
    """Returns (status, applied_fields).

    `path` is passed to every db call explicitly. Do NOT reintroduce reliance
    on db.DB_PATH here: those defaults bind at import time, so reassigning the
    module attribute does nothing and the writes go to the default database.
    """
    path = path or db.DB_PATH
    url = gnews.resolve(deal["url"], path=path, write_cache=not dry)
    if not url:
        if not dry:
            db.record_enrichment(deal["id"], deal["url"], "unresolved", path=path)
        return "unresolved", {}
    try:
        body = article_text(url)
    except Exception as exc:  # noqa: BLE001
        if not dry:
            db.record_enrichment(deal["id"], url, "fetch_failed", path=path)
        return f"fetch_failed ({type(exc).__name__})", {}
    try:
        found = extract(deal["one_line"] or deal["company"], body)
    except Exception as exc:  # noqa: BLE001
        if not dry:
            db.record_enrichment(deal["id"], url, "extract_failed", path=path)
        return f"extract_failed ({type(exc).__name__})", {}

    fields = _merge(deal, found)
    if not dry:
        if fields:
            db.update_deal(deal["id"], dict(fields), path=path)
        db.record_enrichment(deal["id"], url, "ok", found, sorted(fields), path=path)
    return "ok", fields


# --------------------------------------------------------------------------
# INLINE ENTRY POINTS — called by main.py every run, right after stage 2.
#
# Both are TOTALLY NON-FATAL. Enrichment is an improvement to a record that is
# already correct without it, so nothing here may ever stop an alert going out
# or a deal being published. Every failure is caught, logged and swallowed;
# the caller publishes whatever stage 2 produced.
# --------------------------------------------------------------------------
def _deals_by_id(deal_ids, path):
    if not deal_ids:
        return []
    conn = db._conn(path)
    marks = ",".join("?" * len(deal_ids))
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM deals WHERE id IN ({marks})", tuple(deal_ids))]
    conn.close()
    return rows


def enrich_new(deal_ids, limit=None, path=None, dry=False):
    """Enrich deals this run just created, BEFORE their alert is sent.

    This is the half that changes what the banker actually reads: a name found
    in the article reaches the Telegram message instead of surfacing on the
    website an hour later. Returns {deal_id: applied_fields} for the caller to
    refresh its alerts from.
    """
    path = path or db.DB_PATH
    limit = config.ENRICH_NEW_PER_RUN if limit is None else limit
    out = {}
    try:
        deals = _deals_by_id(deal_ids, path)
        # Only ones with nobody named — the rest need nothing.
        deals = [d for d in deals
                 if (d["individuals"] or "[]") in ("[]", "", "null")][:limit]
        for deal in deals:
            try:
                status, fields = enrich_one(deal, dry=dry, path=path)
            except Exception as exc:  # noqa: BLE001
                print(f"[enrich] deal {deal['id']} failed, alert unaffected: "
                      f"{type(exc).__name__}: {exc}")
                continue
            if fields:
                out[deal["id"]] = fields
            print(f"[enrich] new deal {deal['id']} {status}: "
                  f"{', '.join(sorted(fields)) or 'nothing new'}")
    except Exception as exc:  # noqa: BLE001 — never block the alert path
        print(f"[enrich] new-deal pass failed entirely, publishing stage-2 "
              f"output: {type(exc).__name__}: {exc}")
    return out


def drain_backlog(limit=None, path=None, dry=False):
    """Work through older unnamed deals. Runs AFTER alerts are away, so a slow
    fetch cannot delay a notification. Never raises."""
    path = path or db.DB_PATH
    limit = config.ENRICH_BACKLOG_PER_RUN if limit is None else limit
    named = 0
    try:
        queue = candidates(limit, path=path)
        for deal in queue:
            try:
                _, fields = enrich_one(deal, dry=dry, path=path)
                if "individuals" in fields:
                    named += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[enrich] backlog deal {deal['id']}: "
                      f"{type(exc).__name__}: {exc}")
        if queue:
            print(f"[enrich] backlog: read {len(queue)}, named {named}")
    except Exception as exc:  # noqa: BLE001
        print(f"[enrich] backlog pass failed: {type(exc).__name__}: {exc}")
    return named


def main():
    p = argparse.ArgumentParser(description="Stage 3 — read the article, name the individual")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--dry", action="store_true", help="report only, write nothing")
    p.add_argument("--stats", action="store_true", help="show the queue and exit")
    p.add_argument("--type", help="only this deal_type")
    p.add_argument("--db", default=db.DB_PATH,
                   help="operate on this database (for testing against a copy)")
    args = p.parse_args()

    db.init_db(args.db)
    queue = candidates(None, path=args.db)
    if args.type:
        queue = [d for d in queue if (d["deal_type"] or "").lower() == args.type.lower()]

    if args.stats:
        from collections import Counter
        by = Counter((d["deal_type"] or "unknown").lower() for d in queue)
        print(f"{len(queue)} deals await enrichment (no individual named yet)\n")
        for t in PRIORITY:
            if by.get(t):
                print(f"  {t:20} {by[t]:5d}")
        for t, n in by.items():
            if t not in PRIORITY:
                print(f"  {t:20} {n:5d}  (unranked)")
        return

    queue = queue[: args.limit]
    print(f"[enrich] {len(queue)} deals this pass"
          f"{' (dry run, nothing written)' if args.dry else ''}\n")
    stats = {"ok": 0, "named": 0, "filled": 0}
    for i, deal in enumerate(queue, 1):
        status, fields = enrich_one(deal, dry=args.dry, path=args.db)
        if status == "ok":
            stats["ok"] += 1
            if "individuals" in fields:
                stats["named"] += 1
            if fields:
                stats["filled"] += 1
        got = ", ".join(f"{k}={fields[k]}" for k in sorted(fields)) if fields else "nothing new"
        print(f"  [{i:3d}/{len(queue)}] {status:26} {(deal['company'] or '')[:24]:26} {got[:70]}")

    n = len(queue)
    print(f"\n[enrich] read {stats['ok']}/{n}, "
          f"filled a gap on {stats['filled']}, NAMED AN INDIVIDUAL on {stats['named']}")
    line = llm.summary()
    if line:
        print(line)


if __name__ == "__main__":
    main()
