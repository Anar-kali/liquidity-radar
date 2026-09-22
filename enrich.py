"""
Liquidity Radar — stage 3. Read the article, name the individual.

    python enrich.py --dry           # show what it would do, touch nothing
    python enrich.py --limit 20
    python enrich.py --stats

Stage 2 only ever sees a headline and 400 characters of description, so a name
sitting in paragraph four is invisible to it, and there is nothing to summarise
from. This reads the article, for EVERY deal that passed stage 2.

## Everything here is free

  1. Resolve the Google News token to a publisher url  (gnews.py, Google's own
     endpoint, no key)
  2. Fetch the article                                 (plain requests)
  3. Extract                                           (Gemini Flash-Lite,
                                                        free tier, via llm.py)

Measured 2026-09-10 on deals that previously had NO usable link at all:
token resolution 20/20, fetch 19/20, extraction 19/20.

## It only ever looks at recent deals

ENRICH_MAX_AGE_HOURS bounds everything. This is a prospecting tool, not an
archive — a deal from five weeks ago is of no use to a banker, so old deals are
never revisited however many of them name nobody. The window is the same 24
hours the website's front page shows, so "what the site shows" and "what gets
enriched" are the same set. `--max-age-hours 0` lifts the bound for a manual
catch-up, and is the only way to reach older deals.

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

MAX_ATTEMPTS = config.ENRICH_MAX_ATTEMPTS
RETRY_PAUSE_SECONDS = 3   # between a failure and its immediate retry
# The prompt asks for 100-150 words. Anything far under that is the model
# padding a headline rather than summarising an article, and reads worse than
# no synopsis at all. There is no upper guard: an over-long synopsis is still
# accurate prose, and truncating it would cut a sentence in half.
MIN_SYNOPSIS_WORDS = 40
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
        "synopsis": {"type": "STRING", "nullable": True},
        "seller_type": {
            "type": "STRING",
            "enum": ["individual", "family", "fund", "company", "government", "unclear"],
        },
        "seller_stake_pct": {"type": "NUMBER", "nullable": True},
    },
    "required": ["individuals", "amount_cr", "amount_raw", "seller", "buyer",
                 "advisers", "synopsis", "seller_type", "seller_stake_pct"],
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
seller_type — what KIND of party the seller is, which decides whether a person
  is being paid at all:
    individual  a named person selling their own holding
    family      a promoter family, family office or family trust
    fund        a private equity, venture capital or buyout fund, a sovereign
                wealth fund, or a holding vehicle whose owner is one of those.
                Actis, Blackstone, KKR, TPG, Peak XV, Temasek and their peers
                are funds however the article words it. Choose this when the
                company being sold is owned by such a fund, since the proceeds
                go to the fund's investors and not to any individual.
    company     an operating business selling a subsidiary or a stake
    government  a state or central government, or a PSU divesting
    unclear     the article does not say
  Judge the SELLER, not the buyer, and not the company being sold.
seller_stake_pct — the percentage of the company this sale covers, as a number.
  "acquires Athena Renewables from Actis" with no figure but clearly the whole
  business is 100. "sells a 1.6% stake" is 1.6. "sells its entire 25% holding"
  is 25 — the ENTIRE holding is still a quarter of the company, and that is
  what this field means: the share of the COMPANY changing hands, not the
  share of the seller's own position. Null if the article does not say.
amount_cr — the DEAL value in crore INR. Not revenue, not valuation, not market
  cap, not a fund size, not a share price. A valuation alone means null.
amount_raw — the exact phrase the figure came from, quoted from the article,
  plus your conversion if you made one. The site shows this next to the number
  so a reader can check it; a paraphrase is useless there.
advisers — banks or law firms named as advising on this transaction.

Unstated means null, or an empty list.

synopsis — 100 to 150 words, plain prose, for a banker deciding in ten seconds
  whether this deal is worth a call. Cover what happened, who is on each side,
  the money, and the timing or condition that matters. Lead with the fact, not
  with "This article discusses". Write only what the article states — no
  background you are supplying yourself, no market commentary, no advice, and
  no speculation about what it might mean. If the article is too thin to
  support 100 words, write what it does support and stop; a short accurate
  synopsis is worth more than a padded one."""

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
    # Same fill-a-hole rule as everything else: never overwrite a synopsis a
    # previous run already wrote. Re-reading the same article twice can produce
    # slightly different prose, and silently rewording a page the banker may
    # already have read is worse than leaving it alone.
    synopsis = " ".join((found.get("synopsis") or "").split())
    if synopsis and not (deal.get("synopsis") or "").strip():
        words = len(synopsis.split())
        if words < MIN_SYNOPSIS_WORDS:
            # Thin article, or the model padding a headline. Not worth showing.
            print(f"[enrich] deal {deal['id']} synopsis only {words} words, dropped")
        else:
            fields["synopsis"] = synopsis

    st = (found.get("seller_type") or "").strip().lower()
    if st and st != "unclear" and not (deal.get("seller_type") or "").strip():
        fields["seller_type"] = st
    pct = found.get("seller_stake_pct")
    if pct is not None and deal.get("seller_stake_pct") is None:
        try:
            fields["seller_stake_pct"] = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            pass

    for key in ("seller", "buyer"):
        val = " ".join((found.get(key) or "").split())
        if val and not (deal[key] or "").strip():
            # Articles often list several counterparties; the column holds one
            # line, so keep it readable rather than truncating mid-name.
            if len(val) > 120:
                val = val[:117].rsplit(",", 1)[0] + " +others"
            fields[key] = val
    return fields


