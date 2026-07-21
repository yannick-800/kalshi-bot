"""Default trading configuration for Kalshi Bot.

Every runtime knob lives here with a safe default. The UI sends a config
object on every change; `merge_with_defaults` fills any missing key so old
saved configs keep working after an upgrade.

SAFE BY DEFAULT: starts on the Kalshi `demo` environment with trading OFF.
Nothing places a real order until you flip `kalshi_env` to production AND
`enable_trading` to true — two deliberate switches.
"""
from __future__ import annotations

from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    # ── Environment / master switches ───────────────────────────────
    "kalshi_env": "demo",          # "demo" | "production"
    "enable_trading": False,        # master kill-switch

    # ── Paper trading (no API key needed) ───────────────────────────
    # When on, the bot "trades" a virtual bankroll against the real, live
    # public Kalshi feed: it records the trades it WOULD place at real prices
    # and scores them win/loss as those markets resolve. Zero risk, no auth.
    "paper_trading": True,
    "paper_bankroll_usd": 1000.0,
    # Per-position stop-loss, as a fraction of cost (0 = OFF).
    # DISABLED: paper testing proved it was net-destructive on volatile 15-min
    # markets — it cut positions on intra-window noise (−50%) that then recovered
    # and would have won. Stopped trades lost −$301 while trades held to
    # resolution made +$180. The daily stop-loss remains the safety net.
    "paper_stop_loss_pct": 0.0,

    # ── Which signal engines may place trades ───────────────────────
    "trade_whales": True,
    "trade_momentum": True,

    # ── Signal quality gates ────────────────────────────────────────
    # Tuned after paper testing: the bot was chasing whales INTO favourites
    # (high entry price → tiny wins, huge losses). Now: mid-price band with a
    # favourable payoff, a real fee-net edge, and tighter risk.
    "min_edge_pts_whale": 6.0,
    "min_edge_pts_momentum": 6.0,
    "min_confidence_whale": 57.0,
    "min_confidence_momentum": 57.0,
    "fee_aware_edge": True,          # subtract Kalshi taker fee from edge
    "max_entry_slippage_cents": 4,
    "min_market_volume": 1000.0,   # mercados finos dan malos fills
    "min_entry_price_cents": 30,     # skip pure longshots
    "max_entry_price_cents": 60,     # 60c ya exige 61.7% de acierto para empatar
    "max_resolution_days": 30,
    "max_resolution_hours": 0.0,     # 0 = off; >0 only trades markets closing soon
    "strategy_preset": "Conservadora",  # informational label for the active preset
    "allowed_momentum_signal_types": ["trade_cluster", "crypto_spot", "tennis_live", "tennis_favorite"],
    "allowed_categories": None,      # None = all
    "contrarian_only": True,

    # ── Spot-momentum crypto signal (real model edge) ───────────────
    "crypto_signal_enabled": False,
    "crypto_signal_window_min": 12,   # only trade markets closing within N min
    "crypto_signal_min_edge": 8.0,    # blended prob − market price, in points
    "crypto_signal_min_conf": 62.0,   # min blended probability of our side (%)
    "crypto_signal_market_weight": 0.7,  # trust the market this much (skill prior)

    # ── Live-score tennis signal (real model edge from ESPN scores) ──
    "tennis_signal_enabled": False,
    "tennis_signal_min_edge": 6.0,    # blended prob − market price, in points
    "tennis_signal_min_conf": 58.0,   # min blended probability of our side (%)
    "tennis_signal_market_weight": 0.5,  # 50/50 model vs market (skill prior)
    "tennis_signal_max_disagreement": 25.0,  # skip EXTREME model-vs-market gaps
    # (a >25pt gap usually means the market knows a skill edge our score model
    # ignores; a moderate gap after a break is more likely a real price lag).

    # ── "Favourite in the decisive set" (the Polymarket play) ───────
    # Bet the heavy market favourite once the match reaches the Nth set. No
    # model — just the favourite-longshot bias: 90% favourites are slightly
    # underpriced. MEN's matches only (ATP), as the user found they behave.
    "tennis_favorite_enabled": False,
    "tennis_favorite_min_price": 0.90,   # market favourite ≥ this (90c)
    "tennis_favorite_min_set": 3,        # only from the Nth set on (3 = decider)

    # ── Position sizing ─────────────────────────────────────────────
    "sizing_mode": "percent",        # "percent" | "fixed"
    "fixed_trade_usd": 5.0,
    # Smaller, more uniform bets so no single loss is catastrophic.
    "base_size_fraction": 0.015,
    "min_size_fraction": 0.01,
    "max_size_fraction": 0.025,
    "sizing_base_edge": 8.0,
    "sizing_max_edge": 15.0,
    "hard_max_position_usd": 12.0,
    "min_cash_reserve_fraction": 0.10,

    # ── Order placement ─────────────────────────────────────────────
    "order_style": "limit_cross",    # "limit_cross" | "limit_mid" | "market"
    "cross_spread_fallback_offset": 2,
    "order_expiration_sec": 90,

    # ── Concurrency / exposure caps ─────────────────────────────────
    "max_open_positions": 25,
    "max_positions_per_event": 1,
    "max_daily_new_positions": 40,
    "unlimited_daily_new_positions": False,
    "max_total_exposure_fraction": 0.20,

    # ── Loop cadences (seconds) ─────────────────────────────────────
    "trade_scan_interval": 20,
    "position_poll_interval": 30,
    "balance_poll_interval": 60,
    "resolution_check_interval": 300,
    "whale_scan_interval": 120,
    "momentum_scan_interval": 90,
    "market_refresh_interval": 300,
    "db_cleanup_interval": 3600,
    "max_signal_age_sec": 120,

    # ── Daily risk gates ────────────────────────────────────────────
    "start_bankroll_usd": 0.0,
    "stop_loss_on_day": -60.0,       # 6% de la banca; <0 lo arma
    "stop_loss_on_day_pct": 0.0,     # OFF — the dollar value above is the real stop
    "take_profit_on_day": 0.0,       # absolute USD; >0 arms it

    # ── Trading-hours window ────────────────────────────────────────
    "trading_hours_enabled": False,
    "trading_hours_start": "00:00",
    "trading_hours_end": "23:59",
    "trading_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "trading_timezone_offset_min": 0,

    # ── Whale detector ──────────────────────────────────────────────
    "min_whale_usd": 2500.0,

    # ── Recording ───────────────────────────────────────────────────
    "main_record_signals": True,
}


def merge_with_defaults(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return a full config: defaults overlaid with the caller's values.

    Unknown keys from the caller are preserved (forward-compat); missing keys
    fall back to the default so the engine never KeyErrors on a stale config.
    """
    merged = dict(DEFAULT_CONFIG)
    if cfg:
        merged.update(cfg)
    return merged
