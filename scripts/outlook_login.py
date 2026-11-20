"""Interactive OAuth device-code authorization for a dedicated Outlook mailbox."""
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings


def request(endpoint, fields):
    data = urllib.parse.urlencode(fields).encode()
    with urllib.request.urlopen(urllib.request.Request(endpoint, data=data), timeout=30) as response:
        return json.load(response)


def main():
    if not settings.graph_client_id or not settings.graph_tenant_id:
        raise SystemExit("Set GRAPH_CLIENT_ID and GRAPH_TENANT_ID first. Enable public client device-code flow in your app registration.")
    base = "https://login.microsoftonline.com/" + urllib.parse.quote(settings.graph_tenant_id, safe="") + "/oauth2/v2.0"
    scopes = "https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send offline_access"
    authorization = request(base + "/devicecode", {"client_id": settings.graph_client_id, "scope": scopes})
    print(authorization["message"], flush=True)
    deadline = time.time() + authorization["expires_in"]
    interval = authorization.get("interval", 5)
    while time.time() < deadline:
        time.sleep(interval)
        try:
            result = request(base + "/token", {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                               "client_id": settings.graph_client_id, "device_code": authorization["device_code"]})
            settings.data.mkdir(parents=True, exist_ok=True)
            path = settings.data / ".outlook-token.json"
            path.write_text(json.dumps({"refresh_token": result["refresh_token"]}), encoding="utf-8")
            if sys.platform != "win32":
                path.chmod(0o600)
            print("Authorization saved to the private application data folder. Restart the worker.")
            return
        except urllib.error.HTTPError as exc:
            error = json.load(exc).get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise SystemExit("OAuth failed: " + str(error))
    raise SystemExit("Authorization expired. Run this command again.")


if __name__ == "__main__":
    main()
