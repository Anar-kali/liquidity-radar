# Liquidity Radar — build specification

Build this exactly as described. Ask me before deviating.

I am not a programmer. Explain anything you need me to do in plain English,
and tell me exactly which file to open and what to paste where.

## What it does

Monitors Indian corporate filings and financial news for deals where an
individual (promoter, founder, family shareholder) is likely to receive a
large sum of money. Sends a Telegram alert for each qualifying deal.

The user is a private banker prospecting UHNI clients. He wants high recall.
False positives are fine. Missing a real deal is not.

## Environment

- Runs on GitHub Actions, free tier. No VPS.
- Python 3.11.
- All secrets from environment variables, never hardcoded.
- State persists by committing a SQLite file back to the repo after each run.
  Use a `concurrency` group in the workflow so two runs never overlap.

Required secrets (repo Settings > Secrets and variables > Actions):
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`

## Schedules

Cron expressions must be in UTC. IST is UTC+5:30.

| Workflow | Cadence | Sources |
|---|---|---|
| `fast.yml` | every 15 min, Mon-Fri, 09:00-18:30 IST | exchanges + news |
| `news.yml` | every 30 min, all days, 07:00-23:00 IST | news only |
| `slow.yml` | 08:00, 14:00, 20:00 IST daily | SEBI DRHP |
| `digest.yml` | 20:30 IST daily | suppression summary |

## Sources

**Google News RSS — highest yield, weight accordingly.** One feed per query:
`https://news.google.com/rss/search?q={URL-encoded query}+when:2d&hl=en-IN&gl=IN&ceid=IN:en`

Queries:
- `promoter stake sale crore India`
- `block deal promoter shares crore`
- `DRHP filed SEBI offer for sale`
- `private equity acquires majority stake India crore`
- `promoter offloads stake crore`
- `founders sell shares IPO OFS crore`
- `open offer acquisition promoter crore`
- `family office stake sale India crore`

**Trade press RSS.** Mint companies, VCCircle, Entrackr, Business Standard
companies. Do NOT add ET Markets direct RSS — tested, produces only retail
investor noise. ET articles reach us via Google News anyway.

**BSE announcements.** `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w`
Needs a browser User-Agent and `Referer: https://www.bseindia.com/`. Filter by
announcement subcategory to drop routine filings (results, board meeting
notices, newspaper publications, trading window closures). Keep acquisition,
disposal, fundraising and shareholding categories.

**NSE announcements.** `https://www.nseindia.com/api/corporate-announcements?index=equities`
Requires cookie warm-up: GET `https://www.nseindia.com` first, wait one second,
then call the API with a Referer header. NSE blocks aggressively. On failure,
back off and retry next run rather than looping.

**SEBI draft offer documents.** Scrape `https://www.sebi.gov.in/filings/public-issues`
for links matching draft / DRHP / prospectus / offer document.

No keyword prefilter on news. Everything goes to the classifier. The
subcategory filter applies only to exchange filings.

## Classification — ONE stage, Haiku only

Model: `claude-haiku-4-5-20251001`. Do not use Sonnet anywhere. Cost matters
more than marginal accuracy here.

Batch 25 items per API call as a numbered list. For each item send the
headline plus the first 400 characters of description. Ask for a JSON array
with one object per item, in the same order.

### System prompt

