"""
Liquidity Radar — pattern alerts, resolved for display.

A PATTERN alert (sales_tracker.py) fires when several sub-threshold sales by
one person in one company add up over a 90-day window. `pattern_alerts` stores
only the keys and the total, so the readable record — who, which company, and
the ledger of trades behind it — has to be rebuilt from `individual_sales`.

## The dedup, and why it is not optional

NSE publishes the same transaction in BOTH its bulk-deals and block-deals
files. deals_files.py records each as its own `individual_sales` row, one
tagged `bulk` and one `block`, so the rolling sum counts a single trade twice.

Measured on 2026-09-10: 10 of 15 stored alerts were inflated this way, and 6
collapsed to a SINGLE trade once deduplicated — GOVERNMENT OF SINGAPORE /
Ather Energy read "Rs 3,516cr over 2 sales" for one Rs 1,758cr trade.

It also defeats the guard meant to catch exactly this. config's
AGGREGATION_MAX_SINGLE_SHARE (0.70) rejects a "pattern" that is really one
large sale — but two identical rows are 50% each and pass it.

So a trade is identified here by (trade_date, value_cr) regardless of which
file reported it, and an alert whose distinct trades fall below
AGGREGATION_MIN_TRANSACTIONS is dropped: it is not a pattern, it is one sale
seen twice.

`trade_key` / `dedupe_rows` are the single definition of "same trade", used by
BOTH this module and sales_tracker's alerting path, so what fires on Telegram
and what the site shows can never drift apart.

Rows already in the database are left as they are — deals_files.py keeps
writing one row per file, which is faithful to what the exchange published.
The deduplication happens when they are read. Alerts that already fired with
inflated totals stay in `pattern_alerts` as a record of what was sent; the
site recomputes rather than trusting `total_cr`.
"""
from datetime import datetime

import config
import db

_DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d")


def parse_trade_date(value):
    """NSE files use a few formats; returns a date or None."""
    text = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def trade_key(row):
    """
    What makes two rows the same trade: the date and the value. NOT the
    source — that is precisely the field that differs when NSE lists one
    transaction in both its bulk and block files.
    """
    return (row.get("trade_date"), round(float(row.get("value_cr") or 0), 2))


def dedupe_rows(rows):
    """
    Database-shaped rows, one per distinct trade, first occurrence kept.
    Returns (kept, duplicates_removed).

    This is the function the ALERTING path uses (sales_tracker), so the rule
    that decides what fires and the rule that decides what the site shows are
    the same rule. dedupe_sales() below is the display projection of it.
    """
    seen, kept = set(), []
    for row in rows:
        key = trade_key(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept, len(rows) - len(kept)


def qualifies(trades):
    """
    Is this actually a pattern? Total over the threshold, enough distinct
    trades, and no single trade dominating — the last one is what separates a
    salami-slice from one large sale the normal pipeline already caught.

    Takes deduplicated values, and is the single definition of the test:
    sales_tracker applies it before alerting, build() applies it before
    displaying, so the site cannot show a "pattern" the system would no longer
    fire on.
    """
    values = [v for v in trades if v is not None]
    if not values:
        return False
    total = round(sum(values), 2)
    if total < config.AGGREGATION_MIN_CR:
        return False
    if len(values) < config.AGGREGATION_MIN_TRANSACTIONS:
        return False
    return max(values) <= config.AGGREGATION_MAX_SINGLE_SHARE * total


def dedupe_sales(sales):
    """
    One entry per distinct trade, in the shape the site renders. Same identity
    rule as dedupe_rows, but the reporting files are collected so the UI can
    show that a trade appeared in both.
    """
    merged = {}
    for s in sales:
        key = trade_key(s)
        entry = merged.setdefault(
            key,
            {"date": s.get("trade_date"), "valueCr": key[1], "sources": [], "parsed": parse_trade_date(s.get("trade_date"))},
        )
        src = s.get("source")
        if src and src not in entry["sources"]:
            entry["sources"].append(src)
    out = list(merged.values())
    # Undated rows sort last rather than crashing the comparison.
    out.sort(key=lambda e: (e["parsed"] is None, e["parsed"]))
    return out


def build(path=db.DB_PATH):
    """
    Every stored pattern alert, resolved and deduplicated, newest first.
    Returns (alerts, dropped) — `dropped` counts alerts that were not really
    patterns, so the count is reportable rather than silently vanishing.
    """
    conn = db._conn(path)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pattern_alerts ORDER BY alerted_at DESC"
    ).fetchall()]

    alerts, dropped = [], 0
    for row in rows:
        sales = [dict(r) for r in conn.execute(
            "SELECT person_name, company, trade_date, value_cr, source "
            "FROM individual_sales WHERE person_key = ? AND company_key = ? "
            "ORDER BY trade_date",
            (row["person_key"], row["company_key"]),
        ).fetchall()]
        if not sales:
            dropped += 1
            continue

        trades = dedupe_sales(sales)
        if not qualifies([t["valueCr"] for t in trades]):
            dropped += 1
            continue

        total = round(sum(t["valueCr"] for t in trades), 2)
        dated = [t["parsed"] for t in trades if t["parsed"]]
        span_days = (max(dated) - min(dated)).days if len(dated) > 1 else 0

        alerts.append({
            "id": row["id"],
            # person_name / company are denormalised onto every sale row, so
            # the first one is as good as any.
            "person": sales[0]["person_name"] or "Unnamed individual",
            "company": sales[0]["company"] or row["company_key"],
            "totalCr": total,
            "storedTotalCr": row["total_cr"],
            "saleCount": len(trades),
            "weeks": max(1, round(span_days / 7)) if span_days else 1,
            "firstTrade": trades[0]["date"],
            "lastTrade": trades[-1]["date"],
            "alertedAt": row["alerted_at"],
            "sales": [{"date": t["date"], "valueCr": t["valueCr"], "sources": t["sources"]} for t in trades],
        })

    conn.close()
    return alerts, dropped


if __name__ == "__main__":
    built, skipped = build()
    print(f"{len(built)} pattern alerts, {skipped} dropped as not-a-pattern")
    for a in built:
        infl = a["storedTotalCr"] / a["totalCr"] if a["totalCr"] else 1
        flag = f"  (stored Rs {a['storedTotalCr']}cr, {infl:.1f}x)" if infl > 1.05 else ""
        print(f"  Rs {a['totalCr']:>9.2f}cr  {a['saleCount']} trades / {a['weeks']}w  "
              f"{a['person'][:30]:30} {a['company'][:24]}{flag}")
