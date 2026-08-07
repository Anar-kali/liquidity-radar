# Liquidity Radar — as-built specification

This describes the system **as actually built and deployed**, including the
changes made during implementation. (The original pre-build spec is in git
history if you need it.)

Deployed at **github.com/Anar-kali/liquidity-radar** (public), running on
GitHub Actions' free tier, alerting a private Telegram bot (@Deal_trackbot).

## What it does

Monitors Indian corporate filings and financial news for deals where an
individual (promoter, founder, family shareholder) is likely to receive a
large sum of money, and sends a Telegram alert for each qualifying deal.

Bias: the banker wants real leads without noise. The pipeline keeps high
recall early, then applies a strict precision pass, so genuinely large
individual-payout deals get through while listings, IPO-intention chatter, and
small stake buys are filtered out.

## Environment

- Runs entirely on **GitHub Actions**, free tier. No server.
- **Python 3.11.** Dependencies: `anthropic`, `requests`, `feedparser`,
  `beautifulsoup4`.
- All secrets from environment variables, never hardcoded.
- State persists by committing the SQLite file `radar.db` back to the repo
  after each run. A shared `concurrency` group (`liquidity-radar`) ensures two
  runs never overlap and clash on the database.

Required repo secrets (Settings → Secrets and variables → Actions):
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`.

## Scheduling

**GitHub's built-in cron proved unreliable** (it barely fired for the first
day on a new repo), so scheduling is driven by an **external scheduler
(cron-job.org)** that calls GitHub's `workflow_dispatch` API on an exact
cadence. GitHub's own cron is kept only as a lightweight backup.

Two workflows, driven by two cron-job.org jobs:

| Workflow | External cadence | Runs |
|---|---|---|
| `radar.yml` | every 15 min | `python main.py --mode auto` |
| `digest.yml` | 20:30 IST daily | `python digest.py` (suppression summary) |

`--mode auto` is time-aware (IST) so a single trigger does the right thing:

- weekday 09:00–18:30 → **fast**: exchanges + news
- any day 07:00–23:00 → **news**: news only
- 08:00 / 14:00 / 20:00 → additionally fetch **SEBI DRHP**
- outside those hours → no-op (cheap)

The external scheduler authenticates with a dedicated fine-grained GitHub PAT
(repo-scoped, **Actions: Read and write**), stored in cron-job.org.

## Sources

**Google News RSS — highest yield.** One feed per query:
`https://news.google.com/rss/search?q={URL-encoded query}+when:2d&hl=en-IN&gl=IN&ceid=IN:en`

Queries (edit the list in `config.py`):
- `promoter stake sale crore India`
- `block deal promoter shares crore`
- `DRHP filed SEBI offer for sale`
- `private equity acquires majority stake India crore`
- `promoter offloads stake crore`
- `founders sell shares IPO OFS crore`
- `open offer acquisition promoter crore`
- `family office stake sale India crore`

**Trade press RSS** (sent with a browser User-Agent):
- **Mint** companies — `https://www.livemint.com/rss/companies`
- **Entrackr** — `https://entrackr.com/rss`
- *Removed:* VCCircle (no longer serves a working RSS feed) and Business
  Standard (its RSS returns HTTP 403 to server IPs). Both reach us via Google
  News anyway, as does ET Markets (never added — retail noise).

**BSE announcements.** `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w`
with a browser User-Agent and `Referer: https://www.bseindia.com/`. Kept
subcategories relate to acquisition/disposal/fundraising/shareholding; routine
filings (results, board meetings, newspaper publications, trading-window
notices) are dropped.

**NSE announcements.** `https://www.nseindia.com/api/corporate-announcements?index=equities`
with cookie warm-up (GET the home page, wait 1s, then the API with a Referer).
On block/failure it backs off and returns nothing (retries next run).

**SEBI draft offer documents.** Scrapes `https://www.sebi.gov.in/filings/public-issues`
for links matching draft / DRHP / prospectus / offer document.

Every fetcher is defensive: a source that is down or blocking returns an empty
list rather than crashing the run. No keyword prefilter on news — everything
goes to the classifier. The subcategory filter applies only to BSE filings.

## Classification — two Haiku passes (Haiku only)

Model: `claude-haiku-4-5-20251001` for **both** stages. Sonnet was trialled and
rejected: too expensive for this volume. Cost matters more than marginal
accuracy. Items are classified only when **new** (deduped against the `items`
table), so a normal run classifies just the handful of new items.

Batch 25 items per API call as a numbered list: headline plus the first 400
characters of description. Response is a JSON array, one object per item, same
order.

