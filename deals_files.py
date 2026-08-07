"""
Liquidity Radar — v3 Change A: bulk and block deal files.

This is the only source in the stack where the money is confirmed rather than
prospective: the seller is named outright and the value is exact — no
classification, no inference, no false positives. Block deals settle T+1, so
a promoter who sold Thursday morning has unallocated funds landing Friday.

Sources: NSE bulk-deals and block-deals (cookie warm-up, same as sources.py).
BSE's equivalent endpoints were NOT found during development — every guessed
path 302-redirected to an error page, unlike BSE's announcements API, which is
confirmed working. Per the spec's own fallback: ship NSE only. If you find the
real BSE bulk/block endpoints, add a fetcher here following the NSE pattern.

IMPORTANT — NSE's historical/bulk-deal JSON field names could not be verified
live (the endpoint 503s from the development sandbox; NSE's own announcements
API works fine from the same sandbox, so this is endpoint-specific bot
defense, not a broken warm-up). The row parser below tries several candidate
field names and logs the raw keys of the first row of each fetch — if NSE's
real field names differ from what's guessed here, that log line shows you the
actual keys immediately.
"""

import time

import requests

import classify
import cluster
import config
import db
import notify
import sources


def _log(msg):
    print(f"[deals_files] {msg}")


# --------------------------------------------------------------------------
# Fetch — NSE bulk / block deals, 3-day lookback (self-heals a failed run).
# --------------------------------------------------------------------------
def _date_range(days_back):
    from datetime import datetime, timedelta
    today = datetime.now()
    frm = (today - timedelta(days=days_back)).strftime("%d-%m-%Y")
    to = today.strftime("%d-%m-%Y")
    return frm, to


_FIELD_CANDIDATES = {
    "date": ["BD_DT_DATE", "DT_DATE", "date", "dt"],
    "symbol": ["BD_SYMBOL", "SYMBOL", "symbol"],
    "security_name": ["BD_SCRIP_NAME", "SCRIP_NAME", "securityName", "security_name"],
    "client_name": ["BD_CLIENT_NAME", "CLIENT_NAME", "clientName", "client_name"],
    "buy_sell": ["BD_BUY_SELL", "BUY_SELL", "buySell", "buy_sell"],
    "quantity": ["BD_QTY_TRD", "QTY_TRD", "quantity", "qty"],
    "price": ["BD_TP_WATP", "TP_WATP", "tradePrice", "price", "wap"],
}


def _extract(row, field):
    for key in _FIELD_CANDIDATES[field]:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _parse_rows(raw_rows, exchange, deal_type, logged_keys=[False]):
    """Turn raw NSE JSON rows into our normalised shape. Never raises — a
    row that doesn't parse is skipped and logged, not fatal to the run."""
    out = []
    for row in raw_rows:
        if not logged_keys[0]:
            _log(f"first {exchange} {deal_type} row keys: {sorted(row.keys())}")
            logged_keys[0] = True
        try:
            date = _extract(row, "date") or ""
            symbol = _extract(row, "symbol") or ""
            security_name = _extract(row, "security_name") or symbol
            client_name = (_extract(row, "client_name") or "").strip()
            buy_sell = (_extract(row, "buy_sell") or "").strip().upper()
            qty = _extract(row, "quantity")
            price = _extract(row, "price")
            if not (symbol and client_name and buy_sell):
                continue
            qty = float(str(qty).replace(",", "")) if qty is not None else None
            price = float(str(price).replace(",", "")) if price is not None else None
            value_cr = round(qty * price / 1e7, 2) if qty and price else None
            out.append({
                "trade_date": date,
                "symbol": symbol,
                "security_name": security_name,
                "client_name": client_name,
                "buy_sell": buy_sell,
                "quantity": qty,
                "price": price,
                "value_cr": value_cr,
                "exchange": exchange,
                "deal_type": deal_type,
            })
        except (ValueError, TypeError) as exc:
            _log(f"skipping unparseable row: {exc}")
            continue
    return out


