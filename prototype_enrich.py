"""
PROTOTYPE — not wired into the pipeline. Measures whether a full-article read
stage is worth building, in two independently runnable halves:

    python prototype_enrich.py fetch     # no API key needed
    python prototype_enrich.py extract   # needs ANTHROPIC_API_KEY

`fetch` is the half that decides the feature. Reading the article can only help
on deals whose article can actually be retrieved, and 56% of deals carry
nothing but an opaque Google News redirect. Of the 44% that do have a real
publisher URL, an unknown share are paywalled or block bots (livemint refused a
fetch during costing). This measures that share against every fetchable deal.

`extract` then runs the model over what was retrieved and scores it against
what the headline-only pipeline already stored, so the "hit rate" is strictly
NEW information: a field the model filled that the database has empty.
"""
import argparse
import json
import glob
import os
import random
import re
import sys
import time
from collections import Counter

import requests
from bs4 import BeautifulSoup

SITE_DEALS = "site/data/deals"
FETCH_OUT = "prototype_fetch.json"
EXTRACT_OUT = "prototype_extract.json"

# Identify honestly rather than impersonating a browser. A site that blocks
# this is telling us it does not want automated reads, and the point of this
# run is to find out how many do.
UA = "liquidity-radar-research/0.1 (+https://github.com/Anar-kali/liquidity-radar)"
TIMEOUT = 15
DELAY = 0.7  # polite gap between requests to the same host

_STRIP = ("script", "style", "nav", "footer", "header", "aside", "form",
          "noscript", "iframe", "figure")

_PAYWALL_HINTS = (
    "subscribe to read", "subscriber only", "sign in to continue",
    "already a subscriber", "premium article", "unlock this article",
    "create a free account", "continue reading with",
)