```
You screen Indian corporate news and exchange filings for a private banker
who prospects individuals about to receive large sums of money.

Your ONLY job is to decide whether each item is a CONFIRMED NEGATIVE. If it
is not confirmed, it passes. When in doubt, pass it. A false alarm costs the
banker five seconds. A missed deal costs him a client.

Mark confirmed_negative = true ONLY for:
1. Pure debt. NCDs, venture debt, working capital, refinancing, bonds.
2. IBC or NCLT resolution. Creditors are paid, promoters get nothing.
3. PSU or government divestment. Proceeds go to the government.
4. A subsidiary sale by a listed or MNC parent, where proceeds land on a
   corporate balance sheet. EXCEPTION: if the parent is a closely held
   promoter holding company, this is NOT a negative.
5. Internal restructuring, including intra-group transfers between entities
   controlled by the same promoter family. No outside money changes hands.
6. No Indian individual anywhere in the transaction chain.
7. Explicitly all-primary fundraising at seed or Series A stage.
8. A deal size is clearly STATED and converts to less than 250 crore INR.
9. Not a transaction at all: earnings results, share price moves, analyst
   ratings, technical signals, product launches, regulatory disputes,
   aggregate market commentary, or listicles about promoter selling trends.

Rule 8 applies ONLY when a size is stated. Undisclosed terms, no figure
given, "sources say" with no number: these all PASS. Silence is never a
small deal.

Currency: 1 crore = 10 million INR. 1 lakh = 100,000 INR.
Use 1 USD = 88 INR, 1 EUR = 96 INR. Show your working in amount_raw.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input item, same order:
[{
  "n": 1,
  "confirmed_negative": true|false,
  "negative_reason": "rule number and short phrase, or null",
  "company": "",
  "deal_type": "IPO-OFS|block deal|strategic buyout|PE secondary|PE primary|open offer|promoter sale|DRHP filing|other|unknown",
  "amount_cr": null,
  "amount_raw": "exact text the figure came from, plus your conversion, or null",
  "individuals": ["named individuals receiving money, empty if none named"],
  "confidence": "high|medium",
  "one_line": "under 20 words: what happened and who gets paid"
}]

Never invent a figure. If no amount is stated, amount_cr and amount_raw are
null. Do not estimate from stake percentages or valuations.

Set confidence to "high" when a named individual and a stated amount are both
present. Otherwise "medium".
```

## Deal clustering

The most important part. One transaction gets reported by eight outlets. The
user must receive one alert, not eight.

Two tables:
- `items` — every fetched item, keyed on source ID or URL, for dedup.
- `deals` — clustered transactions.

Deal key: normalised company name plus deal type, within a rolling 72 hour
window. Normalise by lowercasing and stripping: private, limited, ltd, pvt,
inc, corp, technologies, industries, enterprises, and all punctuation.

- First item to match a key creates the deal and fires an alert.
- Later items attach silently.
- EXCEPT when a later item adds a material fact the deal record lacks: an
  amount where there was none, a named individual where there was none, a
  named buyer where there was none, or an amount revised by more than 20%.
  Then send one follow-up marked UPDATE.

## Suppression log

Every confirmed negative goes into a `suppressed` table with title, URL, the
rule that killed it, `amount_cr` and `amount_raw`. Never delete from it.

`digest.yml` sends one Telegram message at 20:30 IST daily:

```
Suppressed today: 34

Rule 3 (govt/PSU): 6
Rule 8 (under 250cr): 11
Rule 9 (not a transaction): 14
Rule 5 (intra-group): 3

Largest suppressed: Coal India OFS Rs 5,549cr (rule 3)
```

Also add `python report.py` to print the last 7 days grouped by rule.

## Alert format

Telegram, Markdown. Scannable, he gets 8 to 15 a day.

```
[RED] *{company}* · {deal_type} · {amount or "Size undisclosed"}

_{one_line}_

{names if any, else "No individual named"}

[{source}]({url})
```

Use a red circle emoji for high confidence, yellow for medium. Prefix the
company name with `UPDATE ·` for follow-ups on an existing deal.

## Not building

No valuation lookups. No funding history. No cap table inference. No MCA,
Probe42 or Tracxn. No web dashboard. No family settlement or HUF tracking.
No Sonnet. If you think one of these would help, mention it, do not build it.

## Before you finish

1. Add a `--dry` flag that prints alerts to the terminal instead of Telegram.
2. Add a `--test-telegram` flag that sends one test message and exits.
3. Write a README covering: what each secret is and where to get it, how to
   read the suppression report, how to change the 250 crore threshold, and
   how to add or remove a Google News query.
4. Run it once with `--dry` and show me the output before we go live.
