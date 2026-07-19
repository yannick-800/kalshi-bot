"""Kalshi Bot backend service.

Runs as a child process of the Electron app. Speaks a tiny newline-delimited
JSON-RPC protocol over stdin/stdout:

  in  : {"type":"rpc","id":"r1","method":"setConfig","params":{...}}
  out : {"type":"rpc","id":"r1","ok":true,"result":...}   (responses)
        {"type":"event","name":"account:update","data":...} (pushes)
        {"type":"log","level":"INFO","source":"trader","msg":"..."} (logs)

A single asyncio loop drives market sync, signal scans, trade placement,
order polling and resolution. Everything is local; the only outbound calls are
to Kalshi.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── logging → stdout (JSON) + rotating file ─────────────────────────

class _StdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        source = "backend"
        if record.name.startswith("trader"):
            source = "trader"
        elif record.name.startswith("scanner"):
            source = "scanner"
        try:
            sys.stdout.write(json.dumps({
                "type": "log", "level": record.levelname, "source": source,
                "msg": msg, "ts": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def _setup_logging() -> None:
    base = os.environ.get("KALSHI_BOT_USERDATA")
    log_dir = (Path(base) / "logs") if base else (Path(__file__).resolve().parent / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "backend.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = _StdoutHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("service")

import api            # noqa: E402
import auth           # noqa: E402
import crypto_signal  # noqa: E402
import db             # noqa: E402
import scanner        # noqa: E402
import tennis_signal  # noqa: E402
import trader         # noqa: E402
from config import DEFAULT_CONFIG, merge_with_defaults  # noqa: E402


class State:
    cfg: dict[str, Any] = dict(DEFAULT_CONFIG)
    auth_ok: bool = False
    paused: bool = False
    started_at: str = ""
    active_run_id: int = 0
    last_whale_scan_at: str | None = None
    last_momentum_scan_at: str | None = None
    last_trade_scan_at: str | None = None


STATE = State()
_stdout_lock = asyncio.Lock()
_loop_task: asyncio.Task | None = None
_loop_stop: asyncio.Event | None = None


async def _send(obj: dict) -> None:
    line = json.dumps(obj, default=str) + "\n"
    async with _stdout_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


async def emit_event(name: str, data: Any = None) -> None:
    await _send({"type": "event", "name": name, "data": data})


# ── serialization helpers ───────────────────────────────────────────

def _position_row_to_js(r: dict) -> dict:
    filled = int(r.get("filled_contracts") or 0)
    mark = r.get("mark_price_cents")
    live = None
    if not r.get("resolved") and mark is not None and filled > 0:
        live = round(filled * float(mark) / 100.0 - float(r.get("cost_usd") or 0), 2)
    return {
        "id": int(r["id"]), "signalSource": r["signal_source"], "signalId": int(r["signal_id"]),
        "ticker": r["ticker"], "eventTicker": r.get("event_ticker") or "",
        "title": r.get("title") or "", "category": r.get("category") or "",
        "direction": r["direction"], "action": r.get("action") or "buy",
        "targetContracts": int(r.get("target_contracts") or 0),
        "limitPriceCents": int(r.get("limit_price_cents") or 0),
        "filledContracts": filled, "costUsd": float(r.get("cost_usd") or 0),
        "feesUsd": float(r.get("fees_usd") or 0), "status": r["status"],
        "confidence": float(r.get("confidence") or 0), "edgePts": float(r.get("edge_pts") or 0),
        "resolved": bool(r.get("resolved") or 0),
        "outcomeCorrect": int(r["outcome_correct"]) if r.get("outcome_correct") is not None else None,
        "pnlUsd": float(r["pnl_usd"]) if r.get("pnl_usd") is not None else None,
        "livePnlUsd": live, "kalshiEnv": r.get("kalshi_env") or "demo",
        "createdAt": r.get("created_at") or "", "resolvedAt": r.get("resolved_at"),
        "closeTime": r.get("close_time") or "", "marketUrl": _market_url(r["ticker"]),
        "yesLabel": r.get("yes_label") or "", "mtype": r.get("mtype") or "",
        "eventTitle": r.get("event_title") or "",
        "error": r.get("error"),
    }


def _market_url(ticker: str) -> str:
    """Public Kalshi deep-link to the specific event when we know the series
    slug (kalshi.com/markets/{series}/{slug}/{event}); the series page otherwise."""
    if not ticker:
        return "https://kalshi.com/markets"
    parts = ticker.split("-")
    series = parts[0].lower()
    slug = trader._series_meta_cache.get(parts[0], {}).get("slug", "")
    if slug and len(parts) >= 2:
        event = "-".join(parts[:-1]).lower()
        return f"https://kalshi.com/markets/{series}/{slug}/{event}"
    return f"https://kalshi.com/markets/{series}"


def _signal_row_to_js(r: dict, source: str, traded: bool) -> dict:
    if source == "whale":
        price_c = int(round(float(r.get("price") or 0) * 100))
        return {
            "id": int(r["id"]), "source": "whale", "ticker": r["ticker"],
            "eventTicker": r.get("event_ticker") or "", "title": r.get("title") or r["ticker"],
            "category": r.get("category") or "", "direction": (r.get("taker_side") or "yes").lower(),
            "priceCents": price_c, "confidence": float(r.get("confidence") or 0),
            "edgePts": float(r.get("confidence") or 0) - price_c,
            "dollarValue": float(r.get("dollar_value") or 0),
            "createdAt": r.get("created_at") or "", "traded": traded,
        }
    direction = (r.get("direction") or "yes").lower()
    yes_c = int(round(float(r.get("price") or 0) * 100))
    cost_c = yes_c if direction == "yes" else max(0, 100 - yes_c)
    return {
        "id": int(r["id"]), "source": "momentum", "ticker": r["ticker"],
        "eventTicker": r.get("event_ticker") or "", "title": r.get("title") or r["ticker"],
        "category": r.get("category") or "", "direction": direction, "priceCents": cost_c,
        "confidence": float(r.get("confidence") or 0),
        "edgePts": float(r.get("confidence") or 0) - cost_c,
        "signalType": r.get("signal_type") or "", "createdAt": r.get("created_at") or "",
        "traded": traded,
    }


async def _build_account_snapshot() -> dict:
    paper_mode = bool(STATE.cfg.get("paper_trading")) and not STATE.auth_ok
    if paper_mode:
        env = trader.PAPER_ENV
        cash_usd = trader.paper_available_cash(STATE.cfg)
    else:
        env = auth.get_env()
        cash_cents = 0
        if STATE.auth_ok:
            try:
                await trader.refresh_balance(STATE.cfg, force=False)
            except Exception:
                pass
            bal = trader.cached_balance(env)
            if bal is not None:
                cash_cents = int(bal.get("cents", 0))
        cash_usd = cash_cents / 100.0
    with db.get_db() as conn:
        stats = db.aggregate_stats(conn, env)
    port_usd = stats["open_cost"]
    total = cash_usd + port_usd
    wl = stats["wins"] + stats["losses"]
    return {
        "cashUsd": cash_usd, "portfolioUsd": port_usd, "totalUsd": total,
        "realizedPnlUsd": stats["realized_pnl"], "feesUsd": stats["fees"],
        "wins": stats["wins"], "losses": stats["losses"],
        "winsUsd": stats["gross_win"], "lossesUsd": stats["gross_loss"],
        "winRate": (stats["wins"] / wl * 100.0) if wl else 0.0,
        "todayWins": stats["today_wins"], "todayLosses": stats["today_losses"],
        "openCount": stats["open_filled"], "pendingCount": stats["pending"],
        "resolvedCount": stats["resolved_count"], "totalOpened": stats["total_opened"],
        "env": env,
    }


# ── main loop ───────────────────────────────────────────────────────

async def _loop() -> None:
    last = dict(whale=0.0, momentum=0.0, trade=0.0, paper=0.0, poll=0.0, resolve=0.0,
                market=0.0, account=0.0, cleanup=0.0, snapshot=0.0,
                crypto_sig=0.0, tennis_sig=0.0)
    try:
        cnt = await scanner.sync_markets(max_pages=10)
        logger.info(f"sync inicial de mercados: {cnt} mercados")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"falló el sync inicial de mercados: {e}")

    while not (_loop_stop and _loop_stop.is_set()):
        now = asyncio.get_event_loop().time()
        cfg = STATE.cfg
        if STATE.paused:
            await asyncio.sleep(1)
            continue

        try:
            if now - last["market"] >= float(cfg.get("market_refresh_interval", 300)):
                await scanner.sync_markets(max_pages=10)
                last["market"] = now
        except Exception as e:  # noqa: BLE001
            logger.warning(f"market sync error: {e}")

        try:
            if now - last["whale"] >= float(cfg.get("whale_scan_interval", 120)):
                cnt, rows = await scanner.scan_whales(cfg)
                last["whale"] = now
                STATE.last_whale_scan_at = datetime.now(timezone.utc).isoformat()
                if cnt:
                    logger.info(f"escaneo ballenas: {cnt} nuevas")
                    last["trade"] = 0.0
                with db.get_db() as conn:
                    seen = db.already_traded_signal_ids(conn, "whale", auth.get_env())
                for row in rows:
                    await emit_event("signal:new", _signal_row_to_js(row, "whale", int(row["id"]) in seen))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"whale scan error: {e}")

        try:
            if now - last["momentum"] >= float(cfg.get("momentum_scan_interval", 90)):
                cnt, rows = await scanner.scan_momentum(cfg)
                last["momentum"] = now
                STATE.last_momentum_scan_at = datetime.now(timezone.utc).isoformat()
                if cnt:
                    logger.info(f"escaneo momentum: {cnt} nuevos")
                with db.get_db() as conn:
                    seen = db.already_traded_signal_ids(conn, "momentum", auth.get_env())
                for row in rows:
                    await emit_event("signal:new", _signal_row_to_js(row, "momentum", int(row["id"]) in seen))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"momentum scan error: {e}")

        # ── Predictive engines (our own model vs the market price) ──
        try:
            if cfg.get("crypto_signal_enabled") and now - last["crypto_sig"] >= 8:
                cnt, _ = await crypto_signal.scan(cfg)
                last["crypto_sig"] = now
                if cnt:
                    logger.info(f"cripto-spot: {cnt} señal(es)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"crypto signal error: {e}")

        try:
            if cfg.get("tennis_signal_enabled") and now - last["tennis_sig"] >= 20:
                cnt, _ = await tennis_signal.scan(cfg)
                last["tennis_sig"] = now
                if cnt:
                    logger.info(f"tenis-live: {cnt} señal(es)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tennis signal error: {e}")

        try:
            if STATE.auth_ok and now - last["trade"] >= float(cfg.get("trade_scan_interval", 20)):
                placed = await trader.scan_for_trades(cfg)
                last["trade"] = now
                STATE.last_trade_scan_at = datetime.now(timezone.utc).isoformat()
                for row in placed:
                    await emit_event("position:new", _position_row_to_js(row))
        except Exception as e:  # noqa: BLE001
            logger.error(f"trade scan error: {e}", exc_info=True)

        try:
            # Paper trading: works with NO API key, against the live public feed.
            if cfg.get("paper_trading") and now - last["paper"] >= float(cfg.get("trade_scan_interval", 20)):
                for row in await trader.paper_scan_for_trades(cfg):
                    await emit_event("position:new", _position_row_to_js(row))
                last["paper"] = now
        except Exception as e:  # noqa: BLE001
            logger.error(f"paper scan error: {e}", exc_info=True)

        try:
            if STATE.auth_ok and now - last["poll"] >= float(cfg.get("position_poll_interval", 30)):
                for row in await trader.poll_open_orders(cfg):
                    await emit_event("position:update", _position_row_to_js(row))
                last["poll"] = now
        except Exception as e:  # noqa: BLE001
            logger.error(f"poll error: {e}", exc_info=True)

        try:
            # Resolution reads only PUBLIC market results — runs without auth so
            # paper positions get scored win/loss too.
            if (STATE.auth_ok or cfg.get("paper_trading")) and now - last["resolve"] >= float(cfg.get("resolution_check_interval", 300)):
                for row in await trader.mark_resolved_positions(cfg):
                    await emit_event("position:update", _position_row_to_js(row))
                last["resolve"] = now
        except Exception as e:  # noqa: BLE001
            logger.error(f"resolution error: {e}", exc_info=True)

        try:
            if now - last["account"] >= 4:   # live-ish Panel updates
                snap = await _build_account_snapshot()
                # Persist snapshots in paper mode too (env="paper") so the daily
                # stop-loss can compute the day's P&L and the Panel chart fills.
                paper_mode = bool(cfg.get("paper_trading")) and not STATE.auth_ok
                if (STATE.auth_ok or paper_mode) and now - last["snapshot"] >= 60:
                    last["snapshot"] = now
                    snap_env = trader.PAPER_ENV if paper_mode else auth.get_env()
                    with db.get_db() as conn:
                        db.insert_pnl_snapshot(
                            conn, cash_usd=snap["cashUsd"], portfolio_usd=snap["portfolioUsd"],
                            realized_pnl_usd=snap["realizedPnlUsd"], wins=snap["wins"],
                            losses=snap["losses"], open_positions=snap["openCount"] + snap["pendingCount"],
                            env=snap_env)
                await emit_event("account:update", snap)
                last["account"] = now
        except Exception as e:  # noqa: BLE001
            logger.debug(f"account snapshot error: {e}")

        try:
            if now - last["cleanup"] >= float(cfg.get("db_cleanup_interval", 3600)):
                await asyncio.get_event_loop().run_in_executor(None, db.run_maintenance)
                last["cleanup"] = now
        except Exception as e:  # noqa: BLE001
            logger.warning(f"db maintenance failed: {e}")

        await asyncio.sleep(1)


async def _start_loop() -> None:
    global _loop_task, _loop_stop
    if _loop_task and not _loop_task.done():
        return
    _loop_stop = asyncio.Event()
    _loop_task = asyncio.create_task(_loop())


async def _stop_loop() -> None:
    if _loop_stop:
        _loop_stop.set()
    if _loop_task:
        try:
            await asyncio.wait_for(_loop_task, timeout=5)
        except Exception:
            pass


# ── RPC handlers ────────────────────────────────────────────────────

async def _h_ping(_p):
    return {"pong": True, "ts": datetime.now(timezone.utc).isoformat()}


async def _h_setConfig(p):
    cfg = merge_with_defaults((p or {}).get("config") or {})
    new_env = cfg.get("kalshi_env", "demo")
    prev_env = auth.get_env()
    STATE.cfg = cfg
    logger.info(f"setConfig: env={new_env} trading={cfg.get('enable_trading')} "
                f"whales={cfg.get('trade_whales')} momentum={cfg.get('trade_momentum')}")
    if new_env != prev_env:
        auth.set_env(new_env)
        auth.reset_credential_cache()
        STATE.auth_ok = False
        if auth.credentials_present(new_env):
            try:
                auth.prime_credentials(sync_time=True)
                await api.get_balance()
                STATE.auth_ok = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"env-switch auth failed: {e}")
        await emit_event("backend:authChanged", {"authOk": STATE.auth_ok})
    return {"ok": True}


async def _h_setCredentials(p):
    p = p or {}
    auth.save_credentials(p.get("apiKey", ""), p.get("rsaPem", ""), p.get("env"))
    status = auth.credentials_status_all()
    await emit_event("credentials:changed", status)
    return status


async def _h_clearCredentials(p):
    env = (p or {}).get("env")
    auth.clear_credentials(env)
    if env in (None, auth.get_env()):
        STATE.auth_ok = False
        await emit_event("backend:authChanged", {"authOk": False})
    status = auth.credentials_status_all()
    await emit_event("credentials:changed", status)
    return status


async def _h_credentialStatus(_p):
    return auth.credentials_status_all()


async def _h_testCredentials(p):
    target = (p or {}).get("env") or auth.get_env()
    if not auth.credentials_present(target):
        raise RuntimeError(f"credentials not set for {target}")
    saved = auth.get_env()
    try:
        if target != saved:
            auth.set_env(target)
        auth.reset_credential_cache()
        auth.prime_credentials(sync_time=True)
        bal = await api.get_balance()
    finally:
        if target != saved:
            auth.set_env(saved)
            auth.reset_credential_cache()
    cents = int(bal.get("balance", 0))
    if target == auth.get_env():
        STATE.auth_ok = True
        await emit_event("backend:authChanged", {"authOk": True})
    return {"env": target, "balanceUsd": cents / 100.0}


async def _h_account(_p):
    return await _build_account_snapshot()


async def _h_pnlSeries(p):
    hours = int((p or {}).get("sinceHours", 168))
    with db.get_db() as conn:
        rows = db.get_pnl_snapshots(conn, since_hours=hours, env=auth.get_env())
    return [{"at": r["at"], "cashUsd": float(r["cash_usd"] or 0),
             "portfolioUsd": float(r["portfolio_usd"] or 0), "totalUsd": float(r["total_usd"] or 0),
             "realizedPnlUsd": float(r["realized_pnl_usd"] or 0),
             "openPositions": int(r["open_positions"] or 0)} for r in rows]


async def _h_positions(p):
    f = p or {}
    sql = "SELECT * FROM bot_positions WHERE 1=1"
    args: list = []
    if f.get("status"):
        sql += f" AND status IN ({','.join('?' for _ in f['status'])})"
        args.extend(f["status"])
    if f.get("resolved") is not None:
        sql += " AND resolved=?"
        args.append(1 if f["resolved"] else 0)
    if f.get("signalSource"):
        sql += " AND signal_source=?"
        args.append(f["signalSource"])
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(f.get("limit") or 500))
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    # Warm the series cache so market links are deep-links (not series pages).
    for series in {r["ticker"].split("-")[0] for r in rows if r["ticker"]}:
        if series not in trader._series_meta_cache:
            await trader.ensure_series_meta(series)
    return [_position_row_to_js(dict(r)) for r in rows]


async def _h_signals(p):
    f = p or {}
    src = f.get("source")
    min_conf = float(f.get("minConfidence") or 0)
    limit = int(f.get("limit") or 200)
    out: list = []
    env = auth.get_env()
    with db.get_db() as conn:
        if src in (None, "whale"):
            for r in conn.execute(
                "SELECT * FROM whale_trades WHERE confidence>=? ORDER BY created_at DESC LIMIT ?",
                (min_conf, limit)).fetchall():
                d = dict(r)
                seen = db.already_traded_signal_ids(conn, "whale", env)
                out.append(_signal_row_to_js(d, "whale", int(d["id"]) in seen))
        if src in (None, "momentum"):
            for r in conn.execute(
                "SELECT * FROM alerts WHERE confidence>=? ORDER BY created_at DESC LIMIT ?",
                (min_conf, limit)).fetchall():
                d = dict(r)
                seen = db.already_traded_signal_ids(conn, "momentum", env)
                out.append(_signal_row_to_js(d, "momentum", int(d["id"]) in seen))
    out.sort(key=lambda s: s["createdAt"], reverse=True)
    return out[:limit]


async def _h_scannerStats(_p):
    with db.get_db() as conn:
        markets = db.count_active_markets(conn)
        wt = conn.execute("SELECT COUNT(*) FROM whale_trades").fetchone()[0]
        al = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    return {
        "marketsTracked": int(markets), "whales": {"total": int(wt)},
        "momentum": {"total": int(al)}, "lastWhaleScanAt": STATE.last_whale_scan_at,
        "lastMomentumScanAt": STATE.last_momentum_scan_at, "lastTradeScanAt": STATE.last_trade_scan_at,
    }


async def _h_tradingStatus(_p):
    cfg = STATE.cfg
    paper_mode = bool(cfg.get("paper_trading")) and not STATE.auth_ok
    env = trader.PAPER_ENV if paper_mode else auth.get_env()
    checks = []
    if paper_mode:
        checks.append({"label": "Paper trading (sin clave API)", "ok": True,
                       "detail": "bankroll virtual vs. feed público en vivo"})
    else:
        checks.append({"label": "Autenticado con Kalshi", "ok": STATE.auth_ok})
        checks.append({"label": "Operaciones activadas", "ok": bool(cfg.get("enable_trading"))})
    b2, w2 = trader.is_blocked_by_trading_hours(cfg)
    checks.append({"label": "Dentro del horario de operación", "ok": not b2, "detail": w2})
    checks.append({"label": "Ballenas o momentum activados",
                   "ok": bool(cfg.get("trade_whales") or cfg.get("trade_momentum"))})
    return {"env": env, "checks": checks,
            "trading": paper_mode or (bool(cfg.get("enable_trading")) and STATE.auth_ok)}


async def _h_cancelAllOpen(_p):
    if not STATE.auth_ok:
        raise RuntimeError("not authenticated")
    return {"canceled": await trader.cancel_all_open()}


async def _h_flatten(_p):
    if not STATE.auth_ok:
        raise RuntimeError("not authenticated")
    return {"closed": await trader.cancel_all_open()}


async def _h_runOnce(p):
    action = (p or {}).get("action")
    if action == "syncMarkets":
        return {"summary": f"Synced {await scanner.sync_markets(max_pages=10)} markets"}
    if action == "pollOrders":
        return {"summary": f"Polled, {len(await trader.poll_open_orders(STATE.cfg))} updates"}
    if action == "resolveAll":
        return {"summary": f"Resolved {len(await trader.mark_resolved_positions(STATE.cfg))} positions"}
    raise ValueError(f"unknown action: {action}")


async def _h_pause(p):
    STATE.paused = bool((p or {}).get("paused", False))
    return {"paused": STATE.paused}


async def _h_botRuns(p):
    env = (p or {}).get("env")
    with db.get_db() as conn:
        rows = db.get_recent_runs(conn, env=env, limit=int((p or {}).get("limit") or 100))
        active = db.get_active_run(conn, auth.get_env()) if STATE.active_run_id else None

    def js(r):
        return {"id": int(r["id"]), "kalshiEnv": r.get("kalshi_env") or "demo",
                "startedAt": r.get("started_at") or "", "endedAt": r.get("ended_at"),
                "startTotalUsd": float(r.get("start_total_usd") or 0),
                "endTotalUsd": float(r["end_total_usd"]) if r.get("end_total_usd") is not None else None,
                "pnlUsd": float(r.get("pnl_usd") or 0), "tradesOpened": int(r.get("trades_opened") or 0),
                "tradesWon": int(r.get("trades_won") or 0), "tradesLost": int(r.get("trades_lost") or 0),
                "isActive": r.get("ended_at") is None}
    return {"runs": [js(r) for r in rows], "activeRunId": STATE.active_run_id,
            "activeRun": js(active) if active else None}


async def _h_resetPaper(_p):
    summary = await asyncio.to_thread(db.archive_paper_to_reserve)
    await emit_event("data:reset", {"summary": summary})
    await emit_event("account:update", await _build_account_snapshot())
    return {"ok": True, **summary}


async def _h_factoryReset(_p):
    await _stop_loop()
    summary = await asyncio.to_thread(db.factory_reset)
    await emit_event("data:reset", {"summary": summary})
    await emit_event("account:update", await _build_account_snapshot())
    await _start_loop()
    return {"ok": True, "deleted": summary}


async def _h_kalshiMarketUrl(p):
    p = p or {}
    env = str(p.get("env") or "production")
    host = "https://kalshi.com" if env == "production" else "https://demo.kalshi.co"
    ev = str(p.get("eventTicker") or "").lower()
    tk = str(p.get("ticker") or "").lower()
    return {"url": f"{host}/markets/{ev}" if ev else f"{host}/markets"}


async def _h_shutdown(_p):
    asyncio.create_task(_shutdown())
    return {"shutting_down": True}


# stubs so the UI never errors on optional features
async def _h_empty_status(_p):
    return {"enabled": False, "positions": [], "status": "disabled"}


_HANDLERS = {
    "ping": _h_ping, "setConfig": _h_setConfig, "setCredentials": _h_setCredentials,
    "clearCredentials": _h_clearCredentials, "credentialStatus": _h_credentialStatus,
    "testCredentials": _h_testCredentials, "account": _h_account, "pnlSeries": _h_pnlSeries,
    "positions": _h_positions, "signals": _h_signals, "scannerStats": _h_scannerStats,
    "tradingStatus": _h_tradingStatus, "cancelAllOpen": _h_cancelAllOpen, "flatten": _h_flatten,
    "runOnce": _h_runOnce, "pause": _h_pause, "botRuns": _h_botRuns,
    "factoryReset": _h_factoryReset, "resetPaper": _h_resetPaper,
    "kalshiMarketUrl": _h_kalshiMarketUrl, "shutdown": _h_shutdown,
    "crypto15mStatus": _h_empty_status, "perpsStatus": _h_empty_status,
}


async def _dispatch(msg: dict) -> None:
    req_id = msg.get("id")
    method = msg.get("method")
    handler = _HANDLERS.get(method)
    if handler is None:
        await _send({"type": "rpc", "id": req_id, "ok": False, "error": f"unknown method: {method}"})
        return
    try:
        result = await handler(msg.get("params") or {})
        await _send({"type": "rpc", "id": req_id, "ok": True, "result": result})
    except Exception as e:  # noqa: BLE001
        logger.error(f"rpc {method} failed: {e}", exc_info=True)
        await _send({"type": "rpc", "id": req_id, "ok": False, "error": str(e)})


async def _stdin_reader() -> None:
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "rpc":
            asyncio.create_task(_dispatch(msg))


async def _shutdown() -> None:
    await _stop_loop()
    await api.close_clients()
    await emit_event("backend:shutdown", {})
    os._exit(0)


async def _main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)
    STATE.started_at = datetime.now(timezone.utc).isoformat()
    db.init_db()
    logger.info("backend de Kalshi Bot iniciando")

    env = STATE.cfg.get("kalshi_env", "demo")
    try:
        auth.set_env(env)
    except Exception:
        pass
    if auth.credentials_present(env):
        try:
            auth.prime_credentials(sync_time=True)
            await api.get_balance()
            STATE.auth_ok = True
            logger.info("saved credentials verified")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"saved-credential verify failed: {e}")

    await emit_event("backend:ready", {"startedAt": STATE.started_at})
    await emit_event("backend:authChanged", {"authOk": STATE.auth_ok})
    await emit_event("credentials:changed", auth.credentials_status_all())

    await _start_loop()
    try:
        await _stdin_reader()
    finally:
        await _shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