def article_text(html):
    """Best-effort body text. Returns (text, how_found)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_STRIP)):
        tag.decompose()
    for selector, label in (("article", "article"),
                            ("main", "main"),
                            ("div.story-content", "story-content"),
                            ("div.article-body", "article-body")):
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if len(text.split()) > 120:
                return text, label
    body = soup.body or soup
    return body.get_text(" ", strip=True), "body"


def classify_failure(text, status):
    """Why an otherwise-200 fetch is still unusable."""
    if status != 200:
        return f"http {status}"
    words = len(text.split())
    low = text[:4000].lower()
    if any(h in low for h in _PAYWALL_HINTS):
        return "paywall"
    if words < 120:
        return f"too short ({words}w)"
    return None


def load_candidates():
    """Deals with a real publisher URL, newest first."""
    out = []
    for path in glob.glob(os.path.join(SITE_DEALS, "*.json")):
        deal = json.load(open(path, encoding="utf-8"))
        source = next((s for s in deal["sources"] if s["resolvable"]), None)
        if not source or not source["url"].startswith("http"):
            continue
        out.append({
            "id": deal["id"],
            "company": deal["company"],
            "url": source["url"],
            "outlet": source["outlet"],
            "headline": source["title"],
            # what the headline-only pipeline already knows
            "have": {
                "amountCr": deal["amountCr"],
                "seller": deal["seller"],
                "buyer": deal["buyer"],
                "individuals": deal["individuals"],
                "advisers": deal["advisers"],
            },
            "oneLine": deal["oneLine"],
        })
    out.sort(key=lambda d: -d["id"])
    return out


def run_fetch(limit, seed):
    deals = load_candidates()
    print(f"[fetch] {len(deals)} deals have a resolvable publisher URL")
    if limit and limit < len(deals):
        random.Random(seed).shuffle(deals)
        deals = deals[:limit]
        print(f"[fetch] sampling {limit}")

    results, last_host = [], {}
    for i, deal in enumerate(deals, 1):
        host = deal["url"].split("/")[2].replace("www.", "")
        gap = DELAY - (time.time() - last_host.get(host, 0))
        if gap > 0:
            time.sleep(gap)
        row = {**{k: deal[k] for k in ("id", "company", "url", "outlet", "headline")},
               "host": host, "have": deal["have"], "oneLine": deal["oneLine"]}
        try:
            resp = requests.get(deal["url"], timeout=TIMEOUT,
                                headers={"User-Agent": UA}, allow_redirects=True)
            last_host[host] = time.time()
            ctype = resp.headers.get("content-type", "")
            if "pdf" in ctype or deal["url"].lower().endswith(".pdf"):
                row.update(ok=False, reason="pdf", words=0,
                           bytes=len(resp.content))
            else:
                text, how = article_text(resp.text)
                reason = classify_failure(text, resp.status_code)
                row.update(ok=reason is None, reason=reason,
                           words=len(text.split()), how=how,
                           text=text[:20000] if reason is None else "")
        except Exception as exc:  # noqa: BLE001
            last_host[host] = time.time()
            row.update(ok=False, reason=f"{type(exc).__name__}", words=0)
        results.append(row)
        mark = "ok " if row["ok"] else "-- "
        print(f"  [{i:3d}/{len(deals)}] {mark}{host:28s} {row.get('words',0):5d}w "
              f"{row.get('reason') or ''}")

    json.dump(results, open(FETCH_OUT, "w", encoding="utf-8"), ensure_ascii=False)
    report_fetch(results)


def report_fetch(results):
    total = len(results)
    ok = [r for r in results if r["ok"]]
    print(f"\n{'='*62}\nFETCH RESULT\n{'='*62}")
    print(f"attempted        {total}")
    print(f"usable text      {len(ok)}  ({100*len(ok)/total:.0f}%)")
    print(f"failed           {total-len(ok)}  ({100*(total-len(ok))/total:.0f}%)")

    print("\nfailure reasons:")
    for reason, n in Counter(r["reason"] for r in results if not r["ok"]).most_common():
        print(f"  {n:4d}  {reason}")

    print("\nby host (usable / attempted):")
    hosts = Counter(r["host"] for r in results)
    okh = Counter(r["host"] for r in ok)
    for host, n in hosts.most_common(14):
        print(f"  {okh[host]:3d}/{n:<3d}  {host}")

    if ok:
        words = sorted(r["words"] for r in ok)
        med = words[len(words)//2]
        print(f"\narticle length: median {med}w (~{med*1.4:.0f} tokens) | "
              f"min {words[0]}w | max {words[-1]}w")
        print(f"est. input tokens/deal: ~{med*1.4 + 1200:.0f} "
              f"(article + 1200-token prompt)")
    print(f"\nwrote {FETCH_OUT}")


# ---------------------------------------------------------------------------
# extract — needs ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------
PROMPT = """You are reading a full news article about an Indian M&A / IPO / \
stake-sale transaction. Extract ONLY what the article actually states.

Return one JSON object, no markdown fence:
{
  "amount_cr": <deal value in crore INR as a number, or null>,
  "amount_basis": "<the exact sentence the figure came from, or null>",
  "seller": "<who RECEIVES the money — the selling shareholder, or null>",
  "buyer": "<the acquirer, or null>",
  "individuals": ["<named people who personally receive money>"],
  "advisers": [{"firm": "<name>", "role": "sellside|buyside|legal|unknown"}],
  "confidence": "high|medium|low"
}

Rules:
- Never guess. A field the article does not state is null (or an empty list).
- "seller" is the paying-OUT side. In "X acquires Y", the seller is whoever
  owned Y — if unstated, null. Never put the acquirer there.
- "individuals" are people receiving money personally: promoters, founders,
  family shareholders. Not executives quoted in the article, not journalists,
  not analysts.
- amount_cr is the DEAL value, not revenue, valuation, market cap, or a fund
  size. If the article gives only a valuation, amount_cr is null.
