"""Live-score tennis signal engine for Kalshi Bot.

Like the crypto-spot engine, this generates our OWN probability from an external
truth source — the LIVE MATCH SCORE (ESPN's public scoreboard, no API key) —
and compares it to Kalshi's price. Edge appears when the market's price lags the
live score (e.g. a break of serve just happened and Kalshi hasn't repriced yet).

Flow:
  1. Pull live ATP + WTA matches from ESPN (sets + current-set games per player).
  2. Match each open Kalshi tennis market (by player name) to a live match.
  3. Estimate P(the market's player wins) from the in-play score.
  4. If our model beats the market price by a real margin, emit a signal.

The in-play model is a simple heuristic (sets lead + break lead), not a full
point-by-point simulation — it's meant to catch clear mispricings, not to be
a perfect tennis model. Validate with paper trading before trusting it.
"""
from __future__ import annotations

import logging
import math

import httpx

import api
import db

logger = logging.getLogger(__name__)

ESPN_URLS = [
    "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
    "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard",
]
KALSHI_SERIES = ["KXATPMATCH", "KXWTAMATCH", "KXATPCHALLENGERMATCH", "KXWTACHALLENGERMATCH"]


def _last(name: str) -> str:
    return (name or "").strip().split()[-1].lower() if name else ""


async def _fetch_live_matches() -> list[dict]:
    """Live matches from ESPN: [{players:[{name,last,games_per_set,winner}], state}]."""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=8.0) as client:
        for url in ESPN_URLS:
            try:
                j = (await client.get(url)).json()
            except Exception:
                continue
            for ev in j.get("events", []) or []:
                for grp in ev.get("groupings", []) or []:
                    for m in grp.get("competitions", []) or []:
                        state = m.get("status", {}).get("type", {}).get("state")
                        comps = m.get("competitors", []) or []
                        if len(comps) != 2:
                            continue
                        players = []
                        for cp in comps:
                            ath = cp.get("athlete") or {}
                            nm = ath.get("displayName") or ath.get("shortName") or ""
                            gps = [float(s.get("value") or 0) for s in (cp.get("linescores") or [])]
                            players.append({"name": nm, "last": _last(nm),
                                            "games_per_set": gps, "winner": bool(cp.get("winner"))})
                        out.append({"players": players, "state": state})
    return out


def _sets_and_current(p_ls: list[float], o_ls: list[float]) -> tuple[int, int, int]:
    """From both players' per-set game counts, return (my_sets, opp_sets,
    current_set_game_diff). A set is 'won' at 6+ games by 2, or 7."""
    my = opp = 0
    game_diff = 0
    n = max(len(p_ls), len(o_ls))
    for i in range(n):
        pg = p_ls[i] if i < len(p_ls) else 0
        og = o_ls[i] if i < len(o_ls) else 0
        completed = ((pg >= 6 or og >= 6) and abs(pg - og) >= 2) or pg == 7 or og == 7
        if completed:
            if pg > og:
                my += 1
            elif og > pg:
                opp += 1
        else:
            game_diff = int(pg - og)  # the in-progress set
    return my, opp, game_diff


def win_prob(sets_diff: int, game_diff: int) -> float:
    """In-play win probability (best-of-3 heuristic): a set lead is worth a lot,
    a break lead within the current set a bit."""
    score = 1.6 * sets_diff + 0.25 * game_diff
    return 1.0 / (1.0 + math.exp(-score))


