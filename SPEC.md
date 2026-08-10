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
individual-payout deals get through while trading-debut listings, an
already-open IPO's subscription/GMP chatter, and small stake buys are filtered
out. A company merely *planning or exploring* an IPO is explicitly NOT
filtered — it is one of the highest-value leads in the system (the promoters
haven't picked a banking syndicate yet), even with no size stated and no DRHP
filed.

## Environment

- Runs entirely on **GitHub Actions**, free tier. No server.
- **Python 3.11.** Dependencies: `anthropic`, `requests`, `feedparser`,
  `beautifulsoup4`.
- All secrets from environment variables, never hardcoded.
- State persists by committing the SQLite file `radar.db` back to the repo
  after each run. A shared `concurrency` group (`liquidity-radar`) across
  every workflow that touches it ensures two runs never execute concurrently.
  `radar.yml` and `blockdeals.yml` (the two that write `radar.db`) pin
  `actions/checkout` to `ref: main`, not the default dispatch-time SHA — a
  run queued behind another must check out the *current* tip once it starts,
  or its "Commit state" step's binary-file rebase can conflict and silently
  lose that run's writes (`|| true` swallows the failure). Demonstrated live
  during v4 testing: a real CONFIRMED alert's DB record was lost this way
  before the fix.

Required repo secrets (Settings → Secrets and variables → Actions):
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`.

## Scheduling

**GitHub's built-in cron proved unreliable** (it barely fired for the first
day on a new repo), so scheduling is driven by an **external scheduler
(cron-job.org)** that calls GitHub's `workflow_dispatch` API on an exact
cadence. GitHub's own cron is kept only as a lightweight backup.

| Workflow | External cadence | Runs |
|---|---|---|
| `radar.yml` | every 15 min | `python main.py --mode auto` |
| `digest.yml` | 20:30 IST daily | `python digest.py` (suppression summary) |
| `feedback-report.yml` | Monday 09:00 IST | `python feedback_report.py` (weekly rollup) |
| `blockdeals.yml` | 19:30 IST weekdays | `python blockdeals.py` (bulk/block deals, PIT feed, salami-slice aggregation) |
| `refresh-tickers.yml` | monthly | `python refresh_tickers.py` (NSE/BSE ticker master lists) |

`--mode auto` is time-aware (IST) so a single trigger does the right thing:

- weekday 09:00–18:30 → **fast**: exchanges + news + SEBI DRHP
- any day 07:00–23:00 → **news**: news + SEBI DRHP
- SEBI DRHP is fetched on **every run** in either window (a cheap single-page
  scrape, no rate-limit risk), not on a separate slower schedule — a DRHP
  filing is one of the highest-value IPO-stage signals, so it's checked as
  fast as news is
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
- `plans IPO India crore`
- `appoints bankers IPO India`

**Trade press RSS** (sent with a browser User-Agent):
- **Mint** companies — `https://www.livemint.com/rss/companies`
- **Entrackr** — `https://entrackr.com/rss`
- **Inc42** — `https://inc42.com/feed/` (Indian startup/funding/IPO focus)
- **YourStory** — `https://yourstory.com/feed`
- **Business Line** (The Hindu group) — `https://www.thehindubusinessline.com/companies/feeder/default.rss`
- **ET Corporate/Industry** — `https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms`
- **FT India** — `https://www.ft.com/india?format=rss` (low volume, occasionally
  catches mega cross-border deals Indian-only sources miss)
- **DealStreetAsia** — `https://www.dealstreetasia.com/feed` — its own
  anti-bot layer blocks it from the development network ("temporarily
  disabled to mitigate bot attacks"); kept in the list in case GitHub
  Actions' network fares better (the SEBI fix below is exactly this
  pattern) — if not, it silently returns nothing, same as any dead source.
- *Removed / not added, with reasons:* VCCircle (no working RSS), Business
  Standard (403 to any server IP), Moneycontrol (RSS content is 800+ days
  stale — a dead/abandoned feed, not live), WSJ (RSS content 550+ days stale),
  Financial Express (HTTP 410, publisher explicitly disabled feeds site-wide),
  ET Markets (tested — retail-investor noise only), ET CFO (tested — pure
  macro/policy content, no deal signal), CNBC-TV18 (very high volume but the
  same earnings-report noise pattern as ET Markets), BQ Prime / NDTV Profit
  (no working feed found). All of these still reach the system via Google
  News when they cover a real story.

**BSE announcements.** `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w`
with a browser User-Agent and `Referer: https://www.bseindia.com/`. Kept
subcategories relate to acquisition/disposal/fundraising/shareholding; routine
filings (results, board meetings, newspaper publications, trading-window
notices) are dropped.

**NSE announcements.** `https://www.nseindia.com/api/corporate-announcements?index=equities`
with cookie warm-up (GET the home page, wait 1s, then the API with a Referer).
On block/failure it backs off and returns nothing (retries next run).

**SEBI draft offer documents.** Scrapes SEBI's legacy filings listing page
(`sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=0`) for links
matching draft / DRHP / prospectus / offer document. The modern SPA-style URL
(`/filings/public-issues/`) returns 403 from every network tested, including
GitHub Actions — SEBI never once returned data at that address in this
system's history; the legacy route works and serves the same current filings.

Every fetcher is defensive: a source that is down or blocking returns an empty
list rather than crashing the run. No keyword prefilter on news — everything
goes to the classifier. The subcategory filter applies only to BSE filings.

## Pre-classification filters (v4 Part 1 — `filters.py`)

Stage 1 sees every fetched item, and output tokens dominate its cost, so
everything here runs BEFORE any Anthropic API call and targets stage-1 volume
and output size specifically. Nothing here is meant to reduce recall — see the
shadow-mode safety net (Change 9) below, which exists precisely because these
changes can't be observed any other way if one of them is wrong.

Runtime order (cheapest, most certain filters first — Change 8, with the BSE
market-cap gate slotted in alongside): structural blocklist → title dedup →
BSE market-cap gate → pre-API amount gate → stage 1 → stage 2.

**Change 1 — pre-API amount gate.** Regex-only, no model call: reads every
crore, lakh, plain-rupee, USD, and EUR figure out of the title/description
(`classify.gated_amount`). Suppresses only when the LARGEST *qualifying*
figure is under 250cr — never on a smaller figure when a larger one is also
present ("sells 5% stake for ₹150cr in a ₹2,000cr deal" survives). A figure
only qualifies if it sits within 60 characters of a transaction word (sold,
sells, stake, deal, acquire, block, OFS, …) AND no valuation/performance word
(revenue, EBITDA, market cap, target price, …) appears in that same window —
a regex can't otherwise tell a deal value from a company's revenue, so when
neither test clearly passes the item goes to the model instead of being
guessed at. Logged under Rule 8 with `gate: "pre-api"` on the suppressed row,
distinguishing it from a model-gated Rule 8.

**Change 2 — slim stage 1 output.** Stage 1's only job is a boolean plus a
rule number: `[{"n": 1, "neg": true, "r": 9}, {"n": 2, "neg": false, "r":
null}]` — no company, amount, individuals, or any other field. ~15 output
tokens/item instead of 100+, a 5-6× cut on the dominant cost line. Everything
that used to read a stage-1 field (the amount-based small-deal gate, and the
company handed to `sizing.resolve_size`) now runs once in stage 2 instead,
using stage 2's own full extraction — there's nothing left in stage 1's
output to reuse.

**Change 3 — structural blocklist.** Filters on document TYPE (URL contains
`/liveblog/`, `/stock-liveblog/`, `/slideshow/`, `/photostory/`,
`/videoshow/`; or the title matches "Share Price Live Updates" / "Stock Price
Live" / "... Live Updates:" AND the description is empty), never on content —
a deal cannot be published as a stock-price liveblog regardless of how it's
phrased, so this can't misfire the way a keyword filter could.

**Change 4 — title dedup.** Google News rotates article URLs, so the same
story looks new to the id/URL dedup and gets classified again; clustering
catches it eventually, but only after the API call is already paid for.
Normalise the title (lowercase, strip punctuation, strip the trailing
" - SourceName" Google News appends, collapse whitespace), then: (1) exact
match against normalised titles seen in the last 72h → dup; (2) else, Jaccard
token overlap above 0.85, computed only on **distinguishing tokens** — a
fixed stopword list of generic deal vocabulary (promoter, sells, stake, rs,
crore, block, deal, …) is stripped first, because Indian headlines are
formulaic enough that two DIFFERENT deals ("Promoter sells 2% stake in X for
Rs 500cr" / "...3% stake in Y for Rs 600cr") share nearly every token except
the company name, and on a short headline that boilerplate alone can spike
raw-token Jaccard past the threshold. If either side's distinguishing token
set is empty (an all-boilerplate headline with nothing left to compare), the
Jaccard check is skipped — only the exact-match check can still catch it.
`title_norm` is stored (and indexed) on the `items` table. **Every drop is
logged unconditionally** (not just in shadow mode) to `title_dedup_log`
(title, url, matched title, similarity) — unlike a clustering merge, which
still shows up in `deal_members`, a dedup drop leaves no other trace, so this
is the only way to ever discover a wrong one. Surfaced via
`dedupe_check.py --title-dedup`.

**Change 5 — SEBI DRHP items skip stage 1.** A DRHP filing is by definition a
company going public, which stage 1 would never mark confirmed-negative, so
the call is wasted every time; SEBI-sourced items go straight to stage 2 and
survive a stage-1 failure for free.

**BSE market-cap gate (user-requested, same shadow-mode treatment).** A BSE
filing's company name (`filters.bse_market_cap`, parsed from the item title
`sources.fetch_bse` sets) usually resolves to a real, cached market cap via
the same `sizing.resolve_ticker` / `sizing.market_cap_cr` infrastructure
`sizing.py` already uses for undisclosed-amount news deals — so instead of
guessing deal size from filing text, this filters on company size directly.
Suppressed (`Rule M`, `gate: "pre-api"`) when the resolved market cap is
under `config.BSE_MCAP_MIN_CR` (1,000cr). A no-op for non-BSE items, and for
a BSE company that doesn't resolve to a listed ticker or has no cached market
cap — recall bias: unknown passes, only a CONFIRMED small market cap fires
this. Runs right before the pre-API amount gate, after title dedup.

**Change 6 — failure isolation.** `classify.classify_all` /
`precision_classify` construct the Anthropic client INSIDE their per-batch
try/except, not before it — an unreachable API (bad key, network down) fails
every batch open (items pass through unclassified) instead of raising and
aborting the run. `main.py` wraps each stage in its own outer try/except too,
as a last-resort net, and sends one Telegram warning per failed stage (never
during `--dry`). `blockdeals.py` wraps its three stages
(`deals_files`/`pit_feed`/`sales_tracker`) independently for the same
reason — none of the three needs Anthropic classification for its core
alerting (the ambiguous-seller Haiku call in `deals_files.py` already fails
open internally), so a bug in one must not block the other two.

**Change 7 — Google News query attribution.** Every raw item is tagged with
its `source_query` (`sources.fetch_google_news`) and every (item, query)
pairing is recorded to `item_queries` — even for items the id-based dedup
later drops as a duplicate, so cross-query overlap is fully visible. The
weekly feedback report shows, per query: items **produced**, and how many
deal clusters that query was the **first** to surface (the earliest
`deal_members` row for that deal, traced back through `item_queries`).
First-to-surface, not uniqueness, is the metric that matters — a query that
mostly duplicates others can still be the one that gets there earliest, and
lead time is the entire product. Measurement only, for at least two weeks;
never auto-removes a query.

**Change 9 — shadow mode.** Changes 1, 3, and 4 above only ever ACTUALLY drop
an item when the `PREFILTER_MODE` GitHub Actions repository variable is
exactly `"enforce"` (repo Settings → Secrets and variables → Actions →
Variables; unset or anything else defaults to `"shadow"`, in `config.py`).
In shadow mode every filter still computes and logs its decision — structural
and pre-API-amount to `prefilter_shadow`, title-dedup to its own
unconditional `title_dedup_log` — but the item passes through regardless, so
a trial week costs nothing extra. `python shadow_report.py [--days N]` prints
everything that would have been dropped, grouped by filter, for manual
review before flipping the switch. Rationale: every filter here acts BEFORE
classification, so none of their failures are directly observable — the
Telegram feedback buttons measure precision on what became an alert, and
can't see something that never did.

## Classification — two Haiku passes (Haiku only)

Model: `claude-haiku-4-5-20251001` for **both** stages. Sonnet was trialled and
rejected: too expensive for this volume. Cost matters more than marginal
accuracy. Items are classified only when **new** (deduped against the `items`
table, then filtered per the pre-classification filters above), so a normal
run classifies just the handful of new items.

Batch 25 items per API call as a numbered list: headline plus the first 400
characters of description.

**Stage 1 — reject confirmed noise (high recall), slim output (v4 Change 2).**
Response is `[{"n": 1, "neg": true, "r": 9}, ...]` — a boolean plus a rule
number, nothing else (see Pre-classification filters above for why). Marks an
item as a confirmed negative only for clear cases; when in doubt it passes.
Confirmed negatives include: pure debt; IBC/NCLT; PSU/government divestment; a
subsidiary sale onto a corporate balance sheet (unless the parent is a
closely-held promoter holding company); intra-group restructuring; no Indian
individual in the chain; explicitly all-primary seed/Series-A fundraising
(non-IPO); a clearly stated size under 250 crore; and non-transactions —
earnings, price moves, analyst ratings, product launches, aggregate
commentary, **stock-market listings / trading debuts, and an *already-open*
IPO's subscription / GMP / anchor-book / listing-day coverage**. A company
merely *planning or exploring* an IPO is explicitly carved out of this rule —
see below. SEBI-sourced items skip this stage entirely (Change 5).

**Deterministic amount gate (code, not the model).** A clearly stated size
below the 250-crore threshold is suppressed in code — now mostly caught
earlier by the Change 1 pre-API gate, with this as the remaining backstop for
phrasing the regex can't parse but the model still judges "under threshold."
Conversely, if stage 1 tried to drop something as "under threshold" but the
raw text's stated size (read as INR crore, lakh, plain-rupee, USD, or EUR, via
the general — not proximity-gated — `classify.stated_cr_max`) is actually ≥
250 crore, it is kept for stage 2 instead: this is a rescue check, not a
suppression, so being generous here only costs an extra stage-2 call, never a
wrongly-dropped lead.

**Stage 2 — positively confirm a qualifying lead (precision).** Runs only on
stage-1 survivors. Passes an item only when it is a concrete or
actively-negotiated transaction in which an individual (promoter, founder,
family shareholder, or the owners of a privately held / founder-run company) is
likely to receive a large sum. Large buyouts get the benefit of the doubt even
when the seller isn't named. **A company planning, exploring, or appointing
bankers for an IPO explicitly qualifies here** — even with no size stated and
no DRHP filed — since that is one of the highest-value leads the system can
surface: the promoters haven't yet picked a banking relationship. Drops: an
*already-open* IPO's subscription/GMP/listing coverage (the syndicate is
locked in by then); **primary fundraises with no IPO involved** (money into
the company, or an individual investing in); a company acquiring a
small/minority stake; pure fund-to-fund transfers with no individual; and
anything clearly under 250 crore. An undisclosed size never fails this gate on
its own — see the deterministic size-band gate below, which is what actually
decides whether an unsized item is too small to matter.

