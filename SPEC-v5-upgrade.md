# SPEC v5 — Freshness, sizing, and one-alert-per-deal

Status: **BUILT 2026-09-02, committed on `main`, NOT YET PUSHED.**
Baseline measured the same day against the live `radar.db` and the live feeds.

| Change | Commit | Verified |
|---|---|---|
| 3 — one alert per deal | `f5a7b13` | 14 checks |
| 1 — 48h freshness gate | `f786746` | 26 checks + live feed run |
| 2/4/5/6 — matcher, market-cap, report, re-publisher | `82f61a4` | 43 checks + live NSE pricing |

Changes 2/4/5/6 ship together because Change 2 is meaningless without Change
4 — the market-cap gate can only judge a company whose name resolves.

**On first run after deploy** `db.init_db()` migrates in three new columns
(`items.published_at`, `funnel_runs.stale_dropped`, `funnel_runs.republisher_gated`)
and the `name_match_log` table. Existing rows keep NULL, which reads as
"no timestamp" — i.e. they pass the age gate, same as any undated item.

**Review one week after deploy:** `python unresolved_report.py`. Read the
SUPPRESSED half of the fuzzy-match list. If a company there is not the company
named in the headline, that is a bad match — raise
`config.MATCH_RARE_TOKEN_MAX_DF` or add a correcting alias.

## Why

Three problems, in the user's words:

1. Months-old news articles (notably scanx.trade) are still coming through.
2. Small companies still generate alerts.
3. Follow-up "UPDATE" pings on a deal that was already alerted are unwanted —
   one alert per deal is enough; the research after that is done by hand.

### Measured baseline (2026-09-02)

| Metric | Value |
|---|---|
| Runs per day | 13 (hourly, via cron-job.org) |
| Items into stage 1 / day | 530 |
| Items into stage 2 / day | 197 |
| Alerts / day | 24 |
| Deals last 7 days | 131 — of which **72 (55%) had no amount at all** |
| Deals last 30 days resolving to a listed ticker | 150 of 458 distinct names (33%) |
| scanx.trade articles ingested to date | 484 |
| Estimated Anthropic spend | **~$6/month** (Haiku 4.5, $1/$5 per Mtok) |

Marginal cost of one extra item: **stage 1 ≈ 0.017c, stage 2 ≈ 0.037c.**

---

## Change 1 — Freshness gate: news must be under 48 hours old

Runs in the **first pass, before any API call**, alongside the existing
prefilters in `filters.py` / `main.py`.

- Read the feed's published timestamp; drop news items older than
  `NEWS_MAX_AGE_HOURS = 48`.
- **Applies to news only** — Google News and the trade-press RSS feeds.
  BSE / NSE / SEBI filings are **exempt**: fetched same-day by construction,
  and they carry no feed timestamp.
- **No timestamp -> the item passes.** Same recall bias as every other filter:
  unknown is never suppressed.
- Persist the published timestamp on `items` (new column) and log every age
  drop, so what it removed is auditable.

**Feasibility, measured live 2026-09-02:** timestamp coverage is **100%** —
all 279 Google News entries and all 264 trade-press entries carried a date.
Current staleness: **Entrackr 26 of 50** and **FT India 23 of 25** entries are
already older than 48h.

**Known risk:** in that same snapshot **0 of 279 Google News entries were older
than 48h**, scanx included. If stale scanx articles arrive stamped with a fresh
Google timestamp, this gate will not catch them. The drop log answers that
within a day of running. Fallback, only if needed: read the real publication
date off the article page.

---

## Change 2 — Market-cap gate on every listed company

Today the market-cap check only fires on **BSE filings** (under 1,000cr), and
the stake x market-cap calculation only runs for items with **no stated
amount**. Everything else escapes it.

### The rule
- Resolve the company to a listed ticker (see Change 4).
- **Stake % stated** -> `stake x market cap` must clear **300cr**.
- **No stake stated** -> market cap must be at least **1,000cr**
  (replaces the current `MCAP_PLAUSIBLE_MIN = 350`). **One number everywhere:**
  the existing BSE filing gate is already 1,000cr and stays there, so there is
  a single company-size floor across the whole pipeline.
