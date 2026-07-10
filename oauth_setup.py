#!/usr/bin/env python3
"""
One-time Google OAuth bootstrap for the LifeHouse OS beta email service.

Usage:
  python oauth_setup.py /path/to/client_secret_XXXX.json
  python oauth_setup.py /path/to/client_secret_XXXX.json --push-railway
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
PEOPLE_GROUPS_URL = "https://people.googleapis.com/v1/contactGroups"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "OAuthHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self.server.query_params = params

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if "code" in params:
            message = "Authorization received. You can close this tab and return to Terminal."
        else:
            message = "Authorization did not return a code. Return to Terminal for details."

        self.wfile.write(
            f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>LifeHouse OS OAuth</title></head>
  <body style="font-family: system-ui, sans-serif; padding: 48px;">
    <h1>{message}</h1>
  </body>
</html>""".encode("utf-8")
        )


class OAuthHTTPServer(HTTPServer):
    query_params: dict[str, list[str]] | None = None


def load_oauth_client(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text())
    config = data.get("installed") or data.get("web")
    if not config:
        raise SystemExit("OAuth JSON must contain an 'installed' or 'web' client config.")

    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    if not client_id or not client_secret:
        raise SystemExit("OAuth JSON is missing client_id or client_secret.")

    return {"client_id": client_id, "client_secret": client_secret}


def receive_authorization_code(client_id: str, scopes: list[str], login_hint: str, open_browser: bool, timeout_seconds: int) -> tuple[str, str]:
    state = secrets.token_urlsafe(24)
    server = OAuthHTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
    server.timeout = 1
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "login_hint": login_hint,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    print("Open this Google OAuth URL and approve access for the Iris account:")
    print(auth_url)
    if open_browser:
        webbrowser.open(auth_url)

    print("\nWaiting for Google to redirect back to this machine...")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline and server.query_params is None:
        server.handle_request()

    if server.query_params is None:
        raise SystemExit("Timed out waiting for OAuth callback.")

    if server.query_params.get("state", [""])[0] != state:
        raise SystemExit("OAuth state mismatch. Refusing to use this callback.")

    if "error" in server.query_params:
        error = server.query_params["error"][0]
        raise SystemExit(f"Google returned OAuth error: {error}")

    code = server.query_params.get("code", [""])[0]
    if not code:
        raise SystemExit("OAuth callback did not include an authorization code.")

    return code, redirect_uri


def exchange_code(client: dict[str, str], code: str, redirect_uri: str) -> dict[str, Any]:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    payload = response.json()
    if response.status_code != 200 or "access_token" not in payload:
        raise SystemExit(f"Token exchange failed: {payload.get('error_description') or payload}")
    if "refresh_token" not in payload:
        raise SystemExit(
            "Google did not return a refresh token. Re-run this script and make sure you approve the consent screen. "
            "If it still happens, revoke the old app grant from the Google account and try again."
        )
    return payload


def refresh_access_token(client: dict[str, str], refresh_token: str) -> str:
    response = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    payload = response.json()
    if response.status_code != 200 or "access_token" not in payload:
        raise SystemExit(f"Refresh-token validation failed: {payload.get('error_description') or payload}")
    return payload["access_token"]


def validate_google_access(access_token: str, contact_group: str) -> None:
    headers = {"Authorization": f"Bearer {access_token}"}

    groups_response = httpx.get(PEOPLE_GROUPS_URL, headers=headers, timeout=30)
    if groups_response.status_code == 200:
        groups = groups_response.json().get("contactGroups", [])
        matching = [group for group in groups if group.get("name", "").lower() == contact_group.lower()]
        if matching:
            print(f"People API: found contact group '{contact_group}'.")
        else:
            print(f"People API: accessible, but contact group '{contact_group}' was not found.")
    else:
        print(f"People API validation failed: {groups_response.status_code} {groups_response.text}")

    drive_response = httpx.get(
        DRIVE_FILES_URL,
        headers=headers,
        params={"pageSize": 1, "fields": "files(id,name)"},
        timeout=30,
    )
    if drive_response.status_code == 200:
        print("Drive API: readonly access validated.")
    else:
        print(f"Drive API validation failed: {drive_response.status_code} {drive_response.text}")


def railway_set(key: str, value: str, skip_deploys: bool) -> None:
    cmd = ["railway", "variable", "set", key, "--stdin", "--environment", "production", "--service", "lhos-beta-email"]
    if skip_deploys:
        cmd.append("--skip-deploys")
    subprocess.run(cmd, input=value, text=True, check=True)


def push_railway_vars(client: dict[str, str], refresh_token: str, sender_email: str) -> None:
    print("Pushing Google OAuth variables to Railway production...")
    railway_set("GOOGLE_CLIENT_ID", client["client_id"], skip_deploys=True)
    railway_set("GOOGLE_CLIENT_SECRET", client["client_secret"], skip_deploys=True)
    railway_set("GOOGLE_REFRESH_TOKEN", refresh_token, skip_deploys=True)
    railway_set("LHOS_SENDER_EMAIL", sender_email, skip_deploys=False)
    print("Railway variables updated. A deploy should start from the final variable change.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Google refresh token for LifeHouse OS beta email.")
    parser.add_argument("client_secret_json", type=Path, help="Downloaded OAuth client JSON from Google Cloud.")
    parser.add_argument("--login-hint", default="iris@lifehouseos.com", help="Google account to suggest on the consent screen.")
    parser.add_argument("--sender-email", default="iris@lifehouseos.com", help="Sender email env var to push to Railway.")
    parser.add_argument("--contact-group", default="LifeHouse OS Beta - Active", help="Google Contacts label/group to validate.")
    parser.add_argument("--scope", action="append", dest="scopes", help="Override scopes; pass once per scope.")
    parser.add_argument("--token-out", type=Path, default=Path("google_token.json"), help="Local token output path.")
    parser.add_argument("--no-browser", action="store_true", help="Print the auth URL without opening a browser.")
    parser.add_argument("--push-railway", action="store_true", help="Set GOOGLE_* vars in the linked Railway service.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="How long to wait for the local OAuth callback.")
    args = parser.parse_args()

    client_path = args.client_secret_json.expanduser().resolve()
    if not client_path.exists():
        raise SystemExit(f"OAuth client JSON not found: {client_path}")

    client = load_oauth_client(client_path)
    scopes = args.scopes or DEFAULT_SCOPES
    code, redirect_uri = receive_authorization_code(client["client_id"], scopes, args.login_hint, not args.no_browser, args.timeout_seconds)
    token = exchange_code(client, code, redirect_uri)
    access_token = refresh_access_token(client, token["refresh_token"])

    token_record = {
        "client_id": client["client_id"],
        "refresh_token": token["refresh_token"],
        "scope": token.get("scope", " ".join(scopes)),
        "token_type": token.get("token_type"),
        "created_at": int(time.time()),
    }
    args.token_out.write_text(json.dumps(token_record, indent=2))
    print(f"Saved local refresh token record to {args.token_out.resolve()}")

    validate_google_access(access_token, args.contact_group)

    if args.push_railway:
        push_railway_vars(client, token["refresh_token"], args.sender_email)
    else:
        print("\nRailway was not changed. To push after reviewing, rerun with --push-railway.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
