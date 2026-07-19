"""Headless paper-trading runner for Kalshi Bot.

Runs the full signal → gate → (virtual) trade → resolve loop against the LIVE
public Kalshi feed, with NO API key and NO real orders. Prints a human-readable
report so you can see, over time, whether the trades the bot WOULD make come out
ahead once markets resolve — net of fees.

Usage:
    python paper.py                 # run until Ctrl-C, report every cycle
    python paper.py --minutes 30    # run for 30 minutes then stop
    python paper.py --bankroll 500  # start from a $500 virtual bankroll
    python paper.py --report        # print the current standings and exit

State persists in the same SQLite DB as the app (env label "paper"), so results
accumulate across runs and resolve as their markets settle.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

import api
import crypto_signal
import db
import scanner
import trader
from config import merge_with_defaults

logging.basicConfig(level=logging.WARNING, format="%(message)s")

RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[32m"; RED = "\033[31m"; CYAN = "\033[36m"; YELLOW = "\033[33m"; MAG = "\033[35m"


def _c(txt: str, color: str) -> str:
    return f"{color}{txt}{RESET}"


def _money(n: float, signed: bool = False) -> str:
    color = GREEN if n >= 0 else RED
    s = f"{'+' if signed and n >= 0 else ''}${n:,.2f}"
    return _c(s, color)


def print_report(cfg: dict) -> None:
    env = trader.PAPER_ENV
    bankroll = float(cfg.get("paper_bankroll_usd", 1000.0) or 0.0)
    with db.get_db() as conn:
        stats = db.aggregate_stats(conn, env)
        markets = db.count_active_markets(conn)
        whales_total = conn.execute("SELECT COUNT(*) FROM whale_trades").fetchone()[0]
        mom_total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        # All open positions for the marked-to-market math…
        all_open = conn.execute(
            """SELECT filled_contracts, cost_usd, mark_price_cents
               FROM bot_positions WHERE kalshi_env=? AND resolved=0""", (env,)).fetchall()
        # …and just the latest few for the on-screen list.
        open_rows = conn.execute(
            """SELECT ticker, title, direction, filled_contracts, limit_price_cents,
                      cost_usd, confidence, edge_pts, mark_price_cents
               FROM bot_positions WHERE kalshi_env=? AND resolved=0
               ORDER BY created_at DESC LIMIT 8""", (env,)).fetchall()
        recent_resolved = conn.execute(
            """SELECT ticker, direction, outcome_correct, pnl_usd
               FROM bot_positions WHERE kalshi_env=? AND resolved=1
               ORDER BY resolved_at DESC LIMIT 8""", (env,)).fetchall()

    realized = stats["realized_pnl"]
    open_cost = stats["open_cost"]
    cash = max(0.0, bankroll + realized - open_cost)
    # Unrealized (mark-to-market): value ALL open positions at the live quote.
    unrealized = 0.0
    open_value = 0.0
    for r in all_open:
        mark = r["mark_price_cents"]
        if mark is not None and r["filled_contracts"]:
            mv = r["filled_contracts"] * float(mark) / 100.0
            open_value += mv
            unrealized += mv - float(r["cost_usd"] or 0)
        else:
            open_value += float(r["cost_usd"] or 0)  # no mark yet → hold at cost
    equity = cash + open_value            # marked-to-market equity
    wl = stats["wins"] + stats["losses"]
    wr = (stats["wins"] / wl * 100.0) if wl else 0.0
    roi = ((equity - bankroll) / bankroll * 100.0) if bankroll else 0.0

    print("\n" + _c("━" * 66, DIM))
    print(f"{BOLD}  KALSHI BOT · PAPER TRADING{RESET}   {DIM}(virtual — no real money){RESET}")
    print(_c("━" * 66, DIM))
    print(f"  Feed        {CYAN}{markets}{RESET} live markets   "
          f"{CYAN}{whales_total}{RESET} whale · {CYAN}{mom_total}{RESET} momentum signals")
    print(f"  Bankroll    ${bankroll:,.2f}  →  equity {_money(equity)}  "
          f"({_money(equity - bankroll, True)}, {_c(f'{roi:+.1f}%', GREEN if roi >= 0 else RED)})")
    print(f"  Cash ${cash:,.2f}   in-play ${open_cost:,.2f}   fees ${stats['fees']:,.2f}")
    print(f"  Unrealized  {_money(unrealized, True)} {DIM}(open trades marked to live price){RESET}")
    print(f"  Record      {_c(str(stats['wins'])+'W', GREEN)} · {_c(str(stats['losses'])+'L', RED)}"
          f"   win rate {_c(f'{wr:.0f}%', YELLOW) if wl else _c('—', DIM)}"
          f"   realized {_money(realized, True)}")
    print(f"  Positions   {stats['open_filled']} open · {stats['resolved_count']} resolved "
          f"· {stats['total_opened']} total")

    if open_rows:
        print(_c("\n  OPEN (would-be trades)", MAG))
        for r in open_rows:
            mark = r["mark_price_cents"]
            live = ""
            if mark is not None and r["filled_contracts"]:
                lp = r["filled_contracts"] * float(mark) / 100.0 - float(r["cost_usd"] or 0)
                live = f"  live {_money(lp, True)}"
            title = (r["title"] or r["ticker"])[:38]
            print(f"    {title:<38} {r['direction']:>3} x{r['filled_contracts']:<3} "
                  f"@{r['limit_price_cents']:>2}c  edge {r['edge_pts']:+.1f}{live}")

    if recent_resolved:
        print(_c("\n  RESOLVED (recent)", MAG))
        for r in recent_resolved:
            won = r["outcome_correct"] == 1
            tag = _c("WON ", GREEN) if won else _c("LOST", RED)
            print(f"    {tag}  {r['ticker'][:34]:<34} {r['direction']:>3}  {_money(r['pnl_usd'] or 0, True)}")

    if wl == 0 and stats["total_opened"] > 0:
        print(_c("\n  ⏳ Trades placed but none resolved yet — win/loss appears as those", DIM))
        print(_c("     markets settle. Leave it running (or re-run later).", DIM))
    print(_c("━" * 66, DIM) + "\n")


async def run_cycle(cfg: dict) -> None:
    if cfg.get("crypto_signal_enabled"):
        # Spot-momentum crypto mode: our own model signal, no whale/momentum tape.
        try:
            nc, rows = await crypto_signal.scan(cfg)
            for r in rows:
                imp = r["price"] * 100 if r["direction"] == "yes" else 100 - r["price"] * 100
                print(_c(f"  ~ SEÑAL {r['ticker'][:26]} {r['direction']} modelo {r['confidence']:.0f}% "
                         f"vs mercado {imp:.0f}c (edge {r['confidence']-imp:+.1f})", DIM))
        except Exception as e:  # noqa: BLE001
            print(_c(f"  ! crypto signal: {e}", YELLOW))
    else:
        try:
            await scanner.sync_markets(max_pages=10)
        except Exception as e:  # noqa: BLE001
            print(_c(f"  ! market sync: {e}", YELLOW))
        try:
            nw, _ = await scanner.scan_whales(cfg)
            nm, _ = await scanner.scan_momentum(cfg)
            if nw or nm:
                print(_c(f"  + {nw} whale, {nm} momentum signal(s)", DIM))
        except Exception as e:  # noqa: BLE001
            print(_c(f"  ! scan: {e}", YELLOW))
    try:
        placed = await trader.paper_scan_for_trades(cfg)
        for p in placed:
            print(_c(f"  ▸ PAPER {p['ticker']} {p['direction']} x{p['filled_contracts']} "
                     f"@{p['limit_price_cents']}c  edge {p['edge_pts']:+.1f}", CYAN))
    except Exception as e:  # noqa: BLE001
        print(_c(f"  ! paper trade: {e}", YELLOW))
    try:
        resolved = await trader.mark_resolved_positions(cfg)
        for r in resolved:
            won = r.get("outcome_correct") == 1
            print((_c("  ✓ WON  ", GREEN) if won else _c("  ✗ LOST ", RED))
                  + f"{r['ticker']}  {_money(r.get('pnl_usd') or 0, True)}")
    except Exception as e:  # noqa: BLE001
        print(_c(f"  ! resolve: {e}", YELLOW))


async def main() -> None:
    ap = argparse.ArgumentParser(description="Kalshi Bot paper trading (no API key)")
    ap.add_argument("--minutes", type=float, default=0, help="run duration; 0 = until Ctrl-C")
    ap.add_argument("--bankroll", type=float, default=None, help="starting virtual bankroll USD")
    ap.add_argument("--interval", type=float, default=30, help="seconds between cycles")
    ap.add_argument("--report", action="store_true", help="print standings and exit")
    ap.add_argument("--min-whale", type=float, default=None, help="whale $ threshold (default 2500)")
    ap.add_argument("--cluster-dollars", type=float, default=None, help="momentum cluster $ (default 500)")
    ap.add_argument("--cluster-count", type=int, default=None, help="momentum cluster trade count (default 5)")
    ap.add_argument("--min-conf", type=float, default=None, help="min confidence to trade (default 55)")
    ap.add_argument("--min-edge", type=float, default=None, help="min edge pts to trade (default 6)")
    ap.add_argument("--max-hours", type=float, default=None, help="only trade markets closing within N hours (0=off)")
    ap.add_argument("--aggressive", action="store_true", help="loosen all thresholds for a livelier demo")
    ap.add_argument("--crypto", action="store_true", help="spot-momentum crypto signal mode (our own model)")
    args = ap.parse_args()

    cfg = merge_with_defaults({"paper_trading": True})
    if args.crypto:
        cfg.update({
            "crypto_signal_enabled": True, "trade_whales": False, "trade_momentum": True,
            "min_confidence_momentum": 55, "min_edge_pts_momentum": 8,
            "min_entry_price_cents": 10, "max_entry_price_cents": 90,
            "max_resolution_hours": 0, "max_resolution_days": 1,
            "crypto_signal_min_edge": 8, "crypto_signal_min_conf": 62,
        })
    if args.max_hours is not None:
        cfg["max_resolution_hours"] = args.max_hours
    if args.bankroll is not None:
        cfg["paper_bankroll_usd"] = args.bankroll
    if args.aggressive:
        cfg.update({"min_whale_usd": 300, "min_confidence_whale": 30, "min_confidence_momentum": 30,
                    "min_edge_pts_whale": 0, "min_edge_pts_momentum": 0})
        scanner.MIN_TRADE_CLUSTER_DOLLARS = 150
        scanner.MIN_TRADE_CLUSTER_COUNT = 3
    if args.min_whale is not None:
        cfg["min_whale_usd"] = args.min_whale
    if args.min_conf is not None:
        cfg["min_confidence_whale"] = args.min_conf
        cfg["min_confidence_momentum"] = args.min_conf
    if args.min_edge is not None:
        cfg["min_edge_pts_whale"] = args.min_edge
        cfg["min_edge_pts_momentum"] = args.min_edge
    if args.cluster_dollars is not None:
        scanner.MIN_TRADE_CLUSTER_DOLLARS = args.cluster_dollars
    if args.cluster_count is not None:
        scanner.MIN_TRADE_CLUSTER_COUNT = args.cluster_count

    db.init_db()
    if args.report:
        print_report(cfg)
        return

    print(_c(f"Starting paper trading — bankroll ${cfg['paper_bankroll_usd']:,.2f}, "
             f"cycle {args.interval:.0f}s. Ctrl-C to stop.", BOLD))
    deadline = time.time() + args.minutes * 60 if args.minutes > 0 else None
    cycle = 0
    try:
        while True:
            cycle += 1
            print(_c(f"\n[cycle {cycle}] scanning live Kalshi feed…", DIM))
            await run_cycle(cfg)
            print_report(cfg)
            if deadline and time.time() >= deadline:
                print(_c("Reached time limit — stopping.", BOLD))
                break
            await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        print(_c("\nStopped. Results are saved — re-run `python paper.py --report` anytime.", BOLD))
    finally:
        await api.close_clients()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
