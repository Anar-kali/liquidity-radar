"""
Liquidity Radar — classification. ONE stage, Haiku only.

Items are sent to the model 25 at a time as a numbered list. For each item we
send the headline plus the first 400 characters of the description. The model
returns a JSON array, one object per item, in the same order.
"""

import json
import os
import re

import anthropic

import config

# Matches an INR figure in crore, e.g. "₹3,000 crore", "Rs 2,021 crore",
# "2480cr", "67.65 cr". Deliberately does NOT match "/share" prices or lakh.
_CRORE_RE = re.compile(
    r"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:crore|cr)\b",
    re.IGNORECASE,
)

# Foreign-currency amounts (USD / EUR) with a magnitude word.
_USD_RE = re.compile(
    r"(?:us\$|\$|usd)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|bn|million|mn|m)\b",
    re.IGNORECASE,
)
_EUR_RE = re.compile(
    r"(?:€|eur)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|bn|million|mn|m)\b",
    re.IGNORECASE,
)
_FX_UNIT = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6}

# Any sign that a figure is in a non-INR currency — when present, the crore
# conversion Haiku wrote is unreliable, so we do NOT auto-correct the display.
_FX_SIGNALS = ("$", "usd", "dollar", "€", "eur", "£", "gbp")


def all_crore(*texts):
    """Return every INR-crore figure found across the given texts."""
    out = []
    for t in texts:
        if not t:
            continue
        for m in _CRORE_RE.finditer(str(t)):
            try:
                out.append(float(m.group(1).replace(",", "")))
            except ValueError:
                continue
    return out


def parse_crore(*texts):
    """Return the largest INR-crore figure found, or None."""
    figures = all_crore(*texts)
    return max(figures) if figures else None


def _fx_to_crore(regex, rate, text):
    """Convert USD/EUR figures in `text` to crore INR."""
    out = []
    for m in regex.finditer(str(text)):
        try:
            num = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = _FX_UNIT.get(m.group(2).lower(), 1)
        out.append(num * unit * rate / 1e7)  # INR -> crore
    return out


def stated_cr_max(*texts):
    """
    Largest deal size in crore across the texts, reading INR-crore figures AND
    USD/EUR figures (converted at the config rates). Used by the 'don't drop a
    big deal' safety net so foreign-currency deals are protected too.
    """
    vals = []
    for t in texts:
        if not t:
            continue
        vals += all_crore(t)
        vals += _fx_to_crore(_USD_RE, config.USD_INR, t)
        vals += _fx_to_crore(_EUR_RE, config.EUR_INR, t)
    return max(vals) if vals else None


def reconcile_amount(model_amount_cr, amount_raw):
    """
    Guard against Haiku's occasional 'divide by 10' slip on INR-crore figures
    (e.g. it reads '₹3,000 crore' but returns 300). We correct ONLY when:
      - the amount is NOT foreign-currency denominated (for $/€ deals the crore
        conversion is itself unreliable, so we must not touch it), AND
      - the model's value is NOT itself one of the stated crore figures, AND
      - a stated crore figure equal to model x10 exists.
    This fixes the INR slip while never inflating a correct figure. Returns
    (amount, fixed?).
    """
    if model_amount_cr is None or model_amount_cr <= 0:
        return model_amount_cr, False
    if any(sig in (amount_raw or "").lower() for sig in _FX_SIGNALS):
        return model_amount_cr, False  # dollar/euro deal — leave it alone
    figures = all_crore(amount_raw)
    if not figures:
        return model_amount_cr, False
    # If the model's own value is a stated figure, it read a real number.
    if any(abs(f - model_amount_cr) <= 0.05 * model_amount_cr for f in figures):
        return model_amount_cr, False
    # Otherwise, look for a figure that is ~10x the model's value.
    for f in figures:
        if 8 <= f / model_amount_cr <= 12:
            return f, True
    return model_amount_cr, False


def _client():
    # Reads ANTHROPIC_API_KEY from the environment. Never hardcode the key.
    return anthropic.Anthropic()


def _build_user_message(batch):
    lines = []
    for i, item in enumerate(batch, start=1):
        title = item.get("title", "").strip()
        desc = (item.get("description", "") or "").strip()[: config.DESCRIPTION_CHARS]
        lines.append(f"{i}. HEADLINE: {title}\n   DESCRIPTION: {desc}")
    return "\n\n".join(lines)


def _parse_array(text, expected):
    """Parse the model's JSON array, tolerating stray markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and trailing ```
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON array in model output: {text[:200]!r}")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("model output was not a list")
    return parsed


def classify_batch(client, batch):
    """Classify up to BATCH_SIZE items. Returns a list of result dicts."""
    user_message = _build_user_message(batch)
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=4096,
        system=config.SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    results = _parse_array(text, len(batch))

    # Align results to the batch by position; pad/trim defensively.
    aligned = []
    for i, item in enumerate(batch):
        result = results[i] if i < len(results) else {}
        norm = _normalise(result)
        corrected, changed = reconcile_amount(norm["amount_cr"], norm.get("amount_raw"))
        if changed:
            print(
                f"[classify] corrected 10x amount slip "
                f"{norm['amount_cr']:g} -> {corrected:g} cr for: "
                f"{item.get('title', '')[:60]}"
            )
            norm["amount_cr"] = corrected
        aligned.append((item, norm))
    return aligned


def _normalise(result):
    """Fill in defaults so downstream code never has to guard for missing keys."""
    if not isinstance(result, dict):
        result = {}
    return {
        "confirmed_negative": bool(result.get("confirmed_negative", False)),
        "negative_reason": result.get("negative_reason"),
        "company": result.get("company") or "",
        "deal_type": result.get("deal_type") or "unknown",
        "amount_cr": result.get("amount_cr"),
        "amount_raw": result.get("amount_raw"),
        "individuals": result.get("individuals") or [],
        "buyer": result.get("buyer"),
        "confidence": result.get("confidence") or "medium",
        "one_line": result.get("one_line") or "",
    }


def classify_all(items):
    """
    Classify every item, in batches of BATCH_SIZE.
    Returns a list of (item, result) tuples.

    If a whole batch fails to classify, its items are passed through as
    non-negative with low information (fail open — a missed deal is worse than
    a false alarm).
    """
    if not items:
        return []
    client = _client()
    out = []
    for start in range(0, len(items), config.BATCH_SIZE):
        batch = items[start : start + config.BATCH_SIZE]
        try:
            out.extend(classify_batch(client, batch))
        except Exception as exc:  # noqa: BLE001
            print(f"[classify] batch failed, passing items through: {exc}")
            for item in batch:
                out.append((item, _normalise({"one_line": item.get("title", "")})))
    return out
