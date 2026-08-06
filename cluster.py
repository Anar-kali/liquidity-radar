"""
Liquidity Radar — deal clustering.

One transaction gets reported by many outlets. The banker must receive ONE
alert, not eight.

Deal key = normalised company name + deal type, within a rolling 72h window.

  - First item to match a key creates the deal and fires an alert.
  - Later items attach silently...
  - ...EXCEPT when a later item adds a material fact the deal record lacks:
    an amount where there was none, a named individual where there was none,
    a named buyer where there was none, or an amount revised by >20%. Then we
    fire one follow-up marked UPDATE.

This module decides what happens; it returns "alerts" for main.py to send.
"""

import json
import re

import config
import db


def normalise_company(name):
    """Lowercase, strip common corporate suffixes and all punctuation."""
    if not name:
        return ""
    s = name.lower()
    for word in config.NORMALISE_STOPWORDS:
        s = re.sub(rf"\b{re.escape(word)}\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)  # drop punctuation
    s = re.sub(r"\s+", " ", s).strip()
    return s


def deal_key(company, deal_type):
    return f"{normalise_company(company)}|{(deal_type or 'unknown').lower()}"


def _material_updates(existing, result):
    """
    Compare a new classifier result against the stored deal.
    Returns a dict of columns to update AND a human note, or (None, None) if
    the new item adds nothing material.
    """
    updates = {}
    notes = []

    old_amount = existing.get("amount_cr")
    new_amount = result.get("amount_cr")

    # Amount where there was none.
    if old_amount is None and new_amount is not None:
        updates["amount_cr"] = new_amount
        updates["amount_raw"] = result.get("amount_raw")
        notes.append("amount added")
    # Amount revised by more than 20%.
    elif (
        old_amount is not None
        and new_amount is not None
        and old_amount > 0
        and abs(new_amount - old_amount) / old_amount > 0.20
    ):
        updates["amount_cr"] = new_amount
        updates["amount_raw"] = result.get("amount_raw")
        notes.append(f"amount revised {old_amount:g}→{new_amount:g} cr")

    # Named individual where there was none.
    old_individuals = json.loads(existing.get("individuals") or "[]")
    new_individuals = result.get("individuals") or []
    if not old_individuals and new_individuals:
        updates["individuals"] = new_individuals
        notes.append("individual named")

    # Named buyer where there was none.
    if not existing.get("buyer") and result.get("buyer"):
        updates["buyer"] = result.get("buyer")
        notes.append("buyer named")

    if not updates:
        return None, None
    return updates, "; ".join(notes)


def process(item, result):
    """
    Handle one classified, non-negative item.
    Returns a list of alert dicts (0, 1 for a new deal, or 1 for an UPDATE).
    """
    company = result.get("company") or item.get("title", "")
    dtype = result.get("deal_type") or "unknown"
    key = deal_key(company, dtype)

    existing = db.find_open_deal(key, config.DEAL_WINDOW_HOURS)

    if existing is None:
        # First sighting: create the deal and fire an alert.
        deal = {
            "deal_key": key,
            "company": company,
            "deal_type": dtype,
            "amount_cr": result.get("amount_cr"),
            "amount_raw": result.get("amount_raw"),
            "individuals": result.get("individuals") or [],
            "buyer": result.get("buyer"),
            "confidence": result.get("confidence") or "medium",
            "one_line": result.get("one_line") or "",
            "source": item.get("source", ""),
            "url": item.get("url", ""),
        }
        db.create_deal(deal)
        return [_alert_from_deal(deal, is_update=False)]

    # Deal already known. Does this item add a material fact?
    updates, note = _material_updates(existing, result)
    if updates is None:
        return []  # attach silently

    db.update_deal(existing["id"], dict(updates))
    merged = dict(existing)
    merged.update(updates)
    if isinstance(merged.get("individuals"), str):
        merged["individuals"] = json.loads(merged["individuals"] or "[]")
    # Prefer the fresh item's source/url for the UPDATE alert.
    merged["source"] = item.get("source", existing.get("source", ""))
    merged["url"] = item.get("url", existing.get("url", ""))
    alert = _alert_from_deal(merged, is_update=True)
    alert["note"] = note
    return [alert]


def _alert_from_deal(deal, is_update):
    individuals = deal.get("individuals")
    if isinstance(individuals, str):
        individuals = json.loads(individuals or "[]")
    return {
        "company": deal.get("company", ""),
        "deal_type": deal.get("deal_type", "unknown"),
        "amount_cr": deal.get("amount_cr"),
        "individuals": individuals or [],
        "buyer": deal.get("buyer"),
        "confidence": deal.get("confidence") or "medium",
        "one_line": deal.get("one_line") or "",
        "source": deal.get("source", ""),
        "url": deal.get("url", ""),
        "is_update": is_update,
    }