- Advisers are banks or law firms advising on THIS transaction.
"""


def run_extract(model, limit):
    if not os.path.exists(FETCH_OUT):
        sys.exit(f"run `python {sys.argv[0]} fetch` first")
    rows = [r for r in json.load(open(FETCH_OUT, encoding="utf-8"))
            if r["ok"] and r.get("text")]
    if limit:
        rows = rows[:limit]
    if not rows:
        sys.exit("no usable articles in the fetch output")

    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic()
    print(f"[extract] {len(rows)} articles through {model}")
    out, spend_in, spend_out = [], 0, 0
    for i, row in enumerate(rows, 1):
        msg = (f"Headline: {row['headline']}\nOutlet: {row['outlet']}\n"
               f"Company (per our pipeline): {row['company']}\n\n"
               f"ARTICLE:\n{row['text'][:20000]}")
        try:
            resp = client.messages.create(
                model=model, max_tokens=1024, system=PROMPT,
                messages=[{"role": "user", "content": msg}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            spend_in += resp.usage.input_tokens
            spend_out += resp.usage.output_tokens
            cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                             flags=re.MULTILINE).strip()
            got = json.loads(cleaned)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i:3d}] FAILED {type(exc).__name__}: {exc}")
            continue
        out.append({"id": row["id"], "company": row["company"],
                    "have": row["have"], "got": got, "headline": row["headline"]})
        print(f"  [{i:3d}/{len(rows)}] {row['company'][:34]:34s} "
              f"amt={got.get('amount_cr')} sell={bool(got.get('seller'))} "
              f"ppl={len(got.get('individuals') or [])} "
              f"adv={len(got.get('advisers') or [])}")

    json.dump(out, open(EXTRACT_OUT, "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    report_extract(out, model, spend_in, spend_out)


PRICES = {"claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0),
          "claude-haiku-4-5": (1.0, 5.0)}


def report_extract(rows, model, tin, tout):
    n = len(rows)
    print(f"\n{'='*62}\nEXTRACTION HIT RATE  ({n} articles, {model})\n{'='*62}")
    print("NEW = the model filled a field the pipeline has empty\n")
    print(f"{'field':14s} {'had':>5s} {'NEW':>5s} {'new %':>7s} {'confirmed':>10s} {'conflict':>9s}")

    def scalar(field, key):
        had = sum(1 for r in rows if r["have"][field] not in (None, "", []))
        new = conf = clash = 0
        for r in rows:
            old, got = r["have"][field], r["got"].get(key)
            if got in (None, "", []):
                continue
            if old in (None, "", []):
                new += 1
            elif str(old).strip().lower() == str(got).strip().lower():
                conf += 1
            else:
                clash += 1
        empty = n - had
        pct = f"{100*new/empty:.0f}%" if empty else "—"
        print(f"{field:14s} {had:5d} {new:5d} {pct:>7s} {conf:10d} {clash:9d}")
        return new

    gains = 0
    gains += scalar("amountCr", "amount_cr")
    gains += scalar("seller", "seller")
    gains += scalar("buyer", "buyer")

    for field, key in (("individuals", "individuals"), ("advisers", "advisers")):
        had = sum(1 for r in rows if r["have"][field])
        new = sum(1 for r in rows if not r["have"][field] and r["got"].get(key))
        empty = n - had
        pct = f"{100*new/empty:.0f}%" if empty else "—"
        print(f"{field:14s} {had:5d} {new:5d} {pct:>7s} {'—':>10s} {'—':>9s}")
        gains += new

    print(f"\ndeals gaining at least one field: "
          f"{sum(1 for r in rows if _gained(r))}/{n}")
    pin, pout = PRICES.get(model, (2.0, 10.0))
    cost = tin*pin/1e6 + tout*pout/1e6
    print(f"tokens: {tin:,} in / {tout:,} out | spend this run: ${cost:.4f} "
          f"(${cost/n:.4f}/deal)")
    print(f"at 9 fetchable deals/day: ${cost/n*9*30:.2f}/month")
    print(f"\nwrote {EXTRACT_OUT}")


def _gained(r):
    for f, k in (("amountCr", "amount_cr"), ("seller", "seller"),
                 ("buyer", "buyer"), ("individuals", "individuals"),
                 ("advisers", "advisers")):
        if r["have"][f] in (None, "", []) and r["got"].get(k) not in (None, "", []):
            return True
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["fetch", "extract", "report"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--model", default="claude-sonnet-5")
    a = p.parse_args()
    if a.stage == "fetch":
        run_fetch(a.limit, a.seed)
    elif a.stage == "extract":
        run_extract(a.model, a.limit)
    else:
        report_fetch(json.load(open(FETCH_OUT, encoding="utf-8")))


if __name__ == "__main__":
    main()
