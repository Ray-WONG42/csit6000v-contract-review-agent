import time
import urllib.parse
from conftest import login
from app.config import settings
from app.db import Receipt, session
from app.security import digest
from gmailbox import Gmail


def test_inbox_is_admin_only_before_mail_provider_access(client, monkeypatch):
    monkeypatch.setattr('app.main.graph_client', lambda: (_ for _ in ()).throw(AssertionError('Provider accessed')))
    assert client.get('/api/mail/inbox').status_code == 401
    login(client)
    assert client.get('/api/mail/inbox').status_code == 403
    login(client, 'other')
    assert client.get('/api/mail/inbox').status_code == 403


def test_admin_inbox_returns_metadata_and_intake_reason_only(client, monkeypatch):
    monkeypatch.setattr(settings, 'mail_demo', False)
    monkeypatch.setattr(settings, 'mail_provider', 'gmail')
    monkeypatch.setattr(settings, 'gmail_mailbox', 'project@example.test')
    class Mail:
        enabled = True
        def recent_inbox(self, page=''):
            assert page == 'next-page'
            return {'items': [{'id': 'm', 'subject': 'Synthetic contract', 'sender': 'new@example.test',
                              'sender_name': 'Legal', 'received_at': time.time(), 'unread': True,
                              'body': 'Must never be returned', 'private_trace': 'private'}], 'next_page': ''}
    monkeypatch.setattr('app.main.graph_client', lambda: Mail())
    with session() as db:
        db.add(Receipt(id=digest('project@example.test:m'), state='ignored', note='未登记发件人'))
    login(client, 'admin')
    response = client.get('/api/mail/inbox?page=next-page')
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert item['intake_state'] == 'ignored' and item['intake_note'] == '未登记发件人'
    assert 'body' not in item and 'private_trace' not in item


def test_recent_gmail_filters_24_hours_and_fetches_no_body(monkeypatch):
    mail = Gmail()
    now = int(time.time())
    paths = []
    def api(path):
        paths.append(path)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        if path.startswith('/messages?'):
            assert params['q'] == ['in:inbox after:' + str(now - 86400)]
            return {'messages': [{'id': 'new'}, {'id': 'old'}], 'nextPageToken': 'p2'}
        assert params['format'] == ['metadata']
        assert params['metadataHeaders'] == ['From', 'Subject']
        identifier = path.split('/')[2].split('?')[0]
        return {'id': identifier, 'internalDate': (now - (5 if identifier == 'new' else 90000)) * 1000,
                'labelIds': ['INBOX', 'UNREAD'], 'payload': {'headers': [
                    {'name': 'From', 'value': 'Legal <legal@example.test>'}, {'name': 'Subject', 'value': 'Contract'}]}}
    monkeypatch.setattr(mail, 'api', api)
    result = mail.recent_inbox()
    assert [item['id'] for item in result['items']] == ['new']
    assert result['next_page'] == 'p2'
    assert result['items'][0]['sender'] == 'legal@example.test'


def test_login_shortcuts_expose_emails_without_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, 'login_legal_email', 'first@example.test')
    monkeypatch.setattr(settings, 'login_other_legal_email', 'second@example.test')
    monkeypatch.setattr(settings, 'login_admin_email', 'admin@example.test')
    data = client.get('/api/config').json()
    assert [a['email'] for a in data['login_accounts']] == ['first@example.test', 'second@example.test', 'admin@example.test']
    assert all(set(a) == {'label', 'email'} for a in data['login_accounts'])
