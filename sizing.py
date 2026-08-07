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
def _load_map(csv_path, id_col, suffix):
    """token-frozenset -> yfinance ticker, dropping ambiguous keys."""
    counts, mapping = {}, {}
    try:
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                key = cluster.tokens(row.get("name", ""))
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
                mapping[key] = f"{row[id_col].strip()}{suffix}"
    except FileNotFoundError:
        return {}
    return {k: v for k, v in mapping.items() if counts[k] == 1}  # unique only


_NSE = _load_map(os.path.join(_DATA, "nse_equities.csv"), "symbol", ".NS")
_BSE = _load_map(os.path.join(_DATA, "bse_scrips.csv"), "code", ".BO")


def resolve_ticker(company):
    """Return a yfinance ticker (NSE preferred) or None if not confidently listed."""
    key = cluster.tokens(company)
    if not key:
        return None
    return _NSE.get(key) or _BSE.get(key)


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
    ticker = resolve_ticker(company)
    if not ticker:
        return {"size_source": None}                  # unlisted / unresolved
    mcap = market_cap_cr(ticker)
    if mcap is None:
        return {"size_source": None}                  # lookup failed -> pass
    pct = extract_stake_pct(text)
    if pct is not None:
        return {
            "size_source": "computed",
            "amount_cr": round(mcap * pct / 100.0, 1),
            "mcap_cr": mcap, "pct": pct, "ticker": ticker,
        }
    return {"size_source": "mcap_plausible", "mcap_cr": mcap, "ticker": ticker}
