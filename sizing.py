"""
Liquidity Radar — size resolution for deals with no stated amount.

Runs between stage 1 and stage 2, only on items where amount_cr is null.
Resolves size in priority order, stopping at the first that succeeds:

  1. Stated amount            -> handled upstream (this module not called)
  2. stake% x market cap      -> "computed"        (listed, deterministic)
  3. market cap plausibility  -> "mcap_plausible"  (listed, no % available)
  4. Haiku band estimate      -> done in the stage 2 call, not here
  5. nothing                  -> size_source None, item passes

Recall bias governs: we only ever hand main.py enough to SUPPRESS when we are
confident a deal is small. Unknown passes.
"""

import csv
import os
import re
import time
from collections import Counter, defaultdict

import cluster
import config
import db

_DATA = os.path.join(os.path.dirname(__file__), "data")

# Percentage adjacent to a stake word (either order), e.g. "sells 4.58% stake",
# "pares its 14% holding". Deliberately does NOT match a bare "50% growth".
# Note: no \b after "%" — a percent sign has no word boundary against a space.
_PCT = r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:%|percent|per\s*cent|\bpc\b)"
_STAKE = r"(?:stake|shares?|holding|equity|interest)"
_PCT_NEAR_STAKE = [
    re.compile(_PCT + r"[^.]{0,25}?" + _STAKE, re.I),
    re.compile(_STAKE + r"[^.]{0,25}?" + _PCT, re.I),
]


# --------------------------------------------------------------------------
# Ticker resolution from the committed NSE / BSE master lists.
# Keyed on the SAME token normalisation as clustering. Only a UNIQUE exact
# token-set match resolves; anything ambiguous stays "not listed".
# --------------------------------------------------------------------------
# ETFs and index funds are not operating companies and can never be the target
# of a deal, but they crowd the master lists (265 of 7,550 rows; the token
# "etf" alone appears in 190 names) and they poison matching — "ICICI
# Prudential AMC" matched an ETF in testing. Excluded from the universe.
_FUND_RE = re.compile(
    r"\b(etf|etfs|nifty|sensex|bees|index\s+fund|mutual\s+fund|liquid\s+fund|"
    r"gold\s+fund|fund\s+of\s+funds)\b", re.I)


def _load_map(csv_path, id_col, suffix):
    """token-frozenset -> (ticker, master name), dropping ambiguous keys.

    Ambiguity is resolved by dropping, never by guessing: "DCM Shriram" and
    "DCM Shriram Industries" reduce to the same key, and picking either one
    would silently size a deal against the wrong company. Unknown passes.
    """
    counts, mapping = {}, {}
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                name = row.get("name", "")
                if _FUND_RE.search(name or ""):
                    continue
                key = cluster.tokens(name)
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
                mapping[key] = (f"{row[id_col].strip()}{suffix}", name.strip())
    except FileNotFoundError:
        return {}
    return {k: v for k, v in mapping.items() if counts[k] == 1}  # unique only


_NSE = _load_map(os.path.join(_DATA, "nse_equities.csv"), "symbol", ".NS")
_BSE = _load_map(os.path.join(_DATA, "bse_scrips.csv"), "code", ".BO")

# Combined universe for subset matching. BSE first so NSE overwrites it —
# same NSE-preferred order as the exact tier, so one company sizes identically
# whichever tier resolved it.
_ALL = dict(_BSE)
_ALL.update(_NSE)

# How many master names each token appears in. This is what separates a name
# from a category: "manappuram" appears once, "finance" 147 times.
_TOKEN_DF = Counter()
_TOKEN_INDEX = defaultdict(set)
for _key in _ALL:
    for _tok in _key:
        _TOKEN_DF[_tok] += 1
        _TOKEN_INDEX[_tok].add(_key)


