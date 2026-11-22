import json
from pathlib import Path

import pytest

from app import tacc_sdk


def test_client_submission_only_copies_needed_files(monkeypatch, tmp_path):
    monkeypatch.setattr(tacc_sdk.settings, "app_data", str(tmp_path))
    monkeypatch.setattr(tacc_sdk.settings, "tacc_url", "http://10-0-1-14:8000/v1")
    checkpoints = {}
    def checkpoint(key, value=None):
        if value is not None:
            checkpoints[key] = value
        return checkpoints.get(key)
    def fake_submit(staging):
        assert {p.name for p in staging.iterdir()} == {"job.sh", "tuxiv.conf", "remote.py", "resolve_endpoint.py", "engine.py", "request.json", "prompt_loader.py", "prompts", "receipt.json"}

        assert (staging / 'resolve_endpoint.py').is_file()
        request = json.loads((staging / "request.json").read_text())
        assert request["base_url"] == "http://10-0-1-14:8000/v1"
        assert request["operation"] == "review"
        return "123"
    monkeypatch.setattr(tacc_sdk, "submit", fake_submit)
    monkeypatch.setattr(tacc_sdk, "wait_for_result", lambda run, job, event: {"result": {"rows": []}})
    result = tacc_sdk.run_remote("review", lambda *_: None, checkpoint, paragraphs=[])
    assert result["tacc_client_job"] == "123"
    assert not list(tmp_path.rglob("request.json"))
    monkeypatch.setattr(tacc_sdk, "submit", lambda *_: pytest.fail("Resubmitted TACC job"))
    assert tacc_sdk.run_remote("review", lambda *_: None, checkpoint)["tacc_client_job"] == "123"


def test_wait_for_results_reads_structured_file(monkeypatch):
    run = "a" * 32
    sent = []
    def fake_sdk(args, **kwargs):
        if args[1].startswith("slurm_log/"):
            return 'CLAUSE_PROGRESS ["model","Call Qwen"]\nCLAUSE_RESULT_READY ' + run
        return '{"result":{"ok":true}}'
    monkeypatch.setattr(tacc_sdk, "sdk", fake_sdk)
    assert tacc_sdk.wait_for_result(run, "100", lambda *args: sent.append(args))["result"]["ok"]
    assert sent == [("model", "Call Qwen", "")]


