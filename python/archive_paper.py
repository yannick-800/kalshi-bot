"""Archive & clean the app's paper-trading test data.

Moves the current paper positions and P&L snapshots into reserve tables
(*_archive) so the app shows a clean slate ($1000, 0 positions) while the data
is kept for later analysis. Signals (whale/momentum detections) are cleared
since they regenerate from the live feed and aren't test results.

Reusable: each run APPENDS the current test batch to the reserve, so you can
keep accumulating test data over time without ever seeing it in the app.

Run with the app stopped:
    ./.venv/bin/python archive_paper.py            # archive + clean the APP db
    ./.venv/bin/python archive_paper.py --stats    # show what's in reserve
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def app_db() -> str:
    base = os.environ.get("KALSHI_BOT_USERDATA") or str(
        Path.home() / "Library" / "Application Support" / "Kalshi Bot"
    )
    return str(Path(base) / "data" / "kalshi-bot.db")


def _mirror(conn: sqlite3.Connection, src: str, dst: str) -> None:
    # Create the archive table from the source schema, then keep it in sync:
    # add any columns the source has gained since the archive was created.
    conn.execute(f"CREATE TABLE IF NOT EXISTS {dst} AS SELECT * FROM {src} WHERE 0")
    dst_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({dst})")}
    for r in conn.execute(f"PRAGMA table_info({src})"):
        name, typ = r[1], (r[2] or "TEXT")
        if name not in dst_cols:
            conn.execute(f"ALTER TABLE {dst} ADD COLUMN {name} {typ}")
    if "archived_at" not in dst_cols:
        conn.execute(f"ALTER TABLE {dst} ADD COLUMN archived_at TEXT")


def archive() -> None:
    db = app_db()
    if not Path(db).exists():
        print(f"(no existe la base de la app en {db})")
        return
    stamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db)
    try:
        _mirror(conn, "bot_positions", "bot_positions_archive")
        _mirror(conn, "pnl_snapshots", "pnl_snapshots_archive")

        pos_cols = ",".join(r[1] for r in conn.execute("PRAGMA table_info(bot_positions)"))
        n1 = conn.execute(
            f"INSERT INTO bot_positions_archive ({pos_cols}, archived_at) "
            f"SELECT {pos_cols}, ? FROM bot_positions WHERE kalshi_env='paper'", (stamp,)
        ).rowcount
        conn.execute("DELETE FROM bot_positions WHERE kalshi_env='paper'")

        snap_cols = ",".join(r[1] for r in conn.execute("PRAGMA table_info(pnl_snapshots)"))
        n2 = conn.execute(
            f"INSERT INTO pnl_snapshots_archive ({snap_cols}, archived_at) "
            f"SELECT {snap_cols}, ? FROM pnl_snapshots WHERE env='paper'", (stamp,)
        ).rowcount
        conn.execute("DELETE FROM pnl_snapshots WHERE env='paper'")

        conn.execute("DELETE FROM bot_runs WHERE kalshi_env='paper'")
        conn.execute("DELETE FROM whale_trades")
        conn.execute("DELETE FROM alerts")
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM bot_positions_archive").fetchone()[0]
        print(f"✅ Archivadas {n1} posiciones y {n2} mediciones a reserva. App limpia.")
        print(f"   Reserva total acumulada: {total} posiciones de prueba.")
    finally:
        conn.close()


def stats() -> None:
    db = app_db()
    conn = sqlite3.connect(db)
    try:
        for t in ("bot_positions_archive", "pnl_snapshots_archive"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t}: {n} filas")
            except sqlite3.OperationalError:
                print(f"  {t}: (aún no existe)")
        try:
            rows = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(pnl_usd),0) FROM bot_positions_archive WHERE resolved=1"
            ).fetchone()
            print(f"  Resueltas en reserva: {rows[0]}   P&L acumulado: ${rows[1]:+.2f}")
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true", help="mostrar lo que hay en reserva")
    args = ap.parse_args()
    if args.stats:
        stats()
    else:
        archive()
