"""
Refresh the committed NSE / BSE ticker master lists (data/*.csv).

Run monthly by .github/workflows/refresh-tickers.yml. The download URLs move
occasionally — if this starts writing 0 rows, check them.
"""

import csv
import io
import json
import os
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
DATA = os.path.join(os.path.dirname(__file__), "data")

NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_URL = ("https://api.bseindia.com/BseIndiaAPI/api/ListOfScripData/w"
           "?Group=&Scripcode=&industry=&segment=Equity&status=Active")


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def refresh_nse():
    raw = _get(NSE_URL).decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(raw)))
    hdr = [h.strip() for h in rows[0]]
    si, ni = hdr.index("SYMBOL"), hdr.index("NAME OF COMPANY")
    out = os.path.join(DATA, "nse_equities.csv")
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name"])
        for r in rows[1:]:
            if len(r) > max(si, ni):
                w.writerow([r[si].strip(), r[ni].strip()])
                n += 1
    print(f"NSE: {n} rows")
    return n


def refresh_bse():
    data = json.loads(_get(BSE_URL, {"User-Agent": UA,
                                     "Referer": "https://www.bseindia.com/"}))
    out = os.path.join(DATA, "bse_scrips.csv")
    n = 0
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name"])
        for d in data:
            code = str(d.get("SCRIP_CD", "")).strip()
            name = (d.get("Scrip_Name") or "").strip()
            if code and name:
                w.writerow([code, name])
                n += 1
    print(f"BSE: {n} rows")
    return n


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    # Refresh each independently — one source failing must not wipe the other.
    for fn in (refresh_nse, refresh_bse):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"{fn.__name__} failed (keeping existing file): {exc}")
