#!/usr/bin/env python3
"""Monzo OAuth helper.

Usage:
    # Print the auth URL (default – copy/paste into browser)
    python get_token.py

    # Run local callback server and print auth URL
    python get_token.py --local

    # After approval, the callback handler will exchange the code and
    # store the refresh token in Azure Table Storage (when AZURE_STORAGE
    is configured) or print it to stdout.
"""
import argparse
import os
import logging
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

import requests

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------- CONFIG ----------
# Prefer environment variables. As a fallback, paste values below.
CLIENT_ID = os.getenv("MONZO_CLIENT_ID")
CLIENT_SECRET = os.getenv("MONZO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("MONZO_REDIRECT_URI", "http://localhost:8080/callback")
AUTH_URL = "https://auth.monzo.com"
API_URL = "https://api.monzo.com"

# Azure Table Storage config (optional – when set, tokens are persisted there)
AZURE_STORAGE_CONN_STR = os.getenv("AzureWebJobsStorage")
AZURE_TABLE_NAME = os.getenv("MONZO_TOKENS_TABLE", "monzotokens")
AZURE_PARTITION_KEY = os.getenv("MONZO_TOKENS_PARTITION", "monzo")
AZURE_ROW_KEY = os.getenv("MONZO_TOKENS_ROW", "bot")

# Global state
server_instance = None
state_token = None


class RequestHandler(BaseHTTPRequestHandler):
    """Handles the OAuth redirect to http://localhost:8080/callback"""

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            callback_state = query.get("state", [None])[0]
            if callback_state != state_token:
                self._write(400, b"Invalid state parameter")
                return

            code = query.get("code", [None])[0]
            if not code:
                self._write(400, b"No authorization code received")
                return

            logger.info("Authorization code received successfully")

            self._write(
                200,
                b"<html><body><h1>Got the code!</h1>"
                b"<p>You can close this tab. Check the terminal for the refresh token.</p>"
                b"</body></html>",
                content_type="text/html; charset=utf-8",
            )

            # Exchange code for tokens
            exchange_token(code)

        except Exception as e:
            logger.exception("Error handling callback: %s", e)
            self._write(500, b"Internal server error")

    def log_message(self, fmt, *args):
        # Silence default HTTP server access logs
        logger.debug("%s - %s", self.client_address[0], fmt % args)

    def _write(self, status: int, body: bytes, content_type: str = "text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)


def _save_to_azure_table(refresh_token: str, access_token: str, expires_in: int) -> bool:
    """Persist tokens to Azure Table Storage. Returns True on success."""
    if not AZURE_STORAGE_CONN_STR:
        return False

    try:
        from azure.data.tables import TableServiceClient, UpdateMode
    except ImportError:
        logger.warning("azure-data-tables not installed; skipping Azure Table Storage save.")
        return False

    try:
        service = TableServiceClient.from_connection_string(AZURE_STORAGE_CONN_STR)
        client = service.get_table_client(AZURE_TABLE_NAME)
        try:
            client.create_table()
        except Exception:
            pass  # Table already exists

        payload = {
            "PartitionKey": AZURE_PARTITION_KEY,
            "RowKey": AZURE_ROW_KEY,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expiry_ts": time.time() + expires_in - 120,
        }
        client.upsert_entity(payload, mode=UpdateMode.MERGE)
        logger.info("Saved refresh token to Azure Table Storage (table=%s)", AZURE_TABLE_NAME)
        return True
    except Exception as exc:
        logger.error("Failed to save token to Azure Table Storage: %s", exc)
        return False


def exchange_token(auth_code: str) -> None:
    """Exchange authorization code for access and refresh tokens."""
    global server_instance

    if not CLIENT_ID or not CLIENT_SECRET:
        logger.error("CLIENT_ID or CLIENT_SECRET not set")
        return

    logger.info("Exchanging authorization code for tokens...")
    try:
        resp = requests.post(
            f"{API_URL}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": auth_code,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            try:
                err = resp.json()
            except Exception:
                err = {"raw": resp.text}
            logger.error("Token exchange failed: %s %s", resp.status_code, err)
            return

        data = resp.json()
        refresh_token = data.get("refresh_token")
        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 21600)
        if not refresh_token:
            logger.error("No refresh_token in response: %s", data)
            return

        masked = f"{refresh_token[:6]}...{refresh_token[-4:]}" if len(refresh_token) > 12 else "[redacted]"

        # Try Azure Table Storage first
        saved = _save_to_azure_table(refresh_token, access_token or "", expires_in)
        if saved:
            logger.info("SUCCESS. Refresh token persisted to Azure Table Storage.")
        else:
            logger.info("SUCCESS. Refresh token obtained (not persisted to Azure Table).")
            logger.info("MONZOREFRESHTOKEN=%s", masked)

        # Optional: one-time whoami check to validate token
        if access_token:
            try:
                wi = requests.get(
                    f"{API_URL}/ping/whoami",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if wi.ok:
                    logger.info("whoami: %s", wi.json())
            except Exception:
                logger.info("whoami check skipped or failed")

    except requests.exceptions.RequestException as e:
        logger.error("Request error during token exchange: %s", e)
    finally:
        if server_instance:
            threading.Thread(target=server_instance.shutdown, daemon=True).start()


def get_monzo_refresh_token(local_mode: bool = False) -> str:
    """Generate the Monzo OAuth auth URL and optionally start a local callback server."""
    global state_token, server_instance

    if not CLIENT_ID or not CLIENT_SECRET or "oauth2client_" not in CLIENT_ID:
        logger.error("Set MONZO_CLIENT_ID and MONZO_CLIENT_SECRET, or paste values in the script.")
        sys.exit(1)

    state_token = secrets.token_urlsafe(32)
    params = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "state": state_token,
        }
    )
    login_url = f"{AUTH_URL}/?{params}"

    print(f"\n{'='*60}")
    print("MONZO OAUTH AUTHORIZATION URL")
    print(f"{'='*60}")
    print(login_url)
    print(f"{'='*60}\n")

    if local_mode:
        logger.info("Starting local callback server on %s ...", REDIRECT_URI)
        webbrowser.open(login_url, new=2)
        server_host, server_port = "localhost", 8080
        server_instance = HTTPServer((server_host, server_port), RequestHandler)
        server_instance.timeout = 300
        server_instance.handle_request()
        server_instance.server_close()
    else:
        logger.info("Copy the URL above into your browser.")
        logger.info("After approving in the Monzo app, the redirect will carry the code.")
        if "localhost" in REDIRECT_URI:
            logger.warning("Redirect URI is localhost – you'll need to run this script with --local to capture the callback.")

    return login_url


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monzo OAuth helper")
    parser.add_argument("--local", action="store_true", help="Start local callback server (for localhost redirect URIs)")
    args = parser.parse_args()
    get_monzo_refresh_token(local_mode=args.local)