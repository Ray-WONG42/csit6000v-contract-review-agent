from contextlib import contextmanager
from email.message import EmailMessage
import json
from sqlalchemy import select
from conftest import login
from app.config import settings
from app.db import Job, Delivery, session
from app.worker import receive_message, claim_job, run_job
from app.services import deliver_pending
from gmail_imap import GmailIMAP


def test_new_mail_baseline_uid_filter_and_peek(monkeypatch):
    adapter = GmailIMAP()
    message = EmailMessage()
    message['From'] = 'Legal <legal@example.test>'
    message['Subject'] = 'Review contract'
    message.set_content('Ignore this body; use standard review.')
    message.add_attachment(b'contract', maintype='application', subtype='pdf', filename='sample.pdf')
    calls = []
    class Inbox:
        def response(self, key): return 'OK', [b'12' if key == 'UIDVALIDITY' else b'102']
        def uid(self, command, *args):
            calls.append((command, args))
            if command == 'search': return 'OK', [b'100 101']
            if args[-1] == '(RFC822.SIZE)': return 'OK', [b'1 (UID 101 RFC822.SIZE 1000)']
            return 'OK', [(b'1', message.as_bytes())]
    @contextmanager
    def connection(): yield Inbox()
    monkeypatch.setattr(adapter, 'connect', connection)
    baseline = adapter.delta()
    assert baseline == {'value': [], '@odata.deltaLink': 'imap:12:101'}
    batch = adapter.delta('imap:12:100')
    assert [m['id'] for m in batch['value']] == ['imap:12:101']
    assert batch['@odata.deltaLink'] == 'imap:12:101'
    assert ('fetch', ('101', '(BODY.PEEK[])')) in calls
    assert list(adapter.attachments('imap:12:101')) == [('2', 'sample.pdf', b'contract')]


def test_registered_mail_auto_reviews_once_and_replies_to_sender(client, sample, monkeypatch):
    user = login(client)
    monkeypatch.setattr(settings, 'mail_auto_review', True)
    monkeypatch.setattr(settings, 'mail_demo', False)
    monkeypatch.setattr(settings, 'mail_provider', 'gmail')
    monkeypatch.setattr(settings, 'gmail_mailbox', 'mailbox@example.test')
    monkeypatch.setattr(settings, 'gmail_transport', 'imap')
    class Inbox:
        def attachments(self, message): return [('a', 'synthetic.txt', sample)]
        def mark_read(self, message): pass
    message = {'id': 'imap:12:101', 'internetMessageId': '<original@example.test>',
        'from': {'emailAddress': {'address': user['email']}}, 'subject': 'Please review',
        'body': {'content': 'A body which must not replace standard review.'}}
    receive_message(Inbox(), message)
    receive_message(Inbox(), message)
    with session() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 1 and jobs[0].state == 'queued'
        assert jobs[0].instruction == '标准合同审查'
        assert json.loads(jobs[0].skill)['mail_message_id'] == '<original@example.test>'
    run_job(*claim_job())
    sent = []
    class Sender:
        enabled = True
        def send_reply(self, recipient, subject, text, attachment, in_reply_to=''):
            sent.append((recipient, attachment, in_reply_to))
    monkeypatch.setattr('app.services.graph_client', lambda: Sender())
    deliver_pending()
    deliver_pending()
    assert len(sent) == 1
    assert sent[0][0] == user['email'] and sent[0][1][0].endswith('.docx')
    assert sent[0][2] == '<original@example.test>'
    with session() as db:
        assert db.scalar(select(Delivery)).state == 'accepted'


def test_automatic_messages_do_not_trigger_reply_loops():
    message = EmailMessage()
    message['From'] = 'sender@example.test'
    message['Auto-Submitted'] = 'auto-replied'
    message.set_content('Automatic reply')
    message.add_attachment(b'file', maintype='application', subtype='pdf', filename='report.pdf')
    assert not GmailIMAP.normalize(message, 'imap:1:1')['hasAttachments']
