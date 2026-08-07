"""
Liquidity Radar — deal clustering.

One transaction gets reported by many outlets, often under different framing
("IndiaRF buys Fine Edge" vs "Fine Edge Engineering strategic buyout"). The
banker must receive ONE alert, not several.

Two items are the same deal if EITHER:
  - NAME match: their company name-token sets contain one another (after
    stripping parentheticals, suffixes and corporate stopwords), within 72h; OR
  - AMOUNT match: both amounts are within 5% of each other and the names share
    at least one token, within 7 days (follow-up analyses land days later).

deal_type is deliberately NOT part of the match — one outlet's "strategic
buyout" is another's "PE secondary".

  - First item creates the deal and fires an alert.
  - Later items attach silently...
  - ...EXCEPT when a later item adds a material fact the record lacks. Then we
    fire one follow-up marked UPDATE.
"""

import json
import re
from datetime import datetime, timezone

import config
import db

# Phrases after which the name is descriptive noise ("Fine Edge, a unit of ...").
_TRAIL_MARKERS = re.compile(
    r"\b(formerly|erstwhile|a unit of|division of|arm of|business of)\b", re.I
)


def normalise_company(name):
    """Lowercase, strip common corporate suffixes and all punctuation."""
    if not name:
        return ""
    s = name.lower()
    for word in config.NORMALISE_STOPWORDS:
        s = re.sub(rf"\b{re.escape(word)}\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_name(name):
    """Drop parentheticals, anything after a comma, and descriptive suffixes."""
    if not name:
        return ""
    s = re.sub(r"\([^)]*\)", " ", name)   # remove "(Ashok Iron Works ...)"
    s = s.split(",")[0]                     # remove ", a unit of ..."
    m = _TRAIL_MARKERS.search(s)
    if m:
        s = s[: m.start()]
    return s


def tokens(name):
    """Company name -> set of meaningful lowercase tokens (stopwords dropped)."""
    s = _strip_name(name).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return frozenset(w for w in s.split() if w and w not in config.CLUSTER_STOPWORDS)


def deal_key(company):
    """Stable string key for storage/debug (not used for matching)."""
    return " ".join(sorted(tokens(company))) or normalise_company(company)


def person_tokens(name):
    """
    Individual name -> set of meaningful lowercase tokens, for matching the
    same person across sources that spell/order the name differently
    ("AGARWAL SUNIL KUMAR" / "Sunil K Agarwal"). Strips titles and drops
    single-character tokens (initials), which company names don't need.
    """
    if not name:
        return frozenset()
    s = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [w for w in s.split() if w and w not in config.PERSON_TITLE_STOPWORDS]
    return frozenset(w for w in words if len(w) > 1)


def person_key(name):
    """Stable string key for a person, built the same way as deal_key."""
    return " ".join(sorted(person_tokens(name)))


def token_subset_match(a, b):
    """True if either token set contains the other (both non-empty)."""
    return bool(a) and bool(b) and (a <= b or b <= a)


def resolve_person_key(name, company_key, window_days):
    """
    Stable person_key for `name`, scoped to one company. Different sources
    spell the same person differently ("AGARWAL SUNIL KUMAR" vs "Sunil K
    Agarwal"), and a plain person_key() call would mint a different string
    for each variant — silently breaking the 90-day aggregation. So this
    fuzzy-resolves against existing persons recorded for the same company
    first (same containment logic as company matching), reusing whichever
    key was seen first; only mints a fresh key when nothing matches.

    Returns (person_key, canonical_name) — canonical_name is the
    first-seen spelling, kept stable so alerts don't flip-flop on wording.
    """
    new_tokens = person_tokens(name)
    if not new_tokens:
        return "", name
    for pk, existing_name in db.distinct_persons_for_company(company_key, window_days):
        if token_subset_match(new_tokens, person_tokens(existing_name)):
            return pk, existing_name
    return person_key(name), name


def _amount_match(a1, a2):
    """True if both amounts are present and within AMOUNT_MATCH_TOL of each other."""
    if a1 is None or a2 is None:
        return False
    a1, a2 = float(a1), float(a2)
    if a1 <= 0 or a2 <= 0:
        return False
    return abs(a1 - a2) <= config.AMOUNT_MATCH_TOL * max(a1, a2)


def _age_hours(created_at_iso):
    try:
        dt = datetime.fromisoformat(created_at_iso)
    except (ValueError, TypeError):
        return float("inf")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _find_match(new_tokens, new_amount):
    """Return the first open deal this item belongs to, or None."""
    for cand in db.deals_in_window(config.AMOUNT_WINDOW_HOURS):  # newest first
        age = _age_hours(cand.get("created_at"))
        cand_tokens = tokens(cand.get("company", ""))
        # Name path — containment, within the 72h window.
        if age <= config.DEAL_WINDOW_HOURS and token_subset_match(new_tokens, cand_tokens):
            return cand
        # Amount path — within 5% and sharing a token, up to 7 days.
        if (
            age <= config.AMOUNT_WINDOW_HOURS
            and _amount_match(new_amount, cand.get("amount_cr"))
            and (new_tokens & cand_tokens)
        ):
            return cand
    return None


def _material_updates(existing, result):
    """
    Compare a new classifier result against the stored deal.

    Returns (updates_dict, fire_alert, note):
      - updates_dict: columns to persist (amount, and newly-known individual /
        buyer). Persisted whether or not we alert.
      - fire_alert: True ONLY when the amount appears or revises by >20%. A new
        buyer or individual alone updates the record silently — the banker does
        his own research once alerted; a second ping naming the buyer is noise.
        When an amount update does fire, the alert already carries the latest
        individual and buyer via the merged record.
    """
    updates = {}
    fire_alert = False
    notes = []

    old_amount = existing.get("amount_cr")
    new_amount = result.get("amount_cr")

    if old_amount is None and new_amount is not None:
        updates["amount_cr"] = new_amount
        updates["amount_raw"] = result.get("amount_raw")
        updates["size_source"] = "stated"   # a real figure now exists
        fire_alert = True
        notes.append("amount added")
    elif (
        old_amount is not None
        and new_amount is not None
        and old_amount > 0
        and abs(new_amount - old_amount) / old_amount > 0.20
    ):
        updates["amount_cr"] = new_amount
        updates["amount_raw"] = result.get("amount_raw")
        updates["size_source"] = "stated"
        fire_alert = True
        notes.append(f"amount revised {old_amount:g}→{new_amount:g} cr")

    # Newly-known individual / buyer are persisted but do NOT fire on their own.
    old_individuals = json.loads(existing.get("individuals") or "[]")
    new_individuals = result.get("individuals") or []
    if not old_individuals and new_individuals:
        updates["individuals"] = new_individuals

    if not existing.get("buyer") and result.get("buyer"):
        updates["buyer"] = result.get("buyer")

    return updates, fire_alert, "; ".join(notes)


def _confirmed_updates(existing, result):
    """
    Update rule for a CONFIRMED source (block/bulk deal, PIT disclosure) — the
    money here is a fact, not a classifier estimate, so the semantics differ
    from a news revision:
      - existing amount unknown -> the confirmed figure IS the news. Fire an
        UPDATE.
      - existing amount already known (whatever the source) -> the confirmed
        figure and seller name still get recorded onto the deal record, but
        SILENTLY — the alert already went out for this deal; a fact-check
        attach is not a second ping.
    Always marks the deal `confirmed=1` once any confirmed source touches it.
    """
    updates = {"confirmed": 1}
    fire_alert = existing.get("amount_cr") is None and result.get("amount_cr") is not None

    updates["amount_cr"] = result.get("amount_cr")
    updates["amount_raw"] = result.get("amount_raw")
    updates["size_source"] = "stated"

    old_individuals = json.loads(existing.get("individuals") or "[]")
    new_individuals = result.get("individuals") or []
    if new_individuals and not set(new_individuals) <= set(old_individuals):
        updates["individuals"] = list(dict.fromkeys(old_individuals + new_individuals))

    note = "amount confirmed" if fire_alert else "seller/value confirmed (silent)"
    return updates, fire_alert, note


def process(item, result, confirmed=False):
    """
    Handle one qualifying item.
    Returns a list of alert dicts (a new-deal alert, an UPDATE, or nothing).

    `confirmed=True` marks the source as fact rather than a classifier
    estimate (block/bulk deal files, PIT disclosures — v3 Change A/B) and uses
    `_confirmed_updates` instead of the news-revision rule when attaching to
    an existing deal.
    """
    company = result.get("company") or item.get("title", "")
    new_tokens = tokens(company)
    new_amount = result.get("amount_cr")
    title = item.get("title", "")
    url = item.get("url", "")

    match = _find_match(new_tokens, new_amount)

    if match is None:
        deal = {
            "deal_key": deal_key(company),
            "company": company,
            "deal_type": result.get("deal_type") or "unknown",
            "amount_cr": new_amount,
            "amount_raw": result.get("amount_raw"),
            "individuals": result.get("individuals") or [],
            "buyer": result.get("buyer"),
            "confidence": result.get("confidence") or "medium",
            "one_line": result.get("one_line") or "",
            "source": item.get("source", ""),
            "url": url,
            "size_source": result.get("size_source"),
            "size_band": result.get("size_band"),
            "confirmed": 1 if confirmed else 0,
        }
        deal_id = db.create_deal(deal)
        db.add_deal_member(deal_id, title, url)
        return [_alert_from_deal(deal, deal_id, is_update=False)]

    # Existing deal — record the merge, then decide whether to fire an UPDATE.
    db.add_deal_member(match["id"], title, url)
    if confirmed:
        updates, fire_alert, note = _confirmed_updates(match, result)
    else:
        updates, fire_alert, note = _material_updates(match, result)
    if updates:
        db.update_deal(match["id"], dict(updates))  # persist new facts always
    if not fire_alert:
        return []  # buyer/individual alone (or a silent confirm) don't alert

    merged = dict(match)
    merged.update(updates)
    if isinstance(merged.get("individuals"), str):
        merged["individuals"] = json.loads(merged["individuals"] or "[]")
    merged["source"] = item.get("source", match.get("source", ""))
    merged["url"] = url or match.get("url", "")
    alert = _alert_from_deal(merged, match["id"], is_update=True)
    alert["note"] = note
    return [alert]


def _alert_from_deal(deal, deal_id, is_update):
    individuals = deal.get("individuals")
    if isinstance(individuals, str):
        individuals = json.loads(individuals or "[]")
    return {
        "deal_id": deal_id,
        "company": deal.get("company", ""),
        "deal_type": deal.get("deal_type", "unknown"),
        "amount_cr": deal.get("amount_cr"),
        "individuals": individuals or [],
        "buyer": deal.get("buyer"),
        "confidence": deal.get("confidence") or "medium",
        "one_line": deal.get("one_line") or "",
        "source": deal.get("source", ""),
        "url": deal.get("url", ""),
        "size_source": deal.get("size_source"),
        "size_band": deal.get("size_band"),
        "confirmed": bool(deal.get("confirmed")),
        "is_update": is_update,
    }
