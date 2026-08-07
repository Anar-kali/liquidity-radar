"""
Liquidity Radar — v3 Change B: NSE structured PIT (Prohibition of Insider
Trading) feed.

The PIT disclosure threshold is only 10 lakh, so essentially every promoter,
promoter-group, director, or KMP trade appears here — structured fields
(company, person, category, quantity, value, transaction type), not PDF text.
This has standalone value (a Rs 280cr promoter sale at a mid-cap often gets no
press coverage at all) AND feeds Change B's salami-slice aggregation, since it
captures the small sales that never individually cross the alert threshold.

IMPORTANT — like deals_files.py, the exact JSON field names could not be
verified live (this endpoint 503s / returns an empty stub from the
development sandbox even with no filters, while NSE's announcements endpoint
works fine — endpoint-specific bot defense). The row parser tries several
candidate field names and logs the first row's raw keys so a wrong guess is a
one-line fix, visible in the first production run's log.
"""

from datetime import datetime, timedelta

import cluster
import config
import db
import deals_files
import sources


def _log(msg):
    print(f"[pit_feed] {msg}")


_FIELD_CANDIDATES = {
    "date": ["date", "acqfromDt", "intimDt", "disclosureDate"],
    "symbol": ["symbol", "SYMBOL"],
    "company": ["company", "companyName", "compName"],
    "person_name": ["acqName", "personName", "name", "acquirerDisposerName"],
    "category": ["personCategory", "category", "regPerCategory"],
    "trans_type": ["transactionType", "transType", "acqMode"],
    "quantity": ["secVal", "noOfSecurities", "securitiesAcquiredDisposed", "secAcq"],
    "value_cr": ["valueOfSecurities", "value", "secValue"],
}


def _extract(row, field):
    for key in _FIELD_CANDIDATES[field]:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _parse_rows(raw_rows, logged=[False]):
    out = []
    for row in raw_rows:
        if not logged[0]:
            _log(f"first PIT row keys: {sorted(row.keys())}")
            logged[0] = True
        try:
            date = _extract(row, "date") or ""
            symbol = _extract(row, "symbol") or ""
            company = _extract(row, "company") or symbol
            person = (_extract(row, "person_name") or "").strip()
            category = (_extract(row, "category") or "").strip().lower()
            trans_type = (_extract(row, "trans_type") or "").strip().lower()
            raw_value = _extract(row, "value_cr")
            if not (symbol and person):
                continue
            # NSE reports the raw rupee value of securities; convert to crore.
            value_cr = round(float(str(raw_value).replace(",", "")) / 1e7, 3) \
                if raw_value else None
            out.append({
                "trade_date": date, "symbol": symbol, "company": company,
                "person_name": person, "category": category,
                "trans_type": trans_type, "value_cr": value_cr,
            })
        except (ValueError, TypeError) as exc:
            _log(f"skipping unparseable PIT row: {exc}")
            continue
    return out


def fetch_pit(days_back):
    frm = (datetime.now() - timedelta(days=days_back)).strftime("%d-%m-%Y")
    to = datetime.now().strftime("%d-%m-%Y")
    try:
        session, headers = sources.warm_nse_session()
        r = session.get(config.NSE_PIT_API, headers=headers,
                         params={"index": "equities", "from_date": frm, "to_date": to},
                         timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        parsed = _parse_rows(rows)
        _log(f"PIT feed: {len(parsed)} rows parsed ({frm} to {to})")
        return parsed
    except Exception as exc:  # noqa: BLE001
        _log(f"PIT feed fetch failed, skipping this run: {exc}")
        return []


def _is_disposal(row):
    """Only disposals put money in an individual's pocket — the acquisition
    side of a PIT disclosure is someone spending money, not receiving it."""
    t = row.get("trans_type", "")
    return "dispos" in t or "sale" in t or "sell" in t


def _dedup_id(row):
    return f"NSEPIT:{row['trade_date']}:{row['symbol']}:{row['person_name']}:{row['trans_type']}"


def process_pit_feed(dry=False):
    """
    Fetch the PIT feed (90-day backfill on the very first run so the rolling
    aggregation window isn't empty, 7-day lookback thereafter), keep
    Promoter/Promoter Group/Director/KMP disposals, fire a standalone
    CONFIRMED alert for any single disclosure that itself clears the
    threshold, and record every disposal into individual_sales regardless of
    size for Change B's aggregation.
    """
    first_run = db.get_state("pit_backfilled") is None
    days_back = config.PIT_BACKFILL_DAYS if first_run else config.PIT_LOOKBACK_DAYS
    if first_run:
        _log(f"first run — backfilling {days_back} days so the rolling "
             f"aggregation window starts populated")

    rows = fetch_pit(days_back)

    fresh = []
    for row in rows:
        rid = _dedup_id(row)
        if db.item_seen(rid):
            continue
        db.add_item({"id": rid, "source": "NSE PIT", "title": row["symbol"],
                     "url": "", "description": row["person_name"]})
        fresh.append(row)
    _log(f"{len(rows)} rows fetched, {len(fresh)} new after dedup")

    disposals = [r for r in fresh
                if _is_disposal(r) and r["category"] in config.PIT_KEEP_CATEGORIES]

    alerts_fired = 0
    recorded = 0
    for row in disposals:
        if row["value_cr"] and row["value_cr"] > 0:
            ck = cluster.deal_key(row["company"])
            pk, canonical_name = cluster.resolve_person_key(
                row["person_name"], ck, config.AGGREGATION_WINDOW_DAYS)
            if pk and not dry:
                db.add_individual_sale(pk, canonical_name, row["company"], ck,
                                        row["trade_date"], row["value_cr"], "pit")
                recorded += 1

            if row["value_cr"] >= config.BULK_BLOCK_MIN_CR:
                fired = deals_files.handle_confirmed_row(
                    row["company"], canonical_name, row["value_cr"], "NSE",
                    "PIT disclosure", row["trade_date"], dry=dry)
                alerts_fired += int(fired)

    if first_run and not dry:
        db.set_state("pit_backfilled", "1")

    _log(f"{len(disposals)} promoter/director/KMP disposals, {recorded} recorded "
         f"for aggregation, {alerts_fired} standalone CONFIRMED alert(s) fired")
    return {"fetched": len(rows), "new": len(fresh), "alerts": alerts_fired}
