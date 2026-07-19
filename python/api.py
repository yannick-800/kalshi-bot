"""Kalshi REST client for Kalshi Bot.

Thin async wrapper over the Kalshi trade-api v2. Public reads (markets, trades,
orderbook) use an unsigned client; portfolio/order calls are signed per request.
Retries transient 429/5xx with backoff. Talks ONLY to Kalshi — nowhere else.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

import httpx

import auth

logger = logging.getLogger(__name__)

PREFIX = "/trade-api/v2"
BASES = {
    "demo": "https://demo-api.kalshi.co",
    "production": "https://api.elections.kalshi.com",
}
# Public market data is served off the elections host regardless of trade env.
PUBLIC_BASE = "https://api.elections.kalshi.com"

REQUEST_TIMEOUT = 25.0
HOT_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=6.0, pool=6.0)
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5

_pub_client: Optional[httpx.AsyncClient] = None
_signed_client: Optional[httpx.AsyncClient] = None
_signed_env: str = ""


class KalshiAPIError(Exception):
    def __init__(self, status: int | None, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {str(body)[:200]}")


async def _get_pub_client() -> httpx.AsyncClient:
    global _pub_client
    if _pub_client is None or _pub_client.is_closed:
        _pub_client = httpx.AsyncClient(
            base_url=PUBLIC_BASE,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json", "User-Agent": "KalshiBot/1.0"},
            follow_redirects=True,
        )
    return _pub_client


async def _get_signed_client() -> httpx.AsyncClient:
    global _signed_client, _signed_env
    env = auth.get_env()
    if _signed_client is None or _signed_client.is_closed or _signed_env != env:
        if _signed_client is not None and not _signed_client.is_closed:
            try:
                await _signed_client.aclose()
            except Exception:
                pass
        _signed_client = httpx.AsyncClient(
            base_url=BASES[env],
            timeout=REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "KalshiBot/1.0",
            },
        )
        _signed_env = env
    return _signed_client


async def close_clients() -> None:
    for c in (_pub_client, _signed_client):
        if c is not None and not c.is_closed:
            try:
                await c.aclose()
            except Exception:
                pass


async def _request(
    signed: bool,
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    timeout: httpx.Timeout | float | None = None,
) -> dict:
    full = PREFIX + path
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            if signed:
                client = await _get_signed_client()
                headers = auth.sign_headers(method, full)
                r = await client.request(
                    method, full, headers=headers, json=json, params=params,
                    timeout=timeout or REQUEST_TIMEOUT,
                )
            else:
                client = await _get_pub_client()
                r = await client.request(
                    method, full, params=params, timeout=timeout or REQUEST_TIMEOUT,
                )
            if r.status_code in (429, 500, 502, 503, 504):
                raise KalshiAPIError(r.status_code, r.text)
            if r.status_code >= 400:
                raise KalshiAPIError(r.status_code, r.text)
            return r.json() if r.content else {}
        except KalshiAPIError as e:
            last_exc = e
            if e.status not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(RETRY_BACKOFF ** attempt)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt == MAX_RETRIES - 1:
                raise KalshiAPIError(None, str(e))
            await asyncio.sleep(RETRY_BACKOFF ** attempt)
    raise last_exc or RuntimeError("request failed")


# ── portfolio (signed) ──────────────────────────────────────────────

async def get_balance() -> dict:
    return await _request(True, "GET", "/portfolio/balance", timeout=HOT_TIMEOUT)


async def get_positions() -> dict:
    return await _request(True, "GET", "/portfolio/positions")


async def get_orders(params: dict | None = None) -> dict:
    return await _request(True, "GET", "/portfolio/orders", params=params)


async def get_order(order_id: str) -> dict:
    return await _request(True, "GET", f"/portfolio/orders/{order_id}", timeout=HOT_TIMEOUT)


async def get_fills(params: dict | None = None) -> dict:
    return await _request(True, "GET", "/portfolio/fills", params=params)


def _v2_order_fields(side: str, action: str, price_cents: int) -> tuple[str, int]:
    """Translate (side, action) into the v2 (book_side, price) pair.

    Kalshi's v2 order API always expresses a limit price in terms of the side
    you place on. Buying "yes" bids `price` on the yes book; buying "no" bids
    `price` on the no book. We only place buys in this engine.
    """
    return side, price_cents


async def place_limit_order(
    *,
    ticker: str,
    side: str,
    action: str,
    count: int,
    price_cents: int,
    client_order_id: Optional[str] = None,
) -> dict:
    side = side.lower()
    action = action.lower()
    if side not in ("yes", "no"):
        raise ValueError(f"side must be yes|no, got {side}")
    if action not in ("buy", "sell"):
        raise ValueError(f"action must be buy|sell, got {action}")
    if not (1 <= price_cents <= 99):
        raise ValueError(f"price_cents must be 1..99, got {price_cents}")
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    book_side, price = _v2_order_fields(side, action, int(price_cents))
    body = {
        "ticker": ticker,
        "client_order_id": client_order_id or str(uuid.uuid4()),
        "side": book_side,
        "action": action,
        "type": "limit",
        "count": int(count),
        "yes_price" if book_side == "yes" else "no_price": price,
        "time_in_force": "good_till_canceled",
    }
    return await _request(True, "POST", "/portfolio/orders", json=body, timeout=HOT_TIMEOUT)


async def cancel_order(order_id: str) -> dict:
    return await _request(True, "DELETE", f"/portfolio/orders/{order_id}", timeout=HOT_TIMEOUT)


# ── public market data (unsigned) ───────────────────────────────────

async def get_markets(params: dict | None = None) -> dict:
    return await _request(False, "GET", "/markets", params=params)


async def get_market(ticker: str) -> dict:
    return await _request(False, "GET", f"/markets/{ticker}")


async def get_orderbook(ticker: str) -> dict:
    data = await _request(False, "GET", f"/markets/{ticker}/orderbook", timeout=HOT_TIMEOUT)
    return (data or {}).get("orderbook", {}) or {}


async def get_trades(params: dict | None = None) -> dict:
    return await _request(False, "GET", "/markets/trades", params=params)


async def get_series(series_ticker: str) -> dict:
    return await _request(False, "GET", f"/series/{series_ticker}")


async def get_event(event_ticker: str) -> dict:
    return await _request(False, "GET", f"/events/{event_ticker}")
