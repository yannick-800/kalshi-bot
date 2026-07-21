"""One-time helper: mint the Google Drive refresh token for the online app.

Run this on YOUR machine. It opens a browser, you approve access, and it
prints the block to paste into Streamlit Cloud → Manage app → Settings →
Secrets. Nothing is written to the repo and no token is sent anywhere else.

    python python/setup_gdrive.py /path/to/oauth_client.json <FOLDER_ID>

Get the client JSON at console.cloud.google.com → APIs & Services →
Credentials → Create credentials → OAuth client ID → **Desktop app**.
Use a client dedicated to this bot: the `drive.file` scope limits access to
files created by that client, so a dedicated one can only ever reach this
database — never the files your other apps created.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    client_file, folder_id = Path(sys.argv[1]).expanduser(), sys.argv[2].strip()
    if not client_file.is_file():
        print(f"No encuentro el archivo: {client_file}")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Falta una dependencia. Corre primero:\n"
              "  pip install google-api-python-client google-auth google-auth-oauthlib")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    if not creds.refresh_token:
        print("Google no devolvio refresh_token. Revoca el acceso previo en "
              "https://myaccount.google.com/permissions y volve a correrlo.")
        return 1

    print("\n" + "=" * 62)
    print("Pega EXACTAMENTE esto en Streamlit Cloud → Settings → Secrets:")
    print("=" * 62 + "\n")
    print(f'GDRIVE_CLIENT_ID     = "{creds.client_id}"')
    print(f'GDRIVE_CLIENT_SECRET = "{creds.client_secret}"')
    print(f'GDRIVE_REFRESH_TOKEN = "{creds.refresh_token}"')
    print(f'GDRIVE_FOLDER_ID     = "{folder_id}"')
    print("\n" + "=" * 62)
    print("Tratalo como una contrasena: da acceso a los archivos que esta app")
    print("cree en tu Drive. No lo pegues en el repo ni en un chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