def _load_aliases(path):
    """Brand name -> legal name, e.g. "Nykaa" -> "FSN E-Commerce Ventures".

    Deliberately maps to a NAME, not a ticker: a ticker hardcoded here would
    rot silently when a symbol changes, whereas a legal name is re-validated
    against the master list on every refresh.
    """
    aliases = {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                key = cluster.tokens(row.get("brand", ""))
                legal = (row.get("legal_name") or "").strip()
                if key and legal:
                    aliases[key] = legal
    except FileNotFoundError:
        return {}
    return aliases


_ALIASES = _load_aliases(os.path.join(_DATA, "aliases.csv"))


def resolve_company(company):
    """
    Resolve a company name to a listed ticker, most confident tier first.
    Returns a dict describing the match, or None if nothing resolved
    confidently. Ambiguity always returns None — unknown passes.

      tier "exact"  — unique exact token-set match against the master list.
      tier "alias"  — a curated brand -> legal name mapping, then exact.
      tier "subset" — the name is a proper subset of exactly ONE master name
                      AND rests on a token appearing in at most
                      config.MATCH_RARE_TOKEN_MAX_DF master names. The rarity
                      guard is what makes this safe: without it "Steel Infra
                      Solutions" matches "Magnus Infra Steel" on two words
                      that appear in 35+ names.

    The mirror rule — master name a subset of the deal name — is deliberately
    NOT implemented. It fires whenever a microcap holds a one-word generic
    name: "Royal Challengers Bangalore" -> a company called "Royal",
    "Airtel Africa Finance" -> a company called "Finance".
    """
    key = cluster.tokens(company)
    if not key:
        return None

    hit = _ALL.get(key)
    if hit:
        return {"ticker": hit[0], "master": hit[1], "tier": "exact",
                "rare_token": None, "rare_df": None}

    legal = _ALIASES.get(key)
    if legal:
        hit = _ALL.get(cluster.tokens(legal))
        if hit:
            return {"ticker": hit[0], "master": hit[1], "tier": "alias",
                    "rare_token": None, "rare_df": None}

    # Every master key containing ALL of this name's tokens, by intersecting
    # the per-token postings — cheaper than scanning 5,000 keys per lookup.
    candidates = None
    for tok in key:
        postings = _TOKEN_INDEX.get(tok)
        if not postings:
            return None
        candidates = set(postings) if candidates is None else (candidates & postings)
        if not candidates:
            return None
    candidates = {c for c in candidates if key < c}   # proper subset only
    if len(candidates) != 1:
        return None                                    # ambiguous -> unknown

    rare = min(key, key=lambda t: _TOKEN_DF[t])
    if _TOKEN_DF[rare] > config.MATCH_RARE_TOKEN_MAX_DF:
        return None                                    # only generic words
    ticker, master = _ALL[next(iter(candidates))]
    return {"ticker": ticker, "master": master, "tier": "subset",
            "rare_token": rare, "rare_df": _TOKEN_DF[rare]}


def resolve_ticker(company):
    """Return a yfinance ticker (NSE preferred) or None if not confidently
    listed. Thin wrapper over resolve_company() for callers that only need
    the ticker."""
    match = resolve_company(company)
    return match["ticker"] if match else None


def _load_symbols(csv_path):
    symbols = set()
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                sym = (row.get("symbol") or "").strip().upper()
                if sym:
                    symbols.add(sym)
    except FileNotFoundError:
        pass
    return symbols


# Built from the raw symbol column, NOT from _NSE — a company dropped from the
# name map for being ambiguous still has a perfectly unambiguous ticker.
_NSE_SYMBOLS = _load_symbols(os.path.join(_DATA, "nse_equities.csv"))


def resolve_nse_symbol(title):
    """
    v5: NSE filing titles are `SYMBOL: subject` ("PARKHOSPS: Copy of Newspaper
    Publication") — the ticker itself, so no name matching is needed at all.
    Returns a ticker only when the symbol matches the NSE master list exactly.
    """
    if not title or ":" not in title:
        return None
    symbol = title.split(":", 1)[0].strip().upper()
    return f"{symbol}.NS" if symbol in _NSE_SYMBOLS else None



# --------------------------------------------------------------------------
# Market cap (crore INR), cached in SQLite for 7 days.
# --------------------------------------------------------------------------
def _yf_market_cap_cr(ticker):
    try:
        import yfinance as yf
        mc = yf.Ticker(ticker).fast_info.market_cap  # rupees
        return round(mc / 1e7, 1) if mc else None      # -> crore
    except Exception as exc:  # noqa: BLE001
        print(f"[sizing] yfinance failed for {ticker}: {exc}")
        return None


def _nse_market_cap_cr(ticker):
    """Fallback for NSE symbols via the NSE quote endpoint (often blocked)."""
    if not ticker.endswith(".NS"):
        return None
    symbol = ticker[:-3]
    try:
        import requests
        s = requests.Session()
        h = {"User-Agent": config.BROWSER_UA, "Accept": "application/json",
             "Referer": config.NSE_HOME}
        s.get(config.NSE_HOME, headers=h, timeout=20)
        time.sleep(1)
        r = s.get(f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
                  f"&section=trade_info", headers=h, timeout=20)
        if r.status_code != 200:
            return None
        mc = r.json().get("marketDeptOrderBook", {}).get("tradeInfo", {}).get(
            "totalMarketCap")  # already in crore
        return round(float(mc), 1) if mc else None
    except Exception as exc:  # noqa: BLE001
        print(f"[sizing] NSE fallback failed for {ticker}: {exc}")
        return None


def market_cap_cr(ticker):
    cached = db.get_market_cap(ticker)
    if cached is not None:
        return cached
    mcap = _yf_market_cap_cr(ticker) or _nse_market_cap_cr(ticker)
    if mcap is not None:
        db.set_market_cap(ticker, mcap)
    return mcap


# --------------------------------------------------------------------------
def extract_stake_pct(text):
    """Return a stake percentage (0 < p <= 100) if clearly stated, else None."""
    for rx in _PCT_NEAR_STAKE:
        for m in rx.finditer(text or ""):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if 0 < v <= 100:
                return v
    return None


def resolve_size(company, text):
    """
    For an item with no stated amount, resolve a size signal.
    Returns a dict with `size_source` in {computed, mcap_plausible, None} plus
    supporting fields. main.py applies the gates.
    """
    match = resolve_company(company)
    if not match:
        return {"size_source": None}                  # unlisted / unresolved
    ticker = match["ticker"]
    mcap = market_cap_cr(ticker)
    if mcap is None:
        return {"size_source": None}                  # lookup failed -> pass
    pct = extract_stake_pct(text)
    # `match` rides along so main.py can log HOW the name resolved next to what
    # the pipeline then decided — a fuzzy match that suppresses a real deal is
    # otherwise invisible (v5 Change 4).
    if pct is not None:
        return {
            "size_source": "computed",
            "amount_cr": round(mcap * pct / 100.0, 1),
            "mcap_cr": mcap, "pct": pct, "ticker": ticker, "match": match,
        }
    return {"size_source": "mcap_plausible", "mcap_cr": mcap, "ticker": ticker,
            "match": match}
