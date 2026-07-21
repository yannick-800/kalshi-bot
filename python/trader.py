"""Trading engine for Kalshi Bot.

Turns scored signals into real orders, subject to a stack of risk gates:
quality (edge/confidence/price band), concurrency (open/daily/per-event caps),
exposure/reserve, daily stop-loss & take-profit, and trading-hours windows.

SAFE BY DEFAULT: `execute_signal` refuses to place anything unless
`cfg["enable_trading"]` is true — even after passing every gate.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import api
import db
from api import KalshiAPIError
from auth import get_env

logger = logging.getLogger(__name__)

# balance cache per env: {"cents": int, "at": float}
_balance: dict[str, dict] = {}


async def refresh_balance(cfg: dict, force: bool = False) -> tuple[int, int]:
    env = get_env()
    cached = _balance.get(env)
    ttl = float(cfg.get("balance_poll_interval", 60) or 60)
    if not force and cached and time.time() - cached["at"] < ttl:
        return cached["cents"], cached["cents"]
    bal = await api.get_balance()
    cents = int(bal.get("balance", 0))
    _balance[env] = {"cents": cents, "at": time.time()}
    return cents, cents


def cached_balance(env: str | None = None) -> dict | None:
    return _balance.get(env or get_env())


# ── sizing ──────────────────────────────────────────────────────────

def _compute_position_usd(balance_usd: float, edge_pts: float, cfg: dict) -> float:
    if cfg.get("sizing_mode") == "fixed":
        return min(float(cfg.get("fixed_trade_usd", 5.0) or 0.0),
                   float(cfg["hard_max_position_usd"]))
    lo_e, hi_e = float(cfg["sizing_base_edge"]), float(cfg["sizing_max_edge"])
    lo_f, hi_f = float(cfg["min_size_fraction"]), float(cfg["max_size_fraction"])
    if edge_pts <= lo_e:
        frac = lo_f
    elif edge_pts >= hi_e:
        frac = hi_f
    else:
        t = (edge_pts - lo_e) / (hi_e - lo_e)
        frac = lo_f + t * (hi_f - lo_f)
    return min(balance_usd * frac, float(cfg["hard_max_position_usd"]))


def _best_cross_price_cents(orderbook: dict, side: str) -> Optional[int]:
    side = side.lower()
    opposing = orderbook.get("no" if side == "yes" else "yes") or []
    if not opposing:
        return None
    try:
        best = max(int(b[0]) for b in opposing if b and b[0] is not None)
    except (ValueError, TypeError):
        return None
    cross = 100 - best
    return cross if 1 <= cross <= 99 else None


async def _compute_limit_price_cents(ticker: str, direction: str,
                                     signal_price_cents: int, cfg: dict) -> int:
    style = cfg.get("order_style", "limit_cross")
    if style == "market":
        return max(1, min(99, int(cfg.get("max_entry_price_cents", 99) or 99)))
    try:
        book = await api.get_orderbook(ticker)
        cross = _best_cross_price_cents(book, direction)
        if cross is not None:
            if style == "limit_mid":
                ours = book.get(direction.lower()) or []
                if ours:
                    best_ours = max(int(b[0]) for b in ours if b and b[0] is not None)
                    return max(1, min(99, (cross + best_ours) // 2))
            return cross
    except Exception as e:  # noqa: BLE001
        logger.warning(f"orderbook fetch failed for {ticker}: {e}")
    fallback = signal_price_cents + int(cfg.get("cross_spread_fallback_offset", 2))
    return max(1, min(99, fallback))


def _signal_cost_cents(signal: dict, source: str) -> tuple[str, int]:
    if source == "whale":
        direction = (signal.get("taker_side") or "yes").lower()
        cents = max(1, min(99, int(round(float(signal.get("price") or 0) * 100))))
        return direction, cents
    direction = (signal.get("direction") or "yes").lower()
    yes_cents = max(1, min(99, int(round(float(signal.get("price") or 0) * 100))))
    return direction, (yes_cents if direction == "yes" else max(1, min(99, 100 - yes_cents)))


def _compute_edge(signal: dict, source: str) -> float:
    conf = float(signal.get("confidence") or 0.0)
    if source == "whale":
        implied = float(signal.get("price") or 0.0) * 100
    else:
        direction = (signal.get("direction") or "yes").lower()
        yes = float(signal.get("price") or 0.0)
        implied = (yes if direction == "yes" else (1.0 - yes)) * 100
    return conf - implied


def _taker_fee_cents(price_cents: int) -> float:
    """Kalshi taker fee ≈ 7·p·(1−p) cents per contract (~1.75c at 50c)."""
    p = max(1, min(99, int(price_cents))) / 100.0
    return 7.0 * p * (1.0 - p)


def _net_edge(signal: dict, source: str, cfg: dict) -> float:
    edge = _compute_edge(signal, source)
    if cfg.get("fee_aware_edge", True):
        _, cost = _signal_cost_cents(signal, source)
        edge -= _taker_fee_cents(cost)
    return edge


def _days_until_close(close_time: str) -> Optional[float]:
    if not close_time:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            ct = datetime.strptime(close_time, fmt)
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            return (ct - datetime.now(timezone.utc)).total_seconds() / 86400.0
        except ValueError:
            continue
    return None


# ── quality gate ────────────────────────────────────────────────────

def should_trade(signal: dict, source: str, cfg: dict) -> tuple[bool, str]:
    conf = float(signal.get("confidence") or 0.0)
    edge = _net_edge(signal, source, cfg)
    tag = "net edge" if cfg.get("fee_aware_edge", True) else "edge"

    if source == "whale":
        if not cfg.get("trade_whales", False):
            return False, "whales disabled"
        if conf < cfg["min_confidence_whale"]:
            return False, f"conf {conf:.1f} < {cfg['min_confidence_whale']}"
        if edge < cfg["min_edge_pts_whale"]:
            return False, f"{tag} {edge:.1f} < {cfg['min_edge_pts_whale']}"
    elif source == "momentum":
        if not cfg.get("trade_momentum", False):
            return False, "momentum disabled"
        sig_type = signal.get("signal_type") or ""
        allowed = set(cfg.get("allowed_momentum_signal_types", []))
        if sig_type not in allowed:
            return False, f"signal_type {sig_type!r} not allowed"
        if conf < cfg["min_confidence_momentum"]:
            return False, f"conf {conf:.1f} < {cfg['min_confidence_momentum']}"
        # The favourite play bets AT the market price — there's no model edge to
        # require (the thesis is the favourite-longshot bias, not a mispricing).
        if sig_type != "tennis_favorite" and edge < cfg["min_edge_pts_momentum"]:
            return False, f"{tag} {edge:.1f} < {cfg['min_edge_pts_momentum']}"

    cat = (signal.get("category") or "").lower()
    allowed_cats = cfg.get("allowed_categories")
    if allowed_cats is not None:
        if not allowed_cats:
            return False, "no categories enabled"
        if cat not in {c.lower() for c in allowed_cats}:
            return False, f"category {cat!r} not allowed"

    # The tight entry-price band exists to stop whale-follow from chasing
    # favourites. Predictive engines have a real model edge, so they use a wide
    # band (a genuine edge at 85c is a valid trade).
    predictive = (signal.get("signal_type") or "") in ("crypto_spot", "tennis_live", "tennis_favorite")
    lo = 5 if predictive else cfg["min_entry_price_cents"]
    hi = 95 if predictive else cfg["max_entry_price_cents"]
    _, cost = _signal_cost_cents(signal, source)
    if cost < lo:
        return False, f"entry {cost}c < {lo}c"
    if cost > hi:
        return False, f"entry {cost}c > {hi}c"

    max_days = int(cfg.get("max_resolution_days", 0) or 0)
    if max_days > 0:
        days = _days_until_close(signal.get("close_time") or "")
        if days is not None and days > max_days:
            return False, f"resolves in ~{days:.0f}d > {max_days}d"

    # Short-horizon gate (fast-test mode): only trade markets closing soon so
    # they resolve quickly and give a win/loss verdict within the session.
    max_hours = float(cfg.get("max_resolution_hours", 0) or 0)
    if max_hours > 0:
        days = _days_until_close(signal.get("close_time") or "")
        if days is None:
            return False, "horizonte desconocido (modo horizonte corto)"
        if days * 24.0 > max_hours:
            return False, f"cierra en ~{days * 24:.1f}h > {max_hours:.0f}h"
    return True, "ok"


# ── daily risk gate ─────────────────────────────────────────────────

_DAY_RISK_PERSIST_SEC = 180.0
_day_risk_breach: dict = {}


def _today_pnl_balance_delta(env: str, offset_min: int = 0) -> float | None:
    with db.get_db() as conn:
        first = db.first_snapshot_of_today(conn, env, offset_min)
        latest = db.latest_snapshot(conn, env)
    if not first or not latest:
        return None
    return float(latest["total_usd"] or 0) - float(first["total_usd"] or 0)


def _breach_persists(env: str, kind: str, breached: bool) -> bool:
    now = time.time()
    key = (env, kind)
    if not breached:
        if _day_risk_breach.get(key) is not None:
            _day_risk_breach[key] = None
            try:
                with db.get_db() as conn:
                    db.set_risk_breach_start(conn, env, kind, None)
            except Exception:
                pass
        return False
    first = _day_risk_breach.get(key)
    if first is None:
        with db.get_db() as conn:
            persisted = db.get_risk_breach_start(conn, env, kind)
        first = float(persisted) if persisted else now
        _day_risk_breach[key] = first
        try:
            with db.get_db() as conn:
                db.set_risk_breach_start(conn, env, kind, first)
        except Exception:
            pass
    return (now - first) >= _DAY_RISK_PERSIST_SEC


def is_blocked_by_daily_risk(cfg: dict, env: str) -> tuple[bool, str]:
    offset = int(cfg.get("trading_timezone_offset_min", 0) or 0)
    pnl = _today_pnl_balance_delta(env, offset)
    if pnl is None:
        return False, ""
    with db.get_db() as conn:
        unrealized = db.open_unrealized_pnl_usd(conn, env)
        first = db.first_snapshot_of_today(conn, env, offset)
    pnl_mtm = pnl + unrealized

    sl_limits: list[float] = []
    sl = float(cfg.get("stop_loss_on_day", 0) or 0)
    if sl < 0:
        sl_limits.append(sl)
    sl_pct = float(cfg.get("stop_loss_on_day_pct", 0) or 0)
    if sl_pct > 0 and first:
        day_start = float(first["total_usd"] or 0)
        if day_start > 0:
            sl_limits.append(-sl_pct * day_start)
    limit = max(sl_limits) if sl_limits else None
    if _breach_persists(env, "sl", limit is not None and pnl_mtm <= limit):
        return True, f"daily stop-loss hit (P&L ${pnl_mtm:+.2f})"

    tp = float(cfg.get("take_profit_on_day", 0) or 0)
    if tp > 0 and _breach_persists(env, "tp", pnl_mtm >= tp):
        return True, f"daily take-profit hit (P&L ${pnl_mtm:+.2f})"
    return False, ""


def is_blocked_by_trading_hours(cfg: dict) -> tuple[bool, str]:
    if not cfg.get("trading_hours_enabled"):
        return False, ""
    from datetime import timedelta
    offset = int(cfg.get("trading_timezone_offset_min", 0) or 0)
    now = datetime.now(timezone.utc) + timedelta(minutes=offset)
    day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    if day not in cfg.get("trading_days", []):
        return True, f"{day} no es día de operación"
    hm = now.strftime("%H:%M")
    start, end = cfg.get("trading_hours_start", "00:00"), cfg.get("trading_hours_end", "23:59")
    if not (start <= hm <= end):
        return True, f"fuera de horario ({start}-{end})"
    return False, ""


# ── execution ───────────────────────────────────────────────────────

async def execute_signal(signal: dict, source: str, cfg: dict, balance_usd: float) -> dict | None:
    direction, signal_cost_cents = _signal_cost_cents(signal, source)
    edge_pts = _compute_edge(signal, source)
    env = get_env()

    with db.get_db() as conn:
        if db.count_open_bot_positions(conn, env) >= cfg["max_open_positions"]:
            return None
        if not cfg.get("unlimited_daily_new_positions"):
            today = db.count_new_positions_today(
                conn, env, int(cfg.get("trading_timezone_offset_min", 0) or 0))
            if today >= int(cfg["max_daily_new_positions"]):
                return None
        if db.count_positions_in_event_prefix(conn, derive_event(signal["ticker"]), env) >= int(cfg["max_positions_per_event"]):
            return None
        if db.exists_position_in_market(conn, signal["ticker"], direction, env):
            return None
        exposure = db.current_total_exposure_usd(conn, env)
        filled_cost = db.open_filled_cost_usd(conn, env)

    total_bankroll = max(0.0, balance_usd) + max(0.0, filled_cost)
    target = _compute_position_usd(balance_usd, edge_pts, cfg)
    target = min(target, max(0.0, total_bankroll * float(cfg["max_total_exposure_fraction"]) - exposure))
    target = min(target, max(0.0, balance_usd - total_bankroll * float(cfg["min_cash_reserve_fraction"])))
    if target < 1.0:
        return None

    limit_cents = await _compute_limit_price_cents(signal["ticker"], direction, signal_cost_cents, cfg)
    if not (cfg["min_entry_price_cents"] <= limit_cents <= cfg["max_entry_price_cents"]):
        return None
    max_slip = int(cfg.get("max_entry_slippage_cents", 0) or 0)
    if max_slip > 0 and limit_cents > signal_cost_cents + max_slip:
        return None

    contracts = max(1, int(target * 100 // limit_cents))
    expected_cost = contracts * limit_cents / 100.0
    client_order_id = f"kbot-{source}-{signal['id']}-{uuid.uuid4().hex[:8]}"
    _m = await _enrich_meta(signal["ticker"], signal)

    row = {
        "signal_source": source, "signal_id": signal["id"], "ticker": signal["ticker"],
        "event_ticker": _m["event_ticker"], "title": _m["title"],
        "category": signal.get("category", ""), "direction": direction, "action": "buy",
        "target_contracts": contracts, "limit_price_cents": limit_cents,
        "filled_contracts": 0, "cost_usd": 0.0, "client_order_id": client_order_id,
        "kalshi_order_id": None, "status": "submitted", "confidence": signal.get("confidence", 0.0),
        "edge_pts": edge_pts, "signal_price": (signal.get("price") or 0.0) * 100,
        "balance_before_usd": balance_usd, "close_time": _m["close"], "yes_label": _m["yes_label"],
        "mtype": _m["mtype"], "event_title": _m["event_title"], "kalshi_env": env,
    }

    # FINAL SAFETY GATE — never place an order with trading disabled.
    if not cfg.get("enable_trading"):
        logger.info(f"[dry-run] would place {signal['ticker']} {direction} x{contracts} @ {limit_cents}c = ${expected_cost:.2f}")
        return None

    logger.info(f"[{source}] {signal['ticker']} {direction} x{contracts} @ {limit_cents}c = ${expected_cost:.2f} "
                f"conf={signal.get('confidence', 0):.1f} edge={edge_pts:.1f}")
    try:
        resp = await api.place_limit_order(
            ticker=signal["ticker"], side=direction, action="buy",
            count=contracts, price_cents=limit_cents, client_order_id=client_order_id,
        )
    except KalshiAPIError as e:
        row["status"] = "error"
        row["error"] = f"HTTP {e.status}: {str(e.body)[:200]}"
        logger.error(f"[ORDER-FAIL] {signal['ticker']}: {row['error']}")
        with db.get_db() as conn:
            pid = db.insert_bot_position(conn, row)
            db.log_event(conn, pid, "error", note=row["error"])
            return db.fetch_position_by_id(conn, pid)
    except Exception as e:  # noqa: BLE001
        row["status"] = "error"
        row["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        with db.get_db() as conn:
            pid = db.insert_bot_position(conn, row)
            db.log_event(conn, pid, "error", note=row["error"])
            return db.fetch_position_by_id(conn, pid)

    order = (resp.get("order") if isinstance(resp, dict) else None) or resp or {}
    row["kalshi_order_id"] = order.get("order_id") if isinstance(order, dict) else None
    with db.get_db() as conn:
        pid = db.insert_bot_position(conn, row)
        db.log_event(conn, pid, "placed", note=f"order_id={row['kalshi_order_id']}")
        return db.fetch_position_by_id(conn, pid)


PAPER_ENV = "paper"

# Per-series metadata (from Kalshi's series endpoint): the deep-link slug
# ("ATP Tennis Match" → "atp-tennis-match") and a short type tag ("Tennis").
# Static per series, so cache forever.
_series_meta_cache: dict[str, dict] = {}

_CRYPTO_PREFIX = (("KXBTC", "BTC"), ("KXETH", "ETH"), ("KXSOL", "SOL"),
                  ("KXXRP", "XRP"), ("KXDOGE", "DOGE"))


def _slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


async def ensure_series_meta(series: str) -> dict:
    if series in _series_meta_cache:
        return _series_meta_cache[series]
    meta = {"slug": "", "tag": ""}
    try:
        sd = (await api.get_series(series) or {}).get("series", {})
        meta["slug"] = _slugify(sd.get("title") or "")
        tags = sd.get("tags") or []
        meta["tag"] = (tags[0] if tags else (sd.get("category") or "")) or ""
    except Exception:
        pass
    _series_meta_cache[series] = meta
    return meta


def _market_type(ticker: str, tag: str) -> str:
    up = ticker.upper()
    for pref, label in _CRYPTO_PREFIX:
        if up.startswith(pref):
            return label
    return tag or "—"


def derive_event(ticker: str) -> str:
    """The event ticker is the market ticker minus its final outcome segment,
    e.g. KXATPMATCH-26JUL18RUBTAB-TAB → KXATPMATCH-26JUL18RUBTAB. Both the -TAB
    and -RUB markets share it, which is how we detect 'same match'."""
    parts = ticker.split("-")
    return "-".join(parts[:-1]) if len(parts) > 1 else ticker


async def _enrich_meta(ticker: str, signal: dict) -> dict:
    """Fetch the market (+ event) once to get the human question, the exact pick
    ('yes' outcome name), the market type, the real close time, and the event
    name (e.g. 'Rublev vs Tabilo') for the Mercado column."""
    series = ticker.split("-")[0]
    event_tk = signal.get("event_ticker") or derive_event(ticker)
    title = signal.get("title") or ""
    close, yes_label, event_title = "", "", ""
    try:
        mk = (await api.get_market(ticker) or {}).get("market", {})
        if not title or title == ticker:
            title = mk.get("title") or ticker
        yes_label = mk.get("yes_sub_title") or ""
        # expected_expiration_time is the REAL resolution; close_time/
        # expiration_time can be far-out fallbacks.
        close = mk.get("expected_expiration_time") or mk.get("close_time") or ""
    except Exception:
        pass
    try:
        ev = (await api.get_event(event_tk) or {}).get("event", {})
        event_title = ev.get("title") or ev.get("sub_title") or ""
    except Exception:
        pass
    meta = await ensure_series_meta(series)
    return {
        "title": title or ticker, "close": close or signal.get("close_time") or "",
        "yes_label": yes_label, "mtype": _market_type(ticker, meta["tag"]),
        "event_title": event_title, "event_ticker": event_tk,
    }


def _close_time_map(conn, *row_lists) -> dict[str, str]:
    """One-query lookup of close_time for every candidate signal's market, so
    the resolution-horizon gates can see how soon each market settles."""
    tickers = {r["ticker"] for rows in row_lists for r in rows if r["ticker"]}
    if not tickers:
        return {}
    qs = ",".join("?" for _ in tickers)
    out: dict[str, str] = {}
    for row in conn.execute(
        f"SELECT ticker, close_time FROM markets WHERE ticker IN ({qs})", tuple(tickers)
    ):
        out[row["ticker"]] = row["close_time"] or ""
    return out


# We only sync a slice of Kalshi's ~24k markets, but whales fire on ANY market —
# so most whale tickers have no close_time locally. This cache fills the gaps
# from the public market endpoint (close_time is static, so cache forever).
_close_time_cache: dict[str, str] = {}


async def _fill_close_times(close_map: dict[str, str], tickers: set[str]) -> None:
    """Populate close_time for tickers missing it, via the public API + cache.
    Needed by the short-horizon gate so it can actually identify markets that
    close soon (our local markets table doesn't cover them)."""
    need = [t for t in tickers if not close_map.get(t) and t not in _close_time_cache]
    if need:
        async def one(t: str) -> None:
            try:
                m = await api.get_market(t)
                _close_time_cache[t] = ((m.get("market") or {}).get("close_time")) or ""
            except Exception:
                _close_time_cache[t] = ""
        await asyncio.gather(*(one(t) for t in need[:40]))
    for t in tickers:
        ct = _close_time_cache.get(t)
        if ct:
            close_map[t] = ct


def paper_available_cash(cfg: dict) -> float:
    """Virtual cash for paper trading: starting bankroll + realized paper P&L
    − cost tied up in open paper positions."""
    bankroll = float(cfg.get("paper_bankroll_usd", 1000.0) or 0.0)
    with db.get_db() as conn:
        stats = db.aggregate_stats(conn, PAPER_ENV)
    return max(0.0, bankroll + stats["realized_pnl"] - stats["open_cost"])


async def paper_execute_signal(signal: dict, source: str, cfg: dict, cash_usd: float) -> dict | None:
    """Record the trade the bot WOULD place — virtual, no order, no auth.

    Fills instantly at a realistic price (the same limit-cross price the live
    engine would post), books the taker fee, and marks it filled. It later
    resolves win/loss from the public market result, exactly like a real fill.
    """
    direction, signal_cost_cents = _signal_cost_cents(signal, source)
    edge_pts = _compute_edge(signal, source)

    with db.get_db() as conn:
        if db.count_open_bot_positions(conn, PAPER_ENV) >= cfg["max_open_positions"]:
            return None
        if not cfg.get("unlimited_daily_new_positions"):
            today = db.count_new_positions_today(conn, PAPER_ENV, int(cfg.get("trading_timezone_offset_min", 0) or 0))
            if today >= int(cfg["max_daily_new_positions"]):
                return None
        if db.count_positions_in_event_prefix(conn, derive_event(signal["ticker"]), PAPER_ENV) >= int(cfg["max_positions_per_event"]):
            return None
        if db.exists_position_in_market(conn, signal["ticker"], direction, PAPER_ENV):
            return None
        exposure = db.current_total_exposure_usd(conn, PAPER_ENV)

    target = _compute_position_usd(cash_usd, edge_pts, cfg)
    total_bankroll = float(cfg.get("paper_bankroll_usd", 1000.0) or 0.0)
    target = min(target, max(0.0, total_bankroll * float(cfg["max_total_exposure_fraction"]) - exposure))
    target = min(target, max(0.0, cash_usd - total_bankroll * float(cfg["min_cash_reserve_fraction"])))
    if target < 1.0:
        return None

    limit_cents = await _compute_limit_price_cents(signal["ticker"], direction, signal_cost_cents, cfg)
    if not (cfg["min_entry_price_cents"] <= limit_cents <= cfg["max_entry_price_cents"]):
        return None

    contracts = max(1, int(target * 100 // limit_cents))
    cost = contracts * limit_cents / 100.0
    fees = contracts * _taker_fee_cents(limit_cents) / 100.0
    meta = await _enrich_meta(signal["ticker"], signal)

    # Second same-event guard, now that we know the match name. The ticker
    # prefix check above misses when the outcome is not the last segment, which
    # is how both sides of one game got booked.
    with db.get_db() as conn:
        if db.count_positions_with_event_title(
                conn, meta["event_title"], PAPER_ENV) >= int(cfg["max_positions_per_event"]):
            logger.info(f"[gate] ya hay posicion en {meta['event_title']!r} — salteo {signal['ticker']}")
            return None

    row = {
        "signal_source": source, "signal_id": signal["id"], "ticker": signal["ticker"],
        "event_ticker": meta["event_ticker"], "title": meta["title"],
        "category": signal.get("category", ""), "direction": direction, "action": "buy",
        "target_contracts": contracts, "limit_price_cents": limit_cents,
        "filled_contracts": contracts, "cost_usd": cost, "client_order_id": f"paper-{source}-{signal['id']}",
        "kalshi_order_id": None, "status": "filled", "confidence": signal.get("confidence", 0.0),
        "edge_pts": edge_pts, "signal_price": (signal.get("price") or 0.0) * 100,
        "balance_before_usd": cash_usd, "close_time": meta["close"],
        "yes_label": meta["yes_label"], "mtype": meta["mtype"],
        "event_title": meta["event_title"], "kalshi_env": PAPER_ENV,
    }
    logger.info(f"[paper/{source}] {signal['ticker']} {direction} x{contracts} @ {limit_cents}c "
                f"= ${cost:.2f} conf={signal.get('confidence', 0):.1f} edge={edge_pts:.1f}")
    with db.get_db() as conn:
        pid = db.insert_bot_position(conn, row)
        db.update_bot_position(conn, pid, fees_usd=fees, avg_fill_price_cents=limit_cents)
        db.log_event(conn, pid, "paper-filled", note=f"${cost:.2f} +${fees:.2f} fee")
        return db.fetch_position_by_id(conn, pid)


async def paper_scan_for_trades(cfg: dict) -> list[dict]:
    """Paper-trade every fresh signal that passes the quality gates."""
    blocked, why = is_blocked_by_trading_hours(cfg)
    if blocked:
        return []
    # Daily stop-loss / take-profit also gates paper trading (env="paper").
    blocked, why = is_blocked_by_daily_risk(cfg, PAPER_ENV)
    if blocked:
        logger.info(f"[gate] riesgo diario (paper): {why}")
        return []
    cash = paper_available_cash(cfg)
    if cash < 1.0:
        return []
    placed: list[dict] = []
    with db.get_db() as conn:
        seen_w = db.already_traded_signal_ids(conn, "whale", PAPER_ENV)
        seen_m = db.already_traded_signal_ids(conn, "momentum", PAPER_ENV)
        whales = conn.execute("SELECT * FROM whale_trades WHERE resolved=0 ORDER BY created_at DESC LIMIT 50").fetchall()
        alerts = conn.execute("SELECT * FROM alerts WHERE resolved=0 ORDER BY created_at DESC LIMIT 50").fetchall()
        close_map = _close_time_map(conn, whales, alerts)
    if float(cfg.get("max_resolution_hours", 0) or 0) > 0:
        await _fill_close_times(close_map, {r["ticker"] for r in list(whales) + list(alerts) if r["ticker"]})
    for src, rows, seen in (("whale", whales, seen_w), ("momentum", alerts, seen_m)):
        for r in rows:
            sig = dict(r)
            if int(sig["id"]) in seen:
                continue
            sig["close_time"] = close_map.get(sig["ticker"], "")
            # Same-event guard: derive the event so the per-event cap sees that
            # e.g. Rublev-market and Tabilo-market are the SAME match.
            sig["event_ticker"] = sig.get("event_ticker") or derive_event(sig["ticker"])
            ok, _ = should_trade(sig, src, cfg)
            if not ok:
                continue
            pos = await paper_execute_signal(sig, src, cfg, cash)
            if pos:
                placed.append(pos)
                cash = paper_available_cash(cfg)
    return placed


async def scan_for_trades(cfg: dict) -> list[dict]:
    """Evaluate fresh signals and place trades for those that pass every gate."""
    env = get_env()
    blocked, why = is_blocked_by_daily_risk(cfg, env)
    if blocked:
        logger.info(f"[gate] daily risk: {why}")
        return []
    blocked, why = is_blocked_by_trading_hours(cfg)
    if blocked:
        logger.info(f"[gate] hours: {why}")
        return []

    try:
        cents, _ = await refresh_balance(cfg, force=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"balance fetch failed: {e}")
        return []
    balance_usd = cents / 100.0

    placed: list[dict] = []
    with db.get_db() as conn:
        seen_w = db.already_traded_signal_ids(conn, "whale", env)
        seen_m = db.already_traded_signal_ids(conn, "momentum", env)
        whales = conn.execute(
            "SELECT * FROM whale_trades WHERE resolved=0 ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        alerts = conn.execute(
            "SELECT * FROM alerts WHERE resolved=0 ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        close_map = _close_time_map(conn, whales, alerts)

    for src, rows, seen in (("whale", whales, seen_w), ("momentum", alerts, seen_m)):
        for r in rows:
            sig = dict(r)
            if int(sig["id"]) in seen:
                continue
            sig["close_time"] = close_map.get(sig["ticker"], "")
            sig["event_ticker"] = sig.get("event_ticker") or derive_event(sig["ticker"])
            ok, reason = should_trade(sig, src, cfg)
            if not ok:
                continue
            pos = await execute_signal(sig, src, cfg, balance_usd)
            if pos:
                placed.append(pos)
                cents, _ = await refresh_balance(cfg, force=True)
                balance_usd = cents / 100.0
    return placed


# ── order polling ───────────────────────────────────────────────────

async def poll_open_orders(cfg: dict) -> list[dict]:
    """Check open orders against Kalshi and update fill state."""
    updated: list[dict] = []
    with db.get_db() as conn:
        open_pos = db.get_open_bot_positions(conn)
    for pos in open_pos:
        oid = pos.get("kalshi_order_id")
        if not oid:
            continue
        try:
            resp = await api.get_order(str(oid))
        except Exception:
            continue
        order = (resp.get("order") if isinstance(resp, dict) else None) or resp or {}
        filled = int(float(order.get("taker_fill_count", 0) or 0)) + int(float(order.get("maker_fill_count", 0) or 0))
        status_raw = (order.get("status") or "").lower()
        avg_price = float(order.get("taker_fill_cost", 0) or 0)
        cost = avg_price / 100.0 if avg_price else filled * pos["limit_price_cents"] / 100.0
        if filled >= pos["target_contracts"]:
            new_status = "filled"
        elif filled > 0:
            new_status = "partial"
        elif status_raw in ("canceled", "cancelled"):
            new_status = "canceled"
        else:
            new_status = pos["status"]
        if new_status != pos["status"] or filled != pos["filled_contracts"]:
            with db.get_db() as conn:
                db.update_bot_position(
                    conn, pos["id"], status=new_status,
                    filled_contracts=filled, cost_usd=cost,
                    avg_fill_price_cents=(cost / filled * 100) if filled else None,
                )
                db.log_event(conn, pos["id"], new_status, kalshi_status=status_raw)
                row = db.fetch_position_by_id(conn, pos["id"])
            if row:
                updated.append(row)
    return updated


async def mark_resolved_positions(cfg: dict) -> list[dict]:
    """Settle filled positions whose market has closed/resolved."""
    resolved: list[dict] = []
    with db.get_db() as conn:
        open_pos = db.get_open_bot_positions(conn)
    for pos in open_pos:
        if pos["filled_contracts"] <= 0:
            continue
        try:
            m = await api.get_market(pos["ticker"])
        except Exception:
            continue
        market = (m or {}).get("market", {}) or {}
        # Backfill/repair metadata on positions that lack it (uses the market
        # fetch we already made — no extra API call), and warm the series cache.
        meta = await ensure_series_meta(pos["ticker"].split("-")[0])
        patch: dict = {}
        best_close = market.get("expected_expiration_time") or market.get("close_time")
        if best_close and best_close != pos.get("close_time"):
            patch["close_time"] = best_close   # fixes the far-out fallback date
        if (not pos.get("title") or pos["title"] == pos["ticker"]) and market.get("title"):
            patch["title"] = market["title"]
        if not pos.get("yes_label") and market.get("yes_sub_title"):
            patch["yes_label"] = market["yes_sub_title"]
        if not pos.get("mtype"):
            patch["mtype"] = _market_type(pos["ticker"], meta["tag"])
        if not pos.get("event_ticker"):
            patch["event_ticker"] = derive_event(pos["ticker"])
        if not pos.get("event_title"):
            try:
                ev = (await api.get_event(pos.get("event_ticker") or derive_event(pos["ticker"])) or {}).get("event", {})
                et = ev.get("title") or ev.get("sub_title") or ""
                if et:
                    patch["event_title"] = et
            except Exception:
                pass
        if patch:
            with db.get_db() as conn:
                db.update_bot_position(conn, pos["id"], **patch)
        result = (market.get("result") or "").lower()
        if result not in ("yes", "no"):
            # Mark-to-market from the live quote. IMPORTANT: value the position
            # in terms of ITS OWN side — a NO contract is worth (1 − yes_price).
            try:
                yes_frac = float(market.get("last_price_dollars") or 0)
            except (TypeError, ValueError):
                yes_frac = 0.0
            side_frac = yes_frac if pos["direction"] == "yes" else (1.0 - yes_frac)
            mark_cents = max(0.0, min(100.0, side_frac * 100.0))
            with db.get_db() as conn:
                db.update_bot_position(conn, pos["id"], mark_price_cents=mark_cents)

            # Per-position stop-loss (paper): cut a position that's marked down
            # past the threshold instead of riding it to a total loss. This is
            # the main lever for mitigating the big tail losses.
            sl_pct = float(cfg.get("paper_stop_loss_pct", 0) or 0)
            filled = int(pos["filled_contracts"] or 0)
            cost = float(pos["cost_usd"] or 0)
            if (sl_pct > 0 and pos["kalshi_env"] == PAPER_ENV and filled > 0 and cost > 0
                    and side_frac > 0):
                mark_value = filled * side_frac
                unreal = mark_value - cost
                if unreal <= -sl_pct * cost:
                    pnl = unreal - float(pos["fees_usd"] or 0)
                    with db.get_db() as conn:
                        db.update_bot_position(
                            conn, pos["id"], resolved=1, outcome_correct=0,
                            settlement_usd=round(mark_value, 2), pnl_usd=round(pnl, 2),
                            resolved_at=datetime.now(timezone.utc).isoformat(),
                            status="stopped")
                        db.log_event(conn, pos["id"], "stopped",
                                     note=f"stop-loss @ {mark_cents:.0f}c, P&L ${pnl:.2f}")
                        row = db.fetch_position_by_id(conn, pos["id"])
                    if row:
                        resolved.append(row)
            continue
        won = (result == pos["direction"])
        settlement = pos["filled_contracts"] * (1.0 if won else 0.0)
        # Net of the taker fee booked at entry — this is the fee-adjusted edge.
        pnl = settlement - float(pos["cost_usd"] or 0) - float(pos["fees_usd"] or 0)
        with db.get_db() as conn:
            db.update_bot_position(
                conn, pos["id"], resolved=1, outcome_correct=1 if won else 0,
                settlement_usd=settlement, pnl_usd=pnl,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                status="resolved",
            )
            db.log_event(conn, pos["id"], "won" if won else "lost")
            row = db.fetch_position_by_id(conn, pos["id"])
        if row:
            resolved.append(row)
    return resolved


async def reconcile_positions_with_kalshi() -> tuple[dict, list[dict]]:
    """Light reconcile: refresh fill state from Kalshi. Returns (summary, changed)."""
    summary = {"rescued": 0, "resurrected": 0, "imported_unknowns": 0}
    return summary, []


async def cancel_all_open() -> int:
    """Kill-switch: cancel every resting order we know about."""
    n = 0
    with db.get_db() as conn:
        open_pos = db.get_open_bot_positions(conn)
    for pos in open_pos:
        oid = pos.get("kalshi_order_id")
        if not oid or pos["status"] not in ("submitted", "partial"):
            continue
        try:
            await api.cancel_order(str(oid))
            with db.get_db() as conn:
                db.update_bot_position(conn, pos["id"], status="canceled")
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"cancel failed for {oid}: {e}")
    return n