**Stage 1 — reject confirmed noise (high recall).** Marks an item as a
confirmed negative only for clear cases; when in doubt it passes. Confirmed
negatives include: pure debt; IBC/NCLT; PSU/government divestment; a subsidiary
sale onto a corporate balance sheet (unless the parent is a closely-held
promoter holding company); intra-group restructuring; no Indian individual in
the chain; explicitly all-primary seed/Series-A fundraising; a clearly stated
size under 250 crore; and non-transactions — earnings, price moves, analyst
ratings, product launches, aggregate commentary, **stock-market listings /
trading debuts, IPO subscription / GMP / listing-day coverage, and mere
announcements of an intention to IPO or raise funds**.

**Deterministic amount gate (code, not the model).** A clearly stated size
below the 250-crore threshold is suppressed in code. Conversely, if stage 1
tried to drop something as "under threshold" but the stated size (read as
INR crore, USD, or EUR) is actually ≥ 250 crore, it is kept for stage 2 — a
missed large deal is the one error to avoid.

**Stage 2 — positively confirm a qualifying lead (precision).** Runs only on
stage-1 survivors. Passes an item only when it is a concrete or
actively-negotiated transaction in which an individual (promoter, founder,
family shareholder, or the owners of a privately held / founder-run company) is
likely to receive a large sum. Large buyouts get the benefit of the doubt even
when the seller isn't named. Drops: IPO subscription/listing/intention;
**primary fundraises** (money into the company, or an individual investing in);
a company acquiring a small/minority stake; pure fund-to-fund transfers with no
individual; and anything clearly under 250 crore.

**Amount guards** (`classify.py`): `reconcile_amount` corrects Haiku's
occasional ×10 slip on INR-crore figures (e.g. reads "₹3,000 crore" but returns
300) while leaving foreign-currency ($/€) amounts alone; `stated_cr_max` reads
INR/USD/EUR figures for the recall safety net.

The classifier returns, per item: company, deal_type, amount_cr, amount_raw,
individuals, **buyer**, confidence, one_line, plus the stage decision. The
`buyer` field was **added** beyond the original schema because the clustering
"named buyer" update rule needs it.

Currency: 1 crore = 10M INR, 1 lakh = 100,000 INR, 1 USD = 88 INR, 1 EUR = 96
INR. Never invents a figure; undisclosed size passes (silence is never a small
deal).

## Sizing undisclosed deals (`sizing.py`)

Deals with no stated amount used to all pass through unfiltered, which let
through a lot of small-company noise. Runs between stage 1 and stage 2, only
on items with no stated amount, and resolves size in priority order:

1. **Stake % × market cap** (listed only, deterministic). If the text states a
   stake percentage ("sells 4.58% stake") and the company resolves to a
   ticker, compute `market_cap_cr × pct / 100` — a real figure, not an
   estimate. `size_source = "computed"`.
2. **Market-cap plausibility gate** (listed only). No percentage, but the
   ticker resolved: suppress only if `market_cap_cr < 350` (a promoter selling
   the *entire* company at that size barely clears the 250cr threshold, so any
   partial stake cannot). Above 350, the item passes with
   `size_source = "mcap_plausible"`.
3. **Haiku band estimate** (unlisted only, part of the existing stage 2 call —
   no extra API request). Stage 2 additionally returns `size_band`
   (`UNDER_100 | 100_TO_500 | 500_TO_2000 | OVER_2000 | UNKNOWN`) and
   `size_basis`. The model is told to base this only on what it actually
   knows about the company, and to return UNKNOWN with basis "no information"
   when it has nothing to go on — that is the correct, unpenalised answer.
4. **Nothing resolved** → `size_source = None`, item passes.

**The gate** (recall-biased — suppress only when confident the deal is
small): `computed` amounts under 250cr are suppressed as Rule 8; a
`UNDER_100` band is suppressed as **Rule S**, but only when `size_basis` is
not "no information" (a fabricated band never suppresses). Everything else —
`100_TO_500`, `mcap_plausible` above 350cr, `UNKNOWN` — passes.

**Ticker resolution.** `data/nse_equities.csv` and `data/bse_scrips.csv` are
committed master lists (NSE/BSE published equity data), refreshed monthly by
`.github/workflows/refresh-tickers.yml` via `refresh_tickers.py`. Company names
are matched using the same token-normalisation as clustering; a match resolves
only when it is unique — an ambiguous name resolves to "not listed" rather
than risking the wrong company.

**Market cap.** `yfinance` (`fast_info.market_cap`, NSE `.NS` preferred, BSE
`.BO` fallback), with the NSE quote endpoint as a second fallback if yfinance
fails. Cached in SQLite (`market_caps` table) for 7 days, so a busy day costs
nothing extra and survives rate limiting.

**Alert display never lets an estimate look like a stated fact:**
- Stated: `Rs 2,000cr`
- Computed (stake × mcap): `~Rs 4,987cr (stake x mkt cap)`
- Band: `est. Rs 500-2,000cr`
- Nothing resolved: `Size undisclosed`

