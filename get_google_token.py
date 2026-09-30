#!/usr/bin/env python3
"""
Interactive helper to acquire a Google OAuth 2.0 Refresh Token
for Google Meet REST API (conferenceRecords and transcripts).

Usage:
    python get_google_token.py

Requirements:
    - A Google Cloud project with 'Google Meet API' enabled
    - OAuth 2.0 Client ID (type: 'Desktop app' or 'Web application' with redirect URI http://localhost:8080/callback)
"""

import http.server
import json
import os
import socketserver
import urllib.parse
import webbrowser
from pathlib import Path
import httpx

REDIRECT_URI = "http://localhost:8080/callback"
PORT = 8080
SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

captured_auth_code = None


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global captured_auth_code
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                captured_auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                html = """
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Authorization Successful</title>
                    <style>
                        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                        .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; border: 1px solid #334155; text-align: center; max-width: 480px; box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5); }
                        h1 { color: #10b981; font-size: 1.5rem; margin-bottom: 0.5rem; }
                        p { color: #94a3b8; font-size: 1rem; line-height: 1.5; }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <h1>Authentication Successful!</h1>
                        <p>Google OAuth code received. You can now close this browser tab and return to your terminal.</p>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
            elif "error" in params:
                err = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(f"OAuth error: {err}".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default HTTP server logging
        return


def load_env_var(key: str) -> str:
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(key, "")


def update_env_file(updates: dict[str, str]):
    env_path = Path(".env")
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        example_path = Path(".env.example")
        if example_path.exists():
            lines = example_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        matched = False
        for k, v in updates.items():
            if line.strip().startswith(f"{k}=") or line.strip() == k:
                new_lines.append(f"{k}={v}")
                updated_keys.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n")
    print(f"\n[OK] Updated .env file with new credentials.")


def main():
    print("=" * 65)
    print(" 🎙️  Google Meet OAuth 2.0 Token Generator")
    print("=" * 65)
    print("This utility will help you generate a GOOGLE_REFRESH_TOKEN")
    print("so Fireflies Notetaker can read real Google Meet transcripts.\n")

    client_id = load_env_var("GOOGLE_CLIENT_ID")
    client_secret = load_env_var("GOOGLE_CLIENT_SECRET")

    if not client_id or client_id == "your-google-client-id":
        client_id = input("Enter your Google Client ID: ").strip()
    else:
        use_existing = input(f"Use existing Client ID ({client_id[:12]}...)? [Y/n]: ").strip().lower()
        if use_existing == 'n':
            client_id = input("Enter your Google Client ID: ").strip()

    if not client_secret or client_secret == "your-google-client-secret":
        client_secret = input("Enter your Google Client Secret: ").strip()
    else:
        use_existing = input(f"Use existing Client Secret? [Y/n]: ").strip().lower()
        if use_existing == 'n':
            client_secret = input("Enter your Google Client Secret: ").strip()

    if not client_id or not client_secret:
        print("[Error] Client ID and Client Secret are required.")
        return

    # Build auth URL with offline access to ensure refresh token is returned
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # Forces Google to return a refresh_token every time
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print(f"\n[1] Starting local callback server on http://localhost:{PORT}/callback...")
    server = socketserver.TCPServer(("127.0.0.1", PORT), OAuthCallbackHandler)
    server.timeout = 120

    print("[2] Opening browser for Google authorization...")
    print(f"    If the browser does not open automatically, visit this URL:\n    {url}\n")
    webbrowser.open(url)

    print("[3] Waiting for authorization callback (timeout: 2 minutes)...")
    while not captured_auth_code:
        server.handle_request()

    print("[4] Exchanging authorization code for tokens...")
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": captured_auth_code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f"[Error] Failed to exchange code: {e}")
        return

    if not resp.is_success:
        print(f"[Error] Token exchange failed ({resp.status_code}): {data}")
        return

    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")

    if not refresh_token:
        print("\n[Warning] No refresh token returned. Did you already grant consent previously?")
        print("Note: Google only issues a refresh token when prompt='consent' is used.")
        print(f"Access Token: {access_token[:20]}...")
        return

    print("\n" + "=" * 65)
    print(" ✅ SUCCESS! Refresh Token Generated Successfully")
    print("=" * 65)
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
    print("=" * 65)

    save = input("\nSave these credentials directly into your .env file? [Y/n]: ").strip().lower()
    if save != 'n':
        update_env_file({
            "GOOGLE_CLIENT_ID": client_id,
            "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": refresh_token,
        })

    print("\nNext steps to test a real meeting:")
    print("1. Start a Google Meet (meet.google.com/new)")
    print("2. Turn on Transcripts (Activities -> Transcripts -> Start transcription)")
    print("3. Talk for 1-2 minutes, then end the meeting")
    print("4. In the Fireflies Notetaker UI (http://localhost:8000), click '+ New Meeting'")
    print("   and enter your meeting code (e.g. abc-defg-hij)")
    print("5. Click Process to fetch the transcript and generate the AI summary!\n")


if __name__ == "__main__":
    main()
