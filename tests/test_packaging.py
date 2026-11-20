from app import assistant
from app.config import settings
from conftest import login
from prompt_loader import load_prompt


def test_disabled_tools_and_history_are_not_sent_to_planner(client, monkeypatch):
    user = login(client)
    monkeypatch.setattr(settings, 'model_mode', 'tacc')
    monkeypatch.setattr(settings, 'agent_tools', 'list_skills')
    monkeypatch.setattr(settings, 'agent_short_term_memory', False)
    def decide(system, payload):
        assert set(payload['tools']) == {'list_skills'}
        assert payload['history'] == []
        assert load_prompt('reflection') in system
        return {'action': 'final', 'answer': 'configured'}
    result = assistant.run_assistant(user['id'], {'message': 'hello', 'history': [{'role': 'user', 'content': 'private old turn'}]},
                                     'configured-run', lambda *a: None, lambda *a: None, decide)
    assert result['answer'] == 'configured'
