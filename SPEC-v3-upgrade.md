# Liquidity Radar — v3 upgrade specification

Upgrade to the deployed system at `github.com/Anar-kali/liquidity-radar`.
Read the current `SPEC.md` and the codebase first. This describes only what
changes.

I am not a programmer. Explain each step in plain English and tell me exactly
what I need to do.

**Hard constraint: Haiku only.** No Sonnet. Changes A and B should add close to
zero API cost because they work on structured data, not prose.

Build in this order: **C first, then A, then B.**

C first because labels compound and every day without it is data permanently
lost. It cannot be applied retroactively to alerts already sent. It is also the
smallest change of the three, and it is what measures whether A and B are
actually helping once they ship.

A before B because A supplies one of B's data inputs.

---

# CHANGE A — Bulk and block deal files

## Why this matters

This is the only source in the stack where the money is confirmed rather than
prospective, the seller is named outright, and the value is exact. No inference,
no classification, no false positives.

Block deals settle T+1. A promoter who sold Thursday morning has unallocated
funds landing Friday.

## New module: `deals_files.py`

New workflow `blockdeals.yml`, running once daily at **19:30 IST** on weekdays.
Exchange files publish after close, around 18:00 to 19:00 IST.

## Sources

**NSE** — reuse the cookie warm-up already in `sources.py`:
- Bulk deals: `https://www.nseindia.com/api/historical/bulk-deals?from=DD-MM-YYYY&to=DD-MM-YYYY`
- Block deals: `https://www.nseindia.com/api/historical/block-deals?from=DD-MM-YYYY&to=DD-MM-YYYY`

**BSE** — equivalent bulk and block deal endpoints. These move more often than
NSE's; find the current ones and note them in the README. If BSE proves
unstable, ship NSE only and add BSE later. NSE alone covers most of the volume.

Fetch a **3 day lookback** each run, not just today, so a failed run
self-heals. Dedup on `(exchange, date, symbol, client_name, buy_sell, quantity)`.

## Fields

Each row gives: date, symbol, security name, client name, buy/sell flag,
quantity, trade price. Compute `value_cr = quantity × price / 10^7`.

## Filter

Keep rows where **all** of:
- `buy_sell` is SELL
- `value_cr >= 250`
- the client name is not clearly an institution (below)

## Seller classification

Classify `client_name` into three buckets, keyword-first:

**INSTITUTION** — name contains any of: FUND, MUTUAL, AMC, LLP, SECURITIES,
CAPITAL, INVESTMENTS, INVESTMENT, ADVISORS, ADVISERS, PARTNERS, MAURITIUS, PTE,
PLC, GMBH, SICAV, INSURANCE, BANK, ASSET MANAGEMENT, PORTFOLIO, TRUSTEE, FPI,
FII, GLOBAL, INTERNATIONAL, VENTURES, EQUITY

**INDIVIDUAL** — no institutional keyword present. Personal names. These are the
highest-value alerts.

**AMBIGUOUS** — contains LTD, LIMITED, PVT, HOLDINGS, ENTERPRISES, TRUST, or
CORP but no strong institutional keyword. These are often closely held promoter
vehicles. Example: Indian Continent Investment Ltd is a Bharti promoter entity.

Send AMBIGUOUS names to Haiku in a **single batched call per run** (usually a
handful of names) asking: is this a promoter or family investment vehicle, an
institutional investor, or unclear? Unclear passes. This is the only API cost in
Change A and it is a few hundred tokens a day.

## Integration with existing clusters

Before alerting, check for an existing deal cluster for that company within 72
hours.

- **Cluster exists and already has an amount** → attach the row silently.
  Record the confirmed seller name and exact value on the deal record. No alert.
- **Cluster exists but amount was unknown** → fire an UPDATE carrying the
  confirmed value and seller name.
- **No cluster** → fire a fresh alert.

## Alert format

```
CONFIRMED · {security_name} · block/bulk deal · ₹{value}cr

{client_name} sold {quantity} shares at ₹{price}

Settles T+1 · trade date {date}
{exchange} daily deal file
```

Mark these visually distinct from news-sourced alerts. Use a different emoji.
These are the only alerts in the system where the money is a fact.

---

# CHANGE B — Salami-slice aggregation

## The problem

A promoter sells ₹120cr three times over six weeks. No single transaction trips
the ₹250cr threshold and no publication writes about a series of unremarkable
promoter sales. Cumulatively it is ₹360cr and nothing in the current system
sees it.

## First: stop discarding the data

Check whether the BSE and NSE announcement subcategory filter is currently
dropping insider trading and SAST disclosures. It probably is. Add these
categories to the keep-list:

- Insider Trading / SAST
- Reg. 7(2) continual disclosure (PIT)
- Reg. 29(1) and 29(2) (SAST acquisition disclosures)
- Reg. 31(1) and 31(2) (promoter pledge disclosures)

## Better source: NSE structured PIT feed

`https://www.nseindia.com/api/corporates-pit?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`

Same cookie warm-up. Returns structured fields rather than PDF text: company,
acquirer or disposer name, category (Promoter / Promoter Group / Director /
KMP), securities acquired or disposed, quantity, value, transaction type.

