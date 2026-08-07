# Liquidity Radar — v2 upgrade specification

Upgrade to the existing deployed system at `github.com/Anar-kali/liquidity-radar`.
Read the current `SPEC.md` and the codebase first. This document describes only
what changes.

I am not a programmer. Explain each step in plain English and tell me exactly
what I need to do.

**Hard constraint: Haiku only.** `claude-haiku-4-5-20251001` everywhere. No
Sonnet, no exceptions. Net API cost must not materially increase.

Two changes. Change B is the higher priority of the two — do it first if you
have to pick.

---

# CHANGE A — Size undisclosed deals before alerting

## The problem

Deals with no stated amount currently all pass through. Many are tiny companies
where no individual is receiving anything near 250 crore. These are noise.

## The principle

Most of this is NOT a model problem. For a listed company the market cap is
free public data and the size can be computed. Only unlisted companies need an
estimate, and an estimate is a band, never a number.

**Recall bias still governs.** Suppress only when confident the deal is small.
Unknown passes. Unsure passes. When the model has no basis, it passes.

## New module: `sizing.py`

Runs between stage 1 and stage 2, only on items where `amount_cr` is null.

Resolve size in this priority order and stop at the first that succeeds:

### 1. Stated amount
Already handled upstream. Nothing to do.

### 2. Stated stake percentage × market cap  (listed only, deterministic)

If the item text contains a stake percentage AND the company resolves to a
ticker, compute `market_cap_cr × pct / 100`. This is a real figure, not an
estimate — mark `size_source = "computed"`.

