"""Mailbox review must not inherit the independent API or browser model."""
import json
import pytest
from sqlalchemy import select
from conftest import login
from app.config import settings
from app.db import Contract, File, Job, session
from app.worker import receive_message, claim_job, run_job


@pytest.mark.parametrize('default_mode', ['api', 'ollama'])
def test_live_mail_is_snapshotted_and_executed_on_tacc(client, sample, monkeypatch, default_mode):
    from app import tacc_sdk
    from engine import Model
    monkeypatch.setattr(settings, 'model_mode', default_mode)
    monkeypatch.setattr(settings, 'tacc_protocol', 'sdk')
    monkeypatch.setattr(settings, 'tacc_url', '')
    monkeypatch.setattr(settings, 'mail_auto_review', True)
    monkeypatch.setattr(tacc_sdk, 'sdk_executable', lambda: 'tcloud')
    user = login(client)
    config = client.get('/api/config').json()
    assert config['mail_model_mode'] == 'tacc'
    assert config['model_mode'] == default_mode
    marked = []
    class Inbox:
        def attachments(self, message): return [('a', 'synthetic.txt', sample)]
        def mark_read(self, message): marked.append(message)
    message = {'id': 'fixed-tacc-mail', 'subject': 'Review this contract',
        'from': {'emailAddress': {'address': user['email']}}}
    receive_message(Inbox(), message)
    receive_message(Inbox(), message)
    with session() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 1
        job = jobs[0]
        snapshot = json.loads(job.skill)
        assert snapshot['model_mode'] == snapshot['requested_model_mode'] == 'tacc'
        assert not snapshot['api_model'] and not snapshot['ollama_model']
        assert job.owner_id == user['id'] and job.source == 'email'
        jid = job.id
    assert marked == ['fixed-tacc-mail']
    calls = []
    monkeypatch.setattr('app.worker.queued_service_base', lambda _: 'http://course-node:8000/v1')
    def remote(operation, event, checkpoint, **kwargs):
        calls.append(operation)
        return {'state': 'complete', 'rows': [], 'model_mode': 'tacc'}
    monkeypatch.setattr(tacc_sdk, 'run_remote', remote)
    monkeypatch.setattr(Model, 'call', lambda *_: pytest.fail('Used a non-SDK model'))
    # Later environment/browser choices cannot change the accepted job snapshot.
    monkeypatch.setattr(settings, 'model_mode', 'api')
    run_job(*claim_job())
    with session() as db:
        assert db.get(Job, jid).state == 'completed'
    assert calls == ['review']


def test_mail_does_not_fall_back_to_independent_api_without_tacc(client, sample, monkeypatch):
    monkeypatch.setattr(settings, 'model_mode', 'api')
    monkeypatch.setattr(settings, 'tacc_protocol', 'chat')
    monkeypatch.setattr(settings, 'tacc_url', '')
    monkeypatch.setattr(settings, 'tacc_model', '')
    monkeypatch.setattr(settings, 'llm_base_url', 'https://api.example.test/v1')
    monkeypatch.setattr(settings, 'model_name', 'independent-model')
    monkeypatch.setattr(settings, 'llm_api_key', 'synthetic-key')
    user = login(client)
    class Inbox:
        def attachments(self, message): return [('a', 'synthetic.txt', sample)]
        def mark_read(self, message): pytest.fail('Marked unaccepted email as read')
    with pytest.raises(RuntimeError, match='TACC'):
        receive_message(Inbox(), {'id': 'unavailable-course', 'subject': 'Review',
            'from': {'emailAddress': {'address': user['email']}}})
    with session() as db:
        assert not db.scalars(select(Job)).all()
        assert not db.scalars(select(Contract)).all()
        assert not db.scalars(select(File)).all()


def test_offline_mail_header_still_identifies_demo(client):
    assert settings.model_mode == 'demo'
    assert client.get('/api/config').json()['mail_model_mode'] == 'demo'
