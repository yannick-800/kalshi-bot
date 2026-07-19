"""Kalshi authentication for Kalshi Bot.

Signs every request with the account's RSA private key (RSA-PSS / SHA256 over
`timestamp + METHOD + path`), and stores credentials locally with 0600
permissions. Your keys NEVER leave this machine — the only network calls made
anywhere in this app are to Kalshi and (optionally) public crypto price feeds.

Two environments, two independent credential slots: "demo" and "production".
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)

ENV_LOCK = threading.RLock()

_VALID_ENVS = ("demo", "production")

# In-process state
_env: str = "demo"
_api_keys: dict[str, str] = {}
_private_keys: dict[str, rsa.RSAPrivateKey] = {}
_server_offset_ms: int = 0
_last_clock_sync: float = 0.0

_HOST_FOR_TIME = {
    "demo": "https://demo-api.kalshi.co",
    "production": "https://api.elections.kalshi.com",
}


# ── credential storage ──────────────────────────────────────────────

def _cred_dir() -> Path:
    base = os.environ.get("KALSHI_BOT_USERDATA")
    d = (Path(base) / "credentials") if base else (
        Path(__file__).resolve().parent / "data" / "credentials"
    )
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return d


def _cred_path(env: str) -> Path:
    return _cred_dir() / f"{env}.json"


def _write_0600(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(tmp), flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    if sys.platform != "win32":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(str(tmp), str(path))
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# ── env management ──────────────────────────────────────────────────

def get_env() -> str:
    return _env


def set_env(env: str) -> None:
    global _env
    if env not in _VALID_ENVS:
        raise ValueError(f"invalid env: {env!r}")
    _env = env


def reset_credential_cache() -> None:
    _api_keys.clear()
    _private_keys.clear()


# ── credential ops ──────────────────────────────────────────────────

def save_credentials(api_key: str, rsa_pem: str, env: str | None = None) -> None:
    env = env or _env
    # Validate the PEM parses before persisting.
    serialization.load_pem_private_key(rsa_pem.encode("utf-8"), password=None)
    payload = json.dumps({"api_key": api_key.strip(), "rsa_pem": rsa_pem}).encode("utf-8")
    _write_0600(_cred_path(env), payload)
    reset_credential_cache()
    logger.info(f"credentials saved for env={env}")


def clear_credentials(env: str | None = None) -> None:
    targets = _VALID_ENVS if env is None else (env,)
    for e in targets:
        p = _cred_path(e)
        if p.exists():
            p.unlink()
    reset_credential_cache()


def credentials_present(env: str | None = None) -> bool:
    return _cred_path(env or _env).exists()


def credentials_status_all() -> dict:
    return {e: {"present": credentials_present(e)} for e in _VALID_ENVS}


def _load_raw(env: str) -> dict:
    with open(_cred_path(env), "r", encoding="utf-8") as f:
        return json.load(f)


def _load_api_key() -> str:
    if _env not in _api_keys:
        _api_keys[_env] = _load_raw(_env)["api_key"]
    return _api_keys[_env]


def _load_private_key() -> rsa.RSAPrivateKey:
    if _env not in _private_keys:
        pem = _load_raw(_env)["rsa_pem"]
        _private_keys[_env] = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None
        )
    return _private_keys[_env]


def prime_credentials(sync_time: bool = True) -> bool:
    """Load and cache the active env's key material; optionally sync the clock."""
    _load_api_key()
    _load_private_key()
    if sync_time:
        sync_server_time(force=True)
    return True


# ── clock sync (Kalshi rejects skewed signatures) ──────────────────

def sync_server_time(force: bool = False) -> None:
    global _server_offset_ms, _last_clock_sync
    now = time.time()
    if not force and now - _last_clock_sync < 300:
        return
    try:
        host = _HOST_FOR_TIME[_env]
        r = httpx.get(f"{host}/trade-api/v2/exchange/status", timeout=5.0)
        date_hdr = r.headers.get("Date")
        if date_hdr:
            from email.utils import parsedate_to_datetime
            server = parsedate_to_datetime(date_hdr).timestamp() * 1000
            _server_offset_ms = int(server - time.time() * 1000)
        _last_clock_sync = now
    except Exception as e:  # noqa: BLE001
        logger.debug(f"clock sync skipped: {e}")


def now_ms() -> int:
    return int(time.time() * 1000) + _server_offset_ms


# ── signing ─────────────────────────────────────────────────────────

def _sign(message: bytes) -> str:
    private_key = _load_private_key()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256().digest_size,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def sign_headers(method: str, path: str) -> dict[str, str]:
    """Auth headers for a signed request. `path` must include the
    `/trade-api/v2` prefix and exclude any query string."""
    ts = str(now_ms())
    message = (ts + method.upper() + path).encode("utf-8")
    return {
        "KALSHI-ACCESS-KEY": _load_api_key(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": _sign(message),
    }
