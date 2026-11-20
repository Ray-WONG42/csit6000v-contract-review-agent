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
        assert {p.name for p in staging.iterdir()} == {"job.sh", "tuxiv.conf", "remote.py", "resolve_endpoint.py", "engine.py", "request.json", "prompt_loader.py", "prompts"}

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
    with pytest.raises(subprocess.TimeoutExpired):
        tacc_sdk.sdk(["submit"])
    assert calls == ["submit"]