- **`THRESHOLD_CR` stays 250** for stated deal amounts — unchanged.
- **Unlisted / unresolved companies pass through as today.** Decided
  deliberately: the user triages those by hand.

### Where it runs — two places, no new API call

**The market-cap check is not an Anthropic call.** It is a lookup in the
committed NSE/BSE master CSVs plus a market-cap fetch (yfinance / NSE),
already cached in SQLite for 7 days. Placement therefore changes runtime and
accuracy, not API spend.

1. **Pre-API, on NSE filings.** NSE titles are `SYMBOL: subject`
   (`PARKHOSPS: Copy of Newspaper Publication`) — the ticker itself, so no name
   matching is involved. **987 items/week carry a symbol matching the master
   list exactly (56% of NSE items; NSE is 40% of everything fetched).** NSE
   currently has **no market-cap gate at all** — this is the biggest gap.
   Cost: 702 distinct symbols/week, so ~700 fresh market-data lookups per week
   in steady state (~8 per run, 1-2s each).

2. **After stage 2, on news.** Stage 2 already extracts the company name and
   the stake %, so the gate reuses them for **zero extra tokens**.

**Explicitly rejected:**
- *Guessing the company from a news headline before the API call.* Works ~34%
  of the time; a wrong match silently kills a real deal. Saves ~25c/month.
  Not worth it.
- *Having Haiku look up market caps via a tool call.* The model does not know
  market caps; a mid-conversation round trip roughly doubles stage-2 tokens.
  This is the one genuinely expensive option.

---

## Change 3 — One alert per deal, never an update

`cluster.process()` stops returning follow-up UPDATE alerts entirely.

- New facts arriving later (revised amount, seller, individuals, the
  `confirmed` flag) are still **written to the deal record silently**.
- The daily digest still shows them.
- Applies to **both** update paths: `_material_updates` (news revision) and
  `_confirmed_updates` (block/bulk deal, PIT disclosure).
- **A confirmed block-deal figure landing on an amount-less deal stays
  silent too** — decided deliberately.

For scale: 74 of the last 30 days' deals fired a later update.

---

## Change 4 — Fix the company-name matcher

Everything in Change 2 is capped by whether a company resolves at all. Today
the matcher demands a **unique, exact, word-for-word set match** against the
master list, so only **33%** of alerted companies resolve.

Resolution becomes tiered, most confident first. **Anything ambiguous returns
nothing** — unknown passes, as today.

**Tier 0 — NSE ticker symbol.** For NSE filings, read the symbol straight off
the title. No name matching.

**Tier 1 — Exact token-set match.** Unchanged.

**Tier 2 — Unique subset match, guarded by word rarity.** The deal's name may
be a **subset** of one master name ("Manappuram" ⊂ "Manappuram Finance"), on
two conditions: the match is **unique**, and it rests on at least one **rare**
word — appearing in **3 or fewer** of the 7,550 master names. Generic words
(`finance` 147, `capital` 83, `energy` 60) can never carry a match alone.

*Validated against 30 days of real data: 16 new matches, all correct* —
including FSN E-Commerce (Nykaa), Milky Mist, Eris Lifesciences, WeWork
Management, Yatharth Trauma Care, Nuvama Wealth. The guard rejected exactly the
one wrong match, "Steel Infra Solutions" -> "Magnus Infra Steel" (rarest word
`steel`, 35 names), without being tuned for it.

**Tier 3 — Brand alias table.** A small committed file mapping a brand name to
its **legal name** (not to a ticker — a ticker would rot silently; a legal name
re-validates against the master list on every refresh).

Seed list, each validated against the committed master CSVs:

```
Nykaa        -> FSN E-Commerce Ventures    Mamaearth -> Honasa Consumer
Paytm        -> One 97 Communications      FirstCry  -> Brainbees Solutions
Zomato       -> Eternal                    Groww     -> Billionbrains Garage Ventures
PolicyBazaar -> PB Fintech                 Bikaji    -> Bikaji Foods International
Ola Electric -> Ola Electric Mobility      LIC       -> Life Insurance Corporation of India
Airtel       -> Bharti Airtel              SBI       -> State Bank of India
Vi           -> Vodafone Idea
```

