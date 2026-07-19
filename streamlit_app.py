"""Kalshi Bot — online paper-trading dashboard (Streamlit).

Reuses the EXACT same trading logic as the desktop app (the modules in python/),
just with a Streamlit front-end so you can watch it run online and leave it going
overnight. Paper-trading only — no API keys, zero risk, safe to host publicly.

Deploy on https://share.streamlit.io with main file `streamlit_app.py`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

# Ephemeral, writable DB location (Streamlit Cloud resets /tmp on restart).
os.environ.setdefault("KALSHI_BOT_USERDATA", "/tmp/kalshibot")
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from streamlit_autorefresh import st_autorefresh  # noqa: E402

import crypto_signal  # noqa: E402
import db  # noqa: E402
import scanner  # noqa: E402
import tennis_signal  # noqa: E402
import trader  # noqa: E402
from config import merge_with_defaults  # noqa: E402

st.set_page_config(page_title="Kalshi Bot · Online", page_icon="📈", layout="wide")

# ── Dark-neon theming to match the desktop app ──────────────────────
st.markdown("""
<style>
:root { color-scheme: dark; }
.stApp { background: #0A0A0F; }
h1, h2, h3 { font-family: 'Chakra Petch', sans-serif; letter-spacing: .02em; }
[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; }
.neon { background: linear-gradient(90deg,#6366F1,#A855F7,#EC4899);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


# ── Trading engine — runs ONCE in a background thread ───────────────
@st.cache_resource
def get_engine() -> dict:
    state = {
        "cfg": merge_with_defaults({
            "paper_trading": True,
            "tennis_favorite_enabled": True,   # default: your Polymarket strategy
            "tennis_signal_enabled": False,
            "crypto_signal_enabled": False,
            "trade_whales": False,
            "trade_momentum": True,
            "main_record_signals": True,
        }),
        "running": True, "cycles": 0, "started_at": time.time(), "last_error": "",
    }

    def worker() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run(state))

    threading.Thread(target=worker, daemon=True).start()
    return state


async def _run(state: dict) -> None:
    """The same loop the desktop backend runs — scans → paper trade → resolve."""
    db.init_db()
    try:
        await scanner.sync_markets(max_pages=10)
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"sync inicial: {e}"
    last = {k: 0.0 for k in ("market", "whale", "momentum", "crypto", "tennis", "paper", "resolve")}
    while state["running"]:
        now = time.time()
        cfg = state["cfg"]
        try:
            if now - last["market"] >= 300:
                await scanner.sync_markets(10); last["market"] = now
            if now - last["whale"] >= 120:
                await scanner.scan_whales(cfg); last["whale"] = now
            if now - last["momentum"] >= 90:
                await scanner.scan_momentum(cfg); last["momentum"] = now
            if cfg.get("crypto_signal_enabled") and now - last["crypto"] >= 8:
                await crypto_signal.scan(cfg); last["crypto"] = now
            if (cfg.get("tennis_signal_enabled") or cfg.get("tennis_favorite_enabled")) and now - last["tennis"] >= 20:
                await tennis_signal.scan(cfg); last["tennis"] = now
            if now - last["paper"] >= 20:
                await trader.paper_scan_for_trades(cfg); last["paper"] = now
            if now - last["resolve"] >= 120:
                await trader.mark_resolved_positions(cfg); last["resolve"] = now
            state["cycles"] += 1
        except Exception as e:  # noqa: BLE001
            state["last_error"] = str(e)[:160]
        await asyncio.sleep(1)


engine = get_engine()
cfg = engine["cfg"]

# ── Sidebar: strategy controls (same toggles as the app) ────────────
st.sidebar.markdown("### ⚙️ Estrategia")
cfg["tennis_favorite_enabled"] = st.sidebar.toggle(
    "🎾 Tenis favorito 90% (set decisivo)", value=cfg.get("tennis_favorite_enabled", True))
cfg["tennis_signal_enabled"] = st.sidebar.toggle(
    "🎾 Tenis en vivo (modelo)", value=cfg.get("tennis_signal_enabled", False))
cfg["crypto_signal_enabled"] = st.sidebar.toggle(
    "₿ Cripto spot", value=cfg.get("crypto_signal_enabled", False))
cfg["trade_whales"] = st.sidebar.toggle(
    "🐋 Ballenas", value=cfg.get("trade_whales", False))

up = int(time.time() - engine["started_at"])
st.sidebar.markdown("---")
st.sidebar.caption(f"▶️ corriendo · {engine['cycles']} ciclos · {up // 3600}h {(up % 3600) // 60}m")
if engine["last_error"]:
    st.sidebar.caption(f"⚠️ {engine['last_error']}")
if st.sidebar.button("🔄 Reiniciar a cero"):
    db.archive_paper_to_reserve()
    st.rerun()

# ── Read the current state from the shared SQLite DB ─────────────────
env = "paper"
try:
    with db.get_db() as conn:
        stats = db.aggregate_stats(conn, env)
        markets = db.count_active_markets(conn)
        prows = conn.execute(
            "SELECT * FROM bot_positions WHERE kalshi_env='paper' ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        positions = [dict(r) for r in prows]
except Exception:
    stats = {"wins": 0, "losses": 0, "gross_win": 0, "gross_loss": 0, "realized_pnl": 0,
             "open_cost": 0, "open_filled": 0, "pending": 0, "fees": 0}
    markets, positions = 0, []

bankroll = float(cfg.get("paper_bankroll_usd", 1000.0))
try:
    cash = trader.paper_available_cash(cfg)
except Exception:
    cash = bankroll
open_mtm = 0.0
for p in positions:
    if not p.get("resolved") and (p.get("filled_contracts") or 0) > 0 and p.get("mark_price_cents") is not None:
        open_mtm += p["filled_contracts"] * float(p["mark_price_cents"]) / 100.0
    elif not p.get("resolved"):
        open_mtm += float(p.get("cost_usd") or 0)
equity = cash + open_mtm
wl = stats["wins"] + stats["losses"]

# ── Header + metrics ────────────────────────────────────────────────
st.markdown('<h1><span class="neon">KALSHI BOT</span> · Paper Trading (online)</h1>', unsafe_allow_html=True)
st.caption("Misma lógica que la app de escritorio · sin claves · dinero virtual · seguro para dejar corriendo")

m = st.columns(6)
m[0].metric("Balance", f"${equity:,.2f}", f"{equity - bankroll:+.2f}")
m[1].metric("P&L realizado", f"${stats['realized_pnl']:+.2f}")
m[2].metric("Ganadas", stats["wins"], f"+${stats['gross_win']:.2f}")
m[3].metric("Perdidas", stats["losses"], f"-${abs(stats['gross_loss']):.2f}")
m[4].metric("Acierto", f"{stats['wins'] / wl * 100:.0f}%" if wl else "—")
m[5].metric("Abiertas", stats["open_filled"] + stats["pending"])

# ── Positions table ─────────────────────────────────────────────────
st.markdown("### Posiciones")
if positions:
    def _estado(p: dict) -> str:
        if p.get("status") == "stopped":
            return "cortada ✂"
        if p.get("resolved"):
            return "ganada ✓" if p.get("outcome_correct") == 1 else "perdida ✗"
        return p.get("status") or "—"

    def _pnl(p: dict):
        v = p.get("pnl_usd") if p.get("resolved") else None
        return None if v is None else round(v, 2)

    df = pd.DataFrame([{
        "Tipo": p.get("mtype") or "—",
        "Mercado": (p.get("event_title") or p.get("title") or p.get("ticker") or "")[:34],
        "Puesta": (p.get("yes_label") or "")[:26],
        "Lado": (p.get("direction") or "").upper(),
        "Precio": f"{p.get('limit_price_cents', 0)}c",
        "Costo": f"${float(p.get('cost_usd') or 0):.2f}",
        "P&L": _pnl(p),
        "Estado": _estado(p),
    } for p in positions])

    def _color(v):
        if isinstance(v, (int, float)):
            return "color:#22C55E" if v >= 0 else "color:#EF4444"
        return ""
    st.dataframe(
        df.style.map(_color, subset=["P&L"]).format({"P&L": lambda v: "—" if v is None else f"${v:+.2f}"}),
        use_container_width=True, hide_index=True, height=430,
    )
else:
    st.info("Sin posiciones aún — el bot está escaneando. Con la estrategia de favorito, "
            "solo entra cuando hay un partido de hombres en set decisivo con un favorito ≥90%.")

st.caption(f"Mercados en vivo: {markets} · comisiones acumuladas: ${stats['fees']:.2f}")

# Keep the app awake + live (refresh every 6s while the tab is open).
st_autorefresh(interval=6000, key="refresh")
