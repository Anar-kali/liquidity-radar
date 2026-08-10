"""
Liquidity Radar — persistent memory (SQLite).

Core tables:
  items      — every fetched item, keyed on source id / URL, for dedup.
  deals      — clustered transactions (one row per real-world deal).
  suppressed — every confirmed negative, never deleted.

Plus supporting tables for market-cap caching, Telegram feedback, the v3
salami-slice aggregator (individual_sales / pattern_alerts), and the v4
Part 1 pipeline instrumentation (item_queries, funnel_runs) — see each
table's inline comment below.

The database file is committed back to the repo after each run, so state
survives between GitHub Actions runs.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = "radar.db"


def _conn(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db(path=DB_PATH):
    conn = _conn(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id           TEXT PRIMARY KEY,   -- source id or URL
            source       TEXT,
            title        TEXT,
            url          TEXT,
            description  TEXT,
            title_norm   TEXT,               -- v4: normalised title, for dedup
            source_query TEXT,                -- v4: Google News query, if any
            fetched_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS deals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_key    TEXT,
            company     TEXT,
            deal_type   TEXT,
            amount_cr   REAL,
            amount_raw  TEXT,
            individuals TEXT,               -- JSON list
            buyer       TEXT,
            confidence  TEXT,
            one_line    TEXT,
            source      TEXT,
            url         TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS suppressed (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT,
            url        TEXT,
            rule       TEXT,
            amount_cr  REAL,
            amount_raw TEXT,
            gate       TEXT,      -- v4: "pre-api" (regex, before any model call)
                                   -- or "model" (stage 1 / stage 2 verdict)
            created_at TEXT
        );

        -- which items merged into each deal (for dedupe_check + auditing)
        CREATE TABLE IF NOT EXISTS deal_members (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id     INTEGER,
            title       TEXT,
            url         TEXT,
            attached_at TEXT
        );

        -- market-cap cache (crore INR), refreshed at most weekly
        CREATE TABLE IF NOT EXISTS market_caps (
            ticker         TEXT PRIMARY KEY,
            market_cap_cr  REAL,
            fetched_at     TEXT
        );

        -- Telegram inline-button feedback (Useful / Already knew / Noise)
        CREATE TABLE IF NOT EXISTS feedback (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id    INTEGER,
            verdict    TEXT,
            chat_id    TEXT,
            created_at TEXT
        );

        -- small key/value store; currently just the Telegram getUpdates offset
        -- and the PIT-feed first-run backfill marker
        CREATE TABLE IF NOT EXISTS kv_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- every individual-seller row seen (PIT disclosures, block/bulk deal
        -- files, and news-sourced deals with a named individual + amount) —
        -- feeds the v3 Change B salami-slice aggregation
        CREATE TABLE IF NOT EXISTS individual_sales (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person_key  TEXT,
            person_name TEXT,
            company     TEXT,
            company_key TEXT,
            trade_date  TEXT,
            value_cr    REAL,
            source      TEXT,      -- pit | block | bulk | news
            created_at  TEXT
        );

        -- one row per PATTERN (aggregate) alert fired, for the re-alert
        -- cooldown rule (only alert again at 2x the amount or after 90 days)
        CREATE TABLE IF NOT EXISTS pattern_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            person_key  TEXT,
            company_key TEXT,
            total_cr    REAL,
            alerted_at  TEXT
        );

        -- v4 Change 7: every (item, Google News query) pairing ever seen, even
        -- for items the id-based dedup above later drops as a duplicate —
        -- needed to compute "produced" (every query that surfaced an item)
        -- and, via deal_members, "first to surface" a deal cluster.
        CREATE TABLE IF NOT EXISTS item_queries (
            item_id    TEXT,
            query      TEXT,
            created_at TEXT
        );

        -- v4 Change 9: shadow-mode decision log for the structural blocklist
        -- (Change 3) and pre-API amount gate (Change 1). While
        -- PREFILTER_MODE=shadow, a firing filter logs here instead of
        -- actually dropping the item, so a trial week costs nothing extra
        -- and shadow_report.py can show what enforcing it would remove.
        CREATE TABLE IF NOT EXISTS prefilter_shadow (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            filter     TEXT,     -- "structural" | "pre_api_amount"
            title      TEXT,
            url        TEXT,
            reason     TEXT,
            created_at TEXT
        );

        -- v4 Change 4: every title-dedup drop, logged unconditionally
        -- (not just in shadow mode) — a dedup drop leaves no trace anywhere
        -- else (unlike a clustering merge, which still shows up in
        -- deal_members), so this is the only way to ever discover a wrong
        -- merge. Surfaced in dedupe_check.py --title-dedup.
        CREATE TABLE IF NOT EXISTS title_dedup_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            title         TEXT,
            url           TEXT,
            matched_title TEXT,
            similarity    REAL,
            created_at    TEXT
        );

        -- v4: one row per main.py run, so the daily digest can show where
        -- volume goes without re-deriving it from suppressed/items each time.
        CREATE TABLE IF NOT EXISTS funnel_runs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            mode               TEXT,
            fetched            INTEGER,
            already_seen       INTEGER,
            structural_dropped INTEGER,
            title_deduped      INTEGER,
            pre_api_gated      INTEGER,
            reached_stage1     INTEGER,
            reached_stage2     INTEGER,
            alerted            INTEGER,
            created_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_deal_key ON deals(deal_key);
        CREATE INDEX IF NOT EXISTS idx_member_deal ON deal_members(deal_id);
        CREATE INDEX IF NOT EXISTS idx_item_queries_item ON item_queries(item_id);
        CREATE INDEX IF NOT EXISTS idx_item_queries_query ON item_queries(query);
        """
    )
    # Non-destructive migration: add new columns to an existing deals table.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(deals)")}
    if "size_source" not in existing:
        conn.execute("ALTER TABLE deals ADD COLUMN size_source TEXT")
    if "size_band" not in existing:
        conn.execute("ALTER TABLE deals ADD COLUMN size_band TEXT")
    if "confirmed" not in existing:
        conn.execute("ALTER TABLE deals ADD COLUMN confirmed INTEGER DEFAULT 0")

    existing_items = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "title_norm" not in existing_items:
        conn.execute("ALTER TABLE items ADD COLUMN title_norm TEXT")
    if "source_query" not in existing_items:
        conn.execute("ALTER TABLE items ADD COLUMN source_query TEXT")

    existing_suppressed = {r[1] for r in conn.execute("PRAGMA table_info(suppressed)")}
    if "gate" not in existing_suppressed:
        conn.execute("ALTER TABLE suppressed ADD COLUMN gate TEXT")

    # This index depends on items.title_norm, which the migration above may
    # have JUST added on an existing (pre-v4) database — it must run after
    # the ALTER TABLE, not inside the initial CREATE TABLE IF NOT EXISTS
    # script above, since that script is a no-op for a table that already
    # existed before this column did.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_title_norm ON items(title_norm)")

    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# market cap cache
