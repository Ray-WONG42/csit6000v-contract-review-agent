"""Account output preferences must also reach unattended mail and saved artifacts."""
import io
import json
import zipfile
import pytest
from sqlalchemy import create_engine, inspect, select, text
from conftest import login
from app.config import settings
from app.db import Job, User, File, session
from app.services import deliver_pending, send_mail
from app.worker import receive_message, claim_job, run_job


def test_language_is_authenticated_validated_and_account_scoped(client):
    assert client.put('/api/auth/language', json={'language': 'en'}).status_code == 401
    first = login(client)
    assert client.put('/api/auth/language', headers={'x-csrf-token': ''}, json={'language': 'en'}).status_code == 403
    assert client.put('/api/auth/language', json={'language': 'fr'}).status_code == 422
    assert client.put('/api/auth/language', json={'language': 'en'}).status_code == 200
    assert client.get('/api/auth/me').json()['language'] == 'en'
    login(client, 'other')
    assert client.get('/api/auth/me').json()['language'] == 'en'
    client.put('/api/auth/language', json={'language': 'zh'})
    login(client)
    assert client.get('/api/auth/me').json()['language'] == 'en'
    with session() as db:
        assert db.get(User, first['id']).language == 'en'


def test_registration_keeps_selected_language(client):
    code = client.post('/api/auth/send-code', json={'email': 'english@example.test', 'language': 'en'}).json()['demo_code']
    assert client.post('/api/auth/register', json={'email': 'english@example.test', 'language': 'en',
        'password': 'Synthetic2026!', 'code': code}).status_code == 200
    response = client.post('/api/auth/login', json={'email': 'english@example.test', 'password': 'Synthetic2026!'})
    assert response.json()['language'] == 'en'


@pytest.mark.parametrize('subject, expected', [('Review this contract', 'en'), ('Review [LANG=zh]', 'zh'), ('Review [lang=EN]', 'en')])
def test_mail_uses_sender_language_and_snapshots_report_and_delivery(client, sample, monkeypatch, subject, expected):
    user = login(client)
    client.put('/api/auth/language', json={'language': 'en'})
    monkeypatch.setattr(settings, 'mail_auto_review', True)
    monkeypatch.setattr(settings, 'mail_demo', False)
    class Inbox:
        def attachments(self, message): return [('1', 'synthetic.txt', sample)]
        def mark_read(self, message): pass
    receive_message(Inbox(), {'id': 'language-intake', 'subject': subject,
        'from': {'emailAddress': {'address': user['email']}}})
    with session() as db:
        job = db.scalar(select(Job))
        assert json.loads(job.skill)['language'] == expected
        job_id = job.id
    # Preference changes after intake must not change this task or its email.
    client.put('/api/auth/language', json={'language': 'zh' if expected == 'en' else 'en'})
    run_job(*claim_job())
    sent = []
    class Sender:
        enabled = True
        def send(self, address, subject, body, attachment): sent.append((address, subject, body, attachment))
    monkeypatch.setattr('app.services.graph_client', lambda: Sender())
    deliver_pending()
    assert len(sent) == 1 and sent[0][0] == user['email']
    with session() as db:
        job = db.get(Job, job_id)
        result = json.loads(job.result)
        assert result['language'] == expected
        file = db.get(File, job.report_file)
        content = (settings.data / file.path).read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml = archive.read('word/document.xml').decode()
    if expected == 'en':
        assert sent[0][1].startswith('Contract review result')
        assert sent[0][3][0].endswith('-review-report.docx')
        assert 'Contract Review Report' in xml
        assert 'Checklist item' in xml and '核对项' not in xml
        assert all('证据已提取' not in row['explanation'] for row in result['rows'])
    else:
        assert sent[0][1].startswith('合同审查结果')
        assert '合同审查报告' in xml
    assert any(e['quote'] in sample.decode() for r in result['rows'] for e in r['evidence'])


def test_oversized_email_notice_is_english(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'app_data', str(tmp_path))
    monkeypatch.setattr(settings, 'mail_demo', False)
    (tmp_path / 'large.docx').write_bytes(b'x' * 2_500_001)
    class Attachment:
        path = 'large.docx'
        name = 'large.docx'
    sent = []
    class Sender:
        enabled = True
        def send(self, *args): sent.append(args)
    monkeypatch.setattr('app.services.graph_client', lambda: Sender())
    send_mail('owner@example.test', 'Report', 'Completed.', Attachment(), language='en')
    assert 'Sign in at' in sent[0][2] and '请登录' not in sent[0][2]
    assert sent[0][3] is None


def test_legacy_user_table_is_upgraded_without_losing_accounts(tmp_path, monkeypatch):
    import app.db as database
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE users (id VARCHAR PRIMARY KEY, email VARCHAR UNIQUE, password VARCHAR, role VARCHAR, created FLOAT)'))
            conn.execute(text("INSERT INTO users VALUES ('old', 'old@example.test', 'untouched', 'legal', 1)"))
        monkeypatch.setattr(database, 'engine', engine)
        database.initialize()
        database.initialize()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT password, language FROM users WHERE id='old'")).one() == ('untouched', 'en')
        assert 'language' in {c['name'] for c in inspect(engine).get_columns('users')}
    finally:
        engine.dispose()