def fetch_nse_bulk_deals(days_back=config.BULK_BLOCK_LOOKBACK_DAYS):
    frm, to = _date_range(days_back)
    try:
        session, headers = sources.warm_nse_session()
        r = session.get(config.NSE_BULK_DEALS_API, headers=headers,
                         params={"from": frm, "to": to}, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        parsed = _parse_rows(rows, "NSE", "bulk deal")
        _log(f"NSE bulk deals: {len(parsed)} rows parsed")
        return parsed
    except Exception as exc:  # noqa: BLE001
        _log(f"NSE bulk deals fetch failed, skipping this run: {exc}")
        return []


def fetch_nse_block_deals(days_back=config.BULK_BLOCK_LOOKBACK_DAYS):
    frm, to = _date_range(days_back)
    try:
        session, headers = sources.warm_nse_session()
        r = session.get(config.NSE_BLOCK_DEALS_API, headers=headers,
                         params={"from": frm, "to": to}, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        parsed = _parse_rows(rows, "NSE", "block deal")
        _log(f"NSE block deals: {len(parsed)} rows parsed")
        return parsed
    except Exception as exc:  # noqa: BLE001
        _log(f"NSE block deals fetch failed, skipping this run: {exc}")
        return []


def fetch_bse_bulk_block_deals():
    """BSE bulk/block deal endpoints were not found — see module docstring."""
    if not config.BSE_BULK_DEALS_API and not config.BSE_BLOCK_DEALS_API:
        return []
    return []  # pragma: no cover — wire up if the URLs are ever discovered


# --------------------------------------------------------------------------
# Seller classification — keyword-first, Haiku only for genuine ambiguity.
# --------------------------------------------------------------------------
def classify_seller_keyword(name):
    """Return 'institution', 'individual', or 'ambiguous' by keyword alone."""
    n = (name or "").lower()
    if any(kw in n for kw in config.INSTITUTION_KEYWORDS):
        return "institution"
    if any(kw in n for kw in config.AMBIGUOUS_KEYWORDS):
        return "ambiguous"
    return "individual"


def resolve_ambiguous_sellers(names):
    """
    One batched Haiku call for all AMBIGUOUS names in a run (usually a
    handful). Returns {name: "promoter"|"institution"|"unclear"}. On any
    failure, everything defaults to "unclear" (passes) — fail open.
    """
    names = sorted(set(n for n in names if n))
    if not names:
        return {}
    try:
        client = classify._client()
        listing = "\n".join(f"{i}. {n}" for i, n in enumerate(names, start=1))
        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            system=config.SELLER_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": listing}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        results = classify._parse_array(text, len(names))
        out = {}
        for i, name in enumerate(names):
            r = results[i] if i < len(results) else {}
            out[name] = (r.get("verdict") or "unclear").lower()
        return out
    except Exception as exc:  # noqa: BLE001
        _log(f"ambiguous-seller Haiku batch failed, treating all as unclear: {exc}")
        return {name: "unclear" for name in names}


# --------------------------------------------------------------------------
# Confirmed-row handling — shared with pit_feed.py for large single PIT
# disclosures (they get the same cluster-check-and-alert treatment).
# --------------------------------------------------------------------------
def handle_confirmed_row(company, client_name, value_cr, exchange, deal_type,
                          trade_date, quantity=None, price=None, dry=False):
    """
    Run one confirmed individual-seller row through the same deal-clustering
    system as news. Fires a CONFIRMED alert on a new deal or when the amount
    was previously unknown; attaches silently (but still records the fact)
    when the cluster already had an amount. Returns True if an alert fired.
    """
    result = {
        "company": company,
        "deal_type": deal_type,
        "amount_cr": value_cr,
        "amount_raw": f"{value_cr:g}cr confirmed via {exchange} {deal_type}",
        "individuals": [client_name],
        "buyer": None,
        "confidence": "high",
        "one_line": f"{client_name} sold shares in {company} "
                    f"(confirmed, {exchange} {deal_type})",
        "size_source": "stated",
        "size_band": "UNKNOWN",
    }
    item = {"title": f"{company}: {deal_type}", "url": "", "source": exchange}

    if dry:
        _log(f"(dry) would process confirmed row: {company} / {client_name} / "
             f"Rs {value_cr:g}cr")
        return False

    alerts = cluster.process(item, result, confirmed=True)
    for alert in alerts:
        row = {
            "security_name": company, "client_name": client_name,
            "value_cr": alert.get("amount_cr", value_cr), "exchange": exchange,
            "deal_type": deal_type, "trade_date": trade_date,
            "quantity": quantity, "price": price, "is_update": alert["is_update"],
        }
        notify.send_confirmed_alert(row, deal_id=alert.get("deal_id"))
    return bool(alerts)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _dedup_id(row):
    return (f"{row['exchange']}:{row['deal_type']}:{row['trade_date']}:"
            f"{row['symbol']}:{row['client_name']}:{row['buy_sell']}:{row['quantity']}")


def process_bulk_block_deals(dry=False):
    """
    Fetch NSE bulk + block deals, classify sellers, fire CONFIRMED alerts for
    qualifying rows, and record every individual-seller row into
    individual_sales for Change B's aggregation regardless of size.
    """
    rows = fetch_nse_bulk_deals() + fetch_nse_block_deals() + fetch_bse_bulk_block_deals()

    fresh = []
    for row in rows:
        rid = _dedup_id(row)
        if db.item_seen(rid):
            continue
        db.add_item({"id": rid, "source": row["exchange"], "title": row["symbol"],
                     "url": "", "description": row["deal_type"]})
        fresh.append(row)
    _log(f"{len(rows)} rows fetched, {len(fresh)} new after dedup")

    sell_rows = [r for r in fresh if r["buy_sell"] == "SELL" and r["value_cr"]]

    # Keyword classification first; batch only the genuinely ambiguous names.
    keyword_verdict = {r["client_name"]: classify_seller_keyword(r["client_name"])
                       for r in sell_rows}
    ambiguous_names = [n for n, v in keyword_verdict.items() if v == "ambiguous"]
    haiku_verdict = resolve_ambiguous_sellers(ambiguous_names) if not dry else {}

    alerts_fired = 0
    recorded = 0
    for row in sell_rows:
        name = row["client_name"]
        verdict = keyword_verdict[name]
        if verdict == "ambiguous":
            haiku = haiku_verdict.get(name, "unclear")
            is_individual = haiku in ("promoter", "unclear")  # unclear passes
        else:
            is_individual = verdict == "individual"

        if not is_individual:
            continue

        # Record for aggregation regardless of size (Change B needs the
        # sub-threshold sales too).
        pk, canonical_name = cluster.resolve_person_key(
            name, cluster.deal_key(row["security_name"]), config.AGGREGATION_WINDOW_DAYS)
        if pk and not dry:
            db.add_individual_sale(
                pk, canonical_name, row["security_name"],
                cluster.deal_key(row["security_name"]), row["trade_date"],
                row["value_cr"], row["deal_type"].split()[0])  # 'block' | 'bulk'
            recorded += 1

        # Fire a standalone CONFIRMED alert only above the threshold.
        if row["value_cr"] >= config.BULK_BLOCK_MIN_CR:
            fired = handle_confirmed_row(
                row["security_name"], name, row["value_cr"], row["exchange"],
                row["deal_type"], row["trade_date"], row["quantity"],
                row["price"], dry=dry)
            alerts_fired += int(fired)

    _log(f"{len(sell_rows)} SELL rows with a value, {recorded} recorded for "
         f"aggregation, {alerts_fired} CONFIRMED alert(s) fired")
    return {"fetched": len(rows), "new": len(fresh), "alerts": alerts_fired}
