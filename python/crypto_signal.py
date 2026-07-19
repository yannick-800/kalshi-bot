"""Spot-momentum signal engine for Kalshi Bot.

Instead of imitating whales, this generates our OWN directional signal on the
short-horizon crypto markets (BTC/ETH 15-minute "above/below strike" markets)
from the REAL underlying spot price:

  1. Sample the live spot (Coinbase, public, no key) into a rolling buffer.
  2. Estimate short-term drift (momentum) and volatility per minute.
  3. For each open 15m market, project the spot to the market's close time and
     compute P(settles YES) with a normal model around the strike.
  4. If our model probability beats the market's implied price by a real margin
     (edge), emit a signal on the mispriced side.

The edge here is a genuine model-vs-market gap, not a size heuristic — this is
the honest path to an actual edge. It still passes through every risk gate.
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from datetime import datetime, timezone

import httpx

import api
import db

logger = logging.getLogger(__name__)

# Kalshi series → Coinbase spot pair
ASSETS = {
    "KXBTC15M": "BTC-USD",
    "KXETH15M": "ETH-USD",
    "KXSOL15M": "SOL-USD",
}

# rolling spot buffer: series -> list[(ts_epoch, price)]
_buf: dict[str, list[tuple[float, float]]] = {}
_BUF_SECONDS = 600.0        # keep 10 min of samples
_MIN_SIGMA_PER_MIN = 0.0004  # floor on 1-min vol (~0.04%) so we never over-trust


async def _fetch_spot(client: httpx.AsyncClient, pair: str) -> float | None:
    try:
        r = await client.get(f"https://api.coinbase.com/v2/prices/{pair}/spot", timeout=6.0)
        return float(r.json()["data"]["amount"])
    except Exception:
        return None


async def update_spots(only: set[str] | None = None) -> None:
    """Append one fresh spot sample per asset to the rolling buffer."""
    now = time.time()
    series = [s for s in ASSETS if (only is None or s in only)]
    async with httpx.AsyncClient() as client:
        for s in series:
            p = await _fetch_spot(client, ASSETS[s])
            if p is None or p <= 0:
                continue
            buf = _buf.setdefault(s, [])
            buf.append((now, p))
            cutoff = now - _BUF_SECONDS
            _buf[s] = [(t, x) for (t, x) in buf if t >= cutoff]


def _drift_vol(series: str) -> tuple[float, float, float] | None:
    """(last_price, drift_per_min, sigma_per_min) from the buffer, or None."""
    buf = _buf.get(series) or []
    if len(buf) < 4:
        return None
    rets: list[float] = []
    for (t0, p0), (t1, p1) in zip(buf, buf[1:]):
        dt = (t1 - t0) / 60.0
        if dt > 0 and p0 > 0:
            rets.append((p1 - p0) / p0 / dt)  # per-minute return
    if len(rets) < 3:
        return None
    drift = statistics.fmean(rets)
    sigma = max(_MIN_SIGMA_PER_MIN, statistics.pstdev(rets))
    return buf[-1][1], drift, sigma


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _minutes_to_close(close_time: str) -> float | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            ct = datetime.strptime(close_time, fmt)
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            return (ct - datetime.now(timezone.utc)).total_seconds() / 60.0
        except ValueError:
            continue
    return None


# Short-term drift is mostly noise over a 15-min window; extrapolating it raw
# makes the model wildly overconfident. Damp it hard and cap the projected move.
_DRIFT_WEIGHT = 0.25
_MAX_PROJ_MOVE = 0.004  # ±0.4% cap on the momentum projection


def prob_yes(spot: float, strike: float, strike_type: str, t_min: float,
             drift_per_min: float, sigma_per_min: float,
             drift_weight: float = _DRIFT_WEIGHT) -> float:
    """Model probability the market settles YES. Driven mainly by the current
    spot-vs-strike distance relative to volatility; momentum only nudges it."""
    t = max(0.05, t_min)
    move = drift_per_min * t * drift_weight             # damped momentum
    move = max(-_MAX_PROJ_MOVE, min(_MAX_PROJ_MOVE, move))
    proj = spot * (1.0 + move)
    std = spot * sigma_per_min * math.sqrt(t)           # diffusion over remaining time
    if std <= 0:
        return 1.0 if proj >= strike else 0.0
    z = (proj - strike) / std
    p_above = _norm_cdf(z)
    # "greater_or_equal" → YES = above; "less_or_equal" → YES = below
    return p_above if "greater" in (strike_type or "greater") else (1.0 - p_above)


async def scan(cfg: dict) -> tuple[int, list[dict]]:
    """Sample spot, evaluate open 15m crypto markets, emit mispriced signals."""
    if not cfg.get("crypto_signal_enabled"):
        return 0, []
    window = float(cfg.get("crypto_signal_window_min", 12) or 12)
    min_edge = float(cfg.get("crypto_signal_min_edge", 8) or 8)
    min_conf = float(cfg.get("crypto_signal_min_conf", 62) or 62) / 100.0

    await update_spots()

    new_rows: list[dict] = []
    for series in ASSETS:
        dv = _drift_vol(series)
        if dv is None:
            continue
        spot, drift, sigma = dv
        try:
            data = await api.get_markets({"series_ticker": series, "status": "open", "limit": 200})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"crypto market fetch {series}: {e}")
            continue
        for m in data.get("markets", []) or []:
            strike = m.get("floor_strike") or m.get("cap_strike")
            if not strike:
                continue
            t_min = _minutes_to_close(m.get("close_time", ""))
            if t_min is None or t_min < 1.0 or t_min > window:
                continue
            yes_frac = _to_frac(m.get("last_price_dollars"))
            if yes_frac <= 0.02 or yes_frac >= 0.98:
                continue
            p_model = prob_yes(spot, float(strike), m.get("strike_type", "greater"),
                               t_min, drift, sigma)
            # SKILL PRIOR: anchor to the market price (it already reflects the
            # implied vol/skew) and only nudge toward our spot model.
            w = float(cfg.get("crypto_signal_market_weight", 0.7) or 0.7)
            p = w * yes_frac + (1.0 - w) * p_model
            # Edge on each side = blended prob − implied price (fraction).
            edge_yes = p - yes_frac
            edge_no = (1.0 - p) - (1.0 - yes_frac)  # == yes_frac − p
            if edge_yes >= edge_no:
                direction, side_p, edge = "yes", p, edge_yes
            else:
                direction, side_p, edge = "no", 1.0 - p, edge_no
            if edge * 100.0 < min_edge or side_p < min_conf:
                continue
            row = {
                "ticker": m.get("ticker", ""),
                "event_ticker": m.get("event_ticker", ""),
                "title": m.get("title", "") or m.get("yes_sub_title", "") or m.get("ticker", ""),
                "category": "crypto",
                "direction": direction,
                "price": yes_frac,             # market yes price (fraction)
                "confidence": round(side_p * 100.0, 1),  # model prob of our side
                "signal_type": "crypto_spot",
            }
            with db.get_db() as conn:
                aid = db.insert_alert(conn, row)
            if aid:
                row["id"] = aid
                new_rows.append(row)
    if new_rows:
        logger.info(f"crypto-spot: {len(new_rows)} señal(es) (spot BTC={_buf.get('KXBTC15M',[('',0)])[-1][1] if _buf.get('KXBTC15M') else 0:.0f})")
    return len(new_rows), new_rows


def _to_frac(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0
