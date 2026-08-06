"""
Liquidity Radar — settings.

This is the file you edit to tune the system. Everything a non-programmer is
likely to want to change lives here, near the top, with comments.
"""

# --------------------------------------------------------------------------
# THE MONEY THRESHOLD
# Deals that convert to LESS than this many crore INR are suppressed (rule 8).
# Raise it to get fewer, bigger deals; lower it to catch smaller ones.
# --------------------------------------------------------------------------
THRESHOLD_CR = 250

# --------------------------------------------------------------------------
# THE CLASSIFIER MODEL
# Haiku only, on purpose — it is cheap and the job is "reject confirmed noise".
# Do not change this to Sonnet; cost matters more than marginal accuracy here.
# --------------------------------------------------------------------------
MODEL = "claude-haiku-4-5-20251001"

# How many items we send to the model in one API call.
BATCH_SIZE = 25

# How many characters of each item's description we send (headline is always
# sent in full).
DESCRIPTION_CHARS = 400

# --------------------------------------------------------------------------
# CURRENCY CONVERSION (kept in sync with the system prompt below)
# --------------------------------------------------------------------------
USD_INR = 88
EUR_INR = 96

# --------------------------------------------------------------------------
# GOOGLE NEWS SEARCHES — highest yield source.
# To add a search: add a line. To remove one: delete its line.
# Each becomes one RSS feed, restricted to the last 2 days.
# --------------------------------------------------------------------------
GOOGLE_NEWS_QUERIES = [
    "promoter stake sale crore India",
    "block deal promoter shares crore",
    "DRHP filed SEBI offer for sale",
    "private equity acquires majority stake India crore",
    "promoter offloads stake crore",
    "founders sell shares IPO OFS crore",
    "open offer acquisition promoter crore",
    "family office stake sale India crore",
]

# Template for a Google News RSS search feed.
GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search?q={query}+when:2d"
    "&hl=en-IN&gl=IN&ceid=IN:en"
)

# --------------------------------------------------------------------------
# TRADE PRESS RSS FEEDS.
#
# NOT here, on purpose:
#   ET Markets   — tested, only retail-investor noise; ET reaches us via
#                  Google News anyway.
#   VCCircle     — no longer publishes a working RSS feed (its /rss URL now
#                  serves a JavaScript web page, not XML). VCCircle stories
#                  still reach us via Google News.
#   Business St. — its RSS returns HTTP 403 "Access Denied" to any server IP
#                  (anti-bot block), so it can't be fetched from GitHub
#                  Actions. Business Standard stories reach us via Google News.
#
# To add a feed: add a "Name": "url" line. Give the URL a quick check first —
# some sites serve HTML or block servers.
# --------------------------------------------------------------------------
TRADE_PRESS_FEEDS = {
    "Mint": "https://www.livemint.com/rss/companies",
    "Entrackr": "https://entrackr.com/rss",
}

# --------------------------------------------------------------------------
# BSE announcements API.
# We keep only announcement subcategories that can involve money changing
# hands, and drop routine filings.
# --------------------------------------------------------------------------
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"

# Subcategory names (lower-cased, matched as substrings) we KEEP.
BSE_KEEP_KEYWORDS = [
    "acquisition",
    "acquire",
    "disposal",
    "sale of",
    "stake",
    "fund raising",
    "fund-raising",
    "fundraising",
    "raising of funds",
    "preferential",
    "qualified institution",
    "shareholding",
    "open offer",
    "scheme of arrangement",
]

# Subcategory names we always DROP (routine filings).
BSE_DROP_KEYWORDS = [
    "result",
    "board meeting",
    "newspaper publication",
    "trading window",
    "investor presentation",
    "analyst",
    "earnings call",
    "record date",
    "dividend",
]

# --------------------------------------------------------------------------
# NSE announcements API.
# --------------------------------------------------------------------------
NSE_HOME = "https://www.nseindia.com"
NSE_API = "https://www.nseindia.com/api/corporate-announcements?index=equities"

# --------------------------------------------------------------------------
# SEBI draft offer documents (DRHP) listing page.
# --------------------------------------------------------------------------
SEBI_URL = "https://www.sebi.gov.in/filings/public-issues"
SEBI_LINK_KEYWORDS = ["draft", "drhp", "prospectus", "offer document"]

# --------------------------------------------------------------------------
# Deal-name normalisation. When we compare two company names to decide if two
# articles are about the same deal, we strip these words and all punctuation.
# --------------------------------------------------------------------------
NORMALISE_STOPWORDS = [
    "private",
    "limited",
    "ltd",
    "pvt",
    "inc",
    "corp",
    "technologies",
    "industries",
    "enterprises",
]

# How long (hours) a deal stays "open" for later articles to attach to it.
DEAL_WINDOW_HOURS = 72

# A browser-like User-Agent, needed by BSE and NSE.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# THE CLASSIFIER SYSTEM PROMPT.
# This is the exact instruction given to Claude. Edit with care.
# --------------------------------------------------------------------------
SYSTEM_PROMPT = f"""\
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
8. A deal size is clearly STATED and converts to less than {THRESHOLD_CR} crore INR.
9. Not a transaction at all: earnings results, share price moves, analyst
   ratings, technical signals, product launches, regulatory disputes,
   aggregate market commentary, or listicles about promoter selling trends.

Rule 8 applies ONLY when a size is stated. Undisclosed terms, no figure
given, "sources say" with no number: these all PASS. Silence is never a
small deal.

Currency: 1 crore = 10 million INR. 1 lakh = 100,000 INR.
Use 1 USD = {USD_INR} INR, 1 EUR = {EUR_INR} INR. Show your working in amount_raw.

Return ONLY a JSON array, no markdown fences, no preamble. One object per
input item, same order:
[{{
  "n": 1,
  "confirmed_negative": true|false,
  "negative_reason": "rule number and short phrase, or null",
  "company": "",
  "deal_type": "IPO-OFS|block deal|strategic buyout|PE secondary|PE primary|open offer|promoter sale|DRHP filing|other|unknown",
  "amount_cr": null,
  "amount_raw": "exact text the figure came from, plus your conversion, or null",
  "individuals": ["named individuals receiving money, empty if none named"],
  "buyer": "named acquirer or buyer, or null",
  "confidence": "high|medium",
  "one_line": "under 20 words: what happened and who gets paid"
}}]

Never invent a figure. If no amount is stated, amount_cr and amount_raw are
null. Do not estimate from stake percentages or valuations.

Set confidence to "high" when a named individual and a stated amount are both
present. Otherwise "medium".
"""
