"""
Liquidity Radar — the collectors.

Each fetch function returns a list of dicts shaped like:
    {"id": ..., "source": ..., "title": ..., "url": ..., "description": ...}

Every fetcher is defensive: if a source is down or blocking us, it logs a
warning and returns an empty list rather than crashing the whole run.
"""

import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

import config

IST = timezone(timedelta(hours=5, minutes=30))


def _log(msg):
    print(f"[sources] {msg}")


def _rss_entries(url, source_name):
    """Parse an RSS/Atom feed into our standard item shape."""
    items = []
    try:
        # Send a browser User-Agent — some feeds (e.g. Entrackr) return
        # nothing to the default feedparser agent.
        feed = feedparser.parse(url, agent=config.BROWSER_UA)
        for e in feed.entries:
            link = e.get("link", "")
            title = e.get("title", "")
            if not link and not title:
                continue
            items.append(
                {
                    "id": link or title,
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "description": e.get("summary", "") or e.get("description", ""),
                }
            )
    except Exception as exc:  # noqa: BLE001
        _log(f"{source_name}: feed error: {exc}")
    return items


# --------------------------------------------------------------------------
# Google News — one feed per query. Highest yield.
# --------------------------------------------------------------------------
def fetch_google_news():
    items = []
    for query in config.GOOGLE_NEWS_QUERIES:
        url = config.GOOGLE_NEWS_URL.format(query=urllib.parse.quote_plus(query))
        got = _rss_entries(url, "Google News")
        _log(f"Google News '{query}': {len(got)} items")
        items.extend(got)
    return items


# --------------------------------------------------------------------------
# Trade press RSS.
# --------------------------------------------------------------------------
def fetch_trade_press():
    items = []
    for name, url in config.TRADE_PRESS_FEEDS.items():
        got = _rss_entries(url, name)
        _log(f"{name}: {len(got)} items")
        items.extend(got)
    return items


# --------------------------------------------------------------------------
# BSE announcements.
# --------------------------------------------------------------------------
def _bse_keep(subcategory):
    s = (subcategory or "").lower()
    if any(k in s for k in config.BSE_DROP_KEYWORDS):
        return False
    return any(k in s for k in config.BSE_KEEP_KEYWORDS)