Note "One 97 Communications" carries a space — `One97` matches nothing. Every
alias must be validated against the master file, never written from memory.

> **Hard rule: an alias maps a brand to the SAME legal entity. Never a
> subsidiary to its parent.** "Jio Platforms", "Tata Sons", "Mahanadi
> Coalfields" and "National Stock Exchange" are unlisted entities with listed
> parents. Mapping Jio Platforms to Reliance would size a Jio deal against
> Reliance's market cap. All four must stay unresolved.

**Rejected outright — superset matching.** The mirror rule (deal name longer
than the master name) fires whenever a microcap holds a one-word generic name:

```
Royal Challengers Bangalore -> a company named "Royal"
Airtel Africa Finance       -> a company named "Finance"
Tata Sons                   -> a company named "Tata"
```

This is the same failure mode as the recorded M&M / M&M Financial merge bug.
Not building it.

**Two cleanups:**
- Exclude **ETF and index-fund entries** from the matching universe — 265 of
  7,550 master rows (3.5%); `etf` alone appears in 190 names. In testing,
  "ICICI Prudential AMC" matched an ETF. They can never be a deal target.
- **Prefer NSE over BSE** on a Tier 2 match, matching Tier 1's existing
  behaviour, so the same company sizes identically whichever tier caught it.

**Expected effect: 33% -> ~37-38% of alerted companies resolving.** Modest by
design — 292 of the unresolved names over 60 days are genuinely private
companies (Zetwerk, Zepto, Purple Style Labs, Table Space, Svatantra Microfin),
which is the correct answer for them.

**Safety.** Name matching currently only affects *sizing*, where a wrong match
means a slightly wrong number. Once it drives **suppression**, a wrong match
silently kills a real deal. Every non-exact match must therefore be logged with
the input name, the matched master name, and the rare word that carried it.

---

## Change 5 — Unresolved-company-names report

A small standalone script in the style of `shadow_report.py`: the most frequent
company names that resolve under **no** tier, over a configurable window.

This is what tells the user which alias to add next, so the Tier 3 table grows
from evidence rather than guesswork. Sample output from 60 days of live data:

```
x4  Paytm            x3  Jio Platforms        x2  Zepto
x3  Zetwerk          x3  Gaja Capital         x2  Tata Sons
x3  Purple Style Labs x3 Medicover India      x2  Nykaa
... 292 distinct unresolved names in total
```

---

## Change 6 — Substance gate on re-publishers

Google re-pushes scanx.trade articles with **fresh timestamps**, so Change 1
cannot see that they are old. Verified 2026-09-02: Google's own `when:2d`
recency operator returns the same 8 scanx items — Google itself classifies them
as fresh.

Reading the true publication date is not practical. Google News gives us an
opaque redirect token, not an article URL: the interstitial page is 593KB and
mentions "scanx" **zero** times, and the URL payload decodes to a Google token
rather than a publisher address. Recovering the real article requires Google's
undocumented `batchexecute` RPC, which costs no money but breaks whenever
Google changes it — an unacceptable dependency for an hourly job.

### The rule

For a **re-publisher source** (`config.REPUBLISHER_SOURCES`, seeded with
`scanx.trade`), the standing "unknown passes" bias is **inverted**:

> An item from a re-publisher must carry either a stated amount clearing
> `THRESHOLD_CR` (250cr), **or** a company resolving to a listed ticker that
> clears the market-cap gate of Change 2. No size, no alert.

The publisher is read from the Google News title suffix (` - scanx.trade`),
which `filters.normalise_title` already strips — no fetching, no extra call.

### Where it runs

**After stage 2**, alongside the Change 2 market-cap gate — *not* pre-API.
Pre-API would mean guessing the company from the headline, and here a failed
guess DROPS a real deal rather than merely passing it. Stage 2 has already
extracted the company and the amount, so the gate is accurate and costs no
extra tokens. Letting these items through both stages costs ~0.054c each
(~26c for every scanx article ever ingested) — not worth trading accuracy for.