**Amount guards** (`classify.py`): `reconcile_amount` corrects Haiku's
occasional ×10 slip on INR-crore figures (e.g. reads "₹3,000 crore" but returns
300) while leaving foreign-currency ($/€) amounts alone — applied only in
stage 2 now, since stage 1 no longer extracts an amount to reconcile;
`stated_cr_max` reads INR/lakh/plain-rupee/USD/EUR figures for the recall
safety net (and, via the proximity-gated `gated_amount`, the Change 1 pre-API
gate).

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

## Confirmed deals — bulk/block files and PIT disclosures (`deals_files.py`, `pit_feed.py`)

The only sources in the stack where the money is confirmed rather than
prospective: the seller is named outright and the value is exact. Block deals
settle T+1 — a promoter who sold Thursday morning has funds landing Friday.

**Sources.** NSE's `snapshot-capital-market-largedeal` endpoint (the same
data behind NSE's live market-snapshot widget) — one call returns both bulk
and block deals as a rolling snapshot, not a historical date-range query;
dedup on `(date, symbol, client, buy_sell, qty)` handles seeing the same
window again on the next run. And the NSE structured PIT feed
(`/api/corporates-pit`, ₹10 lakh disclosure threshold, so essentially every
promoter/director/KMP trade appears here — 7-day lookback, or a **90-day
backfill on the very first run** so the salami-slice aggregation window below
doesn't start empty). Both reuse the cookie warm-up already in `sources.py`.
**BSE's equivalent endpoints were not found** — every guessed path
302-redirected to an error page (unlike BSE's announcements API, which is
confirmed working) — so per the spec's own fallback, this ships NSE only.

> **Reliability history.** The originally-shipped endpoints
> (`/api/historical/bulk-deals`, `/block-deals`) never worked — confirmed
> `503` from both the development sandbox and a live GitHub Actions run.
> `snapshot-capital-market-largedeal` was found as a working replacement and
> verified end-to-end (real current deals, correct seller classification,
> alert firing) through the actual pipeline. The PIT feed (`/api/corporates-pit`)
> has never returned data from any network tested — always an empty
> `{"data": []}` stub — and no working alternative was found despite trying
> several. It's left in place in case NSE fixes it; treat PIT-sourced
> confirmed alerts and aggregation input as currently not functioning. Every
> fetcher here stays defensive regardless (log and return nothing on
> failure, retry next run) — a source going dark logs clearly and never
> crashes the run.

**Seller classification** (`deals_files.classify_seller_keyword`) is
keyword-first: a small set of *strong* signals (FUND, MUTUAL, SECURITIES,
CAPITAL, ADVISORS, BANK, INSURANCE, …) auto-classify as institution. A
*weaker* set — corporate suffixes (LTD, PVT, HOLDINGS, TRUST, …) plus generic
words like "Investment(s)", "Global", "Ventures" — route to AMBIGUOUS rather
than auto-institution, because closely-held promoter vehicles routinely carry
those same words (the spec's own worked example, "Indian Continent Investment
Ltd", is a Bharti promoter entity, not a fund — a literal reading of the
spec's combined keyword list would have misclassified its own example).
AMBIGUOUS names are resolved in **one batched Haiku call per run** (usually a
handful); UNCLEAR passes, matching the system's recall bias.

**Integration with existing deal clusters** reuses `cluster.process()` with
`confirmed=True`, which changes the update semantics from a news revision to a
fact-check: if the matched deal's amount is unknown, the confirmed figure
fires an UPDATE; if it's already known, the confirmed value and seller name
still overwrite the record, but **silently** — the alert already went out for
this deal. New deals fire a CONFIRMED alert (🟢, visually distinct from
news-sourced 🔴/🟡) with the same feedback buttons as any other alert.

## Salami-slice aggregation (`sales_tracker.py`)

A promoter selling ₹120cr three times over six weeks never trips the ₹250cr
threshold on any single sale, and no outlet writes about a series of
unremarkable trades — cumulatively it's ₹360cr and nothing else in the system
would ever see it.

Every individual-seller row — from the PIT feed, the bulk/block files above,
**and** any news-sourced deal where the classifier named an individual with a
stated amount (recorded once per genuinely new/updated deal, not once per
duplicate article) — lands in an `individual_sales` table keyed on
`(person_key, company_key)`.

**Person-name matching** reuses the exact company-clustering machinery
(`cluster.token_subset_match`), generalised for people
(`cluster.person_tokens` strips titles and drops single-letter initials).
Because different sources spell the same person differently ("AGARWAL SUNIL
KUMAR" vs "Sunil K Agarwal"), a naive computed key would mint a different
string per variant and silently fragment the aggregation — so
`cluster.resolve_person_key()` fuzzy-matches a new name against every person
already recorded for that company before minting a fresh key, reusing
whichever key (and canonical spelling) was seen first.

**Fires a PATTERN alert (🟣)** when, over a rolling 90-day window: the sum is
≥₹250cr, there are ≥2 distinct transactions, and no single transaction
accounts for more than 70% of the total (if one did, the normal
single-transaction pipeline already caught it). After firing once, it only
fires again for the same person+company once the total has grown to **2×**
the previously alerted amount, or after a **90-day cooldown** — never on every
incremental tick.

Expect this a handful of times a **quarter**, not weekly. The value is that
nothing else in the market catches this pattern, not that it fires often.

## Suppression log, digest, report

Every suppressed item goes into a `suppressed` table with title, URL, the rule
that killed it, `amount_cr`, `amount_raw`, and (v4) `gate` — `"pre-api"` when
a regex or company-size gate suppressed it before any model call, `"model"`
(the default) when stage 1 or stage 2 made the call. Never deleted. Rules
recorded: Rule 1–9 (stage-1 negatives), **Rule 8** (under threshold — pre-API,
model, or the deterministic code gate), **Rule P** (failed the stage-2
precision check), **Rule S** (deterministic size-band gate for an unlisted
company), and **Rule M** (BSE company market cap under `BSE_MCAP_MIN_CR`).

The daily digest additionally reports how many CONFIRMED and PATTERN alerts
fired that day (v3 Changes A/B), and (v4) a permanent funnel line summed
across every `main.py` run that day: fetched → already-seen → structural →
title dedup → pre-API gate → stage 1 → stage 2 → alerted (`funnel_runs`
table, one row per run).

`digest.yml` sends one Telegram message at 20:30 IST daily: total suppressed,
a breakdown by rule, the largest suppressed deal, and the funnel line above.

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

**CONFIRMED alerts** (🟢, v3 Change A — bulk/block deals, PIT disclosures) look
different on purpose, since the money here is fact, not a classifier estimate:

```
🟢 CONFIRMED · {security_name} · block/bulk deal · {amount}

{client_name} sold {quantity} shares at {price}

Settles T+1 · trade date {date}
{exchange} daily deal file
```

**PATTERN alerts** (🟣, v3 Change B — salami-slice aggregation) are a third,
distinct shape — they summarise several sales, not one transaction, so they
carry no single source link:

```
🟣 PATTERN · {person_name} · {company} · {total} over {n} sales

{date}  {amount}
{date}  {amount}
{date}  {amount}

{weeks} weeks · no single sale crossed the threshold
```

## Telegram feedback buttons (`feedback.py`)

Every alert carries an inline keyboard: **Useful / Already knew / Noise**.
There is no persistent server, so this is polling-based (`getUpdates`), not a
webhook — `callback_data` encodes `fb:{deal_id}:{verdict}`.

At the start of every `radar.yml` run, `feedback.poll_feedback()`: fetches
pending `callback_query` updates since the last stored offset, writes each to
a `feedback` table (deal_id, verdict, chat_id, timestamp), calls
`answerCallbackQuery` ("Logged"), edits the original message to append the
verdict (using Telegram's `entities` field rather than re-parsing Markdown, so
already-escaped characters can't cause a parse error on edit), and persists
the new offset in a `kv_state` table. In `--dry` mode nothing is written or
acknowledged, so pending presses are left for the next real run.

`python feedback_report.py [--dry]` sends one Telegram message every Monday
09:00 IST: verdict counts over the last 14 days, a noise breakdown by deal
type / size band / source feed / size source, an "already knew" breakdown by
source feed, the 5 most recent noise-marked alerts in full, and (v4 Change 7)
a Google News query-attribution section: items produced and deal clusters
first-surfaced per query over the last 7 days, shown even in a week with zero
button feedback. This **only reports** — it never modifies prompts,
thresholds, rules, or queries automatically; that stays a manual `config.py`
edit.

`python shadow_report.py [--days N]` (v4 Change 9) prints everything the
pre-classification filters (Changes 1/3/4) would have dropped while
`PREFILTER_MODE=shadow`, grouped by filter — read this, confirm nothing real
is in it, then flip the `PREFILTER_MODE` repository variable to `"enforce"`.

## Command-line flags

- `--mode fast|news|slow|auto` — which sources to fetch (`auto` = time-aware).
- `--dry` — print alerts to the terminal instead of sending to Telegram.
- `--test-telegram` — send one test message and exit.
- `--limit N` — cap how many alerts are sent this run (testing / anti-flood);
  deals are still recorded.

`PREFILTER_MODE` (GitHub Actions repository variable, not a flag — repo
Settings → Secrets and variables → Actions → Variables): `"shadow"` (default)
or `"enforce"`. Read once at import time in `config.py`; takes effect on the
next `radar.yml` run.

## File layout

```
config.py      settings: threshold, queries, feeds, models, both prompts,
               v4 filter tunables (proximity words, dedup stopwords,
               PREFILTER_MODE)
db.py          SQLite schema + helpers (items, deals, suppressed, deal_members,
               market_caps, feedback, kv_state, individual_sales,
               pattern_alerts, item_queries, prefilter_shadow,
               title_dedup_log, funnel_runs)
sources.py     fetchers (Google News, trade press, BSE, NSE, SEBI) + auto plan
               + warm_nse_session() (shared NSE cookie warm-up); tags Google
               News items with source_query (v4 Change 7)
filters.py     v4 Part 1: structural blocklist, title dedup (stopword-aware
               Jaccard), pre-API amount gate — decisions only, main.py
               decides whether to act on them (PREFILTER_MODE)
classify.py    two-stage Haiku classifier + amount guards; gated_amount()
               (proximity-checked figures) for the v4 pre-API gate
sizing.py      resolve size for undisclosed-amount deals (ticker/mcap/band)
refresh_tickers.py  refresh data/nse_equities.csv + data/bse_scrips.csv
cluster.py     deal clustering / dedup / UPDATE logic + person-name matching
               (person_tokens, resolve_person_key) shared with Change B
notify.py      Telegram formatting + sending: news alerts, CONFIRMED alerts,
               PATTERN alerts, feedback keyboard
feedback.py    poll Telegram button presses, log + ack + edit
feedback_report.py  weekly (Monday 09:00 IST) feedback summary + v4 query
               attribution section
deals_files.py   v3 Change A: NSE bulk/block deal files, seller classification
pit_feed.py      v3 Change B source: NSE PIT feed, 90-day first-run backfill
sales_tracker.py v3 Change B: rolling 90-day aggregation, PATTERN alerts
blockdeals.py    entry point: deals_files → pit_feed → sales_tracker, each
                 stage independently failure-isolated (v4 Change 6)
main.py        orchestrator (poll feedback → fetch → v4 pre-classification
               filters → classify → sizing → cluster → alert; also feeds
               news-sourced sales to individual_sales for Change B and
               per-run counters to funnel_runs)
digest.py      daily suppression summary + CONFIRMED/PATTERN counts + v4
               funnel line
report.py      N-day suppression report
dedupe_check.py  N-day clustering audit (what merged into each deal); v4
               --title-dedup shows title-dedup drops instead
shadow_report.py v4 Change 9: what PREFILTER_MODE=enforce would have dropped
data/          committed NSE/BSE ticker master lists (for sizing.py)
.github/workflows/radar.yml         main run (--mode auto), external + backup cron
.github/workflows/digest.yml        daily digest, external + backup cron
.github/workflows/feedback-report.yml  weekly feedback rollup
.github/workflows/blockdeals.yml    v3 A/B: bulk/block/PIT/aggregation, 19:30 IST
.github/workflows/refresh-tickers.yml  monthly ticker list refresh
README.md      plain-English setup + tuning guide
requirements.txt
```

## Deliberately not built

No Sonnet (Haiku only). No valuation lookups, funding history, or cap-table
inference. No MCA / Probe42 / Tracxn. No web dashboard. No family-settlement or
HUF tracking.
