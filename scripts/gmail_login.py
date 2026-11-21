"""Authorize a desktop Gmail OAuth client using a loopback callback and PKCE."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', required=True, type=Path, help='Downloaded Desktop OAuth client JSON')
    parser.add_argument('--no-browser', action='store_true', help='Print the Google consent URL instead of opening a system browser')
    args = parser.parse_args()
    client_document = json.loads(args.client.read_text(encoding='utf-8-sig'))
    client = client_document.get('installed')
    if not client:
        raise ValueError('Create a Desktop app OAuth client, not a Web application client.')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    result = {}
    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if urllib.parse.urlsplit(self.path).path != '/oauth/callback' or params.get('state', [''])[0] != state:
                self.send_error(400, 'Invalid authorization callback')
                return
            result.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Authorization received. You may close this tab and return to the project.')
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Callback)
    server.timeout = 1
    redirect = f'http://127.0.0.1:{server.server_port}/oauth/callback'
    query = urllib.parse.urlencode({'client_id': client['client_id'], 'redirect_uri': redirect,
        'response_type': 'code', 'scope': 'https://www.googleapis.com/auth/gmail.modify',
        'access_type': 'offline', 'prompt': 'consent', 'state': state,
        'login_hint': settings.gmail_mailbox, 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')})
    authorization_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + query
    if args.no_browser:
        print('AUTH_URL=' + authorization_url, flush=True)
    else:
        webbrowser.open(authorization_url)
    print('Complete Google authorization in your browser. Tokens will stay in local data/.', flush=True)
    deadline = time.monotonic() + 300
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if result.get('error') or not result.get('code'):
        raise ValueError('Google authorization was declined or timed out.')
    fields = {'client_id': client['client_id'], 'client_secret': client['client_secret'],
        'code': result['code'], 'code_verifier': verifier, 'redirect_uri': redirect, 'grant_type': 'authorization_code'}
    with urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', urllib.parse.urlencode(fields).encode()), timeout=30) as response:
        token = json.load(response)
    with urllib.request.urlopen(urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me/profile',
            headers={'Authorization': 'Bearer ' + token['access_token']}), timeout=30) as response:
        profile = json.load(response)
    if profile['emailAddress'].lower() != settings.gmail_mailbox.lower():
        raise ValueError('Authorized account differs from GMAIL_MAILBOX. No credentials were saved.')
    if not token.get('refresh_token'):
        raise ValueError('No refresh token returned. Revoke the old app grant and authorize again.')
    settings.data.mkdir(parents=True, exist_ok=True)
    for name, data in (('.gmail-client.json', client_document), ('.gmail-token.json', {'refresh_token': token['refresh_token'], 'mailbox': profile['emailAddress']})):
        target = settings.data / name
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data), encoding='utf-8')
        if os.name != 'nt':
            temporary.chmod(0o600)
        os.replace(temporary, target)
    print('PASS: Gmail authorized for the configured mailbox. No email was sent.')


if __name__ == '__main__':
    main()
