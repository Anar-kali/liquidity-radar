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
from datetime import datetime, timezone

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
# v5 Change 1 — news freshness gate. Feeds keep serving articles long after
# publication; an old story is not a lead. Two deliberate holes, both in the
# recall-safe direction: an item with NO timestamp passes, and exchange /
# regulator filings are exempt entirely (no feed timestamp, same-day by
# construction, so an age check on them could only ever be a false drop).
# --------------------------------------------------------------------------
def published_age_hours(item, now=None):
    """Hours since publication, or None if the item carries no usable
    timestamp. A naive timestamp is read as UTC, matching sources.py."""
    published = item.get("published_at")
    if not published:
        return None
    try:
        dt = datetime.fromisoformat(published)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def stale_news_age(item, now=None):
    """Return the item's age in hours if it should be dropped as stale, else
    None. None covers every pass case — exempt source, no timestamp, or
    genuinely fresh — so the caller never has to distinguish them."""
    if item.get("source") in config.AGE_GATE_EXEMPT_SOURCES:
        return None
    age = published_age_hours(item, now)
    if age is None or age <= config.NEWS_MAX_AGE_HOURS:
        return None
    return age


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


def is_title_dedup_exempt(item):
    """True for sources whose titles are generic category labels rather than
    headlines (see config.TITLE_DEDUP_EXEMPT_SOURCES)."""
    return (item.get("source") or "") in config.TITLE_DEDUP_EXEMPT_SOURCES


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


def filing_market_cap(item):
    """
    Return (market_cap_cr, ticker) for an exchange filing's company, or
    (None, None). A no-op for anything that isn't a BSE or NSE filing.

    The two exchanges name their subject differently, and NSE is the easier
    of the two: its titles carry the TICKER SYMBOL itself ("PARKHOSPS: Copy
    of Newspaper Publication"), so no name matching is involved at all. BSE
    titles carry the scrip name, which goes through the normal matcher.

    v5 Change 2 — NSE filings had no market-cap gate at all before this, even
    though NSE is 40% of everything fetched and 56% of its titles carry a
    symbol matching the master list exactly.
    """
    source = item.get("source")
    if source == "NSE":
        ticker = sizing.resolve_nse_symbol(item.get("title"))
    elif source == "BSE":
        ticker = sizing.resolve_ticker(_bse_company_name(item))
    else:
        return None, None
    if not ticker:
        return None, None
    mcap = sizing.market_cap_cr(ticker)
    if mcap is None:
        return None, None                       # lookup failed -> unknown passes
    return mcap, ticker


# --------------------------------------------------------------------------
# v5 Change 6 — re-publishers. Google re-pushes scanx.trade articles with
# FRESH timestamps, so Change 1 cannot tell they are months old, and the true
# date is unrecoverable: the Google News link is an opaque redirect token, the
# interstitial page carries no publisher URL, and reaching the real article
# needs Google's undocumented batchexecute RPC.
#
# So these are gated on SUBSTANCE instead of age — see main.py. Blanket-
# blocking the publisher was rejected: it would have cost Waaree Energies at
# Rs 14,307cr and CultFit at Rs 2,500cr, both of which scanx broke alone.
# --------------------------------------------------------------------------
def publisher(item):
    """The outlet Google News appends to a headline as ' - Publisher'."""
    match = _TRAILING_SOURCE_RE.search((item.get("title") or "").strip())
    return match.group(0).strip(" -").strip() if match else ""


def is_republisher(item):
    return publisher(item).lower() in {s.lower() for s in config.REPUBLISHER_SOURCES}
