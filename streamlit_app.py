"""Kalshi Bot — online paper-trading app (Streamlit).

Same trading logic as the desktop app (imports the python/ modules unchanged),
rebuilt to look and feel like the desktop app: dark-neon theme, sidebar pages
(Panel · Señales · Posiciones · Ajustes · Registros), the same cards, badges and
coloured tables. Paper-trading only — no keys, safe to host publicly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("KALSHI_BOT_USERDATA", "/tmp/kalshibot")
sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))

import streamlit as st  # noqa: E402
from streamlit_autorefresh import st_autorefresh  # noqa: E402

import api  # noqa: E402
import auth  # noqa: E402
import crypto_signal  # noqa: E402
import db  # noqa: E402
import scanner  # noqa: E402
import tennis_signal  # noqa: E402
import trader  # noqa: E402
from config import merge_with_defaults  # noqa: E402

st.set_page_config(page_title="Kalshi Bot", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

# ── Design system: the same tokens as the desktop app (tailwind.config + index.css) ──
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Press+Start+2P&display=swap');
:root{ color-scheme:dark;
 --void:#0A0A0F; --surface:#11111A; --surface2:#171722; --border:rgba(255,255,255,.08);
 --borderHi:rgba(255,255,255,.16); --muted:#A1A1AA; --dim:#71717A;
 --indigo:#6366F1; --purple:#A855F7; --pink:#EC4899; --win:#22C55E; --loss:#EF4444; --warn:#F59E0B; }
.stApp, [data-testid="stAppViewContainer"]{ background:var(--void); }
[data-testid="stHeader"]{ background:transparent; }
#MainMenu, footer, [data-testid="stToolbar"]{ visibility:hidden; }
html, body, [class*="css"]{ font-family:'Chakra Petch',Inter,system-ui,sans-serif; letter-spacing:.01em; color:#fff; }
.block-container{ padding-top:1.4rem; max-width:none; }
[data-testid="stSidebar"]{ background:var(--void); border-right:1px solid var(--border); }
[data-testid="stSidebar"] .block-container{ padding-top:1rem; }
/* brand */
.k-brand{ font-family:'Press Start 2P',monospace; font-size:13px; line-height:1.5;
 background:linear-gradient(90deg,#6366F1,#A855F7,#EC4899); -webkit-background-clip:text;
 -webkit-text-fill-color:transparent; }
.k-ver{ font-family:'Press Start 2P',monospace; font-size:8px; color:var(--dim); }
.k-neon{ color:#FFFFFF; -webkit-text-fill-color:#FFFFFF; font-weight:700; }
/* cards */
.k-card{ border:1px solid var(--border); background:var(--surface); border-radius:12px; padding:18px; }
.k-label{ font-size:11px; font-weight:500; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.k-val{ font-family:'JetBrains Mono',monospace; font-size:26px; font-weight:600; margin-top:4px; }
.k-sub{ font-size:12px; color:var(--dim); margin-top:2px; }
.k-sect{ font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.16em; color:var(--muted); margin:2px 0 10px; }
.win{ color:var(--win);} .loss{ color:var(--loss);} .warn{ color:var(--warn);} .dim{ color:var(--dim);} .muted{ color:var(--muted);}
/* pill / badge */
.k-badge{ display:inline-flex; align-items:center; gap:4px; border-radius:999px; border:1px solid var(--border);
 background:var(--surface2); padding:2px 9px; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
.b-win{ border-color:rgba(34,197,94,.4); background:rgba(34,197,94,.1); color:var(--win); }
.b-loss{ border-color:rgba(239,68,68,.4); background:rgba(239,68,68,.1); color:var(--loss); }
.b-warn{ border-color:rgba(245,158,11,.4); background:rgba(245,158,11,.1); color:var(--warn); }
.b-info{ border-color:rgba(99,102,241,.4); background:rgba(99,102,241,.1); color:var(--indigo); }
.b-neutral{ border-color:var(--border); background:var(--surface2); color:var(--muted); }
/* table */
table.k-tbl{ width:100%; border-collapse:collapse; font-size:13px; }
table.k-tbl th{ border-bottom:1px solid var(--border); padding:8px 10px; text-align:left; font-size:11px;
 font-weight:600; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
table.k-tbl td{ border-bottom:1px solid rgba(255,255,255,.05); padding:8px 10px; color:rgba(255,255,255,.9); }
table.k-tbl tr:hover td{ background:rgba(255,255,255,.02); }
.mono{ font-family:'JetBrains Mono',monospace; }
.chip{ display:inline-block; border-radius:6px; padding:2px 8px; font-family:'JetBrains Mono',monospace; font-weight:600; }
a.k-link{ color:var(--indigo); text-decoration:none; } a.k-link:hover{ color:var(--purple); text-decoration:underline; }
/* sidebar radio -> nav */
[data-testid="stSidebar"] [role="radiogroup"]{ gap:4px; }
[data-testid="stSidebar"] [role="radiogroup"] label{ padding:8px 12px; border-radius:8px; border:1px solid transparent; width:100%; }
[data-testid="stSidebar"] [role="radiogroup"] label:hover{ background:rgba(255,255,255,.05); }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){ background:#FFFFFF; border-color:#FFFFFF; }
/* every descendant, so the nested markdown span/p can't keep its white text */
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) *{
  color:#0A0A0F !important; -webkit-text-fill-color:#0A0A0F !important; font-weight:700; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked):hover{ background:#E8E8E8; }
[data-testid="stSidebar"] [role="radiogroup"] input{ display:none; }
/* buttons */
.stButton>button{ border:1px solid var(--border); background:var(--surface2); color:#fff; border-radius:8px; font-weight:500; }
.stButton>button:hover{ border-color:var(--borderHi); background:rgba(255,255,255,.06); }
/* selected state (active preset): white on black, unmistakable */
.stButton>button[kind="primary"]{ background:#FFFFFF; color:#0A0A0F; border-color:#FFFFFF; font-weight:700; }
.stButton>button[kind="primary"]:hover,
.stButton>button[kind="primary"]:focus{ background:#E8E8E8; color:#0A0A0F; border-color:#E8E8E8; }
/* inputs — the wrapper carries the fill, the field itself stays transparent.
   Both the testid and the older baseweb names are listed so the styling holds
   whichever Streamlit build serves the page. */
[data-testid="stNumberInputContainer"], [data-testid="stTextInputRootElement"],
[data-testid="stTextAreaRootElement"],
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
[data-baseweb="select"]>div{ background:#79797975 !important; }
[data-testid="stNumberInputField"], [data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInputStepUp"], [data-testid="stNumberInputStepDown"]{
  background:transparent !important; color:#fff !important; }
hr{ border-color:var(--border); }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ── Trading engine (runs ONCE in a background thread) ───────────────
@st.cache_resource
def get_engine() -> dict:
    logs: deque = deque(maxlen=400)

    class _H(logging.Handler):
        def emit(self, r):
            try:
                logs.append({"ts": datetime.now(timezone.utc).isoformat(),
                             "level": r.levelname, "src": r.name.split(".")[0], "msg": self.format(r)})
            except Exception:
                pass
    h = _H(); h.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger(); root.setLevel(logging.INFO)
    if not any(isinstance(x, _H) for x in root.handlers):
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    db.init_db()  # once, synchronously, before the thread or any read touches the DB
    state = {
        "cfg": merge_with_defaults({
            "paper_trading": True, "tennis_favorite_enabled": True,
            "tennis_signal_enabled": False, "crypto_signal_enabled": False,
            "trade_whales": False, "trade_momentum": True, "main_record_signals": True,
        }),
        "running": True, "cycles": 0, "started_at": time.time(), "logs": logs,
        "last_whale": None, "last_tennis": None,
    }
    threading.Thread(target=lambda: _spawn(state), daemon=True).start()
    return state


def _spawn(state):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(_run(state))


async def _run(state):
    try:
        await scanner.sync_markets(max_pages=10)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("service").warning(f"sync inicial: {e}")
    last = {k: 0.0 for k in ("market", "whale", "momentum", "crypto", "tennis", "paper", "resolve", "snap")}
    while state["running"]:
        now = time.time(); cfg = state["cfg"]
        try:
            if now - last["market"] >= 300:
                await scanner.sync_markets(10); last["market"] = now
            if now - last["whale"] >= 120:
                n, _ = await scanner.scan_whales(cfg); last["whale"] = now
                if n:
                    state["last_whale"] = datetime.now(timezone.utc).isoformat()
            if now - last["momentum"] >= 90:
                await scanner.scan_momentum(cfg); last["momentum"] = now
            if cfg.get("crypto_signal_enabled") and now - last["crypto"] >= 8:
                await crypto_signal.scan(cfg); last["crypto"] = now
            if (cfg.get("tennis_signal_enabled") or cfg.get("tennis_favorite_enabled")) and now - last["tennis"] >= 20:
                n, _ = await tennis_signal.scan(cfg); last["tennis"] = now
                if n:
                    state["last_tennis"] = datetime.now(timezone.utc).isoformat()
            if now - last["paper"] >= 20:
                await trader.paper_scan_for_trades(cfg); last["paper"] = now
            if now - last["resolve"] >= 120:
                await trader.mark_resolved_positions(cfg); last["resolve"] = now
            if now - last["snap"] >= 60:
                with db.get_db() as conn:
                    stx = db.aggregate_stats(conn, "paper")
                    cash = trader.paper_available_cash(cfg)
                    db.insert_pnl_snapshot(conn, cash_usd=cash, portfolio_usd=stx["open_cost"],
                                           realized_pnl_usd=stx["realized_pnl"], wins=stx["wins"],
                                           losses=stx["losses"], open_positions=stx["open_filled"], env="paper")
                last["snap"] = now
            state["cycles"] += 1
        except Exception as e:  # noqa: BLE001
            logging.getLogger("service").debug(f"loop: {e}")
        await asyncio.sleep(1)


engine = get_engine()
cfg = engine["cfg"]


# ── small render helpers ────────────────────────────────────────────
def usd(n, signed=False):
    if n is None:
        return "—"
    s = f"{abs(n):,.2f}"
    return f"{'-' if n < 0 else '+' if signed else ''}${s}"


def badge(txt, tone="neutral"):
    return f'<span class="k-badge b-{tone}">{txt}</span>'


# One stable colour per market type, all bright enough to read on the black
# background. Known tickers get their brand colour; anything else (Kalshi
# series tags) hashes into the palette so the same type always looks the same.
_MTYPE_COLORS = {"BTC": "#F7931A", "ETH": "#7C86FF", "SOL": "#14F195",
                 "XRP": "#38BDF8", "DOGE": "#E8B923", "TENNIS": "#A3E635"}
# No palette colour repeats one above, so an explicit type never collides.
_TYPE_PALETTE = ("#22D3EE", "#F472B6", "#FB923C", "#818CF8", "#34D399",
                 "#E879F9", "#60A5FA", "#F87171", "#2DD4BF", "#C084FC",
                 "#FDA4AF", "#4ADE80", "#93C5FD", "#FCA5A5", "#5EEAD4",
                 "#D8B4FE", "#67E8F9", "#BEF264")


def type_badge(t):
    if not t or str(t).strip() in ("", "—"):
        return '<span class="dim">—</span>'
    key = str(t).strip().upper()
    col = _MTYPE_COLORS.get(key) or _TYPE_PALETTE[zlib.crc32(key.encode()) % len(_TYPE_PALETTE)]
    h = col.lstrip("#")
    rgb = f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)}"
    return (f'<span class="k-badge" style="border-color:rgba({rgb},.45);'
            f'background:rgba({rgb},.13);color:{col}">{t}</span>')


def statcard(label, value, sub="", tone=""):
    return (f'<div class="k-card"><div class="k-label">{label}</div>'
            f'<div class="k-val {tone}">{value}</div>'
            f'<div class="k-sub">{sub}</div></div>')


def timeago(iso):
    if not iso:
        return "—"
    try:
        s = max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()))
    except Exception:
        return "—"
    if s < 60:
        return f"hace {s}s"
    if s < 3600:
        return f"hace {s // 60}m"
    if s < 86400:
        return f"hace {s // 3600}h"
    return f"hace {s // 86400}d"


def fdate(iso):
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%d %b, %H:%M")
    except Exception:
        return "—"


def market_url(ticker):
    parts = (ticker or "").split("-")
    series = parts[0].lower() if parts else ""
    slug = trader._series_meta_cache.get(parts[0], {}).get("slug", "") if parts else ""
    if slug and len(parts) >= 2:
        return f"https://kalshi.com/markets/{series}/{slug}/{'-'.join(parts[:-1]).lower()}"
    return f"https://kalshi.com/markets/{series}"


# ── data ────────────────────────────────────────────────────────────
def load():
    try:
        with db.get_db() as conn:
            stats = db.aggregate_stats(conn, "paper")
            markets = db.count_active_markets(conn)
            pos = [dict(r) for r in conn.execute(
                "SELECT * FROM bot_positions WHERE kalshi_env='paper' ORDER BY created_at DESC LIMIT 300").fetchall()]
            whales = [dict(r) for r in conn.execute(
                "SELECT * FROM whale_trades ORDER BY created_at DESC LIMIT 100").fetchall()]
            alerts = [dict(r) for r in conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100").fetchall()]
            snaps = db.get_pnl_snapshots(conn, since_hours=168, env="paper")
        return stats, markets, pos, whales, alerts, snaps
    except Exception:
        z = {"wins": 0, "losses": 0, "gross_win": 0, "gross_loss": 0, "realized_pnl": 0,
             "open_cost": 0, "open_filled": 0, "pending": 0, "fees": 0}
        return z, 0, [], [], [], []


stats, markets, positions, whales, alerts, snaps = load()
bankroll = float(cfg.get("paper_bankroll_usd", 1000.0))
try:
    cash = trader.paper_available_cash(cfg)
except Exception:
    cash = bankroll
open_mtm = sum((p["filled_contracts"] * float(p["mark_price_cents"]) / 100.0)
               if (not p.get("resolved") and p.get("filled_contracts") and p.get("mark_price_cents") is not None)
               else (float(p.get("cost_usd") or 0) if not p.get("resolved") else 0.0) for p in positions)
equity = cash + open_mtm
wl = stats["wins"] + stats["losses"]
open_ct = stats["open_filled"] + stats["pending"]


# ── sidebar (brand + nav + status, like the desktop) ────────────────
with st.sidebar:
    st.markdown('<div class="k-brand">KALSHI BOT</div><div class="k-ver">v1.0.0 · online</div><br>', unsafe_allow_html=True)
    page = st.radio("nav", ["📊  Panel", "📡  Señales", "💼  Posiciones", "⚙️  Ajustes",
                            "🔑  Claves", "📜  Registros"],
                    label_visibility="collapsed")
    st.markdown("<hr>", unsafe_allow_html=True)
    up = int(time.time() - engine["started_at"])
    dot = "🟢" if engine["cycles"] > 0 else "🟡"
    st.markdown(f'<div class="k-sub">{dot} corriendo · {engine["cycles"]} ciclos · {up//3600}h {(up%3600)//60}m</div>',
                unsafe_allow_html=True)
    _env_now = cfg.get("kalshi_env", "demo")
    _live_now = bool(cfg.get("enable_trading")) and not cfg.get("paper_trading", True)
    _cred_now = auth.credentials_present(_env_now)
    if _live_now and _cred_now:
        st.markdown('<div class="k-sub" style="color:#EF4444">⚠ EN VIVO · dinero real</div>', unsafe_allow_html=True)
    else:
        _kt = "clave guardada" if _cred_now else "sin claves"
        st.markdown(f'<div class="k-sub">Paper · dinero virtual · {_kt}</div>', unsafe_allow_html=True)


# ── top bar (badges) ────────────────────────────────────────────────
active = []
if cfg.get("tennis_favorite_enabled"):
    active.append("🎾 favorito")
if cfg.get("tennis_signal_enabled"):
    active.append("🎾 modelo")
if cfg.get("crypto_signal_enabled"):
    active.append("₿ cripto")
if cfg.get("trade_whales"):
    active.append("🐋 ballenas")
_env_badge = cfg.get("kalshi_env", "demo")
_live = bool(cfg.get("enable_trading")) and not cfg.get("paper_trading", True)
if _live:
    mode_badge = badge("PRODUCCIÓN", "loss") + " " + badge("DINERO REAL", "loss")
else:
    mode_badge = badge(_env_badge.upper(), "info") + " " + badge("PAPER · simulación", "neutral")
topbar = mode_badge + " " + \
    badge("operando en vivo" if active else "en pausa", "win" if active else "neutral")
st.markdown(f'<div style="display:flex;gap:8px;margin-bottom:10px">{topbar}</div>', unsafe_allow_html=True)

PAGE = page.split("  ", 1)[-1]

# ══════════════════════════ PANEL ══════════════════════════════════
if PAGE == "Panel":
    st.markdown('<h2 class="k-neon" style="margin:0 0 6px">Panel</h2>', unsafe_allow_html=True)
    c = st.columns(4)
    with c[0]:
        st.markdown(statcard("Balance total", usd(equity), f"efectivo {usd(cash)}"), unsafe_allow_html=True)
    with c[1]:
        st.markdown(statcard("P&L realizado", usd(stats["realized_pnl"], True),
                             f"comisiones {usd(stats['fees'])}", "win" if stats["realized_pnl"] >= 0 else "loss"),
                    unsafe_allow_html=True)
    with c[2]:
        st.markdown(statcard("Tasa de acierto", f"{stats['wins']/wl*100:.0f}%" if wl else "—",
                             f"{stats['wins']}G · {stats['losses']}P"), unsafe_allow_html=True)
    with c[3]:
        st.markdown(statcard("Posiciones abiertas", str(open_ct), f"{stats['pending']} pendientes"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    g = st.columns([2, 1])
    with g[0]:
        st.markdown('<div class="k-sect">Balance (7 días)</div>', unsafe_allow_html=True)
        if len(snaps) > 1:
            import pandas as pd
            df = pd.DataFrame({"t": [datetime.fromisoformat(s["at"].replace("Z", "+00:00")) for s in snaps],
                               "Total": [float(s["total_usd"] or 0) for s in snaps]}).set_index("t")
            st.line_chart(df, height=220, color="#A855F7")
        else:
            st.markdown('<div class="k-card dim">Aún no hay datos suficientes — se acumulan mientras corre.</div>',
                        unsafe_allow_html=True)
    with g[1]:
        st.markdown('<div class="k-sect">¿Por qué no opera?</div>', unsafe_allow_html=True)
        checks = [("Paper trading (sin clave)", True),
                  ("Algún motor activo", bool(active)),
                  ("Mercados sincronizados", markets > 0)]
        rws = ""
        for lbl, ok in checks:
            icon = "✅" if ok else "❌"
            cls = "" if ok else "muted"
            rws += f'<div style="margin:6px 0">{icon} <span class="{cls}">{lbl}</span></div>'
        st.markdown(f'<div class="k-card">{rws}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="k-card" style="margin-top:10px"><div class="k-label">Escáner</div>'
                    f'<div class="k-sub" style="margin-top:6px">{markets} mercados · {len(whales)} ballenas · {len(alerts)} señales</div></div>',
                    unsafe_allow_html=True)

# ══════════════════════════ SEÑALES ═══════════════════════════════
elif PAGE == "Señales":
    st.markdown('<h2 class="k-neon" style="margin:0 0 6px">Señales</h2>', unsafe_allow_html=True)
    sig = []
    for r in whales:
        pc = int(round(float(r.get("price") or 0) * 100))
        sig.append((r.get("created_at"), "ballena", r.get("title") or r.get("ticker"),
                    (r.get("taker_side") or "yes").upper(), pc, float(r.get("confidence") or 0)))
    for r in alerts:
        d = (r.get("direction") or "yes")
        yc = int(round(float(r.get("price") or 0) * 100))
        cc = yc if d == "yes" else max(0, 100 - yc)
        lbl = "tenis" if (r.get("signal_type") or "").startswith("tennis") else "momentum"
        sig.append((r.get("created_at"), lbl, r.get("title") or r.get("ticker"), d.upper(), cc, float(r.get("confidence") or 0)))
    sig.sort(key=lambda x: x[0] or "", reverse=True)
    if sig:
        body = "".join(
            f'<tr><td class="dim">{timeago(t)}</td><td>{badge(src,"info" if src in("ballena","tenis") else "neutral")}</td>'
            f'<td>{(title or "")[:44]}</td><td>{side}</td><td class="mono">{pc}¢</td><td class="mono">{conf:.0f}</td></tr>'
            for t, src, title, side, pc, conf in sig[:120])
        st.markdown(f'<div class="k-card" style="padding:0;overflow-x:auto"><table class="k-tbl">'
                    f'<thead><tr><th>Cuándo</th><th>Fuente</th><th>Mercado</th><th>Lado</th><th>Precio</th><th>Conf</th></tr></thead>'
                    f'<tbody>{body}</tbody></table></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="k-card dim">Sin señales aún — el escáner detecta ballenas y señales en vivo.</div>',
                    unsafe_allow_html=True)

# ══════════════════════════ POSICIONES ════════════════════════════
elif PAGE == "Posiciones":
    st.markdown('<h2 class="k-neon" style="margin:0 0 6px">Posiciones</h2>', unsafe_allow_html=True)
    gw, gl = stats["gross_win"], abs(stats["gross_loss"])
    c = st.columns(4)
    c[0].markdown(statcard("Ganadas", str(stats["wins"]), usd(gw, True), "win"), unsafe_allow_html=True)
    c[1].markdown(statcard("Perdidas", str(stats["losses"]), "-" + usd(gl), "loss"), unsafe_allow_html=True)
    c[2].markdown(statcard("Abiertas", str(open_ct), "en juego"), unsafe_allow_html=True)
    c[3].markdown(statcard("Neto", usd(stats["realized_pnl"], True),
                           f"{stats['wins']/wl*100:.0f}% acierto" if wl else "—",
                           "win" if stats["realized_pnl"] >= 0 else "loss"), unsafe_allow_html=True)
    tot = gw + gl
    if tot > 0:
        pctw = gw / tot * 100
        st.markdown(f'<div style="margin:12px 0"><div style="display:flex;justify-content:space-between;font-size:12px">'
                    f'<span class="win">Ganado {usd(gw)}</span><span class="loss">Perdido {usd(gl)}</span></div>'
                    f'<div style="display:flex;height:10px;border-radius:999px;overflow:hidden;background:var(--surface2);margin-top:4px">'
                    f'<div style="width:{pctw}%;background:var(--win)"></div><div style="width:{100-pctw}%;background:var(--loss)"></div>'
                    f'</div></div>', unsafe_allow_html=True)

    def estado(p):
        if p.get("status") == "stopped":
            return badge("cortada ✂", "loss")
        if p.get("resolved"):
            return badge("ganada ✓", "win") if p.get("outcome_correct") == 1 else badge("perdida ✗", "loss")
        m = {"filled": "warn", "partial": "warn", "submitted": "info", "error": "loss", "canceled": "loss"}
        es = {"filled": "en curso", "partial": "parcial", "submitted": "enviada",
              "error": "error", "canceled": "cancelada"}
        s = p.get("status")
        return badge(es.get(s, s or "—"), m.get(s, "neutral"))

    if positions:
        rows = ""
        for p in positions:
            if p.get("resolved"):
                pnl = p.get("pnl_usd")
            elif p.get("mark_price_cents") is not None and p.get("filled_contracts"):
                pnl = round(p["filled_contracts"] * float(p["mark_price_cents"]) / 100.0 - float(p.get("cost_usd") or 0), 2)
            else:
                pnl = None
            up = (pnl or 0) >= 0
            rgb = "34,197,94" if up else "239,68,68"
            tone = "win" if up else "loss"
            pnl_html = '<span class="dim">—</span>' if pnl is None else \
                f'<span class="chip" style="background:rgba({rgb},.15);color:var(--{tone})">{usd(pnl,True)}</span>'
            tint = "" if pnl is None else f'background:rgba({rgb},{".10" if p.get("resolved") else ".04"})'
            mkt = (p.get("event_title") or p.get("title") or p.get("ticker") or "")[:32]
            rows += (f'<tr style="{tint}"><td class="dim">{timeago(p.get("created_at"))}</td>'
                     f'<td>{type_badge(p.get("mtype"))}</td>'
                     f'<td><a class="k-link" href="{market_url(p.get("ticker"))}" target="_blank">{mkt} ↗</a></td>'
                     f'<td>{(p.get("yes_label") or "—")[:24]}</td><td>{(p.get("direction") or "").upper()}</td>'
                     f'<td class="dim">{fdate(p.get("resolved_at") if p.get("resolved") else p.get("close_time"))}</td>'
                     f'<td class="mono">{p.get("filled_contracts",0)}/{p.get("target_contracts",0)}</td>'
                     f'<td class="mono">{p.get("limit_price_cents",0)}¢</td>'
                     f'<td class="mono dim">{usd(p.get("cost_usd") or 0)}</td><td>{pnl_html}</td><td>{estado(p)}</td></tr>')
        st.markdown(f'<div class="k-card" style="padding:0;overflow-x:auto"><table class="k-tbl"><thead><tr>'
                    f'<th>Abierta</th><th>Tipo</th><th>Mercado</th><th>Puesta</th><th>Lado</th><th>Cierre</th>'
                    f'<th>Ejec.</th><th>Precio</th><th>Costo</th><th>P&L</th><th>Estado</th></tr></thead>'
                    f'<tbody>{rows}</tbody></table></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="k-card dim">Sin posiciones aún — con la estrategia de favorito solo entra en un '
                    'partido de hombres en set decisivo con un favorito ≥90%.</div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🗑  Reiniciar a cero (archiva a reserva)"):
        db.archive_paper_to_reserve()
        st.rerun()

# ══════════════════════════ AJUSTES ═══════════════════════════════
elif PAGE == "Ajustes":
    hcol = st.columns([3, 1])
    hcol[0].markdown('<h2 class="k-neon" style="margin:0">Ajustes del motor</h2>', unsafe_allow_html=True)
    if hcol[1].button("↺ Restablecer"):
        cfg.update(merge_with_defaults({"paper_trading": True, "trade_momentum": True}))
        cfg["strategy_preset"] = "Conservadora"
        st.rerun()

    _RISK = dict(min_entry_price_cents=30, max_entry_price_cents=55, min_edge_pts_whale=6,
                 min_edge_pts_momentum=6, min_confidence_whale=55, min_confidence_momentum=55,
                 hard_max_position_usd=12, max_total_exposure_fraction=0.20, stop_loss_on_day=-30)
    PRESETS = {
        "Conservadora": {**_RISK, "min_whale_usd": 500, "max_resolution_hours": 0},
        "Horizonte corto (test rápido)": {**_RISK, "min_edge_pts_whale": 3, "min_edge_pts_momentum": 3,
                                          "min_confidence_whale": 50, "min_confidence_momentum": 50,
                                          "max_entry_price_cents": 60, "min_whale_usd": 300, "max_resolution_hours": 8},
        "Agresiva (solo demo)": dict(min_entry_price_cents=15, max_entry_price_cents=85, min_edge_pts_whale=0,
                                     min_edge_pts_momentum=0, min_confidence_whale=30, min_confidence_momentum=30,
                                     hard_max_position_usd=50, max_total_exposure_fraction=0.35, stop_loss_on_day=-50,
                                     min_whale_usd=300, max_resolution_hours=0),
    }
    st.markdown('<div class="k-sect">🧪 Preset de estrategia</div>', unsafe_allow_html=True)
    st.caption("Aplican un paquete de parámetros de una vez. El preset activo se muestra abajo.")
    pc = st.columns(3)
    for i, (name, bundle) in enumerate(PRESETS.items()):
        _on = cfg.get("strategy_preset") == name
        if pc[i].button(("✓ " if _on else "") + name, use_container_width=True,
                        type="primary" if _on else "secondary"):
            cfg.update(bundle); cfg["strategy_preset"] = name; st.rerun()
    st.caption(f"Preset activo: **{cfg.get('strategy_preset', '—')}** · cambiar un valor manual no altera el nombre.")

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">Entorno</div>', unsafe_allow_html=True)
    st.markdown(badge("DEMO / PAPER", "info"), unsafe_allow_html=True)
    st.caption("La versión online es **paper** (dinero virtual, sin claves). Producción/real solo en la app de escritorio con tus claves.")

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">Motores de señal</div>', unsafe_allow_html=True)
    cfg["trade_whales"] = st.toggle("Operar ballenas — actuar sobre órdenes grandes del feed.", value=cfg.get("trade_whales", False))
    cfg["trade_momentum"] = st.toggle("Operar momentum — actuar sobre clústeres de volumen/precio.", value=cfg.get("trade_momentum", True))
    cfg["contrarian_only"] = st.toggle("Solo contrarian — ir contra la multitud en momentum.", value=cfg.get("contrarian_only", True))
    cfg["fee_aware_edge"] = st.toggle("Edge neto de comisiones — restar la comisión antes del filtro de edge.", value=cfg.get("fee_aware_edge", True))

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">🧪 Motores predictivos (experimental)</div>', unsafe_allow_html=True)
    st.caption("Generan su propia probabilidad desde una fuente en vivo y la comparan con el precio de Kalshi. Aplican al toque.")
    cfg["tennis_favorite_enabled"] = st.toggle("🎾 Tenis favorito 90% (set decisivo) — apuesta al favorito del mercado en el 3er set, solo hombres.", value=cfg.get("tennis_favorite_enabled", True))
    cfg["tennis_signal_enabled"] = st.toggle("🎾 Tenis en vivo (modelo) — estima probabilidad del marcador y busca rezagos del mercado.", value=cfg.get("tennis_signal_enabled", False))
    cfg["crypto_signal_enabled"] = st.toggle("₿ Cripto spot (BTC/ETH/SOL) — sigue el spot y opera los 15 min cuando Kalshi va rezagado.", value=cfg.get("crypto_signal_enabled", False))
    st.markdown('<div class="k-sub warn" style="margin-top:6px">⚠️ Experimentales, sin edge probado. Mantienen los controles de riesgo (tamaño chico, stop diario, 1 por evento).</div>', unsafe_allow_html=True)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">Filtros de calidad</div>', unsafe_allow_html=True)
    q = st.columns(3)
    cfg["min_whale_usd"] = q[0].number_input("$ mínimo ballena", value=float(cfg.get("min_whale_usd", 500)), step=100.0)
    cfg["min_confidence_whale"] = q[1].number_input("Confianza mínima (ballena)", value=float(cfg.get("min_confidence_whale", 55)), step=1.0)
    cfg["min_confidence_momentum"] = q[2].number_input("Confianza mínima (momentum)", value=float(cfg.get("min_confidence_momentum", 55)), step=1.0)
    cfg["min_edge_pts_whale"] = q[0].number_input("Edge mínimo pts (ballena)", value=float(cfg.get("min_edge_pts_whale", 6)), step=1.0)
    cfg["min_edge_pts_momentum"] = q[1].number_input("Edge mínimo pts (momentum)", value=float(cfg.get("min_edge_pts_momentum", 6)), step=1.0)
    cfg["min_market_volume"] = q[2].number_input("Volumen mínimo de mercado", value=float(cfg.get("min_market_volume", 100)), step=50.0)
    cfg["min_entry_price_cents"] = q[0].number_input("Precio entrada mín (¢)", value=int(cfg.get("min_entry_price_cents", 30)), step=1)
    cfg["max_entry_price_cents"] = q[1].number_input("Precio entrada máx (¢)", value=int(cfg.get("max_entry_price_cents", 55)), step=1)
    cfg["max_resolution_days"] = q[2].number_input("Horizonte máx (días)", value=int(cfg.get("max_resolution_days", 30)), step=1)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">Tamaño de posición</div>', unsafe_allow_html=True)
    cfg["sizing_mode"] = st.radio("Modo de tamaño", ["percent", "fixed"],
                                  index=0 if cfg.get("sizing_mode", "percent") == "percent" else 1, horizontal=True)
    s = st.columns(2)
    cfg["fixed_trade_usd"] = s[0].number_input("$ por operación (fijo)", value=float(cfg.get("fixed_trade_usd", 5)), step=1.0)
    cfg["hard_max_position_usd"] = s[1].number_input("$ máximo por posición", value=float(cfg.get("hard_max_position_usd", 12)), step=1.0)

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-sect">Concurrencia y riesgo</div>', unsafe_allow_html=True)
    r = st.columns(2)
    cfg["max_open_positions"] = r[0].number_input("Máx. posiciones abiertas", value=int(cfg.get("max_open_positions", 25)), step=1)
    cfg["max_daily_new_positions"] = r[1].number_input("Máx. nuevas posiciones/día", value=int(cfg.get("max_daily_new_positions", 40)), step=1)
    cfg["max_total_exposure_fraction"] = r[0].number_input("Exposición total máx (fracción)", value=float(cfg.get("max_total_exposure_fraction", 0.20)), step=0.05, format="%.2f")
    cfg["max_positions_per_event"] = r[1].number_input("Máx. por evento", value=int(cfg.get("max_positions_per_event", 1)), step=1)
    cfg["stop_loss_on_day"] = r[0].number_input("Stop-loss diario $ (negativo lo arma)", value=float(cfg.get("stop_loss_on_day", -30)), step=5.0)
    cfg["take_profit_on_day"] = r[1].number_input("Take-profit diario $ (0 = off)", value=float(cfg.get("take_profit_on_day", 0)), step=5.0)
    st.caption("Todos los cambios se aplican **en vivo** al motor. Modo paper — dinero virtual.")

# ══════════════════════════ CLAVES ════════════════════════════════
elif PAGE == "Claves":
    st.markdown('<h2 class="k-neon" style="margin:0 0 6px">🔑 Credenciales de Kalshi</h2>', unsafe_allow_html=True)

    st.markdown(
        '<div class="k-card" style="border-color:rgba(239,68,68,.35);background:rgba(239,68,68,.06)">'
        '<b style="color:#EF4444">⚠ Leé esto antes de cargar claves de producción.</b><br>'
        '<span style="color:var(--muted);font-size:13px;line-height:1.6">'
        'Esta versión corre en <b>Streamlit Cloud</b> (infra compartida de un tercero). '
        'Si cargás tu clave privada RSA aquí, sale de tu equipo y viaja a esos servidores — '
        'justo el riesgo que quisimos evitar. El disco es efímero: si la app se reinicia, la clave se borra. '
        'Recomendación: usá <b>demo</b> aquí para probar la conexión, y operá en <b>producción con dinero real '
        'solo desde la app de escritorio</b>, donde la clave nunca sale de tu Mac (guardada 0600).'
        '</span></div>', unsafe_allow_html=True)

    status = auth.credentials_status_all()
    kenv = st.radio("Entorno de la clave", ["demo", "production"], horizontal=True,
                    format_func=lambda e: "demo" if e == "demo" else "producción",
                    key="cred_env")
    present = status.get(kenv, {}).get("present", False)
    st.markdown(badge("clave guardada", "win") if present else badge("sin configurar", "neutral"),
                unsafe_allow_html=True)

    api_key = st.text_input("ID de clave API", value="", type="password",
                            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                            help="En Kalshi → Settings → API Keys.")
    rsa_pem = st.text_area("Clave privada RSA (PEM)", value="", height=160,
                           placeholder="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
                           help="El archivo .pem que te dio Kalshi al crear la clave.")

    b = st.columns(3)
    if b[0].button("💾 Guardar", use_container_width=True, disabled=not (api_key and rsa_pem)):
        try:
            auth.save_credentials(api_key.strip(), rsa_pem, kenv)
            st.success(f"Credenciales guardadas para {'demo' if kenv=='demo' else 'producción'}.")
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo guardar: {e}")
    if b[1].button("🛡 Probar conexión", use_container_width=True, disabled=not present):
        try:
            with auth.ENV_LOCK:
                saved = auth.get_env()
                try:
                    auth.set_env(kenv)
                    auth.reset_credential_cache()
                    auth.prime_credentials(sync_time=True)
                    bal = asyncio.run(api.get_balance())
                finally:
                    auth.set_env(saved)
                    auth.reset_credential_cache()
            cents = int(bal.get("balance", 0))
            st.success(f"Conectado — saldo ${cents/100:.2f}.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Falló la prueba: {e}")
    if b[2].button("🗑 Borrar", use_container_width=True, disabled=not present):
        auth.clear_credentials(kenv)
        st.success(f"Credenciales de {'demo' if kenv=='demo' else 'producción'} borradas.")

    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown('<div class="k-label">Operar en vivo (dinero real)</div>', unsafe_allow_html=True)
    prod_ready = status.get("production", {}).get("present", False)
    live_on = bool(cfg.get("enable_trading")) and not cfg.get("paper_trading", True)
    st.caption("Requiere clave de **producción** guardada y probada. Doble interruptor de seguridad: "
               "cambia el entorno a producción, apaga el modo paper y activa el kill-switch.")
    new_live = st.toggle("Activar operación real con dinero", value=live_on, disabled=not prod_ready)
    if new_live != live_on:
        if new_live:
            cfg["kalshi_env"] = "production"; cfg["paper_trading"] = False; cfg["enable_trading"] = True
            st.warning("Operación REAL activada. El motor colocará órdenes con dinero real en Kalshi.")
        else:
            cfg["kalshi_env"] = "demo"; cfg["paper_trading"] = True; cfg["enable_trading"] = False
            st.info("Vuelto a paper (dinero virtual).")
    if not prod_ready:
        st.caption("Guardá y probá una clave de **producción** arriba para habilitar este interruptor.")

    st.markdown(
        '<div class="k-card" style="font-size:12px;color:var(--dim);line-height:1.6">'
        '<b style="color:var(--muted)">Privacidad:</b> las claves se escriben en un archivo legible solo por el '
        'proceso (0600). La app no hace ninguna llamada de red salvo a Kalshi (y, si los activás, a feeds públicos '
        'de precios cripto). No hay telemetría ni cuenta.</div>', unsafe_allow_html=True)

# ══════════════════════════ REGISTROS ═════════════════════════════
elif PAGE == "Registros":
    st.markdown('<h2 class="k-neon" style="margin:0 0 6px">Registros</h2>', unsafe_allow_html=True)
    logs = list(engine["logs"])[-200:][::-1]
    if logs:
        lvl = {"ERROR": "loss", "WARNING": "warn", "INFO": "muted", "DEBUG": "dim"}
        body = "".join(
            f'<div class="mono" style="font-size:12px;margin:2px 0"><span class="dim">{fdate(l["ts"])}</span> '
            f'<span class="{lvl.get(l["level"],"muted")}">{l["level"][:4]}</span> '
            f'<span style="color:var(--purple)">{l["src"]}</span> <span style="color:rgba(255,255,255,.8)">{l["msg"]}</span></div>'
            for l in logs)
        st.markdown(f'<div class="k-card" style="max-height:560px;overflow-y:auto">{body}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="k-card dim">Sin registros aún.</div>', unsafe_allow_html=True)

st_autorefresh(interval=6000, key="refresh")
