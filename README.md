# Kalshi Bot

A **local-first** Kalshi auto-trading desktop app — whale tracker, momentum
scanner and a configurable trading engine in one dark-neon UI. Your API key and
RSA private key **never leave this machine**; they are only used to sign your
own requests to Kalshi.

> ⚠️ **This app can place real orders on your Kalshi account. Trading carries
> real financial risk.** The bundled strategies are heuristics with **no proven,
> fee-adjusted edge** and may lose money. **Not financial advice.** It starts on
> the Kalshi **demo** environment with **trading off** — nothing trades for real
> until you switch to production *and* turn trading on.

## Architecture

```
Electron (TypeScript)                     React renderer (Vite + Tailwind)
  main.ts / ipc.ts  ──IPC (preload)──►    App + pages (Dashboard, Signals, …)
        │
        │ spawn() + newline-delimited JSON-RPC over stdin/stdout
        ▼
Python backend (asyncio)
  service.py  → loop: sync markets → scan signals → gate → size → order → poll
  ├── auth.py     RSA-PSS signing, local 0600 credential storage
  ├── api.py      Kalshi REST client (signed + public)
  ├── db.py       SQLite (WAL): markets, signals, positions, P&L, runs
  ├── scanner.py  whale + momentum detection
  └── trader.py   risk gates, sizing, order placement, reconcile
```

Everything is local. The only outbound network calls are to Kalshi.
There is **no telemetry, no account, no external services.**

## Requirements

- [Node.js](https://nodejs.org) 18+
- [Python](https://python.org) 3.10+ on your PATH
- A Kalshi account with an API key + RSA private key
  ([demo](https://demo.kalshi.co) recommended to start)

## Run (development)

```bash
npm install
npm run dev      # auto-creates python/.venv on first run (~30s)
```

Then open **API Keys**, pick **demo**, paste your API Key ID and RSA private
key, and click **Test connection**. It starts in demo + dry-run, so nothing
trades until you switch the environment to production and turn trading on.

## Build a desktop installer

```bash
npm run dist     # outputs to /release
```

(Add an app icon at `resources/icon.png` before packaging.)

## Safety features

- Starts in **demo + trading off** — two deliberate switches to go live.
- **Kill switch** in the top bar disables trading and cancels resting orders.
- Daily **stop-loss / take-profit** and open/daily/per-event position caps.
- Cash reserve + max total exposure fraction.
- Credentials stored with `0600` perms; never transmitted anywhere but Kalshi.

## ⚠️ Validate order placement before going live

The order-placement body in `python/api.py::place_limit_order` targets Kalshi's
trade-api v2. **Before trusting production**, place and cancel a single 1-contract
order in **demo** (via the app, with trading on) and confirm it appears and
cancels correctly. Kalshi's exact field expectations can change; the demo
environment is where you verify them at zero risk.

## License

MIT. Provided **as-is, with no warranty**.