Because the PIT disclosure threshold is only ₹10 lakh, essentially every
promoter trade appears here. This feed has standalone value: a ₹280cr promoter
sale at a mid-cap often gets no press coverage at all, and it would alert on its
own merits through the normal threshold.

Fetch daily in the same `blockdeals.yml` workflow. 7 day lookback for
self-healing.

## New table: `individual_sales`

Columns: `person_key`, `person_name_raw`, `company`, `company_key`,
`trade_date`, `value_cr`, `source` (pit / block / bulk / news), `already_alerted`

Populate from three places: the PIT feed, the block and bulk deal files from
Change A, and any news-sourced deal where the classifier named an individual and
extracted an amount.

## Person name normalisation

Names arrive in inconsistent order and form. "AGARWAL SUNIL KUMAR",
"SUNIL KUMAR AGARWAL", "Sunil K Agarwal".

1. Uppercase, strip punctuation and titles (MR, MRS, SHRI, SMT, DR)
2. Tokenise
3. Drop single-character tokens (initials)
4. Sort tokens alphabetically and join → `person_key`
5. Match two people if either token set is a **subset** of the other

This is the same containment logic as the deal clustering fix. Reuse the
function rather than writing a second one.

Scope `person_key` to company. The same common name at two different companies
is two different people until proven otherwise.

## Aggregation rule

After each insert, for every `(person_key, company_key)` compute the rolling
**90 day** sum of `value_cr`.

Fire an aggregate alert when **all** of:
- rolling sum >= 250cr
- at least 2 distinct transactions in the window
- no aggregate alert sent for this person and company in the last 90 days
- the sum is not attributable to a single transaction already alerted
  individually (if one transaction is >= 250cr on its own, the normal pipeline
  already caught it, so require that no single row accounts for more than 70%
  of the total)

## Re-alerting

Do not re-alert every time the total ticks up. After an aggregate alert, only
alert again on that person and company if the total reaches **2× the previously
alerted amount**, or after a 90 day cooldown.

## Alert format

```
PATTERN · {person_name_raw} · {company} · ₹{total}cr over {n} sales

{date}  ₹{amount}cr
{date}  ₹{amount}cr
{date}  ₹{amount}cr

{weeks} weeks · no single sale crossed the threshold
```

## Expectation setting

Be honest in the README: this pattern is real but not frequent. Expect a handful
a quarter, not one a week. The value is that nothing else in the market catches
it, not that it fires often.

---

# CHANGE C — Telegram feedback buttons

## Check this first

The weekly report below breaks noise down by deal type, size band, source feed
and which stage passed the item. That only works if those fields are persisted
on the `deals` record at alert time.

Before building anything else, check what the `deals` table currently stores. If
`source_feed`, `size_source`, `size_band` and the stage decision are not on the
record, add them now. Feedback collected without them is much less useful, and
it cannot be backfilled later.

## Constraint

Inline button presses normally require a webhook. There is no persistent server.
Use **polling** instead.

## Sending

Attach an inline keyboard to every alert, three buttons in one row:

```
[ Useful ]  [ Already knew ]  [ Noise ]
```

`callback_data` encodes the deal id and the verdict, e.g. `fb:1423:useful`.
Keep it under Telegram's 64 byte limit.

## Receiving

At the start of every scheduled run in `main.py`:

1. Call `https://api.telegram.org/bot{TOKEN}/getUpdates?offset={last_offset+1}`
2. Filter for `callback_query` objects
3. Write each to a new `feedback` table: `deal_id`, `verdict`, `chat_id`,
   `timestamp`
4. Call `answerCallbackQuery` for each so the button stops spinning, with a
   short toast: "Logged"
5. Edit the original message to append the verdict, so a glance at history shows
   what was already rated
6. Store the new `offset` in a `state` table so the next run resumes correctly

Feedback lands within 15 minutes. That is fine. Nothing needs instant
acknowledgement.

Note: `getUpdates` and webhooks are mutually exclusive. If a webhook is ever set
on this bot, delete it first.

## Why three buttons, not two

**Useful** and **Noise** measure filtering quality. **Already knew** measures
something completely different: the system was correct but too slow, or the
source it came from is not buying any lead time. These need different fixes, so
they need to be separable in the data.

## Weekly feedback report

New script `feedback_report.py`, and a Telegram message every Monday at 09:00
IST.

Group the last 14 days of feedback by:
- verdict counts overall
- **noise** broken down by deal_type, size band, source feed, and which stage
  passed the item
- **already knew** broken down by source feed

Print the five most recent noise-marked alerts in full so the pattern is visible
rather than just counted.

## Do NOT auto-tune

Never let feedback automatically modify the prompts, thresholds or rules. The
report tells me what to change; I edit `config.py` myself. Automatic tuning
drifts silently and leaves no audit trail, and a system that quietly teaches
itself to suppress leads is the worst possible failure mode here.

---

# Before you finish

1. Run `--dry` for each new component and show me the output.
2. For Change A, show me one day of raw block and bulk deal rows before
   filtering, so I can see what is actually in the file.
3. For Change B, backfill the PIT feed for the last 90 days on first run so the
   rolling window is populated from day one rather than starting empty.
4. Add Change A and B alerts to the daily digest counts.
5. Update `SPEC.md` to describe the system as built after these changes.
