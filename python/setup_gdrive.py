"""One-time helper: mint the Google Drive refresh token for the online app.

Run this on YOUR machine. It opens a browser, you approve access, and it
prints the block to paste into Streamlit Cloud → Manage app → Settings →
Secrets. Nothing is written to the repo and no token is sent anywhere else.

    python python/setup_gdrive.py <FOLDER_ID>                # usa el cliente de Invoice Maker
    python python/setup_gdrive.py <FOLDER_ID> otro_client.json

It reuses the Invoice Maker Nube OAuth client file, so there is nothing new to
create in Google Cloud. It does NOT reuse that app's token: that one carries
full-Drive and Gmail scopes, and a token on a third-party host should never
carry more than the job needs. This mints a fresh token limited to
`drive.file` — access to files this client created, nothing else in the Drive
and no Gmail at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

DEFAULT_CLIENT = Path.home() / "Claude" / "Invoice Maker Nube" / "json" / "google_credentials.json"


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    folder_id = sys.argv[1].strip()
    client_file = (Path(sys.argv[2]).expanduser() if len(sys.argv) == 3
                   else DEFAULT_CLIENT)
    if not client_file.is_file():
        print(f"No encuentro el archivo de cliente: {client_file}")
        return 1
    print(f"Cliente OAuth: {client_file}")
    print(f"Permiso pedido: drive.file (solo archivos de esta app)\n")

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
