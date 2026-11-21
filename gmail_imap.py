"""Gmail IMAP/SMTP with an application password; no Cloud project required."""
import imaplib
import re
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from contextlib import contextmanager


class GmailIMAP:
    def __init__(self):
        from app.config import settings
        self.address = settings.gmail_mailbox
        self.password = settings.gmail_app_password.replace(' ', '')
        self.smtp_port = settings.gmail_smtp_port
        if self.smtp_port not in (465, 587):
            raise ValueError("Gmail SMTP port must be 465 or 587")
        self.cache = {}

    @property
    def enabled(self):
        return bool(self.address and self.password)

    @contextmanager
    def connect(self):
        client = imaplib.IMAP4_SSL('imap.gmail.com', 993, ssl_context=ssl.create_default_context(), timeout=30)
        try:
            client.login(self.address, self.password)
            status, _ = client.select('INBOX')
            if status != 'OK':
                raise RuntimeError('Unable to select Gmail INBOX')
            yield client
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    @staticmethod
    def number(client, key):
        _, data = client.response(key)
        return int(data[0]) if data and data[0] else 0

    @staticmethod
    def normalize(message, identifier):
        automated = message.get('Auto-Submitted', 'no').lower() != 'no'
        attachments = [p for p in message.walk() if p.get_filename() and p.get_filename().lower().endswith(('.pdf', '.docx', '.txt'))]
        body = message.get_body(preferencelist=('plain',))
        return {'id': identifier, 'subject': str(message.get('Subject', '')),
            'from': {'emailAddress': {'address': parseaddr(str(message.get('From', '')))[1]}},
            'body': {'content': body.get_content() if body else ''},
            'internetMessageId': str(message.get('Message-ID', '')),
            'hasAttachments': bool(attachments) and not automated}

    def delta(self, cursor=''):
        self.cache.clear()
        with self.connect() as client:
            validity = self.number(client, 'UIDVALIDITY')
            next_uid = self.number(client, 'UIDNEXT')
            if not validity or not next_uid:
                raise RuntimeError('Gmail did not return stable mailbox identifiers')
            saved = re.fullmatch(r'imap:(\d+):(\d+)', cursor)
            if not saved or int(saved[1]) != validity:
                # First connection starts with new mail; it never bulk-imports old mail.
                return {'value': [], '@odata.deltaLink': f'imap:{validity}:{next_uid - 1}'}
            last = int(saved[2])
            status, data = client.uid('search', None, 'UID', f'{last + 1}:*')
            if status != 'OK':
                raise RuntimeError('Gmail UID search failed')
            ids = sorted(int(uid) for uid in data[0].split() if int(uid) > last) if data and data[0] else []
            selected = ids[:25]
            messages = []
            for uid in selected:
                status, sizes = client.uid('fetch', str(uid), '(RFC822.SIZE)')
                if status != 'OK':
                    raise RuntimeError('Gmail message size lookup failed')
                meta = b' '.join(x for x in sizes if isinstance(x, bytes))
                size = re.search(rb'RFC822.SIZE (\d+)', meta)
                if not size:
                    raise RuntimeError('Gmail message size is unavailable')
                if int(size[1]) > 20 * 1024 * 1024:
                    continue
                status, raw = client.uid('fetch', str(uid), '(BODY.PEEK[])')
                contents = next((part[1] for part in raw if isinstance(part, tuple)), None)
                if status != 'OK' or contents is None:
                    raise RuntimeError('Gmail message fetch failed')
                message = BytesParser(policy=policy.default).parsebytes(contents)
                identifier = f'imap:{validity}:{uid}'
                self.cache[identifier] = message
                messages.append(self.normalize(message, identifier))
            end = selected[-1] if selected else last
            return {'value': messages, ('@odata.nextLink' if len(ids) > 25 else '@odata.deltaLink'): f'imap:{validity}:{end}'}

    def attachments(self, identifier):
        message = self.cache.get(identifier)
        if message is None:
            raise RuntimeError('Message is not part of the current inbox batch')
        for index, part in enumerate(message.walk()):
            name = part.get_filename() or ''
            if not name.lower().endswith(('.pdf', '.docx', '.txt')):
                continue
            raw = part.get_payload(decode=True)
            if raw and len(raw) <= 12 * 1024 * 1024:
                yield str(index), name, raw

    def mark_read(self, identifier):
        matched = re.fullmatch(r'imap:(\d+):(\d+)', identifier)
        if not matched:
            raise ValueError('Invalid Gmail message identifier')
        with self.connect() as client:
            if self.number(client, 'UIDVALIDITY') != int(matched[1]):
                raise RuntimeError('Gmail mailbox changed; refusing to mark another message')
            status, _ = client.uid('store', matched[2], '+FLAGS.SILENT', '(\\Seen)')
            if status != 'OK':
                raise RuntimeError('Gmail message read flag could not be saved')

    @contextmanager
    def smtp_connection(self):
        context = ssl.create_default_context()
        if self.smtp_port == 465:
            client = smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context, timeout=30)
        else:
            client = smtplib.SMTP('smtp.gmail.com', 587, timeout=30)
            try:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            except BaseException:
                client.close()
                raise
        try:
            client.login(self.address, self.password)
            yield client
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()

    def send(self, address, subject, text, attachment=None):
        return self.send_reply(address, subject, text, attachment)

    def send_reply(self, address, subject, text, attachment=None, in_reply_to=''):
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = self.address, address, subject
        message['Auto-Submitted'] = 'auto-replied'
        if re.fullmatch(r'<[^<>\r\n ]+@[^<>\r\n ]+>', in_reply_to):
            message['In-Reply-To'] = in_reply_to
            message['References'] = in_reply_to
        message.set_content(text)
        if attachment:
            name, raw = attachment
            message.add_attachment(raw, maintype='application', subtype='octet-stream', filename=name)
        with self.smtp_connection() as client:
            refused = client.send_message(message)
            if refused:
                raise RuntimeError('SMTP rejected the recipient')