def candidates(limit, path=db.DB_PATH, max_age_hours=None):
    """Recent deals not yet read, best type first.

    EVERY deal that passed stage 2 is a candidate, including ones that already
    name somebody. Until 2026-09-15 this skipped them — stage 3 existed only to
    find names, so a deal that had one needed nothing. Then stage 3 started
    producing the synopsis the site shows, and that logic inverted: skipping
    named deals meant the BEST deals were the ones with no synopsis. Of the 49
    deals in the first four days, 11 were skipped exactly that way.

    Bounded by age on purpose. This is a prospecting tool, not an archive: a
    deal from five weeks ago is of no use to a banker, so old ones are never
    revisited. The window matches the site's own 24-hour front page.
    """
    hours = config.ENRICH_MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    conn = db._conn(path)
    sql = ("SELECT d.* FROM deals d "
           "LEFT JOIN deal_enrichment e ON e.deal_id = d.id "
           "WHERE (e.deal_id IS NULL OR e.attempts < ?) "
           "  AND (e.status IS NULL OR e.status != 'ok') ")
    params = [MAX_ATTEMPTS]
    if hours:
        sql += "  AND d.created_at >= datetime('now', ?) "
        params.append(f"-{int(hours)} hour")
    rows = [dict(r) for r in conn.execute(sql + "ORDER BY d.id DESC", params)]
    conn.close()
    rank = {t: i for i, t in enumerate(PRIORITY)}
    rows.sort(key=lambda d: (rank.get((d["deal_type"] or "").lower(), len(PRIORITY)), -d["id"]))
    return rows[:limit] if limit else rows


def _try_once(deal, path, dry):
    """One pass at resolve -> fetch -> extract. Writes nothing.

    Returns (status, url, found). status is "ok" or a failure reason.
    """
    url = gnews.resolve(deal["url"], path=path, write_cache=not dry)
    if not url:
        return "unresolved", deal["url"], None
    try:
        body = article_text(url)
    except Exception as exc:  # noqa: BLE001
        return f"fetch_failed:{type(exc).__name__}", url, None
    try:
        return "ok", url, extract(deal["one_line"] or deal["company"], body)
    except Exception as exc:  # noqa: BLE001
        return f"extract_failed:{type(exc).__name__}", url, None


