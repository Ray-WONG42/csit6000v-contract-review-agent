"""Outlook via Microsoft Graph. Tokens and credentials stay on the server."""
import base64
import json
import os
import time
import urllib.parse
import urllib.request


class Outlook:
    def __init__(self):
        from app.config import settings
        self.tenant = settings.graph_tenant_id
        self.client = settings.graph_client_id
        self.secret = settings.graph_client_secret
        self.refresh = settings.graph_refresh_token
        self.address = settings.graph_mailbox
        self.token_path = settings.data / ".outlook-token.json"
        if not self.refresh and self.token_path.exists():
            self.refresh = json.loads(self.token_path.read_text(encoding="utf-8")).get("refresh_token", "")
        self.access = ""
        self.expires = 0

    @property
    def enabled(self):
        return bool(self.tenant and self.client and self.address and (self.secret or self.refresh))

    def token(self):
        if time.time() < self.expires:
            return self.access
        fields = {"client_id": self.client, "scope": "https://graph.microsoft.com/.default"}
        if self.secret:
            fields["client_secret"] = self.secret
        if self.refresh:
            fields.update(grant_type="refresh_token", refresh_token=self.refresh,
                          scope="https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send offline_access")
        else:
            fields["grant_type"] = "client_credentials"
        endpoint = "https://login.microsoftonline.com/" + urllib.parse.quote(self.tenant, safe="") + "/oauth2/v2.0/token"
        request = urllib.request.Request(endpoint, urllib.parse.urlencode(fields).encode())
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.load(response)
        self.access = data["access_token"]
        self.refresh = data.get("refresh_token", self.refresh)
        if self.refresh:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.token_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"refresh_token": self.refresh}), encoding="utf-8")
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self.token_path)
        self.expires = time.time() + max(1, data["expires_in"] - 90)
        return self.access

    def api(self, path, body=None, method=None):
        prefix = "/me" if self.refresh else "/users/" + urllib.parse.quote(self.address, safe="")
        root = "https://graph.microsoft.com/v1.0"
        url = path if path.startswith(root + "/") else root + prefix + path
        request = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                         {"Authorization": "Bearer " + self.token(), "Content-Type": "application/json",
                                          "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'}, method=method)
        with urllib.request.urlopen(request, timeout=25) as response:
            contents = response.read()
            return json.loads(contents) if contents else {}

    def inbox(self):
        filters = urllib.parse.urlencode({"$filter": "hasAttachments eq true",
                                          "$select": "id,subject,from,sender,body,receivedDateTime", "$top": 25})
        page = "/mailFolders/inbox/messages?" + filters
        for _ in range(12):
            if not page:
                break
            batch = self.api(page)
            yield from batch.get("value", [])
            page = batch.get("@odata.nextLink")

    def delta(self, cursor=""):
        if cursor:
            return self.api(cursor)
        query = urllib.parse.urlencode({"$select": "id,subject,from,sender,body,receivedDateTime,hasAttachments", "$top": 25})
        return self.api("/mailFolders/inbox/messages/delta?" + query)

    def attachments(self, message):
        path = "/messages/" + urllib.parse.quote(message, safe="") + "/attachments"
        page = path + "?$select=id,name,size,isInline&$top=25"
        for _ in range(10):
            if not page:
                break
            batch = self.api(page)
            for meta in batch.get("value", []):
                name = meta.get("name", "")
                if meta.get("isInline") or meta.get("size", 0) > 12 * 1024 * 1024:
                    continue
                if not name.lower().endswith((".pdf", ".docx", ".txt")):
                    continue
                attached = self.api(path + "/" + urllib.parse.quote(meta["id"], safe=""))
                if attached.get("contentBytes"):
                    yield meta["id"], name, base64.b64decode(attached["contentBytes"], validate=True)
            page = batch.get("@odata.nextLink")

    def mark_read(self, message):
        self.api("/messages/" + urllib.parse.quote(message, safe=""), {"isRead": True}, "PATCH")

    def send(self, address, subject, text, attachment=None):
        message = {"subject": subject, "body": {"contentType": "Text", "content": text},
                   "toRecipients": [{"emailAddress": {"address": address}}]}
        if attachment:
            name, raw = attachment
            message["attachments"] = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                                       "contentBytes": base64.b64encode(raw).decode("ascii")}]
        self.api("/sendMail", {"message": message, "saveToSentItems": True})