# --------------------------------------------------------------------------
def get_market_cap(ticker, max_age_days=7, path=DB_PATH):
    """Return cached market cap (crore) if fresh, else None."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = _conn(path)
    row = conn.execute(
        "SELECT market_cap_cr FROM market_caps WHERE ticker = ? AND fetched_at >= ?",
        (ticker, cutoff),
    ).fetchone()
    conn.close()
    return row["market_cap_cr"] if row else None


def set_market_cap(ticker, market_cap_cr, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT OR REPLACE INTO market_caps (ticker, market_cap_cr, fetched_at) "
        "VALUES (?, ?, ?)",
        (ticker, market_cap_cr, now_iso()),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# feedback + kv_state
# --------------------------------------------------------------------------
def get_state(key, default=None, path=DB_PATH):
    conn = _conn(path)
    row = conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_state(key, value, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO kv_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def add_feedback(deal_id, verdict, chat_id, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO feedback (deal_id, verdict, chat_id, created_at) VALUES (?, ?, ?, ?)",
        (deal_id, verdict, chat_id, now_iso()),
    )
    conn.commit()
    conn.close()


def feedback_since(days, path=DB_PATH):
    """Feedback rows from the last N days, joined with the deal they rate."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT f.verdict, f.created_at, d.company, d.deal_type, d.size_band, "
        "d.size_source, d.source AS source_feed, d.one_line, d.url AS deal_url "
        "FROM feedback f LEFT JOIN deals d ON d.id = f.deal_id "
        "WHERE f.created_at >= ? ORDER BY f.created_at DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------
def item_seen(item_id, path=DB_PATH):
    conn = _conn(path)
    row = conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return row is not None


