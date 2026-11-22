import copy
import json
import pytest
from sqlalchemy import select
from conftest import login
from app import assistant, tacc_sdk
from app.config import settings
from app.db import session, User


def test_context_pack_keeps_question_cursor_and_original_observations(monkeypatch):
    monkeypatch.setattr(settings, 'tacc_context_limit', 9000)
    monkeypatch.setattr(settings, 'tacc_max_output_tokens', 1000)
    result = {'contract_id': 'c', 'paragraphs': [{'id': 'P0001', 'text': '原文' * 6000}],
              'total_paragraphs': 50, 'start': 0, 'next_start': 3, 'truncated': True}
    observations = [{'tool': 'read_contract', 'arguments': {'contract_id': 'c'}, 'result': result}] * 3
    before = copy.deepcopy(observations)
    payload = {'message': '合同的重要结构有哪些？', 'tools': {},
               'history': [{'role': 'user', 'content': '保留背景' * 1000}] * 8,
               'observations': assistant.observation_context(observations)}
    packed = assistant.fit_chat_payload('system', payload, 'tacc')
    assert packed['message'] == payload['message']
    assert len(packed['observations']) == 1
    assert packed['observations'][0]['coverage']['next_start'] == 3
    assert packed['observations'][0]['excerpt_truncated']
    assert observations == before
    assert len(json.dumps(packed, ensure_ascii=False).encode()) < 8000


def test_unfittable_current_question_is_rejected_before_remote_submission(monkeypatch):
    monkeypatch.setattr(settings, 'tacc_context_limit', 4096)
    monkeypatch.setattr(settings, 'tacc_max_output_tokens', 1000)
    with pytest.raises(ValueError, match='尚未提交 TACC'):
        assistant.fit_chat_payload('system', {'message': '当前问题' * 3000, 'tools': {}, 'history': [], 'observations': []}, 'tacc')


def test_read_contract_has_real_cursor_and_section_outline(client):
    who = login(client)
    raw = ('Section 1. Definitions\n' + 'A' * 1700 + '\n\nSection 2. Payment\n' + 'B' * 1700 + '\n\nSection 2.8 [Escalation] below), a cross-reference.\n\nSection 3. Termination\n' + 'C' * 1700).encode()
    cid = client.post('/api/contracts', files={'file': ('synthetic.txt', raw)}).json()['id']
    with session() as db:
        user = db.get(User, who['id'])
    first = assistant.run_tool('read_contract', {'contract_id': cid, 'limit': 1}, user, 'read', 0, {})
    second = assistant.run_tool('read_contract', {'contract_id': cid, 'start': first['next_start'], 'limit': 1}, user, 'read', 1, {})
    assert first['paragraphs'][0]['id'] != second['paragraphs'][0]['id']
    assert len(first['outline']) == 3
    assert first['truncated']


def test_remote_context_failure_is_not_reported_as_network_failure(monkeypatch):
    run = 'a' * 32
    def sdk(args, **kwargs):
        if args[1].startswith('slurm_log/'):
            return 'CLAUSE_RESULT_READY ' + run
        return '{"error":"ContextBudgetExceeded"}'
    monkeypatch.setattr(tacc_sdk, 'sdk', sdk)
    with pytest.raises(ValueError, match='不是网络断线'):
        tacc_sdk.wait_for_result(run, '123', lambda *_: None)


def test_equivalent_contract_reads_reuse_result_and_normalize_defaults(client, monkeypatch):
    user = login(client)
    monkeypatch.setattr(settings, 'model_mode', 'api')
    calls = []
    def read(*args):
        calls.append(1)
        return {'contract_id': 'c', 'paragraphs': [], 'start': 0, 'next_start': 8, 'truncated': True}
    monkeypatch.setattr(assistant, 'run_tool', read)
    decisions = iter([
        {'action': 'tool', 'tool': 'read_contract', 'arguments': {'contract_id': 'c'}},
        {'action': 'tool', 'tool': 'read_contract', 'arguments': {'contract_id': 'c', 'start': 0}},
        {'action': 'final', 'answer': 'Use the next page.'}])
    state = {}
    def checkpoint(k, v=None):
        if v is not None: state[k] = v
        return state.get(k)
    result = assistant.run_assistant(user['id'], {'message': 'Explain the contract', 'history': []},
                                     'cache-read', lambda *_: None, checkpoint, lambda *_: next(decisions))
    assert result['answer'] and calls == [1]
    assert state['observations'][1]['result']['reused']
    assert len(assistant.observation_context(state['observations'])) == 1


def test_schema_compaction_preserves_fields_named_title_and_validation_rules():
    schema = assistant.compact_schema(assistant.SaveSkillArgs.model_json_schema())
    custom = next(item for item in schema['$defs'].values() if 'question' in item.get('properties', {}))
    assert 'title' in custom['properties'] and 'title' in custom['required']
    assert custom['properties']['title']['type'] == 'string'
    assert schema['additionalProperties'] is False