### Measured effect

63 deals have ever involved a scanx article; 45 had scanx as the only source.

- **10 of the 45 had a real size — all 10 survive this rule** (smallest is
  357cr): Waaree Energies 14,307cr, CultFit 2,500cr, KDDL 2,324cr, Anlon
  Healthcare 1,533cr, Dr. Agarwal's Health Care 806cr, RMIL 565cr, P&G Hygiene
  557cr, SNA/DFSU 543cr, Chandenville Infra Park 536cr, Neo Semi SG 358cr.
- **35 had no amount at all — these drop.** 22 of them resolve to microcaps
  (Ramgopal Polytex, Umiya Tubes, Baba Arts, Patspin India) that Change 2 would
  catch anyway; the other 13 currently leak through entirely.

**Rejected — blanket-blocking scanx.** Free and trivial, and it would have cost
Waaree at 14,307cr and CultFit at 2,500cr. A publisher that occasionally breaks
a real story should not be silenced wholesale.

**Known limitation, accepted:** this does not verify age and cannot, given the
opaque URLs. A stale scanx article carrying a large stated amount will still
come through. What it removes is the microcap-with-no-size flood, which is
where the volume is.

---

## Rollout

Existing `PREFILTER_MODE` semantics are honoured: anything not exactly
`"enforce"` means shadow. New filters log their decisions either way.

**Decided: every tier enforces from day one**, including the fuzzy tiers 2
and 3. The user's call, made knowing that a wrong fuzzy match suppresses a real
deal — the mitigation is auditability, not delay.

**This makes the match log load-bearing, not a nicety.** Because a wrong
suppression is invisible in the alert stream, the log is the ONLY way to find
one. Every non-exact match must therefore record enough to both *detect* and
*reverse* a mistake:

- the input company name and the item's title + URL
- the master-list name matched, the resolved ticker, and the tier that fired
- the rare token that carried a Tier 2 match (and its document frequency)
- the market cap read, and the resulting decision (passed / suppressed)

Suppressions from this path go to the `suppressed` table under their own rule
label so they are queryable, not just printed.

**Review one week after ship.** Read the log, confirm nothing real was killed,
and adjust the rarity threshold or the alias table if it was.

## Settled during review

- **Company-size floor is 1,000cr everywhere.** The new no-stake rule and the
  existing BSE filing gate use the same number.
- **All matcher tiers enforce from day one**, with full logging and a review
  one week after ship.
- **Dedup window stays at 72h**, confirmed by a 3-10 day sensitivity sweep
  (replayed over 12,939 items, scored on the 5,801 with a full 10-day
  lookback):

  | Window | Articles dropped | vs 72h | Deals lost entirely |
  |---|---|---|---|
  | **3d (current)** | 413 | — | **0** |
  | 4d | 415 | +2 | 1 |
  | 5d | 417 | +4 | 1 |
  | 6d | 420 | +7 | 2 |
  | 7d | 421 | +8 | 2 |
  | 10d | 422 | +9 | 2 |

  Widening to 10 days buys 9 extra drops out of 5,801 items (~$0.005 saved)
  and starts destroying real deals at day 4 — Yatharth Hospitals at 4-5d, and
  **Avaada Electro at Rs 7,600cr from day 6**. Everything worth catching is
  caught inside 72h; past that the rule stops catching repeats and starts
  merging *different deals at the same company*.

  *Method note:* a replay counts distinct persisted articles, while
  `title_dedup_log` counts every copy — including the same article arriving
  twice in one run under two different search queries (502 such extra copies in
  the period). That gap is the known non-bug, not a discrepancy.

## Suggested order of work

1. **Change 3** (no updates) — smallest, self-contained, immediate relief.
2. **Change 1** (freshness gate) — self-contained.
3. **Change 4 + 5** (matcher + report) — Change 2 depends on this.
4. **Change 2 + Change 6** (market-cap gate and re-publisher substance gate)
   — last, together: both are post-stage-2 size gates and Change 6 reuses
   Change 2's market-cap rule.
