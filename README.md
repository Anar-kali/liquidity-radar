# Liquidity Radar

A private early-warning system for a banker who prospects **individuals about
to receive a large sum of money** — promoters, founders, family shareholders —
from deals happening in Indian markets. It watches financial news and stock
exchange filings around the clock, decides which ones are genuine leads, and
pings a private Telegram chat with a one-line summary and a link.

It costs nothing to run (GitHub's free tier + Claude Haiku, the cheapest
Claude model) and needs no server — it's a handful of Python scripts that
GitHub triggers on a schedule.

**Philosophy: recall over precision, tempered by a second opinion.** Missing a
real deal costs a client. A false alarm costs five seconds. So the first
filter is deliberately generous — when in doubt, it lets an item through — and
a second, stricter pass then cleans up the noise that generosity lets in.

---

## What you actually get

A Telegram message per qualifying deal, within minutes of it breaking:

```
🔴 *Fine Edge Engineering* · strategic buyout · Rs 2,000cr

_IndiaRF acquires Fine Edge Engineering, a machine castings maker, for ~₹2,000cr._

No individual named

[Economic Times](https://...)

[ 👍 Useful ]  [ 🤷 Already knew ]  [ 🗑 Noise ]
```

🔴 = high confidence (a named individual *and* a size are both known), 🟡 =
medium. Tap a button to rate the alert — that feedback rolls up into a weekly
report so the filters can be tuned deliberately, based on evidence, not guesswork.

---

## How it works, end to end

**1. Fetch.** Every run pulls from several places at once: eight targeted
Google News searches, trade-press RSS (Mint, Entrackr), BSE and NSE exchange
announcements, and SEBI's draft-IPO filings. Nothing here goes through the
model yet — this stage just collects candidates and throws away anything
already seen (tracked by URL/ID in `radar.db`, GitHub's copy of the system's
memory).

**2. Classify — stage 1 (Haiku, cheap, generous).** Every new item is batched
25-at-a-time to Claude Haiku with one instruction: *flag only the ones you're
sure are noise.* Pure debt, government divestment, internal restructuring,
earnings news, an *already-open* IPO's subscription/listing-day chatter, a
clearly-stated deal under the size threshold — those get dropped. A company
merely *planning* an IPO is deliberately not on that list (see stage 2 below).
Everything else, including anything ambiguous, passes through.

**3. Size the ones with no stated amount.** A lot of news doesn't quote a
number. For a *listed* company this doesn't need a guess: the market
capitalisation is public data, so a stated stake percentage times market cap
gives a real figure, and even with no percentage, a company's market cap alone
can rule out anything too small to matter. Only for unlisted companies does
Haiku estimate a rough size band — and only when the text actually gives it
something to go on; otherwise it says so, and the item passes rather than
being suppressed on a guess.

**4. Classify — stage 2 (Haiku, strict).** The survivors go through a second,
much stricter Haiku pass whose only job is to *positively confirm* this looks
like a real lead: a concrete or actively-negotiated deal, an individual likely
to be paid, at real scale. This is where "company raises funding" (money
flowing *in*, nobody cashing out) and small stake purchases get caught — the
noise stage 1's generosity let through.

A company **planning or exploring an IPO counts as a lead here, on purpose** —
even with no size stated and no DRHP filed yet. The whole point is reaching
the company before it has picked a banking syndicate. What *does* still get
filtered is the late stage of an *already-open* issue: subscription numbers,
grey-market-premium chatter, anchor-book allotments, listing-day coverage —
by then the syndicate is locked in and there's nothing to prospect.

**5. Cluster.** The same transaction gets reported by five outlets under five
different headlines. Matching company names (stripped of "Ltd", parent-company
asides, punctuation) and matching amounts fold repeats into a single alert. A
follow-up article only earns you a second ping if it reveals the deal's size
for the first time, or revises it by more than 20% — a newly-named buyer or
individual updates the record quietly rather than pinging you again.

**6. Alert — and listen.** The message goes to Telegram with three feedback
buttons. Button presses are logged (still no server needed — the next
scheduled run just checks for new presses) and rolled into a weekly report.

---

## Setup

### The three secrets

Set these in **repo Settings → Secrets and variables → Actions → New repository
secret**. Never put them in code.

| Secret | What it is | Where to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot's password. | Message **@BotFather** in Telegram, send `/newbot`, follow the prompts. |
| `TELEGRAM_CHAT_ID` | Which chat gets the alerts. | Message your bot once (say "hi"), then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and read the `"chat":{"id":...}` field. |
| `ANTHROPIC_API_KEY` | Lets the system call Claude to classify items. | **console.anthropic.com → API Keys.** Starts with `sk-ant-...`. |

### Test before going live

```bash
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python main.py --test-telegram        # sends one test message

export ANTHROPIC_API_KEY=...
python main.py --mode fast --dry      # fetch + classify for real, but PRINT
                                       # alerts instead of sending them
```

---

## Scheduling — how it actually runs