## Deal clustering

One transaction reported by many outlets — often under different framing
("IndiaRF buys Fine Edge" vs "Fine Edge Engineering strategic buyout") — must
produce **one** alert.

Tables:
- `items` — every fetched item, keyed on source ID or URL, for dedup.
- `deals` — clustered transactions.
- `deal_members` — which item titles merged into each deal (audit / dedupe check).

The classifier's `company` field is always the **target** (the entity whose
ownership is changing), never the acquirer or investor.

A new item joins an existing deal if EITHER:

- **Name match**, within 72 hours: the two company names, reduced to token
  sets, contain one another. Reduction: drop parentheticals, anything after a
  comma or a descriptive marker ("formerly", "a unit of", "division of", …),
  punctuation, and corporate stopwords (private, ltd, technologies, industries,
  group, holdings, india, engineering, services, …). `{fine, edge}` is a subset
  of `{fine, edge, ashok, iron, works}` → same deal.
- **Amount match**, within 7 days: both amounts are within 5% of each other and
  the names share at least one token. (The wider window catches follow-up
  analysis pieces that land days later.)

`deal_type` is deliberately NOT part of the match — one outlet's "strategic
buyout" is another's "PE secondary".

- First item creates the deal and fires an alert.
- Later matching items attach silently.
- EXCEPT when the **amount** appears (from unknown) or revises by more than
  20% — then one follow-up is sent, marked UPDATE, carrying whatever else is
  newly known (buyer, individuals). A newly-named buyer or individual on its
  own is persisted to the record but does **not** alert — the banker researches
  once alerted, so a second ping naming the buyer is noise.

Accepted trade-off: one company doing two genuinely different deals within the
window can merge into a single alert. Rare, and the cost is only a missed
second ping about a company already on the radar.

`python dedupe_check.py [--days N]` prints the deals clustered in the last N
days with the item titles that merged into each, so over-merging can be spotted.

Note: because Google News rotates its article URLs, the same story can look
"new" to item-level dedup; this clustering layer is the real guard against
duplicate alerts.

## Suppression log, digest, report

Every suppressed item goes into a `suppressed` table with title, URL, the rule
that killed it, `amount_cr` and `amount_raw`. Never deleted. Rules recorded:
Rule 1–9 (stage-1 negatives), **Rule 8** (under threshold — model or code), and
**Rule P** (failed the stage-2 precision check).

`digest.yml` sends one Telegram message at 20:30 IST daily: total suppressed,
a breakdown by rule, and the largest suppressed deal.

`python report.py [--days N]` prints the last N days (default 7) grouped by
rule.

## Alert format

Telegram, Markdown, scannable:

```
[EMOJI] *{company}* · {deal_type} · {amount or "Size undisclosed"}

_{one_line}_

{names if any, else "No individual named"}[  ·  buyer: {buyer}]

[{source}]({url})
```

Red circle 🔴 for high confidence, yellow 🟡 for medium. Follow-ups are prefixed
`UPDATE ·`. Markdown-breaking characters in fields are escaped.

## Command-line flags

- `--mode fast|news|slow|auto` — which sources to fetch (`auto` = time-aware).
- `--dry` — print alerts to the terminal instead of sending to Telegram.
- `--test-telegram` — send one test message and exit.
- `--limit N` — cap how many alerts are sent this run (testing / anti-flood);
  deals are still recorded.

## File layout

```
config.py      settings: threshold, queries, feeds, models, both prompts
db.py          SQLite schema + helpers (items, deals, suppressed, deal_members,
               market_caps)
sources.py     fetchers (Google News, trade press, BSE, NSE, SEBI) + auto plan
classify.py    two-stage Haiku classifier + amount guards
sizing.py      resolve size for undisclosed-amount deals (ticker/mcap/band)
refresh_tickers.py  refresh data/nse_equities.csv + data/bse_scrips.csv
cluster.py     deal clustering / dedup / UPDATE logic
notify.py      Telegram formatting + sending
main.py        orchestrator (fetch → classify → sizing → cluster → alert)
digest.py      daily suppression summary
report.py      N-day suppression report
dedupe_check.py  N-day clustering audit (what merged into each deal)
data/          committed NSE/BSE ticker master lists (for sizing.py)
.github/workflows/radar.yml    main run (--mode auto), external + backup cron
.github/workflows/digest.yml   daily digest, external + backup cron
README.md      plain-English setup + tuning guide
requirements.txt
```

## Deliberately not built

No Sonnet (Haiku only). No valuation lookups, funding history, or cap-table
inference. No MCA / Probe42 / Tracxn. No web dashboard. No family-settlement or
HUF tracking.