def _to_frac(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


async def scan(cfg: dict) -> tuple[int, list[dict]]:
    """Match live tennis to open Kalshi markets and emit signals from whichever
    engine is on: the model (mispricing) and/or the favourite (decisive set)."""
    model_on = bool(cfg.get("tennis_signal_enabled"))
    fav_on = bool(cfg.get("tennis_favorite_enabled"))
    if not (model_on or fav_on):
        return 0, []
    min_edge = float(cfg.get("tennis_signal_min_edge", 8) or 8)
    min_conf = float(cfg.get("tennis_signal_min_conf", 62) or 62) / 100.0
    fav_price = float(cfg.get("tennis_favorite_min_price", 0.90) or 0.90)
    fav_min_set = int(cfg.get("tennis_favorite_min_set", 3) or 3)

    matches = await _fetch_live_matches()
    live = [m for m in matches if m["state"] == "in"]
    if not live:
        return 0, []
    # index players by last name → (their games_per_set, opponent games_per_set)
    idx: dict[str, tuple[list, list]] = {}
    for m in live:
        p0, p1 = m["players"]
        if p0["last"]:
            idx[p0["last"]] = (p0["games_per_set"], p1["games_per_set"])
        if p1["last"]:
            idx[p1["last"]] = (p1["games_per_set"], p0["games_per_set"])

    def _base(mk, direction, price, conf, stype):
        return {
            "ticker": mk.get("ticker", ""), "event_ticker": mk.get("event_ticker", ""),
            "title": mk.get("title", "") or mk.get("ticker", ""), "category": "tennis",
            "direction": direction, "price": price, "confidence": round(conf, 1),
            "signal_type": stype,
        }

    new_rows: list[dict] = []
    for series in KALSHI_SERIES:
        men = series.startswith("KXATP")   # ATP = men's; WTA excluded from favourite
        try:
            data = await api.get_markets({"series_ticker": series, "status": "open", "limit": 200})
        except Exception:
            continue
        for mk in data.get("markets", []) or []:
            player = mk.get("yes_sub_title") or ""
            last = _last(player)
            if last not in idx:
                continue
            my_ls, opp_ls = idx[last]
            yes_frac = _to_frac(mk.get("last_price_dollars"))
            if yes_frac <= 0.02 or yes_frac >= 0.98:
                continue
            set_num = max(len(my_ls), len(opp_ls))
            row = None
            log = ""

            # ── FAVOURITE mode: heavy market favourite in the decisive set ──
            if fav_on and men and set_num >= fav_min_set:
                if yes_frac >= fav_price:
                    row = _base(mk, "yes", yes_frac, yes_frac * 100, "tennis_favorite")
                    log = f"tennis-fav: {player} (favorito {yes_frac*100:.0f}%, set {set_num})"
                elif yes_frac <= 1.0 - fav_price:
                    row = _base(mk, "no", yes_frac, (1 - yes_frac) * 100, "tennis_favorite")
                    log = f"tennis-fav: contra {player} (favorito {(1-yes_frac)*100:.0f}%, set {set_num})"

            # ── MODEL mode: mispricing vs our live-score model ──
            if row is None and model_on:
                my_sets, opp_sets, gdiff = _sets_and_current(my_ls, opp_ls)
                p_model = win_prob(my_sets - opp_sets, gdiff)
                max_dis = float(cfg.get("tennis_signal_max_disagreement", 25) or 25)
                if abs(p_model - yes_frac) * 100.0 <= max_dis:
                    w = float(cfg.get("tennis_signal_market_weight", 0.5) or 0.5)
                    p = w * yes_frac + (1.0 - w) * p_model
                    if (p - yes_frac) >= (yes_frac - p):
                        direction, side_p, edge = "yes", p, p - yes_frac
                    else:
                        direction, side_p, edge = "no", 1.0 - p, yes_frac - p
                    if edge * 100.0 >= min_edge and side_p >= min_conf:
                        row = _base(mk, direction, yes_frac, side_p * 100, "tennis_live")
                        log = (f"tennis-live: {player} {direction} ajustado {side_p*100:.0f}% "
                               f"(crudo {p_model*100:.0f}%) vs {yes_frac*100:.0f}c, edge {edge*100:+.0f}")

            if row:
                with db.get_db() as conn:
                    aid = db.insert_alert(conn, row)
                if aid:
                    row["id"] = aid
                    new_rows.append(row)
                    logger.info(log)
    return len(new_rows), new_rows