def enrich_one(deal, dry=False, path=None):
    """Returns (status, applied_fields).

    Retries ONCE immediately before giving up. Most failures here are
    transient — a publisher rate-limiting, a slow gateway, a momentary 5xx —
    and a second try seconds later costs one request and often succeeds.

    The immediate retry is deliberately NOT a second recorded attempt. What
    gets counted in `deal_enrichment.attempts` is one attempt PER RUN, because
    that is what the user-facing wording promises: "will try next run". Two
    recorded attempts means we have tried across two runs and stopped.

    `path` is passed to every db call explicitly. Do NOT reintroduce reliance
    on db.DB_PATH here: those defaults bind at import time, so reassigning the
    module attribute does nothing and the writes go to the default database.
    """
    path = path or db.DB_PATH
    status, url, found = _try_once(deal, path, dry)
    if status != "ok":
        time.sleep(RETRY_PAUSE_SECONDS)
        retry_status, retry_url, retry_found = _try_once(deal, path, dry)
        print(f"[enrich] deal {deal['id']} {status} — retried immediately, "
              f"{'recovered' if retry_status == 'ok' else retry_status}")
        status, url, found = retry_status, retry_url, retry_found

    if status != "ok":
        if not dry:
            db.record_enrichment(deal["id"], url, status.split(":")[0], path=path)
        return status, {}

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


# The three states the rest of the system renders. Derived from
# deal_enrichment rather than stored twice, so they cannot drift.
#
#   None        enriched fine, or never attempted — show nothing
#   "retrying"  failed, another run will try — Telegram AND website
#   "failed"    failed twice, we have stopped — WEBSITE ONLY, no second text
#
# The asymmetry is deliberate: the banker is told once that a deal is
# incomplete and will be retried, and never pinged again about the same deal.
# The website carries the final state because it is pulled, not pushed.
STATE_RETRYING = db.ENRICHMENT_RETRYING
STATE_FAILED = db.ENRICHMENT_FAILED
enrichment_state = db.enrichment_state


def retry_failed(limit=None, path=None, dry=False):
    """Second and final attempt at deals whose enrichment failed earlier.

    NOT a sweep over everything unnamed — only deals that actually failed and
    have attempts left, inside the window. A deal that simply had no name in
    its article is finished, not pending, and must not be fetched again.

    Runs after the alerts are away and never raises. Sends nothing: the deal
    has already been alerted once, and a second text about the same deal is
    exactly what we are avoiding.
    """
    path = path or db.DB_PATH
    limit = config.ENRICH_RETRY_PER_RUN if limit is None else limit
    hours = config.ENRICH_MAX_AGE_HOURS
    recovered = 0
    try:
        conn = db._conn(path)
        rows = [dict(r) for r in conn.execute(
            "SELECT d.* FROM deals d JOIN deal_enrichment e ON e.deal_id = d.id "
            "WHERE e.status != 'ok' AND e.attempts < ? "
            "  AND d.created_at >= datetime('now', ?) "
            "ORDER BY d.id DESC LIMIT ?",
            (MAX_ATTEMPTS, f"-{int(hours)} hour", limit))]
        conn.close()
        for deal in rows:
            try:
                status, fields = enrich_one(deal, dry=dry, path=path)
                if status == "ok":
                    recovered += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[enrich] retry deal {deal['id']}: {type(exc).__name__}: {exc}")
        if rows:
            print(f"[enrich] retried {len(rows)} failed deals, {recovered} recovered, "
                  f"{len(rows) - recovered} now marked failed for good")
    except Exception as exc:  # noqa: BLE001
        print(f"[enrich] retry pass failed: {type(exc).__name__}: {exc}")
    return recovered


_PEOPLE_IN_SELLER = re.compile(
    r"promoter|founder|family|\bheir|\bmr\.?\b|\bmrs\.?\b|\bshri\b|"
    r"individual|personal|huf\b", re.I)


def _mentions_people(seller):
    """Does the seller string name or describe actual people?

    An article routinely says "the promoters" without listing them, which
    `individuals` cannot capture — but a promoter selling IS the lead.
    """
    return bool(_PEOPLE_IN_SELLER.search(seller or ""))


