# Liquidity Radar

Monitors Indian corporate filings and financial news for deals where an
individual (promoter, founder, family shareholder) is likely to receive a
large sum of money, and sends a Telegram alert for each qualifying deal.

Built for high recall: false alarms are fine, missing a real deal is not.

It runs entirely on **GitHub Actions' free tier** — no server to rent. State
(the `radar.db` file) is committed back to the repo after each run so the
system remembers what it has already seen.

---

## 1. The three secrets — what they are and where to get them

Set these in **repo Settings → Secrets and variables → Actions → New repository
secret**. Never put them in the code.

| Secret | What it is | Where to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | The password for your Telegram bot. | In Telegram, message **@BotFather**, send `/newbot`, follow the prompts. It gives you a token like `123456:ABC-DEF...`. |
| `TELEGRAM_CHAT_ID` | Which chat the alerts go to (you). | Message your new bot once (say "hi"). Then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and look for `"chat":{"id":...}`. That number is your chat id. |
| `ANTHROPIC_API_KEY` | The key that lets the system call Claude to classify items. | Create one at **console.anthropic.com → API Keys**. Starts with `sk-ant-...`. |

---

## 2. First run — test it before going live

You need Python 3.11.

```bash
pip install -r requirements.txt

# Check Telegram is wired up (sends one test message):
export TELEGRAM_BOT_TOKEN=...      # or set them however you like
export TELEGRAM_CHAT_ID=...
python main.py --test-telegram

# Do a real fetch + classify, but PRINT alerts instead of sending them:
export ANTHROPIC_API_KEY=...
python main.py --mode fast --dry
```

`--dry` prints every alert to the terminal and sends nothing. Use it whenever
you want to see what the system would do without pinging your phone.

---

## 3. Going live

Push this repo to GitHub. The four schedules under `.github/workflows/` then
run automatically:

| Workflow | When (IST) | What it looks at |
|---|---|---|
| `fast.yml`  | every 15 min, Mon-Fri, 09:00-18:30 | exchanges + news |
| `news.yml`  | every 30 min, all days, 07:00-23:00 | news only |
| `slow.yml`  | 08:00, 14:00, 20:00 daily | SEBI draft offer documents |
| `digest.yml`| 20:30 daily | the suppression summary |

A shared `concurrency` group means two runs never overlap.

You can also trigger any workflow by hand: **Actions → pick a workflow → Run
workflow.**

---

## 4. Reading the suppression report

Everything the system filtered out is kept forever in the `suppressed` table.

- **Daily digest** (`digest.yml`) sends a Telegram summary at 20:30 IST: how
  many were suppressed, broken down by rule, plus the largest one.
- **7-day report** — run locally:

  ```bash
  python report.py            # last 7 days, grouped by rule
  python report.py --days 30  # last 30 days
  ```

If you see real deals showing up in the suppression report, the classifier is
being too aggressive — loosen the relevant rule in the system prompt (see
below).

---

## 5. Changing the 250-crore threshold

Open **`config.py`** and edit one line near the top:

```python
THRESHOLD_CR = 250
```

Raise it for fewer, bigger deals; lower it to catch smaller ones. The value is
also injected into the instructions given to Claude, so the two always agree.

---

## 6. Adding or removing a Google News search

Also in **`config.py`**, edit the `GOOGLE_NEWS_QUERIES` list:

```python
GOOGLE_NEWS_QUERIES = [
    "promoter stake sale crore India",
    "block deal promoter shares crore",
    # add a new line here, in quotes, to add a search
    # delete a line to remove a search
]
```

Each entry becomes one Google News feed restricted to the last two days.

---

## How it works (one paragraph)

Each run fetches items, drops anything already in `radar.db`, and sends the
rest to Claude Haiku in batches of 25. Claude's only job is to flag
**confirmed negatives** (pure debt, government divestment, deals clearly under
the threshold, non-transactions, etc.); everything else passes. Passed items go
through **clustering** — the same deal reported by eight outlets becomes one
alert, keyed on the normalised company name + deal type within a rolling 72-hour
window. A later article only interrupts you again ("UPDATE") if it adds a
material fact the deal record lacked: an amount, a named individual, a named
buyer, or an amount revised by more than 20%.

---

## What this is deliberately *not*

No valuation lookups, no funding history, no cap-table inference, no MCA /
Probe42 / Tracxn, no web dashboard, no HUF/family-settlement tracking, and no
Sonnet — Haiku only, because cost matters more than marginal accuracy here.