@pytest.mark.parametrize('state', ['PD', 'R'])
def test_polling_window_expiry_keeps_active_client_pending(monkeypatch, state):
    clock = iter([0, 1000])
    monkeypatch.setattr(tacc_sdk.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(tacc_sdk, 'sdk', lambda args, **kw: f'123 task user {state}')
    with pytest.raises(tacc_sdk.TaccClientPending) as pending:
        tacc_sdk.wait_for_result('a' * 32, '123', lambda *_: None)
    assert pending.value.job_id == '123'


def test_expired_poll_reads_result_of_just_finished_job(monkeypatch):
    clock = iter([0, 1000])
    monkeypatch.setattr(tacc_sdk.time, 'monotonic', lambda: next(clock))
    def sdk(args, **kw):
        if args[0] == 'ps':
            raise tacc_sdk.TaccSDKError('ps', RuntimeError('Invalid job id specified'))
        assert args == ['cat', 'clause-results/' + 'a' * 32 + '.json']
        return '{"result":{"ok":true}}'
    monkeypatch.setattr(tacc_sdk, 'sdk', sdk)
    assert tacc_sdk.wait_for_result('a' * 32, '123', lambda *_: None)['result']['ok']


def test_transient_read_resumes_same_client_without_submission(monkeypatch):
    import subprocess
    saved = {'run': 'a' * 32, 'job_id': '123'}
    monkeypatch.setattr(tacc_sdk, 'submit', lambda *_: pytest.fail('Duplicate client'))
    def timeout(*_):
        raise tacc_sdk.TaccSDKError('cat', subprocess.TimeoutExpired('sdk', 60))
    monkeypatch.setattr(tacc_sdk, 'wait_for_result', timeout)
    with pytest.raises(tacc_sdk.TaccClientPending):
        tacc_sdk.run_remote('review', lambda *_: None, lambda key: saved)
    monkeypatch.setattr(tacc_sdk, 'wait_for_result', lambda *args: {'result': {'rows': []}})
    assert tacc_sdk.run_remote('review', lambda *_: None, lambda key: saved)['tacc_client_job'] == '123'


def test_confirmed_client_failure_is_not_hidden_as_waiting(monkeypatch):
    monkeypatch.setattr(tacc_sdk, 'sdk', lambda *a, **kw: 'CANCELLED: time limit')
    with pytest.raises(RuntimeError, match='客户端执行失败'):
        tacc_sdk.wait_for_result('a' * 32, '123', lambda *_: None)


def test_read_timeout_retries_but_submission_does_not(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(tacc_sdk.time, "sleep", lambda *_: None)
    def flaky(args, *unused):
        calls.append(args[0])
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("wsl", 60)
        return "ready"
    monkeypatch.setattr(tacc_sdk, "_sdk_once", flaky)
    assert tacc_sdk.sdk(["cat", "log"]) == "ready"
    assert len(calls) == 2
    calls.clear()
    with pytest.raises(tacc_sdk.TaccSDKError, match="提交作业") :
        tacc_sdk.sdk(["submit"])
    assert calls == ["submit"]


def test_missing_job_retains_machine_readable_reason(monkeypatch):
    def fail(*a): raise RuntimeError("TACC SDK failed: Invalid job id specified")
    monkeypatch.setattr(tacc_sdk, "_sdk_once", fail)
    with pytest.raises(tacc_sdk.TaccSDKError) as info:
        tacc_sdk.sdk(["ps", "-j", "1"])
    assert info.value.code == "job_missing"


def test_ambiguous_submission_checkpoint_blocks_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(tacc_sdk.settings, "app_data", str(tmp_path))
    monkeypatch.setattr(tacc_sdk.settings, "tacc_url", "http://10-0-1-14:8000/v1")
    memory = {}
    def checkpoint(k, v=None):
        if v is not None: memory[k] = v
        return memory.get(k)
    calls = []
    def submit(*a):
        calls.append(1)
        raise RuntimeError("lost submit response")
    monkeypatch.setattr(tacc_sdk, "submit", submit)
    with pytest.raises(RuntimeError):
        tacc_sdk.run_remote("review", lambda *_: None, checkpoint, paragraphs=[])
    with pytest.raises(RuntimeError, match="阻止重复提交"):
        tacc_sdk.run_remote("review", lambda *_: None, checkpoint, paragraphs=[])
    assert len(calls) == 1
    assert not list(tmp_path.rglob("request.json"))


def test_uncertain_gpu_submission_is_not_repeated(monkeypatch, tmp_path):
    monkeypatch.setattr(tacc_sdk.settings, "app_data", str(tmp_path))
    monkeypatch.setattr(tacc_sdk.settings, "tacc_url", "")
    submissions = []

    def submit(cwd):
        submissions.append(cwd)
        raise RuntimeError("lost submission response")

    monkeypatch.setattr(tacc_sdk, "submit", submit)
    with pytest.raises(RuntimeError, match="lost submission response"):
        tacc_sdk.queued_service_base(None)
    with pytest.raises(RuntimeError, match="提交结果不确定"):
        tacc_sdk.start_service_job()
    with pytest.raises(RuntimeError, match="提交结果不确定"):
        tacc_sdk.queued_service_base(None)
    assert len(submissions) == 1


def test_status_read_has_time_for_course_gateway(monkeypatch):
    captured = {}
    def read(args, **kwargs):
        captured.update(kwargs)
        return '30427 tacc clause-qwen-serve user R'
    monkeypatch.setattr(tacc_sdk, 'sdk', read)
    monkeypatch.setattr(tacc_sdk.settings, 'tacc_sdk_read_timeout', 60)
    assert tacc_sdk.service_job_active('30427')
    assert captured['timeout'] == 60


def test_failed_gpu_service_reports_memory_and_does_not_resubmit(monkeypatch, tmp_path):
    monkeypatch.setattr(tacc_sdk.settings, 'app_data', str(tmp_path))
    monkeypatch.setattr(tacc_sdk.settings, 'tacc_url', '')
    monkeypatch.setattr(tacc_sdk, 'service_log', lambda _: 'CLAUSE_STARTUP_FAILURE: insufficient free memory on an allocated GPU')
    status = tacc_sdk.inspect_service('123')
    assert status['state'] == 'failed' and '显存不足' in status['error']
    tacc_sdk.save_service({'job_id': '123', **status})
    monkeypatch.setattr(tacc_sdk, 'submit', lambda *_: pytest.fail('Resubmitted failed GPU service'))
    with pytest.raises(tacc_sdk.TaccServiceError, match='显存不足'):
        tacc_sdk.queued_service_base(None)


def test_expired_service_reports_time_limit_without_claiming_memory_failure(monkeypatch):
    monkeypatch.setattr(tacc_sdk, 'service_log', lambda _: 'JOB 123 CANCELLED DUE TO TIME LIMIT')
    status = tacc_sdk.inspect_service('123')
    assert status['state'] == 'failed' and not status['ready']
    assert '一小时' in status['error'] and '显存' not in status['error']


def test_automatic_service_honors_node_exclusion(monkeypatch, tmp_path):
    monkeypatch.setattr(tacc_sdk.settings, 'app_data', str(tmp_path))
    monkeypatch.setattr(tacc_sdk.settings, 'tacc_exclude_nodes', 'busy-node')
    def submit(cwd):
        assert '    - exclude=busy-node' in (cwd / 'tuxiv.conf').read_text()
        assert (cwd / 'job.sh').exists() and (cwd / 'service_supervisor.py').exists()
        return '123'
    monkeypatch.setattr(tacc_sdk, 'submit', submit)
    assert tacc_sdk._submit_service({})['job_id'] == '123'
