"""
Liquidity Radar — v4 Part 1, Changes 1/3/4: pre-classification filters.

Everything here runs BEFORE any Anthropic API call, on regex/string logic
alone. Nothing in this module reduces recall on a real deal — every filter
is either structural (a document TYPE, not its content) or a near-duplicate
check (the same story, not a different one).

None of these functions decide whether to actually DROP an item — that's
main.py's job, gated by config.PREFILTER_MODE (v4 Change 9: shadow mode logs
every decision without acting on it for a trial week before enforcing).
"""

import re

import classify
import config
import sizing

# --------------------------------------------------------------------------
# Change 3 — structural blocklist. Filters on document type, where the URL or
# an exact title pattern tells you what the page is regardless of content.
# Deliberately NOT a keyword content filter: a deal phrased unusually can
# slip past a keyword filter, but a deal cannot be published as a stock
# price liveblog. Keep this list short and structural only.
# --------------------------------------------------------------------------
_TITLE_LIVE_RE = re.compile(r"live updates\s*:", re.IGNORECASE)


def is_structural_noise(item):
    url = (item.get("url") or "").lower()
    if any(p in url for p in config.STRUCTURAL_BLOCK_URL_PATTERNS):
        return True

    title = (item.get("title") or "").strip()
    description = (item.get("description") or "").strip()
    if description:
        return False  # the title patterns below only apply when there's
                       # nothing else in the item to go on
    title_l = title.lower()
    if any(p in title_l for p in config.STRUCTURAL_BLOCK_TITLE_PATTERNS):
        return True
    if _TITLE_LIVE_RE.search(title_l):
        return True
    return False


# --------------------------------------------------------------------------
# Change 4 — title dedup before classification. Google News rotates article
# URLs, so the same story looks new to the id/URL-based dedup and gets
# classified again; clustering catches it afterwards, but only after the API
# call has already been paid for.
# --------------------------------------------------------------------------
_TRAILING_SOURCE_RE = re.compile(r"\s+-\s+[^-]+$")
_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalise_title(title):
    """Lowercase, strip the trailing ' - SourceName' Google News appends,
    strip punctuation, collapse whitespace."""
    t = (title or "").strip()
    t = _TRAILING_SOURCE_RE.sub("", t)
    t = _PUNCT_RE.sub(" ", t.lower())
    t = _WHITESPACE_RE.sub(" ", t).strip()
    return t


def distinguishing_tokens(title_norm):
    """normalise_title() output with generic finance vocabulary stripped —
    what's left is what actually distinguishes one headline from another."""
    return frozenset(
        w for w in title_norm.split() if w not in config.TITLE_DEDUP_STOPWORDS
    )


def jaccard(a, b):
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def title_dedup_decision(title_norm, tokens, recent):
    """
    `recent` is an iterable of (title_norm, distinguishing_tokens) pairs
    already seen in the dedup window. Returns (is_dup, matched_title_norm,
    similarity).

    Step 1 — exact match on the full normalised title — always runs.
    Step 2 — Jaccard overlap on DISTINGUISHING tokens only (stopwords
    stripped) — only runs when this item's own distinguishing token set is
    non-empty, and skips any candidate whose distinguishing set is also
    empty. An all-boilerplate headline ("Stake Sale" and nothing else) has
    nothing left to compare, so it can only be caught by the exact match.
    """
    if not title_norm:
        return False, None, None

    for prev_norm, _ in recent:
        if title_norm == prev_norm:
            return True, prev_norm, 1.0

    if tokens:
        for prev_norm, prev_tokens in recent:
            if not prev_tokens:
                continue
            sim = jaccard(tokens, prev_tokens)
            if sim > config.TITLE_DEDUP_JACCARD:
                return True, prev_norm, sim

    return False, None, None


# --------------------------------------------------------------------------
# Change 1 — pre-API amount gate. Regex-only, no model call. A figure only
# counts when it's near a transaction word and not near a valuation word
# (see classify.gated_amount / config.py) — never suppress on a smaller
# figure when a larger one is also present.
# --------------------------------------------------------------------------
def pre_api_stated_cr(item):
    """Return the largest proximity-gated crore figure in this item's raw
    text, or None if no qualifying figure was found."""
    return classify.gated_amount(item.get("title"), item.get("description"))


# --------------------------------------------------------------------------
# BSE market-cap gate — a BSE filing's company name usually resolves to a
# real, cached market cap (sizing.py), unlike free-text news, so filings from
# small companies can be filtered on company size directly rather than
# guessing deal size from text. Same shadow-mode-first treatment as the
# regex/string filters above: an unlisted or unresolved company is NOT
# suppressed (recall bias — unknown passes), only a CONFIRMED small market
# cap fires this.
# --------------------------------------------------------------------------
def _bse_company_name(item):
    """sources.fetch_bse() titles items 'ScripName: headline'."""
    title = (item.get("title") or "").strip()
    return title.split(":", 1)[0].strip() if ":" in title else title


def bse_market_cap(item):
    """Return (market_cap_cr, ticker) for a BSE item's company if it
    resolves to a listed ticker with a known market cap, else (None, None).
    A no-op for any non-BSE item."""
    if item.get("source") != "BSE":
        return None, None
    ticker = sizing.resolve_ticker(_bse_company_name(item))
    if not ticker:
        return None, None
    mcap = sizing.market_cap_cr(ticker)
    if mcap is None:
        return None, None
    return mcap, ticker