def post_gate(deal):
    """Why this deal should be dropped now that the article has been read, or
    None to keep it.

    Both gates are pure judgement on facts stage 3 has ALREADY produced — no
    API call, no extra latency. They exist because every earlier gate ran
    before those facts existed: stage 2 saw a headline and 400 characters, so
    it had no amount to measure and no seller to judge.

    SIZE. Stage 2 lets a deal through as "size undisclosed" and stage 3 then
    finds the figure in the article. Eight deals reached the site that way,
    including one at Rs 0.07cr against a Rs 250cr floor. Size wins over a named
    individual, the same way stage 2's own Rule 8 already works — a Rs 1cr sale
    is not a lead however well we know the seller.

    FUND. A company owned outright by a fund pays its investors, not a person.
    Athena Renewables sold by Actis for Rs 2,500cr is a real transaction and a
    useless lead. Only fires when NOBODY is named: if a promoter is selling
    alongside the fund, the promoter is the lead and the deal stays.
    """
    amount = deal.get("amount_cr")
    if amount is not None and amount < config.THRESHOLD_CR:
        return f"below threshold: Rs {amount:,.2f}cr < Rs {config.THRESHOLD_CR}cr"

    if (deal.get("seller_type") or "").strip().lower() == "fund":
        seller = (deal.get("seller") or "the seller").strip()
        try:
            named = bool(json.loads(deal.get("individuals") or "[]"))
        except (TypeError, ValueError):
            named = False
        # A seller described as promoters, founders or a family is a person in
        # the chain even when the article never lists names. RSB Transmissions,
        # at Rs 17,600cr, was sold by "RSB Transmissions promoters/Bain
        # Capital" and the first version of this gate dropped it — the largest
        # single loss it caused.
        if _mentions_people(seller):
            return None
        pct = deal.get("seller_stake_pct")
        # Being PE-backed is not being PE-owned. A fund trimming a minority
        # stake leaves the promoters holding the rest, so there is still
        # somebody to call. Only a company owned outright pays nobody but the
        # fund's own investors. An unstated stake keeps the deal.
        if not named and pct is not None and pct >= config.FUND_WHOLE_OWNERSHIP_PCT:
            return (f"wholly fund-owned ({pct:.0f}% sold), no individual paid: "
                    f"{seller[:50]}")
    return None


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
        # Every deal that reached here passed stage 2, and every one of them
        # gets read. Filtering to nameless deals would leave the best deals —
        # the ones that already name somebody — with no synopsis.
        deals = deals[:limit]
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


def enrich_recent(limit=None, path=None, dry=False):
    """Catch anything inside the window that still names nobody — a deal whose
    fetch failed, or one created just before this shipped.

    Runs AFTER alerts are away, so a slow fetch cannot delay a notification.
    Never raises. Deals older than ENRICH_MAX_AGE_HOURS are out of scope and
    stay that way; we do not go back over old deals.
    """
    path = path or db.DB_PATH
    limit = config.ENRICH_RECENT_PER_RUN if limit is None else limit
    named = 0
    try:
        queue = candidates(limit, path=path)
        for deal in queue:
            try:
                _, fields = enrich_one(deal, dry=dry, path=path)
                if "individuals" in fields:
                    named += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[enrich] recent deal {deal['id']}: "
                      f"{type(exc).__name__}: {exc}")
        if queue:
            print(f"[enrich] recent: read {len(queue)}, named {named}")
    except Exception as exc:  # noqa: BLE001
        print(f"[enrich] recent pass failed: {type(exc).__name__}: {exc}")
    return named


def main():
    p = argparse.ArgumentParser(description="Stage 3 — read the article, name the individual")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--dry", action="store_true", help="report only, write nothing")
    p.add_argument("--stats", action="store_true", help="show the queue and exit")
    p.add_argument("--type", help="only this deal_type")
    p.add_argument("--db", default=db.DB_PATH,
                   help="operate on this database (for testing against a copy)")
    p.add_argument("--max-age-hours", type=int, default=None,
                   help=f"only deals newer than this (default "
                        f"{config.ENRICH_MAX_AGE_HOURS}; 0 = no limit, which "
                        f"reaches back over old deals we normally ignore)")
    args = p.parse_args()

    db.init_db(args.db)
    queue = candidates(None, path=args.db, max_age_hours=args.max_age_hours)
    if args.type:
        queue = [d for d in queue if (d["deal_type"] or "").lower() == args.type.lower()]

    if args.stats:
        from collections import Counter
        by = Counter((d["deal_type"] or "unknown").lower() for d in queue)
        hours = (config.ENRICH_MAX_AGE_HOURS if args.max_age_hours is None
                 else args.max_age_hours)
        window = f"last {hours}h" if hours else "ALL TIME (no age limit)"
        print(f"{len(queue)} deals await enrichment, {window} "
              f"(no individual named yet)\n")
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