def fetch_bse():
    headers = {
        "User-Agent": config.BROWSER_UA,
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/json, text/plain, */*",
    }
    params = {
        "pageno": 1,
        "strCat": -1,
        "strPrevDate": time.strftime("%Y%m%d"),
        "strToDate": time.strftime("%Y%m%d"),
        "strSearch": "P",
        "strscrip": "",
        "strType": "C",
    }
    items = []
    try:
        r = requests.get(config.BSE_API, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data.get("Table", []) if isinstance(data, dict) else []
        for row in rows:
            subcat = row.get("SUBCATNAME") or row.get("NEWSSUB") or ""
            if not _bse_keep(subcat):
                continue
            headline = row.get("HEADLINE") or row.get("NEWSSUB") or ""
            scrip = row.get("SLONGNAME") or row.get("SCRIP_CD") or ""
            news_id = str(row.get("NEWSID") or row.get("NEWS_DT") or headline)
            attach = row.get("ATTACHMENTNAME") or ""
            url = (
                f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attach}"
                if attach
                else "https://www.bseindia.com/corporates/ann.html"
            )
            items.append(
                {
                    "id": f"BSE:{news_id}",
                    "source": "BSE",
                    "title": f"{scrip}: {headline}".strip(": "),
                    "url": url,
                    "description": f"{subcat}. {headline}",
                }
            )
        _log(f"BSE: {len(items)} kept items")
    except Exception as exc:  # noqa: BLE001
        _log(f"BSE: error, skipping this run: {exc}")
    return items


# --------------------------------------------------------------------------
# NSE announcements. Needs a cookie warm-up and blocks aggressively.
# On failure we back off and return nothing (retry happens next run).
# --------------------------------------------------------------------------
def warm_nse_session():
    """
    A requests.Session() with NSE's cookie warm-up already done, plus the
    headers NSE expects. Reused by deals_files.py and pit_feed.py so the
    warm-up isn't duplicated across every NSE-hitting module.
    """
    session = requests.Session()
    headers = {
        "User-Agent": config.BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": config.NSE_HOME,
    }
    session.get(config.NSE_HOME, headers=headers, timeout=30)
    time.sleep(1)
    return session, headers


def fetch_nse():
    items = []
    try:
        session, headers = warm_nse_session()
        r = session.get(config.NSE_API, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])
        for row in rows:
            subject = row.get("subject") or row.get("desc") or ""
            symbol = row.get("symbol") or ""
            attach = row.get("attchmntFile") or ""
            when = row.get("an_dt") or row.get("dt") or ""
            items.append(
                {
                    "id": f"NSE:{symbol}:{when}:{subject}"[:200],
                    "source": "NSE",
                    "title": f"{symbol}: {subject}".strip(": "),
                    "url": attach or "https://www.nseindia.com/companies-listing/"
                    "corporate-filings-announcements",
                    "description": subject,
                }
            )
        _log(f"NSE: {len(items)} items")
    except Exception as exc:  # noqa: BLE001
        _log(f"NSE: blocked or error, backing off until next run: {exc}")
    return items


# --------------------------------------------------------------------------
# SEBI draft offer documents (DRHP).
# --------------------------------------------------------------------------
def fetch_sebi():
    headers = {"User-Agent": config.BROWSER_UA}
    items = []
    try:
        r = requests.get(config.SEBI_URL, headers=headers, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            haystack = f"{text} {href}".lower()
            if not any(k in haystack for k in config.SEBI_LINK_KEYWORDS):
                continue
            if not text:
                continue
            url = href if href.startswith("http") else f"https://www.sebi.gov.in{href}"
            items.append(
                {
                    "id": f"SEBI:{url}",
                    "source": "SEBI",
                    "title": text,
                    "url": url,
                    "description": f"SEBI draft offer document: {text}",
                }
            )
        _log(f"SEBI: {len(items)} draft-document links")
    except Exception as exc:  # noqa: BLE001
        _log(f"SEBI: error, skipping this run: {exc}")
    return items


# --------------------------------------------------------------------------
# Source groups, keyed by workflow mode.
# --------------------------------------------------------------------------
def fetch_auto(now=None):
    """
    Time-aware plan, so a single trigger (fired every ~15 min) does the right
    thing. Uses IST:
      weekday 09:00-18:30 -> fast (exchanges + news)
      any day 07:00-23:00 -> news
      outside those        -> nothing
    Plus SEBI DRHP at the 08:00 / 14:00 / 20:00 checkpoints.
    """
    now = now or datetime.now(IST)
    minutes = now.hour * 60 + now.minute
    weekday = now.weekday() < 5  # Mon-Fri
    items, ran = [], []

    if weekday and 540 <= minutes <= 1110:      # 09:00 - 18:30 IST
        items += fetch_google_news()
        items += fetch_trade_press()
        items += fetch_bse()
        items += fetch_nse()
        ran.append("fast")
    elif 420 <= minutes <= 1380:                # 07:00 - 23:00 IST
        items += fetch_google_news()
        items += fetch_trade_press()
        ran.append("news")

    if now.hour in (8, 14, 20) and now.minute < 15:  # DRHP checkpoints
        items += fetch_sebi()
        ran.append("slow")

    day = "weekday" if weekday else "weekend"
    _log(f"auto plan ({now:%H:%M} IST {day}): {ran or ['idle — outside hours']}")
    return items


def fetch_for_mode(mode):
    """
    fast  -> exchanges + news
    news  -> news only
    slow  -> SEBI DRHP
    auto  -> pick based on the current IST time (used by the scheduler)
    """
    if mode == "auto":
        return fetch_auto()
    items = []
    if mode == "fast":
        items += fetch_google_news()
        items += fetch_trade_press()
        items += fetch_bse()
        items += fetch_nse()
    elif mode == "news":
        items += fetch_google_news()
        items += fetch_trade_press()
    elif mode == "slow":
        items += fetch_sebi()
    else:
        raise ValueError(f"unknown mode: {mode}")
    return items
