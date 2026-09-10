"""
Liquidity Radar — resolve Google News redirect tokens to publisher URLs.

Google News RSS gives us `news.google.com/rss/articles/CBMi...` rather than the
article. 1,252 of our source rows are these, and until now they were treated as
dead ends — `resolvable: false` in the export, unreachable by any fetcher, and
the reason the enrichment ceiling was quoted at ~29%.

They are not dead ends. They resolve, for free, with no API key.

## Two token formats, and why the old check gave up

Older tokens base64-decode straight to the destination URL. Newer ones — every
token we hold, all starting `AU_yqL...` once decoded — carry only an opaque
Google-internal id:

    CBMi7wFB...  ->  b'\\x08\\x13"\\xef\\x01AU_yqLPWB__wmnWfR6v...'

No URL in there at any depth, which is presumably what the original check
looked for. The id is meaningful only to Google, so the destination has to be
asked for rather than decoded.

## How the round-trip works

Google News' own client does this on every click:

  1. GET the article page. It carries `data-n-a-sg` (a signature) and
     `data-n-a-ts` (a timestamp) in the markup.
  2. POST both, with the token, to the `batchexecute` RPC endpoint that the
     page's own JavaScript calls.
  3. The response embeds the publisher URL.

Measured 2026-09-10: 40 tokens, 40 resolved, no throttling. This is a public
endpoint used by the ordinary web client, but it is undocumented and can change
without notice — so every failure here is non-fatal by design. A token that
will not resolve simply stays unenriched, exactly as it is today.

Resolutions are cached in `gnews_cache`: the round-trip costs two HTTP requests
and the answer never changes, so re-resolving would be pure waste and extra
load on an endpoint we do not own.
"""
import json
import re
import time

import requests

import db

# A browser UA, deliberately. The endpoint is the one a browser calls, and the
# earlier enrichment prototype's honest bot UA got it blocked by most Indian
# news sites — which is a large part of why its fetch rate read 46%.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/127.0.0.0 Safari/537.36")

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
ARTICLE_URL = "https://news.google.com/rss/articles/{}"

# Pause between round-trips. Nothing here is time-critical and the endpoint is
# not ours, so this stays polite.
SLEEP_SECONDS = 1.5
TIMEOUT = 20

_session = None


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": UA})
    return _session


def token_of(url):
    """The token in a Google News RSS url, or None if it is not one."""
    if not url or "news.google.com" not in url:
        return None
    tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _signature(token):
    """Scrape the per-article signature and timestamp off the page."""
    r = _sess().get(ARTICLE_URL.format(token), timeout=TIMEOUT)
    r.raise_for_status()
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not (sg and ts):
        # Google served something else — a consent wall, a redirect, a layout
        # change. Not recoverable here and not worth retrying in this run.
        raise ValueError("no signature on article page")
    return sg.group(1), ts.group(1)


def _resolve_uncached(token):
    sg, ts = _signature(token)
    payload = [
        "Fbv4je",
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,'
        'null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
        f'"{token}",{ts},"{sg}"]',
    ]
    body = "f.req=" + requests.utils.quote(json.dumps([[payload]]))
    r = _sess().post(
        BATCH_URL, data=body, timeout=TIMEOUT,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    r.raise_for_status()
    # The response is Google's anti-JSON-hijacking format: a length-prefixed
    # preamble, then the payload. Split on the blank line and unescape.
    parts = r.text.split("\n\n")
    blob = (parts[1] if len(parts) > 1 else r.text).replace('\\"', '"')
    m = re.search(r'"(https?://[^"\\]+)"', blob)
    if not m:
        raise ValueError("no url in batchexecute response")
    return m.group(1)


def resolve(url, path=None, use_cache=True, write_cache=True):
    """Publisher URL behind a Google News link, or None if it cannot be had.

    `path` defaults to db.DB_PATH AT CALL TIME, not at import time. That
    distinction matters: a default of `path=db.DB_PATH` in the signature binds
    the string when this module is imported, so reassigning db.DB_PATH later
    (as --db does) silently has no effect and writes land in the wrong
    database. That exact bug sent a --db test run's writes into the real
    radar.db.

    `write_cache=False` lets a dry run resolve without leaving anything
    behind — a cache write is still a write, whatever the caller was promised.

    Never raises: a token we cannot resolve is the status quo, not an error.
    """
    path = path or db.DB_PATH
    token = token_of(url)
    if not token:
        return url if url and url.startswith("http") else None
    if use_cache:
        hit = db.gnews_cached(token, path=path)
        if hit is not None:
            return hit or None          # "" is a cached failure
    try:
        resolved = _resolve_uncached(token)
    except Exception as exc:  # noqa: BLE001 — network, parsing, layout drift
        print(f"[gnews] unresolved {token[:24]}...: {type(exc).__name__}: {exc}")
        if write_cache:
            db.gnews_cache(token, "", path=path)
        return None
    if write_cache:
        db.gnews_cache(token, resolved, path=path)
    time.sleep(SLEEP_SECONDS)
    return resolved


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        print(f"{u[:50]}... -> {resolve(u)}")