def add_item(item, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT OR IGNORE INTO items "
        "(id, source, title, url, description, title_norm, source_query, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            item["id"],
            item.get("source", ""),
            item.get("title", ""),
            item.get("url", ""),
            item.get("description", ""),
            item.get("title_norm"),
            item.get("source_query"),
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def recent_title_norms(hours, path=DB_PATH):
    """Every non-empty title_norm seen in the window — used by the v4
    Change 4 title-dedup check. filters.py computes distinguishing tokens
    from these on demand (stopword-stripping is a filtering concern, not a
    storage one)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT DISTINCT title_norm FROM items "
        "WHERE fetched_at >= ? AND title_norm IS NOT NULL AND title_norm != ''",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [r["title_norm"] for r in rows]


# --------------------------------------------------------------------------
# prefilter_shadow / title_dedup_log (v4 Changes 4 and 9)
# --------------------------------------------------------------------------
def add_prefilter_shadow(filter_name, title, url, reason, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO prefilter_shadow (filter, title, url, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (filter_name, title, url, reason, now_iso()),
    )
    conn.commit()
    conn.close()


def prefilter_shadow_since(hours, path=DB_PATH):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM prefilter_shadow WHERE created_at >= ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_title_dedup_log(title, url, matched_title, similarity, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO title_dedup_log (title, url, matched_title, similarity, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, url, matched_title, similarity, now_iso()),
    )
    conn.commit()
    conn.close()


def title_dedup_log_since(hours, path=DB_PATH):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM title_dedup_log WHERE created_at >= ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_item_query(item_id, query, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO item_queries (item_id, query, created_at) VALUES (?, ?, ?)",
        (item_id, query, now_iso()),
    )
    conn.commit()
    conn.close()


def query_item_stats(days, path=DB_PATH):
    """
    v4 Change 7 (revised). Returns {query: {"produced": n, "first_to_surface": n}}
    for Google News queries with any activity in the last `days`.

    "produced" counts every item the query surfaced (even if another query
    also found the same item). "first_to_surface" counts deal clusters where
    this query's item was the EARLIEST member attached to that deal (by
    deal_members.attached_at) — lead time is the point of a search query, not
    overlap, so a query that mostly duplicates others but consistently gets
    there first is still valuable; one that never arrives first is a
    candidate to cut.
    """
    from collections import defaultdict

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT item_id, query FROM item_queries WHERE created_at >= ?", (cutoff,)
    ).fetchall()
    if not rows:
        conn.close()
        return {}

    produced = defaultdict(int)
    queries_by_item = defaultdict(set)
    for r in rows:
        produced[r["query"]] += 1
        queries_by_item[r["item_id"]].add(r["query"])

    # Earliest deal_member per deal, restricted to members we can trace back
    # to a tracked item_id (so we know which query, if any, found it).
    member_rows = conn.execute(
        "SELECT dm.deal_id, dm.attached_at, i.id AS item_id "
        "FROM deal_members dm JOIN items i ON i.url = dm.url AND dm.url != '' "
        "WHERE dm.attached_at >= ? ORDER BY dm.attached_at",
        (cutoff,),
    ).fetchall()
    conn.close()

    first_item_for_deal = {}
    for r in member_rows:
        # Rows are ordered by attached_at ascending, so the first time a
        # deal_id is seen here is its earliest member.
        first_item_for_deal.setdefault(r["deal_id"], r["item_id"])

    first_to_surface = defaultdict(int)
    for item_id in first_item_for_deal.values():
        for q in queries_by_item.get(item_id, ()):
            first_to_surface[q] += 1

    all_queries = set(produced) | set(first_to_surface)
    return {
        q: {"produced": produced.get(q, 0), "first_to_surface": first_to_surface.get(q, 0)}
        for q in all_queries
    }


# --------------------------------------------------------------------------
# deals
# --------------------------------------------------------------------------
def find_open_deal(deal_key, window_hours, path=DB_PATH):
    """Return the most recent deal with this key created inside the window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    conn = _conn(path)
    row = conn.execute(
        "SELECT * FROM deals WHERE deal_key = ? AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 1",
        (deal_key, cutoff),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def deals_in_window(hours, path=DB_PATH):
    """All deals created within the last `hours`, most recent first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM deals WHERE created_at >= ? ORDER BY created_at DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_deal_member(deal_id, title, url, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO deal_members (deal_id, title, url, attached_at) "
        "VALUES (?, ?, ?, ?)",
        (deal_id, title, url, now_iso()),
    )
    conn.commit()
    conn.close()


def members_for_deal(deal_id, path=DB_PATH):
    conn = _conn(path)
    rows = conn.execute(
        "SELECT title, url, attached_at FROM deal_members WHERE deal_id = ? "
        "ORDER BY attached_at",
        (deal_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_deal(deal, path=DB_PATH):
    conn = _conn(path)
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO deals (deal_key, company, deal_type, amount_cr, amount_raw, "
        "individuals, buyer, confidence, one_line, source, url, "
        "size_source, size_band, confirmed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            deal["deal_key"],
            deal["company"],
            deal["deal_type"],
            deal["amount_cr"],
            deal["amount_raw"],
            json.dumps(deal["individuals"]),
            deal["buyer"],
            deal["confidence"],
            deal["one_line"],
            deal["source"],
            deal["url"],
            deal.get("size_source"),
            deal.get("size_band"),
            deal.get("confirmed", 0),
            ts,
            ts,
        ),
    )
    conn.commit()
    deal_id = cur.lastrowid
    conn.close()
    return deal_id


def update_deal(deal_id, fields, path=DB_PATH):
    """fields: dict of column -> new value. individuals may be a list."""
    if "individuals" in fields and isinstance(fields["individuals"], list):
        fields["individuals"] = json.dumps(fields["individuals"])
    fields["updated_at"] = now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _conn(path)
    conn.execute(f"UPDATE deals SET {cols} WHERE id = ?", (*fields.values(), deal_id))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# suppressed
# --------------------------------------------------------------------------
def add_suppressed(title, url, rule, amount_cr, amount_raw, gate="model", path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO suppressed (title, url, rule, amount_cr, amount_raw, gate, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, url, rule, amount_cr, amount_raw, gate, now_iso()),
    )
    conn.commit()
    conn.close()


def suppressed_since(hours, path=DB_PATH):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM suppressed WHERE created_at >= ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# individual_sales / pattern_alerts (v3 Change B — salami-slice aggregation)
# --------------------------------------------------------------------------
def add_individual_sale(person_key, person_name, company, company_key,
                         trade_date, value_cr, source, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO individual_sales "
        "(person_key, person_name, company, company_key, trade_date, value_cr, "
        "source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (person_key, person_name, company, company_key, trade_date, value_cr,
         source, now_iso()),
    )
    conn.commit()
    conn.close()


def sales_for_person_company(person_key, company_key, days, path=DB_PATH):
    """Rows for one (person, company) pair in the last N days, oldest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM individual_sales WHERE person_key = ? AND company_key = ? "
        "AND created_at >= ? ORDER BY trade_date",
        (person_key, company_key, cutoff),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def distinct_persons_for_company(company_key, days, path=DB_PATH):
    """(person_key, a person_name seen under it) pairs for one company in the
    last N days — used to fuzzy-resolve a new name to an existing person_key."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT person_key, person_name FROM individual_sales "
        "WHERE company_key = ? AND created_at >= ? AND person_key != '' "
        "GROUP BY person_key",
        (company_key, cutoff),
    ).fetchall()
    conn.close()
    return [(r["person_key"], r["person_name"]) for r in rows]


def distinct_person_companies(days, path=DB_PATH):
    """Every (person_key, company_key) pair with activity in the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT DISTINCT person_key, company_key FROM individual_sales "
        "WHERE created_at >= ? AND person_key != ''",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [(r["person_key"], r["company_key"]) for r in rows]


def last_pattern_alert(person_key, company_key, path=DB_PATH):
    """Most recent PATTERN alert for this pair, or None."""
    conn = _conn(path)
    row = conn.execute(
        "SELECT * FROM pattern_alerts WHERE person_key = ? AND company_key = ? "
        "ORDER BY alerted_at DESC LIMIT 1",
        (person_key, company_key),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def pattern_alerts_since(hours, path=DB_PATH):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM pattern_alerts WHERE alerted_at >= ? ORDER BY alerted_at",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_pattern_alert(person_key, company_key, total_cr, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO pattern_alerts (person_key, company_key, total_cr, alerted_at) "
        "VALUES (?, ?, ?, ?)",
        (person_key, company_key, total_cr, now_iso()),
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# funnel_runs (v4) — one row per main.py run, so the daily digest can show
# where volume goes: fetched -> structural -> title dedup -> pre-API gate ->
# stage 1 -> stage 2 -> alerted.
# --------------------------------------------------------------------------
_FUNNEL_FIELDS = (
    "mode", "fetched", "already_seen", "structural_dropped", "title_deduped",
    "pre_api_gated", "reached_stage1", "reached_stage2", "alerted",
)


def add_funnel_run(counters, path=DB_PATH):
    conn = _conn(path)
    cols = ", ".join(_FUNNEL_FIELDS)
    placeholders = ", ".join("?" for _ in _FUNNEL_FIELDS)
    values = [counters.get(f) for f in _FUNNEL_FIELDS]
    conn.execute(
        f"INSERT INTO funnel_runs ({cols}, created_at) VALUES ({placeholders}, ?)",
        (*values, now_iso()),
    )
    conn.commit()
    conn.close()


def funnel_since(hours, path=DB_PATH):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = _conn(path)
    rows = conn.execute(
        "SELECT * FROM funnel_runs WHERE created_at >= ? ORDER BY created_at",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
