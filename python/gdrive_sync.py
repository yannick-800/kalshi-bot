"""Persist the SQLite database to Google Drive.

Streamlit Cloud's free tier has no persistent disk: every deploy or restart
destroys the container and everything in /tmp, which is where the database
lives. This module keeps a copy of the DB in a Google Drive folder so a
restart restores the previous state instead of starting from scratch.

How it works
    restore()  — on boot, download the .db from Drive (if it exists) before
                 anything opens the database.
    push()     — periodically, write a *consistent* snapshot with
                 `VACUUM INTO` (a plain file copy could catch a half-written
                 WAL) and upload it over the same Drive file.

Credentials
    An OAuth refresh token scoped to `drive.file`, which grants access ONLY to
    files this client created — it cannot read the rest of the Drive. Supply
    it via the GDRIVE_* settings (Streamlit secrets or env vars); use a
    dedicated OAuth client, not one shared with another app, so a leaked token
    can only ever touch this one database file.

Everything here is best-effort: if Drive is not configured or a call fails,
we log and carry on with a local-only database. Sync must never take the
trading engine down.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import db

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
REMOTE_NAME = "kalshi-bot.db"
DAILY_PREFIX = "kalshi-bot-"   # kalshi-bot-2026-07-20.db
KEEP_DAILY = 7

_TOKEN_URI = "https://oauth2.googleapis.com/token"
_file_id: str | None = None   # cached across pushes so we update, not duplicate
_last_daily: str = ""         # yyyy-mm-dd of the last dated copy written


def _setting(name: str) -> str:
    """Read config from Streamlit secrets first, then the environment."""
    try:
        import streamlit as st
        v = st.secrets.get(name)
        if v:
            return str(v).strip()
    except Exception:  # noqa: BLE001 — no secrets file, or not under Streamlit
        pass
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    return all(_setting(k) for k in
               ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET",
                "GDRIVE_REFRESH_TOKEN", "GDRIVE_FOLDER_ID"))


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=_setting("GDRIVE_REFRESH_TOKEN"),
        client_id=_setting("GDRIVE_CLIENT_ID"),
        client_secret=_setting("GDRIVE_CLIENT_SECRET"),
        token_uri=_TOKEN_URI,
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find(svc) -> str | None:
    """The id of our DB file in the target folder, or None the first time."""
    global _file_id
    if _file_id:
        return _file_id
    folder = _setting("GDRIVE_FOLDER_ID")
    q = (f"name = '{REMOTE_NAME}' and '{folder}' in parents "
         f"and trashed = false")
    files = svc.files().list(q=q, fields="files(id)", spaces="drive",
                             pageSize=1).execute().get("files", [])
    _file_id = files[0]["id"] if files else None
    return _file_id


def restore() -> bool:
    """Pull the saved DB down before anything opens it. True if restored."""
    if not configured():
        logger.info("Drive sync apagado — la base es solo local y se pierde al reiniciar")
        return False
    try:
        from googleapiclient.http import MediaIoBaseDownload

        svc = _service()
        fid = _find(svc)
        if not fid:
            logger.info("Drive: todavia no hay respaldo, arranca base nueva")
            return False

        target = db.db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".download")
        with open(tmp, "wb") as fh:
            dl = MediaIoBaseDownload(fh, svc.files().get_media(fileId=fid))
            done = False
            while not done:
                _, done = dl.next_chunk()

        # A truncated download would be worse than no download at all.
        with sqlite3.connect(str(tmp)) as probe:
            if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("el respaldo descargado esta corrupto")

        # Stale WAL/journal siblings would shadow the file we just restored.
        for ext in ("-wal", "-shm", "-journal"):
            Path(str(target) + ext).unlink(missing_ok=True)
        os.replace(str(tmp), str(target))
        logger.info(f"Drive: base restaurada ({target.stat().st_size // 1024} KB)")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Drive: no se pudo restaurar ({e}) — sigo con base local")
        return False


def push() -> bool:
    """Upload a consistent snapshot of the current DB. True if uploaded."""
    if not configured():
        return False
    try:
        from googleapiclient.http import MediaFileUpload

        src = db.db_path()
        if not src.exists():
            return False

        # VACUUM INTO gives a clean, fully-checkpointed copy while the engine
        # keeps writing; copying the file directly could catch a partial WAL.
        with tempfile.TemporaryDirectory() as tmpd:
            snap = Path(tmpd) / REMOTE_NAME
            with sqlite3.connect(str(src), timeout=30) as conn:
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute(f"VACUUM INTO '{snap}'")

            svc = _service()
            folder = _setting("GDRIVE_FOLDER_ID")
            media = MediaFileUpload(str(snap), mimetype="application/x-sqlite3",
                                    resumable=False)
            fid = _find(svc)
            if fid:
                svc.files().update(fileId=fid, media_body=media).execute()
            else:
                meta = {"name": REMOTE_NAME, "parents": [folder]}
                created = svc.files().create(body=meta, media_body=media,
                                             fields="id").execute()
                globals()["_file_id"] = created["id"]
            logger.info(f"Drive: respaldo subido ({snap.stat().st_size // 1024} KB)")

            # A dated copy once a day. The main file is overwritten constantly,
            # so if something wipes the data (a stray reset, a bad run) these
            # are the only way back — the app is open to anyone with the URL.
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if _last_daily != today:
                name = f"{DAILY_PREFIX}{today}.db"
                q = (f"name = '{name}' and '{folder}' in parents and trashed = false")
                found = svc.files().list(q=q, fields="files(id)", spaces="drive",
                                         pageSize=1).execute().get("files", [])
                dated = MediaFileUpload(str(snap), mimetype="application/x-sqlite3",
                                        resumable=False)
                if found:
                    svc.files().update(fileId=found[0]["id"], media_body=dated).execute()
                else:
                    svc.files().create(body={"name": name, "parents": [folder]},
                                       media_body=dated, fields="id").execute()
                globals()["_last_daily"] = today
                logger.info(f"Drive: copia diaria {name}")
                _prune_daily(svc, folder)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Drive: no se pudo respaldar ({e})")
        return False


def _prune_daily(svc, folder: str) -> None:
    """Keep only the newest KEEP_DAILY dated copies."""
    try:
        q = (f"name contains '{DAILY_PREFIX}' and '{folder}' in parents "
             f"and trashed = false")
        files = svc.files().list(q=q, fields="files(id,name)", spaces="drive",
                                 pageSize=100).execute().get("files", [])
        # Names are ISO-dated, so a plain sort is chronological.
        old = sorted(files, key=lambda f: f["name"])[:-KEEP_DAILY]
        for f in old:
            svc.files().delete(fileId=f["id"]).execute()
            logger.info(f"Drive: copia vieja borrada {f['name']}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Drive: no se pudieron limpiar copias viejas ({e})")
