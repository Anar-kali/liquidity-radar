"""
Liquidity Radar — v3 Change A: bulk and block deal files.

This is the only source in the stack where the money is confirmed rather than
prospective: the seller is named outright and the value is exact — no
classification, no inference, no false positives. Block deals settle T+1, so
a promoter who sold Thursday morning has unallocated funds landing Friday.

Source: NSE's `snapshot-capital-market-largedeal` endpoint — the same data
that powers NSE's live market-snapshot widget, confirmed returning current
bulk AND block deals in one call with real field names (buySell, clientName,
date, name, qty, symbol, watp). The originally-documented
`/api/historical/bulk-deals` and `/block-deals` never worked (503 from every
network tested); see config.py for the full history. BSE's equivalent
endpoints were NOT found during development — every guessed path
302-redirected to an error page, unlike BSE's announcements API, which is
confirmed working. Per the spec's own fallback: ship NSE only.
"""

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
# Fetch — NSE bulk + block deals in one call (a rolling snapshot, not a
# historical date-range query — dedup on (date, symbol, client, buy_sell,
# qty) handles seeing the same window again on the next run).
# --------------------------------------------------------------------------
def _parse_rows(raw_rows, exchange, deal_type, logged_keys={}):
    """Turn raw NSE JSON rows into our normalised shape. Never raises — a
    row that doesn't parse is skipped and logged, not fatal to the run."""
    out = []
    for row in raw_rows:
        if deal_type not in logged_keys:
            _log(f"first {exchange} {deal_type} row keys: {sorted(row.keys())}")
            logged_keys[deal_type] = True
        try:
            date = row.get("date") or ""
            symbol = row.get("symbol") or ""
            security_name = row.get("name") or symbol
            client_name = (row.get("clientName") or "").strip()
            buy_sell = (row.get("buySell") or "").strip().upper()
            qty = row.get("qty")
            price = row.get("watp")
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


def fetch_nse_large_deals():
    """One call, returns both bulk and block deals."""
    try:
        session, headers = sources.warm_nse_session()
        r = session.get(config.NSE_LARGE_DEALS_API, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        bulk = _parse_rows(data.get("BULK_DEALS_DATA", []) or [], "NSE", "bulk deal")
        block = _parse_rows(data.get("BLOCK_DEALS_DATA", []) or [], "NSE", "block deal")
        _log(f"NSE large deals: {len(bulk)} bulk, {len(block)} block")
        return bulk + block
    except Exception as exc:  # noqa: BLE001
        _log(f"NSE large deals fetch failed, skipping this run: {exc}")
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
            temperature=0,  # deterministic — classification, not writing
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
    rows = fetch_nse_large_deals() + fetch_bse_bulk_block_deals()

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
