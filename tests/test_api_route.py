import io
import json
import urllib.request
from conftest import login
from app.config import settings
from app.worker import run_assistant_once
from engine import Model


def test_api_uses_own_endpoint_model_and_key_when_tacc_sdk_is_configured(monkeypatch):
    monkeypatch.setattr(settings, 'tacc_protocol', 'sdk')
    monkeypatch.setattr(settings, 'tacc_model', 'qwen-course')
    monkeypatch.setattr(settings, 'tacc_url', 'https://course.example.test/v1/chat/completions')
    monkeypatch.setattr(settings, 'tacc_api_key', 'course-test-key')
    monkeypatch.setattr(settings, 'llm_base_url', 'https://api.deepseek.com')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'llm_api_key', 'synthetic-api-key')
    def response(request, timeout):
        body = json.loads(request.data)
        assert request.full_url == 'https://api.deepseek.com/chat/completions'
        assert request.headers['Authorization'] == 'Bearer synthetic-api-key'
        assert body['model'] == 'deepseek-flash'
        assert body['thinking'] == {'type': 'disabled'}
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"ok":true}'}}]}).encode())
    monkeypatch.setattr(urllib.request, 'urlopen', response)
    assert Model(lambda *a: None, 'api').call('Return JSON', {}, 'probe') == {'ok': True}


def test_explicit_api_chat_never_calls_tacc(client, monkeypatch):
    from app import tacc_sdk
    monkeypatch.setattr(settings, 'model_mode', 'api')
    monkeypatch.setattr(settings, 'tacc_protocol', 'sdk')
    monkeypatch.setattr(settings, 'llm_base_url', 'https://api.example.test')
    monkeypatch.setattr(settings, 'model_name', 'synthetic-model')
    monkeypatch.setattr(settings, 'llm_api_key', 'synthetic-key')
    monkeypatch.setattr(tacc_sdk, 'run_remote', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('API called TACC')))
    def call(self, *args):
        assert self.mode == 'api'
        return {'action': 'final', 'answer': 'API route selected'}
    monkeypatch.setattr(Model, 'call', call)
    login(client)
    started = client.post('/api/assistant/runs', json={'request_key': 'separate-api-route', 'message': 'Hello', 'model_mode': 'api'})
    assert started.status_code == 200
    assert run_assistant_once()
    result = client.get('/api/assistant/runs/' + started.json()['id']).json()
    assert result['state'] == 'completed'
    assert result['result']['mode'] == 'api'
