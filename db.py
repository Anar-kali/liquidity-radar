"""
Liquidity Radar — persistent memory (SQLite).

Three tables:
  items      — every fetched item, keyed on source id / URL, for dedup.
  deals      — clustered transactions (one row per real-world deal).
  suppressed — every confirmed negative, never deleted.

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
            id         TEXT PRIMARY KEY,   -- source id or URL
            source     TEXT,
            title      TEXT,
            url        TEXT,
            description TEXT,
            fetched_at TEXT
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

        CREATE INDEX IF NOT EXISTS idx_deal_key ON deals(deal_key);
        CREATE INDEX IF NOT EXISTS idx_member_deal ON deal_members(deal_id);
        """
    )
    # Non-destructive migration: add size columns to an existing deals table.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(deals)")}
    if "size_source" not in existing:
        conn.execute("ALTER TABLE deals ADD COLUMN size_source TEXT")
    if "size_band" not in existing:
        conn.execute("ALTER TABLE deals ADD COLUMN size_band TEXT")
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
        "INSERT OR IGNORE INTO items (id, source, title, url, description, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            item["id"],
            item.get("source", ""),
            item.get("title", ""),
            item.get("url", ""),
            item.get("description", ""),
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()


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
        "size_source, size_band, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
def add_suppressed(title, url, rule, amount_cr, amount_raw, path=DB_PATH):
    conn = _conn(path)
    conn.execute(
        "INSERT INTO suppressed (title, url, rule, amount_cr, amount_raw, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title, url, rule, amount_cr, amount_raw, now_iso()),
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