**GitHub's own cron scheduler is unreliable** — on a fresh repo it can sit
silent for hours before firing, and it never guarantees exact timing. So the
real trigger is an **external scheduler, [cron-job.org](https://cron-job.org)**
(free), which calls GitHub's API directly to kick off each workflow — GitHub's
built-in schedule is kept on every workflow only as a backup, in case the
external trigger is ever down.

| Workflow | External cadence | What it does |
|---|---|---|
| `radar.yml` | every 15 min | `main.py --mode auto` — auto-detects what to fetch from the current time (see below) |
| `digest.yml` | 20:30 IST daily | suppression summary |
| `feedback-report.yml` | Monday 09:00 IST | weekly feedback rollup |
| `blockdeals.yml` | 19:30 IST weekdays | NSE bulk/block deal files + PIT feed + salami-slice aggregation (see below) |
| `refresh-tickers.yml` | monthly | refreshes the NSE/BSE ticker lists used for sizing |

`--mode auto` is time-aware, so the one 15-minute trigger does the right thing
without needing separate schedules:

- **weekday 09:00–18:30 IST** → full sweep: exchanges + news
- **any day 07:00–23:00 IST** → news only
- **08:00 / 14:00 / 20:00** → also checks SEBI for new IPO filings
- outside those hours → does nothing (cheap no-op)

A shared `concurrency` group means two runs never overlap and clash on the
database. You can trigger any workflow by hand any time: **Actions → pick a
workflow → Run workflow.**

---

## Visibility — three different reports

| What | Where | Shows |
|---|---|---|
| **Daily digest** | Telegram, 20:30 IST | how many items were filtered out today, broken down by which rule caught them, plus the single largest one |
| **Weekly feedback report** | Telegram, Monday 09:00 IST | your 👍/🤷/🗑 button ratings from the last 14 days, with noise broken down by deal type, size, and source — so you can see *where* the filters are still letting junk through |
| **7-day suppression report** | run locally: `python report.py [--days N]` | the same suppression data as the digest, in more detail, on demand |
| **Clustering audit** | run locally: `python dedupe_check.py [--days N]` | which article titles merged into each alert, so you can sanity-check nothing is being over- or under-merged |

**The feedback report never changes anything automatically.** It's there so
you can look at the evidence and decide what to tune yourself — see below.

---

## Two more alert types: when the money is a *fact*, not a classifier guess

Everything above works from news and public filings, which means every alert
is ultimately Claude's best read of prose. Two extra sources skip that
entirely:

- **🟢 CONFIRMED alerts** — India's stock exchanges publish daily files of
  every bulk and block deal (large on-market trades), and a structured feed of
  every promoter/director trade disclosure (PIT). When one of these directly
  names an individual selling ≥₹250cr, you get a CONFIRMED alert — no
  inference, no false positives, the seller's name and the exact value are
  right there in the exchange's own data. Block deals settle T+1, so this can
  land the morning after a promoter sold.
- **🟣 PATTERN alerts** — the same feeds also catch something news never will:
  a promoter selling ₹120cr three times over six weeks. No single sale trips
  the threshold and no outlet writes about it, but the total is ₹360cr. This
  fires only a handful of times a **quarter** — the value is that nothing else
  catches it, not that it's frequent.

**Honesty note:** the exchange data feeds behind these two alert types are
guarded more heavily by NSE than the news/announcement feeds elsewhere in this
system, and their exact response format couldn't be confirmed before shipping
this. It's built to fail safe either way — if NSE blocks a fetch, it logs
clearly and tries again the next day rather than breaking anything else. BSE's
equivalent bulk/block deal files aren't included; their web addresses weren't
findable during development (unlike BSE's announcement feed, which works
fine) — NSE alone still covers most of the volume.

---

## Tuning

Everything below is a small edit to **`config.py`**, then commit + push (or
edit directly on GitHub: open the file → pencil icon → commit).

**Change the size threshold** (currently ₹250 crore):
```python
THRESHOLD_CR = 250
```

**Add or remove a Google News search:**
```python
GOOGLE_NEWS_QUERIES = [
    "promoter stake sale crore India",
    "block deal promoter shares crore",
    # add a line to add a search; delete a line to remove one
]
```

**Change how cautious the "listed company, no stated amount" gate is**
(currently: below ₹350cr market cap, don't bother — see step 3 above):
```python
MCAP_PLAUSIBLE_MIN = 350
```

**Tighten or loosen either classifier stage:** the exact instructions given to
Claude are `SYSTEM_PROMPT` (stage 1, generous) and `STAGE2_SYSTEM_PROMPT`
(stage 2, strict) in `config.py`. If the weekly feedback report shows a
specific pattern of noise, this is where to add a rule against it.

---

## Deliberately not built

No valuation lookups, no funding-history tracking, no cap-table inference, no
MCA / Probe42 / Tracxn, no web dashboard, no HUF/family-settlement tracking.
**No Sonnet, anywhere** — Haiku only, on both classifier stages, because cost
matters more than marginal accuracy at this volume.

---

For the full technical specification — exact prompts, table schemas,
clustering algorithm, every module — see `SPEC.md`.