Indian coverage states percentages constantly ("sells 4.58% stake", "pares 14%
stake"), so this case will fire often. Extract the percentage with a regex in
code, not with the model.

### 3. Market cap plausibility gate  (listed only)

No percentage available but ticker resolved. Suppress ONLY if
`market_cap_cr < 350`. At that size, a promoter selling the entire company
barely clears the threshold and any partial stake cannot. Anything above 350
passes with `size_source = "mcap_plausible"`.

Do not try to be cleverer than this. A 900cr company can easily produce a 300cr
promoter sale.

### 4. Haiku band estimate  (unlisted only)

Add to the **stage 2** call — do not make a separate API request. Extra fields
in the existing per-item response object:

```
"size_band": "UNDER_100|100_TO_500|500_TO_2000|OVER_2000|UNKNOWN",
"size_basis": "one short phrase: what you based this on, or 'no information'"
```

Prompt addition for stage 2:

```
When no deal amount is stated and the company is not listed, estimate the
likely total deal size as a BAND, never a number.

Base it only on what you actually know about the company: its sector, scale,
whether you recognise it at all, and any revenue or headcount or footprint
detail in the text. If you do not recognise the company and the text gives you
nothing to work with, return UNKNOWN with basis "no information". That is the
correct answer and it is never penalised.

Do NOT infer size from the fact that a deal is happening. Do NOT guess from
the company name. Do NOT produce a band you cannot justify in size_basis.

An unknown company that turns out to be large is a recoverable mistake. A
fabricated band that suppresses a real lead is not.
```

### 5. Nothing resolved
`size_band = "UNKNOWN"`, item passes.

## The gate

Suppress ONLY these two cases:

- `size_source = "computed"` and computed value < 250cr → existing Rule 8
- `size_band = "UNDER_100"` AND `size_basis` is not "no information" → new
  **Rule S** in the suppression log

Everything else passes. `100_TO_500` passes. `UNKNOWN` passes. Band with no
basis passes.

## Market cap lookup

**Ticker resolution.** Commit `data/nse_equities.csv` and `data/bse_scrips.csv`
into the repo — the NSE and BSE published equity master lists, which map
company names to symbols. Find the current download URLs; they move
occasionally. Add a monthly workflow to refresh them.

Match the classifier's `company` field against these using the same
normalisation as clustering (below). Require a confident match; an ambiguous
match resolves to "not listed" rather than the wrong company.

**Market cap.** `yfinance`, appending `.NS` for NSE symbols and `.BO` for BSE.
Prefer `fast_info.market_cap` over `.info` — lighter and far more reliable.
Value returns in rupees; divide by 10^7 for crore.

**Cache in SQLite for 7 days.** New table `market_caps` keyed on ticker. This
keeps cost at zero, survives rate limiting, and means a busy day doesn't hammer
the API.

**Fallback.** If yfinance fails or is blocked from the Actions runner, fall back
to the NSE quote endpoint `https://www.nseindia.com/api/quote-equity?symbol=X`,
reusing the cookie warm-up already in `sources.py`. If both fail, treat as "not
listed" and let the item pass.

## Alert display

An estimate must never look like a stated fact.

- Stated: `₹2,000cr`
- Computed from stake × mcap: `~₹1,713cr (4.58% × mcap)`
- Band: `est. ₹500-2,000cr`
- Nothing: `Size undisclosed`

Never render a band as a midpoint number.

---

# CHANGE B — Tighten deal clustering

## The problem

The same transaction alerts twice under different framing. Real example:

- `IndiaRF buys Fine Edge Engineering for 2000cr`
- `Fine Edge Engineering (Ashok Iron Works Engineering business) strategic buyout for 2000cr`

Three independent failures cause this.

## Fix 1: company field must be the target, not the buyer

Add to both stage prompts:

```
"company" is ALWAYS the entity whose ownership is changing — the target, or
the company whose shares are being sold. NEVER the acquirer or investor.
In "IndiaRF acquires Fine Edge Engineering", company is Fine Edge Engineering
and buyer is IndiaRF.
```

## Fix 2: token containment instead of string equality

Replace exact key matching in `cluster.py` with:

1. Strip everything in parentheses: `re.sub(r'\([^)]*\)', '', name)`
2. Strip after a comma or the words "formerly", "erstwhile", "a unit of",
   "division of", "arm of", "business of"
3. Lowercase, strip punctuation, tokenise
4. Drop corporate stopwords: private, limited, ltd, pvt, inc, corp, corporation,
   technologies, technology, industries, enterprises, group, holdings, india,
   company, co, and, the, engineering, services, solutions
5. **Match if either token set is a subset of the other**, provided the smaller
   set has at least one token remaining

On the example: `{fine, edge}` vs `{fine, edge, ashok, iron, works, business}`.
The first is a subset of the second. Match.

## Fix 3: drop deal_type from the key, add amount as a matcher

Deal type is unreliable — one outlet's "strategic buyout" is another's "PE
secondary". Remove it from the clustering key entirely.

Two items cluster together if EITHER:

- **Name match** (Fix 2) within a 72 hour window, OR
- **Amount match**: both have `amount_cr` within 5% of each other, within a
  **7 day** window, and either name shares at least one non-stopword token

The amount matcher alone would have caught the example, since both said 2000cr.
The longer window on the amount path is deliberate: follow-up analysis pieces
land days later.

Accepted trade-off: a company doing two genuinely different deals inside 72
hours now merges into one alert. This is rare, and the cost is missing a second
ping about a company already on his radar. Worth it.

## Fix 4: optional Haiku tie-break

If Fixes 1 to 3 leave residual duplicates after a week of running, add a
fallback: when two items in the same window have amounts within 5% but names
that do not match, batch them into one Haiku call asking "same transaction or
different?" This will be one or two calls a day at most. Do NOT build this
now — wait and see whether it is needed.

---

# CHANGE C — Narrow the UPDATE rule

Currently UPDATE fires on: amount appears, individual appears, buyer appears,
or amount revises by more than 20%.

Reduce to **one trigger**: `amount_cr` goes from null to a value, or revises by
more than 20%.

Buyer and individual no longer fire an alert by themselves. When an amount
update does fire, the message carries whatever else is newly known, including
the individual and buyer, in the same alert.

Reason: he does his own research once alerted. A second message telling him who
the buyer was is noise, not signal.

---

# Cost

This should not measurably change the bill.

- Market cap lookups: free, cached
- Ticker master files: free, monthly refresh
- Band estimation: extra fields on the existing stage 2 call, marginal output
  tokens only
- All clustering changes: pure code

If you think any part of this needs a paid data source or a second API call per
item, stop and tell me instead of building it.

---

# Before you finish

1. Run `--dry` and show me the output.
2. Report how many items in a dry run hit each `size_source` path: stated,
   computed, mcap_plausible, band, unknown. I want to know the mix.
3. Add `python dedupe_check.py --days 7` that prints deals clustered in the last
   week with the item titles that merged into each, so I can verify the
   clustering is actually working and not over-merging.
4. Add the new Rule S to the daily digest breakdown.
5. Update `SPEC.md` to describe the system as built after these changes.
