"""Gmail OAuth adapter; exposes the mailbox interface used by the worker."""
import base64
import json
import os
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def parts(payload):
    yield payload
    for child in payload.get('parts', []):
        yield from parts(child)


class Gmail:
    def __init__(self):
        from app.config import settings
        self.address = settings.gmail_mailbox
        self.token_path = settings.data / '.gmail-token.json'
        self.client_path = settings.data / '.gmail-client.json'
        self.access, self.expires = '', 0
        self._opener = (urllib.request.build_opener(urllib.request.ProxyHandler(
            {'https': settings.gmail_https_proxy})) if settings.gmail_https_proxy else None)

    def open(self, request):
        # Per-mailbox proxy only; certificate validation remains enabled.
        return (self._opener.open if self._opener else urllib.request.urlopen)(request, timeout=30)

    @property
    def enabled(self):
        if not (self.address and self.token_path.exists() and self.client_path.exists()):
            return False
        try:
            saved = json.loads(self.token_path.read_text(encoding="utf-8"))
            return saved.get("mailbox", "").lower() == self.address.lower() and bool(saved.get("refresh_token"))
        except (OSError, ValueError):
            return False

    def token(self):
        if self.access and time.time() < self.expires:
            return self.access
        stored = json.loads(self.token_path.read_text(encoding='utf-8'))
        if stored.get('mailbox', '').lower() != self.address.lower():
            raise ValueError('Gmail authorization does not match the configured mailbox.')
        client = json.loads(self.client_path.read_text(encoding='utf-8'))['installed']
        fields = {'grant_type': 'refresh_token', 'refresh_token': stored['refresh_token'],
                  'client_id': client['client_id'], 'client_secret': client['client_secret']}
        req = urllib.request.Request('https://oauth2.googleapis.com/token', urllib.parse.urlencode(fields).encode())
        with self.open(req) as response:
            data = json.load(response)
        self.access, self.expires = data['access_token'], time.time() + max(1, data['expires_in'] - 90)
        return self.access

    def api(self, path, body=None):
        req = urllib.request.Request('https://gmail.googleapis.com/gmail/v1/users/me' + path,
             data=json.dumps(body).encode() if body is not None else None,
             headers={'Authorization': 'Bearer ' + self.token(), 'Content-Type': 'application/json'})
        with self.open(req) as response:
            return json.load(response)

    def delta(self, cursor=''):
        if not cursor.startswith('gmailh:'):
            profile = self.api('/profile')
            return {'value': [], '@odata.deltaLink': 'gmailh:' + str(profile['historyId']) + ':'}
        _, history_id, page = cursor.split(':', 2)
        query = {'startHistoryId': history_id, 'historyTypes': 'messageAdded', 'labelId': 'INBOX', 'maxResults': 25}
        if page:
            query['pageToken'] = page
        batch = self.api('/history?' + urllib.parse.urlencode(query))
        identifiers = list(dict.fromkeys(item['message']['id'] for record in batch.get('history', [])
                                       for item in record.get('messagesAdded', [])))
        messages = []
        for identifier in identifiers:
            raw = self.api('/messages/' + urllib.parse.quote(identifier, safe='') + '?format=full')
            payload = raw.get('payload', {})
            headers = {x['name'].lower(): x['value'] for x in payload.get('headers', [])}
            body = '\n'.join(decode(p['body']['data']).decode('utf-8', errors='replace')
                             for p in parts(payload) if p.get('mimeType') == 'text/plain' and p.get('body', {}).get('data'))
            attached = any(p.get('filename', '').lower().endswith(('.pdf', '.docx', '.txt')) for p in parts(payload))
            messages.append({'id': raw['id'], 'subject': headers.get('subject', ''),
                'from': {'emailAddress': {'address': parseaddr(headers.get('from', ''))[1]}},
                'body': {'content': body}, 'internetMessageId': headers.get('message-id', ''),
                'hasAttachments': attached and headers.get('auto-submitted', 'no').lower() == 'no'})
        next_page = batch.get('nextPageToken')
        if next_page:
            return {'value': messages, '@odata.nextLink': 'gmailh:' + history_id + ':' + next_page}
        return {'value': messages, '@odata.deltaLink': 'gmailh:' + str(batch.get('historyId', history_id)) + ':'}

    def attachments(self, message):
        raw = self.api('/messages/' + urllib.parse.quote(message, safe='') + '?format=full')
        for part in parts(raw.get('payload', {})):
            name, body = part.get('filename', ''), part.get('body', {})
            if not name.lower().endswith(('.pdf', '.docx', '.txt')) or body.get('size', 0) > 12 * 1024 * 1024:
                continue
            attachment = body
            if body.get('attachmentId'):
                attachment = self.api('/messages/' + urllib.parse.quote(message, safe='') + '/attachments/' + urllib.parse.quote(body['attachmentId'], safe=''))
            if attachment.get('data'):
                content = decode(attachment['data'])
                if len(content) <= 12 * 1024 * 1024:
                    yield part.get('partId', ''), name, content

    def mark_read(self, message):
        self.api('/messages/' + urllib.parse.quote(message, safe='') + '/modify', {'removeLabelIds': ['UNREAD']})

    def send(self, address, subject, text, attachment=None):
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = self.address, address, subject
        message['Auto-Submitted'] = 'auto-replied'
        message.set_content(text)
        if attachment:
            name, raw = attachment
            message.add_attachment(raw, maintype='application', subtype='octet-stream', filename=name)
        return self.api('/messages/send', {'raw': base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')})
