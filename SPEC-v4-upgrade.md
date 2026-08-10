# Liquidity Radar — v4 upgrade specification

Upgrade to the deployed system at `github.com/Anar-kali/liquidity-radar`.
Read the current `SPEC.md` and the codebase first. This describes only what
changes.

I am not a programmer. Explain each step in plain English and tell me exactly
what I need to do.

Two goals: cut API spend without touching recall, and make the stage 2 model
switchable between Haiku and Sonnet without a code change.

---

# Where the money actually goes

Stage 1 sees every item. Stage 2 only sees survivors. So stage 1 is the
dominant cost line.

Within stage 1, **output tokens dominate**, because output is priced several
times higher than input. Stage 1 currently returns a full object per item —
company, deal_type, amount_cr, amount_raw, individuals, buyer, confidence,
one_line, size_band, size_basis — for items that are about to be discarded as
earnings reports.

Everything in Part 1 below targets stage 1 volume and stage 1 output size.
Nothing in it reduces recall.

---

# PART 1 — Lighter

Build in the order given. Changes 1 and 2 are coupled and must land together.

## Change 1 — Pre-API amount gate

Move the existing deterministic under-threshold gate to **before** the stage 1
API call.

Regex all rupee figures out of the title and description in code. Handle crore,
lakh, and plain rupee amounts, plus `$`/USD and `€`/EUR with the existing
conversion rates.

Suppress the item **only if the largest figure found is under 250 crore**. Never
suppress on a smaller figure when a larger one is also present — "sells 5% stake
for ₹150 crore in a ₹2,000 crore transaction" must survive.

**Proximity requirement.** A regex cannot tell a deal value from revenue, EBITDA,
market cap or a target price, but the model currently can. Only fire the gate
when both hold:

- the figure sits within roughly 60 characters of a transaction word (sold,
  sells, sale, stake, deal, acquire, acquisition, buy, offer, OFS, block, divest)
- no valuation-or-performance word appears within the same window (revenue,
  turnover, profit, PAT, EBITDA, market cap, m-cap, order book, target price,
  per annum, annually, capex)

If either test fails, pass the item to the model rather than guessing. The whole
point of this gate is to skip calls that are obviously unnecessary, not to
replace the model's judgement.

Log these under the existing Rule 8. Add `gate: "pre-api"` to the suppression
row so I can tell code-gated from model-gated later.

Expected: roughly 10% of items never reach the API.

## Change 2 — Slim stage 1 output

This is the single biggest saving. Stage 1 has exactly one job: is this a
confirmed negative.

Change the stage 1 response schema to:

```json
[{"n": 1, "neg": true, "r": 9}, {"n": 2, "neg": false, "r": null}]
```

`n` is the item index, `neg` is the boolean, `r` is the rule number when neg is
true. Nothing else.

Roughly 15 output tokens per item instead of 100 plus. A five to six times cut
on the dominant cost line.

Add to the stage 1 prompt: `Return only the index, the boolean, and the rule
number. Do not extract company names, amounts, or any other field.`

**Before doing this**, check what downstream code currently reads from stage 1's
output. The deterministic amount gate is the likely consumer, and Change 1
replaces its input with a regex. Anything else that reads a stage 1 field needs
to move to stage 2, which already extracts the full object on survivors.

## Change 3 — Structural blocklist

Not a keyword content filter. Filter on **document type**, where the URL or an
exact title pattern tells you what the page is regardless of content.

Drop before the API, no logging needed beyond a counter:

- URL contains `/liveblog/`, `/stock-liveblog/`, `/slideshow/`, `/photostory/`,
  `/videoshow/`
- Title matches `Share Price Live Updates`, `Stock Price Live`, or
  `... Live Updates:` **and** the description is empty

Keep this list short and structural. Do not add content keywords to it — a deal
phrased unusually can slip past a keyword filter, but a deal cannot be published
as a stock price liveblog.

## Change 4 — Title dedup before classification

Google News rotates article URLs, so the same story looks new to item-level
dedup and gets classified again. Clustering catches it afterwards, but only
after the API call has been paid for.

Add a title check ahead of classification:

1. Normalise: lowercase, strip punctuation, strip the trailing ` - SourceName`
   that Google News appends, collapse whitespace
2. **Exact match** against normalised titles seen in the last 72 hours → skip
3. **Token overlap**, computed only on distinguishing tokens → skip above 0.85

**Strip generic finance vocabulary before computing similarity.** Indian
headlines are formulaic, so "Promoter sells 2% stake in X for Rs 500 crore" and
"Promoter sells 3% stake in Y for Rs 600 crore" share nearly every token except
the company name. On short headlines the denominator is small and Jaccard spikes
on that shared boilerplate.

Remove before comparing: promoter, promoters, sells, sell, sold, sale, stake,
shares, share, block, bulk, deal, crore, cr, rs, worth, per, cent, buy, buys,
acquires, acquired, in, for, to, of, the, a, at, via, after, as, over, likely.

If the remaining token sets are empty on either side, do not dedup.

Store `title_norm` on the items table and index it.

**Log every drop.** A dedup drop happens before the deals table, so unlike a
clustering merge it leaves no trace anywhere. Write both titles and the computed
similarity to a `title_dedup_log` table and surface them in
`dedupe_check.py`. Without this there is no way to ever discover a wrong merge.

Set the threshold conservatively at 0.85. Over-deduping loses a real story;
under-deduping only costs a few tokens. If the counters show it is barely
firing after a week, we can lower it then.

