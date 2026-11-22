import base64
import io
import json
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from conftest import login
from app.config import settings
from app.db import Job, session
from app.assistant import run_assistant
from engine import docx_report, report_text
from gmailbox import Gmail


def test_gmail_send_uses_mime_and_downloadable_attachment(monkeypatch):
    monkeypatch.setattr(settings, 'gmail_mailbox', 'mailbox@example.test')
    client = Gmail()
    calls = []
    monkeypatch.setattr(client, 'api', lambda path, body=None: calls.append((path, body)) or {})
    client.send('legal@example.test', 'Report / 报告', 'Review complete', ('report.docx', b'synthetic-docx'))
    assert calls[0][0] == '/messages/send'
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(calls[0][1]['raw']))
    assert message['To'] == 'legal@example.test'
    attachment = list(message.iter_attachments())[0]
    assert attachment.get_filename() == 'report.docx'
    assert attachment.get_payload(decode=True) == b'synthetic-docx'


def test_gmail_intake_normalizes_sender_and_limits_attachments(monkeypatch):
    client = Gmail()
    encoded = base64.urlsafe_b64encode(b'Please review payment terms').decode().rstrip('=')
    payload = {'headers': [{'name': 'From', 'value': 'Legal <legal@example.test>'},
                           {'name': 'Subject', 'value': 'Contract [TOKEN=synthetic]'}],
               'parts': [{'mimeType': 'text/plain', 'body': {'data': encoded}},
                         {'partId': '1', 'filename': 'contract.pdf', 'body': {'size': 5, 'attachmentId': 'a'}},
                         {'partId': '2', 'filename': 'program.exe', 'body': {'size': 5, 'data': encoded}}]}
    def api(path, body=None):
        if '/attachments/' in path:
            return {'data': base64.urlsafe_b64encode(b'valid').decode()}
        if path == '/profile': return {'historyId': '100'}
        if path.startswith('/history?'):
            return {'history': [{'messagesAdded': [{'message': {'id': 'm'}}]}], 'historyId': '101'}
        return {'id': 'm', 'payload': payload}
    monkeypatch.setattr(client, 'api', api)
    assert client.delta() == {"value": [], "@odata.deltaLink": "gmailh:100:"}
    batch = client.delta("gmailh:100:")
    assert batch['value'][0]['from']['emailAddress']['address'] == 'legal@example.test'
    assert batch['value'][0]['body']['content'] == 'Please review payment terms'
    assert batch['@odata.deltaLink'] == 'gmailh:101:'
    assert list(client.attachments('m')) == [('1', 'contract.pdf', b'valid')]


def test_review_language_is_snapshotted_and_quotes_are_preserved(client):
    login(client)
    cid = client.post('/api/contracts', files={'file': ('sample.txt', b'Notices require 60 days.')} ).json()['id']
    jid = client.post(f'/api/contracts/{cid}/jobs', json={'request_key': 'english-review', 'language': 'en'}).json()['id']
    with session() as db:
        assert json.loads(db.get(Job, jid).skill)['language'] == 'en'
    result = {'language': 'en', 'model_mode': 'tacc', 'model_name': 'backend-private-model', 'rows': [{'label': '自动续约', 'status': 'found',
        'finding': 'At least 60 days notice.', 'evidence': [{'paragraph_id': 'P0001', 'quote': 'Notices require 60 days.'}],
        'assessment': '待核验', 'explanation': '', 'missing': [], 'question': ''}]}
    raw = docx_report(report_text('sample.txt', result), result)
    with zipfile.ZipFile(io.BytesIO(raw)) as document:
        xml = document.read('word/document.xml').decode()
    assert 'Contract Review Report' in xml and 'Automatic renewal' in xml
    assert 'Notices require 60 days.' in xml and '合同风险表' not in xml
    assert all(value not in xml for value in ('TACC', 'backend-private-model', 'Execution mode:'))
    result['language'] = 'zh'
    assert '执行模式' not in report_text('sample.txt', result)
    assert 'backend-private-model' not in report_text('sample.txt', result)


def test_english_assistant_prompt_and_list_tool_result(client, monkeypatch):
    user = login(client)
    monkeypatch.setattr(settings, 'model_mode', 'tacc')
    saved = {}
    def checkpoint(key, value=None):
        if value is None:
            return saved.get(key)
        saved[key] = value
    decisions = iter([{'action': 'tool', 'tool': 'list_skills', 'arguments': {}},
                      {'action': 'final', 'answer': 'No Skills saved.'}])
    def decide(system, payload):
        assert 'Use English' in system
        return next(decisions)
    result = run_assistant(user['id'], {'message': 'List my Skills', 'language': 'en', 'history': []},
                           'english-list', lambda *args: None, checkpoint, decide=decide)
    assert result['answer'] == 'No Skills saved.'


def test_bilingual_template_files_match_schema():
    from app.schemas import SkillInput
    root = Path(__file__).resolve().parent.parent / 'templates'
    for language in ('en', 'zh'):
        assert SkillInput.model_validate_json((root / language / 'payment-skill.json').read_text(encoding='utf-8'))
