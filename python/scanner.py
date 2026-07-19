"""Signal detection for Kalshi Bot.

Two detectors, both reading the PUBLIC Kalshi feed (no auth needed):

  • Whales   — large taker prints on the live trade tape.
  • Momentum — volume spikes / price moves / trade clusters across markets.

Each emits rows with a `confidence` in [0,100] and the market's implied price,
stored in SQLite. The trader later decides which (if any) to act on.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

import api
import db

logger = logging.getLogger(__name__)

# Momentum thresholds
MIN_VOLUME_24H = 50
MIN_VOLUME_SPIKE_RATIO = 2.0
MIN_PRICE_MOVE = 0.08
MIN_TRADE_CLUSTER_COUNT = 5
MIN_TRADE_CLUSTER_DOLLARS = 500

_CATEGORY_KEYWORDS = {
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol"),
    "economics": ("cpi", "inflation", "fed", "gdp", "jobs", "rate"),
    "politics": ("election", "president", "senate", "congress", "poll"),
    "sports": ("nba", "nfl", "mlb", "game", "match", "win"),
    "weather": ("temperature", "rain", "snow", "hurricane"),
}


def _categorize(title: str, existing: str = "") -> str:
    if existing:
        return existing.lower()
    t = (title or "").lower()
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(k in t for k in kws):
            return cat
    return "other"


def _to_float(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


# ── market sync ─────────────────────────────────────────────────────

async def sync_markets(max_pages: int = 10) -> int:
    total = 0
    cursor = None
    rows: list[dict] = []
    for _ in range(max_pages):
        params = {"limit": 200, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        data = await api.get_markets(params)
        markets = data.get("markets", []) or []
        for m in markets:
            # Kalshi market fields are *_dollars (string $, already a fraction of $1)
            # and *_fp (string counts). Titles come from title/yes_sub_title.
            rows.append({
                "ticker": m.get("ticker", ""),
                "event_ticker": m.get("event_ticker", ""),
                "title": m.get("title", "") or m.get("yes_sub_title", "") or m.get("subtitle", ""),
                "category": _categorize(m.get("title", ""), m.get("category", "")),
                "status": m.get("status", "open"),
                "close_time": m.get("close_time", ""),
                "volume": _to_float(m.get("volume_fp")),
                "volume_24h": _to_float(m.get("volume_24h_fp")),
                "yes_bid": _to_float(m.get("yes_bid_dollars")),
                "yes_ask": _to_float(m.get("yes_ask_dollars")),
                "last_price": _to_float(m.get("last_price_dollars")),
            })
        cursor = data.get("cursor")
        total += len(markets)
        if not cursor or not markets:
            break
    if rows:
        with db.get_db() as conn:
            db.upsert_markets(conn, rows)
    return total


async def sync_events() -> int:
    return 0  # events endpoint not needed for the core engine


# ── whale scan ──────────────────────────────────────────────────────

async def scan_whales(cfg: dict) -> tuple[int, list[dict]]:
    """Read the recent trade tape and flag large taker prints."""
    min_usd = float(cfg.get("min_whale_usd", 2500) or 2500)
    data = await api.get_trades({"limit": 1000})
    trades = data.get("trades", []) or []
    new_rows: list[dict] = []
    with db.get_db() as conn:
        for t in trades:
            side = (t.get("taker_side") or "yes").lower()
            # Kalshi tape fields: count_fp (string), yes/no_price_dollars (string, in $)
            price = _to_float(t.get("yes_price_dollars") if side == "yes" else t.get("no_price_dollars"))
            count = _to_float(t.get("count_fp"))
            dollar = count * price
            if dollar < min_usd:
                continue
            ticker = t.get("ticker", "")
            market = db.get_market(conn, ticker) or {}
            # Confidence = implied price + a size bonus (bigger prints = more signal).
            implied = price * 100.0
            size_bonus = min(15.0, dollar / min_usd * 5.0)
            confidence = min(95.0, implied + size_bonus)
            row = {
                "ticker": ticker,
                "event_ticker": market.get("event_ticker", "") or t.get("event_ticker", ""),
                "title": market.get("title", "") or ticker,
                "category": market.get("category", "") or _categorize(ticker),
                "taker_side": side,
                "price": price,
                "dollar_value": dollar,
                "confidence": confidence,
                "trade_id": str(t.get("trade_id", "")),
            }
            wid = db.insert_whale(conn, row)
            if wid:
                row["id"] = wid
                new_rows.append(row)
    return len(new_rows), new_rows


# ── momentum scan ───────────────────────────────────────────────────

async def scan_momentum(cfg: dict) -> tuple[int, list[dict]]:
    """Detect trade clusters from the tape, biased contrarian if configured."""
    contrarian = bool(cfg.get("contrarian_only", True))
    data = await api.get_trades({"limit": 1000})
    trades = data.get("trades", []) or []

    clusters: dict[str, dict] = defaultdict(lambda: {"count": 0, "dollars": 0.0, "yes": 0, "no": 0})
    for t in trades:
        ticker = t.get("ticker", "")
        if not ticker:
            continue
        side = (t.get("taker_side") or "yes").lower()
        price = _to_float(t.get("yes_price_dollars") if side == "yes" else t.get("no_price_dollars"))
        count = _to_float(t.get("count_fp"))
        c = clusters[ticker]
        c["count"] += 1
        c["dollars"] += count * price
        c[side] += 1

    new_rows: list[dict] = []
    with db.get_db() as conn:
        for ticker, c in clusters.items():
            if c["count"] < MIN_TRADE_CLUSTER_COUNT or c["dollars"] < MIN_TRADE_CLUSTER_DOLLARS:
                continue
            market = db.get_market(conn, ticker)
            if not market:
                continue
            crowd_yes = c["yes"] >= c["no"]
            direction = ("no" if crowd_yes else "yes") if contrarian else ("yes" if crowd_yes else "no")
            yes_price = _to_float(market.get("last_price"))
            confidence = min(90.0, 50.0 + (c["count"] - MIN_TRADE_CLUSTER_COUNT) * 3.0)
            row = {
                "ticker": ticker,
                "event_ticker": market.get("event_ticker", ""),
                "title": market.get("title", "") or ticker,
                "category": market.get("category", ""),
                "direction": direction,
                "price": yes_price,
                "confidence": confidence,
                "signal_type": "trade_cluster",
            }
            aid = db.insert_alert(conn, row)
            if aid:
                row["id"] = aid
                new_rows.append(row)
    return len(new_rows), new_rows


# ── resolution (settle recorded signals) ────────────────────────────

async def resolve_alerts_from_markets() -> int:
    return 0  # resolution wiring lives in trader for positions; signals settle lazily


async def resolve_whales_from_markets() -> int:
    return 0