## Change 5 — Route SEBI DRHP items past stage 1

A DRHP filing is by definition a company going public, which stage 1 would never
mark as a confirmed negative. The call is wasted every time.

Send SEBI-sourced items straight to stage 2. Small saving, and it means DRHP
alerts survive a stage 1 failure.

## Change 6 — Failure isolation

Wrap each stage in its own try/except so one failing component cannot abort the
run.

Specifically: if the Anthropic API is unreachable, the block and bulk deal path
and single PIT disclosures above threshold must still alert, because neither
needs classification at all. That is the system's only fully API-independent
alert path and it should degrade to filings-only rather than stopping.

Log a warning to the admin chat when this happens so I know the system is
running degraded.

## Change 7 — Query attribution

Record which Google News query each item arrived on, in a new `source_query`
column.

Add to the weekly report, **per query**: items produced, and how many deal
clusters that query was the **first** to surface.

First-to-surface is the metric, not uniqueness. A query that mostly duplicates
others can still be the one that gets there earliest, and lead time is the entire
product. Cut queries that never arrive first, not queries that overlap.

Do not remove any query now. Measure for at least two weeks first.

## Change 8 — Runtime ordering

Runtime order is not the same as build order. Run the cheapest filters first so
nothing expensive is spent on items a cheaper filter would have killed:

1. structural blocklist (string match)
2. title dedup (hash and token comparison)
3. pre-API amount gate (regex)
4. stage 1 (API)
5. stage 2 (API)

## Change 9 — Shadow week before enforcing

Every filter in Part 1 removes items **before** classification, so none of their
failures are observable. The v3 feedback buttons measure precision, not recall,
and cannot see something that never became an alert.

So do not enable enforcement immediately.

Add a repository variable `PREFILTER_MODE` with values `shadow` or `enforce`,
defaulting to `shadow`.

In shadow mode, Changes 1, 3 and 4 compute their decision, write it to a
`prefilter_shadow` table with the item title, URL, which filter fired and why,
then **pass the item through anyway**. Everything continues to reach the API as
it does today.

Run for seven days at unchanged cost. Add a `python shadow_report.py` that prints
what each filter would have dropped, grouped by filter. I read it, confirm
nothing real is in there, then flip to `enforce`.

A week of unchanged spend is cheap insurance against four simultaneous
unobservable filtering changes.

---

# PART 2 — Switchable stage 2 model

## Requirement

Stage 2 runs on Haiku by default. I want to switch it to Sonnet without editing
code or committing anything.

## Mechanism

Use a **GitHub Actions repository variable**, not a secret and not a config
edit. Variables are editable in the repo settings UI and take effect on the next
run.

Variable name: `STAGE2_MODEL`. Values: `haiku` or `sonnet`. Absent or
unrecognised falls back to `haiku`.

In `config.py`:

```python
MODELS = {
    "haiku":  {"id": "claude-haiku-4-5-20251001", "batch": 25,
               "in_per_mtok": ..., "out_per_mtok": ...},
    "sonnet": {"id": "claude-sonnet-4-6",         "batch": 25,
               "in_per_mtok": ..., "out_per_mtok": ...},
}
STAGE1_MODEL = "haiku"   # not switchable, stays on Haiku
STAGE2_MODEL = os.getenv("STAGE2_MODEL", "haiku")
```

Look up current per-million-token pricing for both models and put real numbers
in. Note in the README where to check it, since pricing changes.

**Stage 1 is not switchable.** It stays on Haiku permanently. It is the
high-volume stage and the job is simple enough that a bigger model buys nothing.

## One prompt, not two

Use the same stage 2 prompt for both models. Do not maintain model-specific
prompts — the maintenance cost outweighs the marginal gain and it makes the two
incomparable.

Keep `reconcile_amount` active for both. It was written for a Haiku slip, but it
only corrects against the source text so it is harmless as a general safety net.

## Cost visibility

Whichever model is active, the daily digest gains one line:

```
API: 23 stage-1 calls, 2 stage-2 calls (haiku) · est. ₹X.XX today
```

Track token counts from the API response usage fields, multiply by the configured
rates. This means flipping the switch shows up in the digest the next morning
rather than as a surprise on the bill.

---

# PART 3 — Shadow mode (optional, default off)

## The question this answers

Should I actually be paying for Sonnet? Right now I have no evidence either way.

## Design

A second repository variable `SHADOW_SONNET` set to `on` or `off`, default off.

When on, after stage 2 runs on Haiku, take **only the items Haiku rejected**
(Rule P suppressions) and re-run them through Sonnet with the identical prompt.
Do not alert on the results. Write disagreements to a `shadow_log` table:
item title, Haiku verdict, Sonnet verdict, both one_line fields.

Rejected items are the right population to test, because that is where a mistake
costs me a lead. Items Haiku already passed have been alerted anyway.

Add to the weekly report: how many items Sonnet would have passed that Haiku
rejected, with titles, so I can judge whether they were real.

Volume here is small, maybe 20 to 30 items a day. Run it for two weeks, read the
log, then decide. Turn it off afterwards.

---

# Before you finish

1. Run `--dry` and show me the output.
2. Report the funnel counts for one run: items fetched, dropped by structural
   blocklist, dropped by title dedup, dropped by pre-API amount gate, reaching
   stage 1, reaching stage 2, alerted. I want to see where the volume goes.
3. Add those same counters to the daily digest permanently.
4. Confirm `--dry` still works with `STAGE2_MODEL=sonnet` set.
5. Update `SPEC.md` to describe the system as built after these changes.
