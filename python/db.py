"""Local SQLite store for Kalshi Bot.

Everything the bot knows — markets, signals, positions, P&L snapshots and run
history — lives in a single WAL-mode SQLite file under the app's user-data dir.
Nothing here ever leaves the machine.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    base = os.environ.get("KALSHI_BOT_USERDATA")
    d = (Path(base) / "data") if base else (Path(__file__).resolve().parent / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return _data_dir() / "kalshi-bot.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    category TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    close_time TEXT DEFAULT '',
    volume REAL DEFAULT 0,
    volume_24h REAL DEFAULT 0,
    yes_bid REAL DEFAULT 0,
    yes_ask REAL DEFAULT 0,
    last_price REAL DEFAULT 0,
    prev_price REAL DEFAULT 0,
    result TEXT DEFAULT '',
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS whale_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    event_ticker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    category TEXT DEFAULT '',
    taker_side TEXT DEFAULT 'yes',
    price REAL DEFAULT 0,
    dollar_value REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    trade_id TEXT DEFAULT '',
    resolved INTEGER DEFAULT 0,
    outcome_correct INTEGER,
    created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_whale_tradeid ON whale_trades(trade_id);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    event_ticker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    category TEXT DEFAULT '',
    direction TEXT DEFAULT 'yes',
    price REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    signal_type TEXT DEFAULT '',
    resolved INTEGER DEFAULT 0,
    outcome_correct INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(ticker, created_at);

CREATE TABLE IF NOT EXISTS bot_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_source TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    event_ticker TEXT DEFAULT '',
    title TEXT DEFAULT '',
    category TEXT DEFAULT '',
    direction TEXT NOT NULL,
    action TEXT DEFAULT 'buy',
    target_contracts INTEGER DEFAULT 0,
    limit_price_cents INTEGER DEFAULT 0,
    filled_contracts INTEGER DEFAULT 0,
    avg_fill_price_cents REAL,
    cost_usd REAL DEFAULT 0,
    fees_usd REAL DEFAULT 0,
    client_order_id TEXT DEFAULT '',
    kalshi_order_id TEXT,
    status TEXT DEFAULT 'submitted',
    confidence REAL DEFAULT 0,
    edge_pts REAL DEFAULT 0,
    signal_price REAL DEFAULT 0,
    resolved INTEGER DEFAULT 0,
    outcome_correct INTEGER,
    settlement_usd REAL,
    pnl_usd REAL,
    mark_price_cents REAL,
    balance_before_usd REAL,
    close_time TEXT DEFAULT '',
    yes_label TEXT DEFAULT '',
    mtype TEXT DEFAULT '',
    event_title TEXT DEFAULT '',
    reason TEXT DEFAULT '',          -- why the bot took this bet, in plain Spanish
    kalshi_env TEXT DEFAULT 'demo',
    error TEXT,
    created_at TEXT,
    last_updated TEXT,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pos_status ON bot_positions(status, kalshi_env);
CREATE INDEX IF NOT EXISTS idx_pos_signal ON bot_positions(signal_source, signal_id, kalshi_env);

CREATE TABLE IF NOT EXISTS position_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    kalshi_status TEXT,
    note TEXT,
    at TEXT
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    cash_usd REAL DEFAULT 0,
    portfolio_usd REAL DEFAULT 0,
    total_usd REAL DEFAULT 0,
    realized_pnl_usd REAL DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pnl_env_at ON pnl_snapshots(env, at);

CREATE TABLE IF NOT EXISTS bot_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kalshi_env TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    start_cash_usd REAL DEFAULT 0,
    start_portfolio_usd REAL DEFAULT 0,
    start_total_usd REAL DEFAULT 0,
    end_cash_usd REAL,
    end_portfolio_usd REAL,
    end_total_usd REAL,
    pnl_usd REAL DEFAULT 0,
    trades_opened INTEGER DEFAULT 0,
    trades_won INTEGER DEFAULT 0,
    trades_lost INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS risk_breaches (
    env TEXT NOT NULL,
    kind TEXT NOT NULL,
    started_at REAL,
    PRIMARY KEY (env, kind)
);

-- Strategy settings, so a restart resumes with the tuning you chose instead
-- of the defaults. Living in the DB means the Drive backup carries it too.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CONFIG_KEY = "trader_config"


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        # Migrations for existing DBs (ALTER is a no-op-safe add).
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_positions)")}
        for col in ("close_time", "yes_label", "mtype", "event_title", "reason"):
            if col not in cols:
                conn.execute(f"ALTER TABLE bot_positions ADD COLUMN {col} TEXT DEFAULT ''")
    logger.info(f"database ready at {db_path()}")


def save_config(cfg: dict) -> None:
    """Persist the strategy settings. Credentials never go in here."""
    import json
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (CONFIG_KEY, json.dumps(cfg), _now()))


def load_config() -> dict:
    """The saved settings, or {} the first time / if the row is unreadable."""
    import json
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?",
                               (CONFIG_KEY,)).fetchone()
        return json.loads(row["value"]) if row else {}
    except Exception as e:  # noqa: BLE001 — never block startup over settings
        logger.warning(f"could not load saved config: {e}")
        return {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── markets ─────────────────────────────────────────────────────────

def upsert_markets(conn, rows: Iterable[dict]) -> int:
    n = 0
    for m in rows:
        conn.execute(
            """INSERT INTO markets
               (ticker, event_ticker, title, category, status, close_time,
                volume, volume_24h, yes_bid, yes_ask, last_price, prev_price, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(ticker) DO UPDATE SET
                 event_ticker=excluded.event_ticker, title=excluded.title,
                 category=excluded.category, status=excluded.status,
                 close_time=excluded.close_time, volume=excluded.volume,
                 volume_24h=excluded.volume_24h, yes_bid=excluded.yes_bid,
                 yes_ask=excluded.yes_ask, prev_price=markets.last_price,
                 last_price=excluded.last_price, last_updated=excluded.last_updated""",
            (
                m["ticker"], m.get("event_ticker", ""), m.get("title", ""),
                m.get("category", ""), m.get("status", "open"), m.get("close_time", ""),
                m.get("volume", 0), m.get("volume_24h", 0), m.get("yes_bid", 0),
                m.get("yes_ask", 0), m.get("last_price", 0), m.get("last_price", 0),
                _now(),
            ),
        )
        n += 1
    return n


def get_market(conn, ticker: str) -> dict | None:
    r = conn.execute("SELECT * FROM markets WHERE ticker=?", (ticker,)).fetchone()
    return dict(r) if r else None


def count_active_markets(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM markets WHERE status IN ('active','open')"
    ).fetchone()[0]


# ── signals ─────────────────────────────────────────────────────────

def insert_whale(conn, row: dict) -> int | None:
    try:
        cur = conn.execute(
            """INSERT INTO whale_trades
               (ticker, event_ticker, title, category, taker_side, price,
                dollar_value, confidence, trade_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                row["ticker"], row.get("event_ticker", ""), row.get("title", ""),
                row.get("category", ""), row.get("taker_side", "yes"),
                row.get("price", 0), row.get("dollar_value", 0),
                row.get("confidence", 0), row.get("trade_id", ""), _now(),
            ),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # duplicate trade_id


def insert_alert(conn, row: dict) -> int | None:
    # de-dupe: same ticker within cooldown window
    recent = conn.execute(
        """SELECT id FROM alerts WHERE ticker=? AND created_at > ?""",
        (row["ticker"], (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()),
    ).fetchone()
    if recent:
        return None
    cur = conn.execute(
        """INSERT INTO alerts
           (ticker, event_ticker, title, category, direction, price,
            confidence, signal_type, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            row["ticker"], row.get("event_ticker", ""), row.get("title", ""),
            row.get("category", ""), row.get("direction", "yes"),
            row.get("price", 0), row.get("confidence", 0),
            row.get("signal_type", ""), _now(),
        ),
    )
    return cur.lastrowid


def already_traded_signal_ids(conn, source: str, env: str) -> set[int]:
    rows = conn.execute(
        "SELECT signal_id FROM bot_positions WHERE signal_source=? AND kalshi_env=?",
        (source, env),
    ).fetchall()
    return {int(r[0]) for r in rows}


# ── positions: gating queries ───────────────────────────────────────

def count_open_bot_positions(conn, env: str) -> int:
    return conn.execute(
        """SELECT COUNT(*) FROM bot_positions
           WHERE kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled')""",
        (env,),
    ).fetchone()[0]


def _day_start_iso(offset_min: int) -> str:
    now = datetime.now(timezone.utc) + timedelta(minutes=offset_min)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start - timedelta(minutes=offset_min)).isoformat()


def count_new_positions_today(conn, env: str, offset_min: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND created_at >= ?",
        (env, _day_start_iso(offset_min)),
    ).fetchone()[0]


def count_positions_in_event(conn, event_ticker: str, env: str) -> int:
    if not event_ticker:
        return 0
    return conn.execute(
        """SELECT COUNT(*) FROM bot_positions
           WHERE event_ticker=? AND kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled')""",
        (event_ticker, env),
    ).fetchone()[0]


def count_positions_with_event_title(conn, event_title: str, env: str) -> int:
    """Same-event count by the human match name ("Cincinnati vs Seattle").

    The ticker-prefix check above only groups two markets when they share a
    prefix, which assumes the outcome is the ticker's last segment. When that
    assumption breaks the bot books both sides of one game — it did, and lost
    twice on the same match. The title comes from the event itself, so this
    catches it whatever the ticker looks like.
    """
    t = (event_title or "").strip().lower()
    if not t:
        return 0
    return conn.execute(
        """SELECT COUNT(*) FROM bot_positions
           WHERE kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled')
             AND LOWER(TRIM(event_title))=?""",
        (env, t),
    ).fetchone()[0]


def count_positions_in_event_prefix(conn, event_prefix: str, env: str) -> int:
    """Robust same-event count: any open position whose market ticker belongs to
    this event (e.g. all markets under KX...SERBER), regardless of how its
    event_ticker was stored. This is what stops betting both sides of a match."""
    if not event_prefix:
        return 0
    return conn.execute(
        """SELECT COUNT(*) FROM bot_positions
           WHERE kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled')
             AND (event_ticker=? OR ticker LIKE ?)""",
        (env, event_prefix, event_prefix + "-%"),
    ).fetchone()[0]


def exists_position_in_market(conn, ticker: str, direction: str, env: str) -> bool:
    r = conn.execute(
        """SELECT 1 FROM bot_positions
           WHERE ticker=? AND direction=? AND kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled') LIMIT 1""",
        (ticker, direction, env),
    ).fetchone()
    return r is not None


def current_total_exposure_usd(conn, env: str) -> float:
    r = conn.execute(
        """SELECT COALESCE(SUM(
              CASE WHEN filled_contracts>0 THEN cost_usd
                   ELSE target_contracts*limit_price_cents/100.0 END),0)
           FROM bot_positions
           WHERE kalshi_env=? AND resolved=0
             AND status IN ('submitted','partial','filled')""",
        (env,),
    ).fetchone()[0]
    return float(r or 0.0)


def open_filled_cost_usd(conn, env: str) -> float:
    r = conn.execute(
        """SELECT COALESCE(SUM(cost_usd),0) FROM bot_positions
           WHERE kalshi_env=? AND resolved=0 AND filled_contracts>0""",
        (env,),
    ).fetchone()[0]
    return float(r or 0.0)


def open_unrealized_pnl_usd(conn, env: str) -> float:
    rows = conn.execute(
        """SELECT filled_contracts, cost_usd, mark_price_cents FROM bot_positions
           WHERE kalshi_env=? AND resolved=0 AND filled_contracts>0
             AND mark_price_cents IS NOT NULL""",
        (env,),
    ).fetchall()
    total = 0.0
    for r in rows:
        mv = int(r["filled_contracts"]) * float(r["mark_price_cents"]) / 100.0
        total += mv - float(r["cost_usd"] or 0.0)
    return total


# ── positions: writes ───────────────────────────────────────────────

_POS_FIELDS = [
    "signal_source", "signal_id", "ticker", "event_ticker", "title", "category",
    "direction", "action", "target_contracts", "limit_price_cents",
    "filled_contracts", "cost_usd", "client_order_id", "kalshi_order_id",
    "status", "confidence", "edge_pts", "signal_price", "balance_before_usd",
    "close_time", "yes_label", "mtype", "event_title", "kalshi_env", "error",
    "reason",
]


def insert_bot_position(conn, row: dict) -> int:
    cols = [f for f in _POS_FIELDS if f in row]
    placeholders = ",".join("?" for _ in cols)
    vals = [row[c] for c in cols]
    cur = conn.execute(
        f"""INSERT INTO bot_positions ({','.join(cols)}, created_at, last_updated)
            VALUES ({placeholders}, ?, ?)""",
        (*vals, _now(), _now()),
    )
    return cur.lastrowid


def update_bot_position(conn, pid: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE bot_positions SET {sets}, last_updated=? WHERE id=?",
        (*fields.values(), _now(), pid),
    )


def fetch_position_by_id(conn, pid: int) -> dict | None:
    r = conn.execute("SELECT * FROM bot_positions WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def get_open_bot_positions(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM bot_positions WHERE resolved=0
           AND status IN ('submitted','partial','filled')"""
    ).fetchall()
    return [dict(r) for r in rows]


def log_event(conn, pid: int, kind: str, kalshi_status: str | None = None,
              note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO position_events (position_id, kind, kalshi_status, note, at) VALUES (?,?,?,?,?)",
        (pid, kind, kalshi_status, note, _now()),
    )


# ── aggregate stats ─────────────────────────────────────────────────

def aggregate_stats(conn, env: str) -> dict:
    def q(sql: str, args=()):
        return conn.execute(sql, args).fetchone()[0] or 0

    open_cost = q(
        """SELECT COALESCE(SUM(cost_usd),0) FROM bot_positions
           WHERE kalshi_env=? AND resolved=0 AND filled_contracts>0""", (env,))
    wins = q("SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND outcome_correct=1", (env,))
    losses = q("SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND outcome_correct=0", (env,))
    realized = q("SELECT COALESCE(SUM(pnl_usd),0) FROM bot_positions WHERE kalshi_env=? AND resolved=1", (env,))
    gross_win = q("SELECT COALESCE(SUM(pnl_usd),0) FROM bot_positions WHERE kalshi_env=? AND resolved=1 AND pnl_usd>0", (env,))
    gross_loss = q("SELECT COALESCE(SUM(pnl_usd),0) FROM bot_positions WHERE kalshi_env=? AND resolved=1 AND pnl_usd<0", (env,))
    fees = q("SELECT COALESCE(SUM(fees_usd),0) FROM bot_positions WHERE kalshi_env=?", (env,))
    pending = q(
        "SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND resolved=0 AND status='submitted'", (env,))
    open_filled = q(
        """SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND resolved=0
           AND filled_contracts>0 AND status IN ('filled','partial')""", (env,))
    resolved_count = q("SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND resolved=1", (env,))
    total_opened = q("SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=?", (env,))
    day_start = _day_start_iso(0)
    today_wins = q(
        "SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND outcome_correct=1 AND resolved_at>=?",
        (env, day_start))
    today_losses = q(
        "SELECT COUNT(*) FROM bot_positions WHERE kalshi_env=? AND outcome_correct=0 AND resolved_at>=?",
        (env, day_start))
    return {
        "open_cost": float(open_cost), "wins": int(wins), "losses": int(losses),
        "gross_win": float(gross_win), "gross_loss": float(gross_loss),
        "realized_pnl": float(realized), "fees": float(fees), "pending": int(pending),
        "open_filled": int(open_filled), "resolved_count": int(resolved_count),
        "total_opened": int(total_opened), "today_wins": int(today_wins),
        "today_losses": int(today_losses),
    }


# ── pnl snapshots ───────────────────────────────────────────────────

def insert_pnl_snapshot(conn, *, cash_usd, portfolio_usd, realized_pnl_usd,
                        wins, losses, open_positions, env) -> None:
    conn.execute(
        """INSERT INTO pnl_snapshots
           (env, cash_usd, portfolio_usd, total_usd, realized_pnl_usd,
            wins, losses, open_positions, at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (env, cash_usd, portfolio_usd, cash_usd + portfolio_usd, realized_pnl_usd,
         wins, losses, open_positions, _now()),
    )


def get_pnl_snapshots(conn, since_hours: int, env: str) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM pnl_snapshots WHERE env=? AND at>=? ORDER BY at ASC",
        (env, since),
    ).fetchall()
    return [dict(r) for r in rows]


def first_snapshot_of_today(conn, env: str, offset_min: int) -> dict | None:
    r = conn.execute(
        "SELECT * FROM pnl_snapshots WHERE env=? AND at>=? ORDER BY at ASC LIMIT 1",
        (env, _day_start_iso(offset_min)),
    ).fetchone()
    return dict(r) if r else None


def latest_snapshot(conn, env: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM pnl_snapshots WHERE env=? ORDER BY at DESC LIMIT 1", (env,)
    ).fetchone()
    return dict(r) if r else None


def earliest_pnl_total(conn, env: str) -> float | None:
    r = conn.execute(
        "SELECT total_usd FROM pnl_snapshots WHERE env=? ORDER BY at ASC LIMIT 1", (env,)
    ).fetchone()
    return float(r[0]) if r else None


# ── risk breach persistence ─────────────────────────────────────────

def get_risk_breach_start(conn, env: str, kind: str) -> float | None:
    r = conn.execute(
        "SELECT started_at FROM risk_breaches WHERE env=? AND kind=?", (env, kind)
    ).fetchone()
    return float(r[0]) if r and r[0] is not None else None


def set_risk_breach_start(conn, env: str, kind: str, started_at: float | None) -> None:
    if started_at is None:
        conn.execute("DELETE FROM risk_breaches WHERE env=? AND kind=?", (env, kind))
    else:
        conn.execute(
            """INSERT INTO risk_breaches (env, kind, started_at) VALUES (?,?,?)
               ON CONFLICT(env, kind) DO UPDATE SET started_at=excluded.started_at""",
            (env, kind, started_at),
        )


# ── bot runs ────────────────────────────────────────────────────────

def start_bot_run(conn, *, env, cash_usd, portfolio_usd, lifetime_trades,
                  lifetime_wins, lifetime_losses) -> int:
    cur = conn.execute(
        """INSERT INTO bot_runs
           (kalshi_env, started_at, start_cash_usd, start_portfolio_usd, start_total_usd)
           VALUES (?,?,?,?,?)""",
        (env, _now(), cash_usd, portfolio_usd, cash_usd + portfolio_usd),
    )
    return cur.lastrowid


def get_active_run(conn, env: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM bot_runs WHERE kalshi_env=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (env,),
    ).fetchone()
    return dict(r) if r else None


def heartbeat_bot_run(conn, run_id: int, *, cash_usd, portfolio_usd,
                      lifetime_trades, lifetime_wins, lifetime_losses) -> None:
    conn.execute(
        """UPDATE bot_runs SET end_cash_usd=?, end_portfolio_usd=?, end_total_usd=?,
             pnl_usd=(?-start_total_usd), trades_opened=?, trades_won=?, trades_lost=?
           WHERE id=?""",
        (cash_usd, portfolio_usd, cash_usd + portfolio_usd, cash_usd + portfolio_usd,
         lifetime_trades, lifetime_wins, lifetime_losses, run_id),
    )


def end_bot_run(conn, run_id: int) -> None:
    conn.execute("UPDATE bot_runs SET ended_at=? WHERE id=? AND ended_at IS NULL",
                 (_now(), run_id))


def get_recent_runs(conn, env: str | None, limit: int) -> list[dict]:
    if env:
        rows = conn.execute(
            "SELECT * FROM bot_runs WHERE kalshi_env=? ORDER BY id DESC LIMIT ?",
            (env, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── maintenance ─────────────────────────────────────────────────────

def run_maintenance() -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    with get_db() as conn:
        cur = conn.execute("DELETE FROM pnl_snapshots WHERE at < ?", (cutoff,))
        deleted = cur.rowcount
    return {"deleted": deleted, "vacuumed": False, "reclaimable_mb": 0}


def archive_paper_to_reserve() -> dict:
    """Move paper positions + P&L snapshots to reserve tables (*_archive) and
    clear signals — a clean slate the user can trigger from the app. Appends to
    the reserve so test data keeps accumulating out of sight."""
    stamp = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        for src, dst in (("bot_positions", "bot_positions_archive"),
                         ("pnl_snapshots", "pnl_snapshots_archive")):
            conn.execute(f"CREATE TABLE IF NOT EXISTS {dst} AS SELECT * FROM {src} WHERE 0")
            dst_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({dst})")}
            for r in conn.execute(f"PRAGMA table_info({src})"):
                if r[1] not in dst_cols:
                    conn.execute(f"ALTER TABLE {dst} ADD COLUMN {r[1]} {r[2] or 'TEXT'}")
            if "archived_at" not in dst_cols:
                conn.execute(f"ALTER TABLE {dst} ADD COLUMN archived_at TEXT")
        pcols = ",".join(r[1] for r in conn.execute("PRAGMA table_info(bot_positions)"))
        n1 = conn.execute(
            f"INSERT INTO bot_positions_archive ({pcols}, archived_at) "
            f"SELECT {pcols}, ? FROM bot_positions WHERE kalshi_env='paper'", (stamp,)).rowcount
        conn.execute("DELETE FROM bot_positions WHERE kalshi_env='paper'")
        scols = ",".join(r[1] for r in conn.execute("PRAGMA table_info(pnl_snapshots)"))
        n2 = conn.execute(
            f"INSERT INTO pnl_snapshots_archive ({scols}, archived_at) "
            f"SELECT {scols}, ? FROM pnl_snapshots WHERE env='paper'", (stamp,)).rowcount
        conn.execute("DELETE FROM pnl_snapshots WHERE env='paper'")
        conn.execute("DELETE FROM bot_runs WHERE kalshi_env='paper'")
        conn.execute("DELETE FROM whale_trades")
        conn.execute("DELETE FROM alerts")
    logger.info(f"paper reset: archived {n1} positions, {n2} snapshots to reserve")
    return {"archivedPositions": n1, "archivedSnapshots": n2}


def factory_reset() -> dict:
    summary: dict[str, Any] = {}
    with get_db() as conn:
        for t in ("markets", "whale_trades", "alerts", "bot_positions",
                  "position_events", "pnl_snapshots", "bot_runs", "risk_breaches"):
            try:
                summary[t] = conn.execute(f"DELETE FROM {t}").rowcount
            except Exception as e:  # noqa: BLE001
                summary.setdefault("_errors", []).append(f"{t}: {e}")
    return summary
