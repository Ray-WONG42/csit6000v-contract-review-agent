import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import select
from conftest import login
from app.config import settings
from app.db import session, AssistantRun, Job, Delivery, File, FileRemoval, Ticket, User, Checkpoint, Event, Receipt
from app.worker import claim_job, run_job, receive_message, run_assistant_once
from app.services import deliver_pending, remove_deleted_files, write_file
from app.security import digest
from engine import parse, review, Model, DEFAULT_SKILL, validate, semantic_chunks, annotate


def submit(client, sample, key="test-request-1", source="web"):
    if source == "email":
        r = client.post("/api/demo/mail", files={"file": ("sample.txt", sample)}, data={"instruction": "标准审查"})
        assert r.status_code == 200, r.text
        return r.json()["contract_id"], r.json()["job_id"]
    r = client.post("/api/contracts", files={"file": ("sample.txt", sample)}, data={"company": "Acme"})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    r = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": key})
    assert r.status_code == 200, r.text
    return cid, r.json()["id"]


def execute():
    claimed = claim_job()
    assert claimed
    run_job(*claimed)
    return claimed


def test_delete_history_keeps_unrelated_records_and_prevents_mail_reimport(client, sample):
    user = login(client)
    cid, jid = submit(client, sample, source="email")
    execute()
    deliver_pending()
    report = client.get(f"/api/jobs/{jid}").json()["report_file"]
    edited = client.post(f"/api/jobs/{jid}/report-versions", json={"content": "Private draft"}).json()["file_id"]
    followup = client.post(f"/api/contracts/{cid}/jobs", json={"parent_id": jid, "request_key": "followup"}).json()["id"]
    run = client.post("/api/assistant/runs", json={
        "request_key": "contract-chat", "contract_id": cid, "message": "/生成文档 合同审查笔记",
    }).json()["id"]
    with session() as db:
        db.add(Receipt(id="test-mail-digest", job_id=jid, state="accepted"))
        paths = [settings.data / db.get(File, id).path for id in (report, edited)]
    assert all(path.is_file() for path in paths)
    assert client.delete(f"/api/contracts/{cid}").status_code == 409
    assert client.delete(f"/api/jobs/{followup}").status_code == 409
    client.headers["x-csrf-token"] = "invalid"
    assert client.delete(f"/api/jobs/{jid}").status_code == 403
    login(client, "other")
    assert client.delete(f"/api/contracts/{cid}").status_code == 404
    assert client.delete(f"/api/jobs/{jid}").status_code == 404
    login(client, "admin")
    assert client.delete(f"/api/contracts/{cid}").status_code == 403
    assert client.delete(f"/api/jobs/{jid}").status_code == 403
    login(client)
    assert client.delete(f"/api/jobs/{jid}").json() == {"ok": True}
    assert client.get(f"/api/jobs/{jid}/events").status_code == 404
    assert client.get(f"/api/files/{report}").status_code == 404
    assert client.get(f"/api/files/{edited}").status_code == 404
    assert client.get("/api/mail/local").json() == []
    assert not any(path.exists() for path in paths)
    with session() as db:
        assert db.get(Job, followup).parent_id == ""
        assert db.get(Receipt, "test-mail-digest").job_id == ""
        assert db.scalar(select(Event.id).where(Event.job_id == jid)) is None
        assert db.scalar(select(Checkpoint.id).where(Checkpoint.job_id == jid)) is None
        assert db.scalar(select(Delivery.id).where(Delivery.job_id == jid)) is None

    execute()
    assert run_assistant_once()
    chat_doc = client.get(f"/api/assistant/runs/{run}").json()["result"]["artifacts"][0]["file_id"]
    with session() as db:
        # Exercise older rows whose contract association exists only in File.job_id.
        db.get(File, chat_doc).contract_id = ""
        unrelated = write_file(db, user["id"], "", "Unrelated.docx", b"independent", "draft").id
    original = client.get("/api/contracts").json()[0]["file_id"]
    assert client.delete(f"/api/contracts/{cid}").json() == {"ok": True}
    for path in (f"/api/jobs/{followup}", f"/api/assistant/runs/{run}", f"/api/files/{chat_doc}", f"/api/files/{original}"):
        assert client.get(path).status_code == 404
    assert client.get(f"/api/files/{unrelated}").content == b"independent"
    assert client.get("/api/contracts").json() == []
    login(client, "admin")
    assert client.get("/api/admin/contracts").json() == []


def test_delete_history_retries_attachment_cleanup(client, sample, monkeypatch):
    login(client)
    contract = client.post("/api/contracts", files={"file": ("sample.txt", sample)}).json()
    with session() as db:
        stored_path = db.get(File, contract["file_id"]).path
    original_unlink = Path.unlink
    def blocked_unlink(path, *args, **kwargs):
        if path == settings.data / stored_path:
            raise PermissionError("attachment is open")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as controlled:
        controlled.setattr(Path, "unlink", blocked_unlink)
        assert client.delete("/api/contracts/" + contract["id"]).status_code == 200
        assert client.get("/api/files/" + contract["file_id"]).status_code == 404
    with session() as db:
        assert db.get(FileRemoval, stored_path)
    remove_deleted_files()
    assert not (settings.data / stored_path).exists()
    with session() as db:
        assert db.get(FileRemoval, stored_path) is None


def test_end_to_end_and_cross_account_security(client, sample):
    login(client)
    cid, jid = submit(client, sample)
    execute()
    detail = client.get("/api/jobs/" + jid)
    assert detail.status_code == 200, detail.text
    job = detail.json()
    assert job["state"] == "completed", job
    assert len(job["result"]["rows"]) == 8
    config_categories = {item["id"]: item["label"] for item in client.get("/api/config").json()["categories"]}
    assert config_categories["agreement_date"] == "协议日期" and "liquidated_damages" not in config_categories
    dates = {row["category"]: row for row in job["result"]["rows"]}
    assert "liquidated_damages" not in dates
    assert dates["agreement_date"]["evidence"][0]["quote"] == "This Agreement is dated 15 January 2026"
    renewal = next(r for r in job["result"]["rows"] if r["category"] == "automatic_renewal")
    assert renewal["assessment"] == "符合"
    assert len(renewal["evidence"]) == 2
    assert [item["retrieval_sources"] for item in renewal["evidence"]] == [["keyword"], ["adjacent"]]
    events = client.get(f"/api/jobs/{jid}/events").json()
    assert any(e["stage"] == "expand" for e in events)
    report = client.get("/api/files/" + job["report_file"])
    assert report.status_code == 200
    with zipfile.ZipFile(io.BytesIO(report.content)) as archive:
        assert "word/document.xml" in archive.namelist()
        assert b"<w:tbl>" in archive.read("word/document.xml")
    draft = client.post(f"/api/jobs/{jid}/report-versions", json={"content": "# Edited\nReviewed by human"}).json()["file_id"]
    login(client, "other")
    for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/events", f"/api/contracts/{cid}/paragraphs", "/api/files/" + job["report_file"]):
        assert client.get(path).status_code == 404
    assert client.post(f"/api/jobs/{jid}/report-versions", json={"content": "attack"}).status_code == 404
    login(client, "admin")
    assert client.get("/api/admin/contracts").json()[0]["id"] == cid
    assert client.get("/api/files/" + job["report_file"]).status_code == 200
    assert client.get("/api/files/" + draft).status_code == 404
    assert client.get(f"/api/jobs/{jid}/events").status_code == 403
    artifacts = client.get(f"/api/admin/contracts/{cid}/artifacts").json()
    assert {f["kind"] for f in artifacts} == {"input", "report"}


def test_chinese_review_uses_faiss_and_saves_verified_source(client, monkeypatch):
    from app.config import settings

    question = "收到发票后多久付款？"
    selected_model = "review-model:latest"
    clause = "Invoices are due within forty-five days after receipt."
    sample = ("Opening.\n\nTerms.\n\nNotices.\n\nInsurance.\n\nAppendix A\n\n" + clause).encode()
    monkeypatch.setattr(settings, "model_mode", "ollama")
    monkeypatch.setattr(settings, "contract_vector_search", True)
    monkeypatch.setattr(Model, "local_ollama_models", lambda self, selected: [selected_model])

    def embed(self, texts):
        return [[1, 0] if text in (question, clause) else [0, 1] for text in texts]

    def call(self, system, payload, phase):
        assert self.ollama_model == selected_model
        if phase == "survey":
            return {}
        assert phase == "custom_question"
        assert any(p["id"] == "P0006" for p in payload["paragraphs"])
        return {"rows": [{"category": phase, "status": "found", "evidence": [
            {"paragraph_id": "P0006", "quote": clause, "retrieval_sources": ["keyword"]}]}]}

    monkeypatch.setattr(Model, "embed", embed)
    monkeypatch.setattr(Model, "call", call)
    login(client)
    cid = client.post("/api/contracts", files={"file": ("sample.txt", sample)}).json()["id"]
    body = {"instruction": question, "request_key": "faiss-test-1", "ollama_model": selected_model}
    job = client.post(f"/api/contracts/{cid}/jobs", json=body).json()
    assert job["model_name"] == selected_model
    jid = job["id"]
    assert client.post(f"/api/contracts/{cid}/jobs", json={**body, "ollama_model": "other-model:latest"}).status_code == 409
    execute()
    detail = client.get(f"/api/jobs/{jid}").json()
    assert detail["state"] == "completed", detail
    assert detail["result"]["model_name"] == selected_model
    assert detail["result"]["rows"][0]["evidence"] == [
        {"paragraph_id": "P0006", "quote": clause, "page": None, "retrieval_sources": ["vector"]}]
    with session() as db:
        saved = db.scalar(select(Checkpoint).where(Checkpoint.job_id == jid, Checkpoint.key == "vector_hits"))
        assert json.loads(saved.value)["custom_question"][0] == "P0006"


def test_batch_evidence_labels_both_retrieval_methods():
    paragraphs = parse("sample.txt", b"Termination may occur on notice.\n\nCancellation requires consent."
                        b"\n\nEither party may end the agreement after 45 days.")

    class ControlledModel(Model):
        def call(self, system, payload, phase):
            assert phase == "batch_extract"
            return {"rows": [{"category": "termination", "status": "found", "evidence": [
                {"paragraph_id": p["id"], "quote": p["text"]} for p in payload["paragraphs"]]}]}

    result = review(paragraphs, {**DEFAULT_SKILL, "categories": ["termination"]}, "",
                    ControlledModel(lambda *_: None, "tacc"),
                    vector_hits={"termination": ["P0003", "P0002"]})
    assert {item["paragraph_id"]: item["retrieval_sources"] for item in result["rows"][0]["evidence"]} == {
        "P0001": ["keyword"], "P0002": ["keyword", "vector"], "P0003": ["vector"]}


def test_tacc_selection_routes_chat_and_reviews_through_sdk(client, monkeypatch):
    from app import tacc_sdk
    from app.config import settings

    monkeypatch.setattr(settings, "model_mode", "ollama")
    monkeypatch.setattr(settings, "tacc_protocol", "sdk")
    monkeypatch.setattr(settings, "tacc_url", "http://tacc-node:8000/v1")
    monkeypatch.setattr(settings, "contract_vector_search", False)
    login(client)
    assert client.get("/api/config").json()["tacc_configured"]
    cid = client.post("/api/contracts", files={"file": ("sample.txt", b"Notices must be in writing.")}).json()["id"]
    operations = []

    def run_remote(operation, event, checkpoint, **arguments):
        operations.append(operation)
        if operation == "decision":
            if not arguments["payload"]["observations"]:
                return {"action": "tool", "tool": "review_contract", "arguments": {"contract_id": cid, "instruction": "标准审查"}}
            return {"action": "final", "answer": "已创建审查。"}
        assert operation == "review" and arguments["skill"]["model_mode"] == "tacc"
        return {"state": "complete", "rows": [], "model_mode": "tacc"}

    monkeypatch.setattr(tacc_sdk, "run_remote", run_remote)
    body = {"request_key": "tacc-chat-1", "message": "审查这份合同", "model_mode": "tacc", "contract_id": cid}
    run_id = client.post("/api/assistant/runs", json=body).json()["id"]
    assert run_assistant_once()
    chat = client.get(f"/api/assistant/runs/{run_id}").json()["result"]
    assert chat["mode"] == "tacc"
    linked_job = chat["reviews"][0]["job_id"]
    assert client.get(f"/api/jobs/{linked_job}").json()["model_mode"] == "tacc"
    execute()
    assert client.get(f"/api/jobs/{linked_job}").json()["result"]["model_mode"] == "tacc"

    review_body = {"request_key": "tacc-job-1", "model_mode": "tacc"}
    review = client.post(f"/api/contracts/{cid}/jobs", json=review_body).json()
    assert review["model_mode"] == "tacc"
    assert client.post(f"/api/contracts/{cid}/jobs", json={**review_body, "model_mode": "ollama"}).status_code == 409
    execute()
    assert client.get(f"/api/jobs/{review['id']}").json()["result"]["model_mode"] == "tacc"
    assert operations == ["decision", "decision", "review", "review"]


def test_unavailable_tacc_cannot_be_selected(client, monkeypatch):
    from app import tacc_sdk
    from app.config import settings

    monkeypatch.setattr(settings, "model_mode", "ollama")
    monkeypatch.setattr(settings, "tacc_protocol", "sdk")
    monkeypatch.setattr(settings, "tacc_url", "")
    monkeypatch.setattr(tacc_sdk, "sdk_executable", lambda: None)
    login(client)
    assert not client.get("/api/config").json()["tacc_configured"]
    request = {"request_key": "tacc-chat-1", "message": "你好", "model_mode": "tacc"}
    assert client.post("/api/assistant/runs", json=request).status_code == 503
    monkeypatch.setattr(settings, "model_mode", "demo")
    assert client.post("/api/assistant/runs", json=request).status_code == 422


def test_tacc_requests_wait_for_one_gpu_service_then_resume(client, monkeypatch, tmp_path):
    from app import tacc_sdk
    from app.config import settings

    monkeypatch.setattr(settings, "app_data", str(tmp_path))
    monkeypatch.setattr(settings, "model_mode", "ollama")
    monkeypatch.setattr(settings, "tacc_protocol", "sdk")
    monkeypatch.setattr(settings, "tacc_url", "")
    monkeypatch.setattr(settings, "contract_vector_search", False)
    monkeypatch.setattr(tacc_sdk, "sdk_executable", lambda: "tcloud")
    submissions = []
    clients = {}

    def submit(cwd):
        if cwd.name == "serve":
            submissions.append("gpu")
            return "100"
        request = json.loads((cwd / "request.json").read_text())
        submissions.append(request["operation"])
        job_id = str(100 + len(submissions))
        clients[job_id] = request
        return job_id

    def sdk(args, **kwargs):
        if args == ["ps", "-j", "100"]:
            return "100 clause-qwen-serve RUNNING"
        if args == ["cat", "slurm_log/clause-qwen-serve-100.log"]:
            return "SERVICE_BASE_URL=http://gpu-node:8000/v1\nApplication startup complete\n"
        raise AssertionError(args)

    def result(run, job_id, event):
        request = clients[job_id]
        assert request["run"] == run
        if request["operation"] == "decision":
            return {"result": {"action": "final", "answer": "集群回复"}}
        return {"result": {"state": "complete", "rows": [], "model_mode": "tacc"}}

    monkeypatch.setattr(tacc_sdk, "submit", submit)
    monkeypatch.setattr(tacc_sdk, "sdk", sdk)
    monkeypatch.setattr(tacc_sdk, "wait_for_result", result)

    login(client)
    assert client.get("/api/config").json()["tacc_configured"]
    run_id = client.post("/api/assistant/runs", json={
        "request_key": "queue-chat-1", "message": "你好", "model_mode": "tacc"}).json()["id"]
    assert run_assistant_once()
    assert client.get(f"/api/assistant/runs/{run_id}").json()["state"] == "waiting_tacc"

    login(client, "other")
    cid = client.post("/api/contracts", files={"file": ("sample.txt", b"Notices must be in writing.")}).json()["id"]
    job_id = client.post(f"/api/contracts/{cid}/jobs", json={
        "request_key": "queue-review-1", "model_mode": "tacc"}).json()["id"]
    execute()
    assert client.get(f"/api/jobs/{job_id}").json()["state"] == "waiting_tacc"
    assert client.get(f"/api/assistant/runs/{run_id}").status_code == 404
    assert submissions == ["gpu"]
    assert not run_assistant_once() and claim_job() is None

    with session() as db:
        for item in (db.get(AssistantRun, run_id), db.get(Job, job_id)):
            assert item.attempts == 0
            item.lease = 0
    tacc_sdk.save_service({**tacc_sdk.service_record(), "checked_at": 0})
    assert run_assistant_once()
    execute()
    assert client.get(f"/api/jobs/{job_id}").json()["result"]["model_mode"] == "tacc"
    login(client)
    assert client.get(f"/api/assistant/runs/{run_id}").json()["result"]["answer"] == "集群回复"
    assert submissions == ["gpu", "decision", "review"]


def test_known_tacc_client_waiting_recovers_without_failure_or_resubmission(client, sample, monkeypatch):
    from app import tacc_sdk
    monkeypatch.setattr(settings, 'model_mode', 'ollama')
    monkeypatch.setattr(settings, 'tacc_protocol', 'sdk')
    monkeypatch.setattr(tacc_sdk, 'sdk_executable', lambda: 'tcloud')
    login(client)
    cid = client.post('/api/contracts', files={'file': ('sample.txt', sample)}).json()['id']
    jid = client.post(f'/api/contracts/{cid}/jobs', json={
        'request_key': 'resume-known-client', 'model_mode': 'tacc'}).json()['id']
    with session() as db:
        db.add(Checkpoint(job_id=jid, key='tacc_client', value=json.dumps({'run': 'a' * 32, 'job_id': '123'})))
    monkeypatch.setattr(tacc_sdk, 'submit', lambda *_: pytest.fail('Resubmitted known client'))
    def pending(*_): raise tacc_sdk.TaccClientPending('123')
    monkeypatch.setattr(tacc_sdk, 'wait_for_result', pending)
    execute()
    status = client.get(f'/api/jobs/{jid}').json()
    assert status['state'] == 'tacc_client' and not status['error']
    with session() as db:
        job = db.get(Job, jid)
        assert job.attempts == 0
        job.lease = 0
    monkeypatch.setattr(tacc_sdk, 'wait_for_result', lambda *_: {'result': {'state': 'complete', 'rows': []}})
    execute()
    assert client.get(f'/api/jobs/{jid}').json()['state'] == 'completed'


def test_idempotency_and_new_question(client, sample):
    login(client)
    cid, jid = submit(client, sample)
    response = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "test-request-1"})
    assert response.json()["id"] == jid
    second = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "new-question-1", "instruction": "检查续约", "parent_id": jid})
    assert second.status_code == 200 and second.json()["id"] != jid
    mismatch = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "test-request-1", "instruction": "changed"})
    assert mismatch.status_code == 409


def test_verification_expiry_attempts_and_csrf(client):
    code = client.post("/api/auth/send-code", json={"email": "new@example.com"}).json()["demo_code"]
    response = client.post("/api/auth/register", json={"email": "new@example.com", "password": "NewPassword2026!", "code": code})
    assert response.status_code == 200
    assert client.post("/api/auth/register", json={"email": "new@example.com", "password": "NewPassword2026!", "code": code}).status_code == 422
    login(client)
    client.headers["x-csrf-token"] = "wrong"
    assert client.post("/api/mail/ticket").status_code == 403
    client.headers.pop("x-csrf-token")
    assert client.post("/api/auth/send-code", headers={"origin": "https://evil.example"}, json={"email": "victim@example.com"}).status_code == 403
    code = client.post("/api/auth/send-code", json={"email": "locked@example.com"}).json()["demo_code"]
    for _ in range(5):
        assert client.post("/api/auth/register", json={"email": "locked@example.com", "password": "NewPassword2026!", "code": "000000"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "locked@example.com", "password": "NewPassword2026!", "code": code}).status_code == 422


def test_demo_mail_delivery_and_reuse(client, sample):
    login(client)
    _, jid = submit(client, sample, source="email")
    execute()
    deliver_pending()
    local = client.get("/api/mail/local").json()
    assert len(local) == 1 and local[0]["file_id"]
    deliver_pending()
    assert len(client.get("/api/mail/local").json()) == 1
    assert client.get("/api/jobs/" + jid).json()["delivery"] == "demo"


def test_worker_claim_recovery_and_fencing(client, sample):
    login(client)
    _, jid = submit(client, sample)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim_job(), range(2)))
    claims = [r for r in results if r]
    assert len(claims) == 1
    with session() as db:
        db.get(Job, jid).lease = time.time() - 10
    new_claim = claim_job()
    assert new_claim and new_claim[1] != claims[0][1]
    run_job(*claims[0])
    with session() as db:
        assert db.get(Job, jid).claim == new_claim[1]
    run_job(*new_claim)
    assert client.get("/api/jobs/" + jid).json()["state"] == "completed"


def test_mail_receipt_dedup_and_confirmation(client, sample):
    user = login(client)
    ticket = client.post("/api/mail/ticket").json()["ticket"]
    class Inbox:
        def attachments(self, message): return [("att", "sample.txt", sample)]
        def mark_read(self, message): pass
    message = {"id": "mail-1", "from": {"emailAddress": {"address": user["email"]}},
               "subject": "[TOKEN=" + ticket + "] review", "body": {"content": ""}}
    receive_message(Inbox(), message)
    receive_message(Inbox(), message)
    with session() as db:
        assert len(db.scalars(select(Job)).all()) == 1
        assert db.scalar(select(Job)).state == "queued"
    message["id"] = "mail-2"
    receive_message(Inbox(), message)
    with session() as db:
        states = [j.state for j in db.scalars(select(Job))]
        assert "awaiting_confirmation" in states


def test_skill_snapshot_and_followup(client, sample):
    login(client)
    skill = {"name": "续约检查", "categories": ["automatic_renewal"], "focus": "通知期限", "policy": "supplier-demo@1"}
    sid = client.post("/api/skills", json=skill).json()["id"]
    cid, _ = submit(client, sample)
    jid = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "skill-request-1", "skill_id": sid}).json()["id"]
    skill["categories"] = ["insurance"]
    assert client.put("/api/skills/" + sid, json=skill).status_code == 200
    with session() as db:
        assert json.loads(db.get(Job, jid).skill)["categories"] == ["automatic_renewal"]


def test_new_standard_review_replaces_date_category_after_legacy_job(client, sample):
    login(client)
    cid, legacy_id = submit(client, sample)
    old_categories = ["liquidated_damages" if key == "agreement_date" else key for key in DEFAULT_SKILL["categories"]]
    with session() as db:
        job = db.get(Job, legacy_id)
        job.skill = json.dumps({**json.loads(job.skill), "categories": old_categories})
    execute()
    legacy = client.get(f"/api/jobs/{legacy_id}").json()["result"]["rows"]
    assert legacy[-1]["category"] == "liquidated_damages" and legacy[-1]["status"] == "found"

    followup = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "new-standard-date",
                           "instruction": "标准审查", "parent_id": legacy_id}).json()
    execute()
    current = client.get(f"/api/jobs/{followup['id']}").json()["result"]["rows"]
    assert current[-1]["category"] == "agreement_date" and current[-1]["status"] == "found"


@pytest.mark.parametrize(("text", "date"), [
    ("Agreement Date | January 15, 2026", "January 15, 2026"),
    ("本合同签订于2026年1月15日。", "2026年1月15日"),
    ("The effective date is 15 January 2026. The first payment is due on 1 February 2026.", None),
])
def test_agreement_date_requires_explicit_date_evidence(text, date):
    result = review(parse("sample.txt", text.encode()), DEFAULT_SKILL, "协议日期", Model(lambda *_: None, "demo"))
    [row] = result["rows"]
    assert row["category"] == "agreement_date"
    if date:
        assert row["status"] == "found" and date in row["evidence"][0]["quote"]
    else:
        assert row["status"] == "not_found" and not row["evidence"]


def test_followup_uses_previous_scope_and_cached_survey(client, sample):
    login(client)
    cid, _ = submit(client, sample)
    execute()
    first = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "renewal-question", "instruction": "检查自动续约"}).json()
    execute()
    trace = client.get(f"/api/jobs/{first['id']}/events").json()
    assert any(e["stage"] == "cache" and "通读" in e["title"] for e in trace)
    second = client.post(f"/api/contracts/{cid}/jobs", json={"request_key": "referential-question", "instruction": "那需要多久通知？", "parent_id": first["id"]}).json()
    execute()
    result = client.get("/api/jobs/" + second["id"]).json()["result"]
    assert [r["category"] for r in result["rows"]] == ["automatic_renewal"]


def test_parsing_docx_and_pdf():
    root = Path(__file__).resolve().parent.parent / "fixtures"
    for suffix in (".docx", ".pdf"):
        path = root / ("Acme-Service-Agreement" + suffix)
        paragraphs = parse(path.name, path.read_bytes())
        assert any("automatically renew" in p["text"] for p in paragraphs)
        assert any("This Agreement is dated 15 January 2026" in p["text"] for p in paragraphs)
        assert len({p["id"] for p in paragraphs}) == len(paragraphs)


def test_long_chunk_coverage():
    text = ("First sentence has words. " * 900) + "TAILWITHOUTSPACES"
    paragraphs = [{"id": "P0001", "page": 1, "text": text}]
    chunks = list(semantic_chunks(paragraphs, 1000))
    pieces = [p for chunk in chunks for p in chunk]
    assert "".join(p["text"] for p in pieces) == text
    assert all(len(p["text"]) <= 1000 for p in pieces)
    assert pieces[-1]["end"] == len(text)


def test_fake_evidence_is_rejected(sample):
    paragraphs = parse("sample.txt", sample)
    with pytest.raises(ValueError):
        validate("automatic_renewal", {"status": "found", "evidence": [{"paragraph_id": "P0001", "quote": "Unlimited liability."}]}, paragraphs)
    with pytest.raises(ValueError):
        validate("automatic_renewal", {"status": "not_found", "evidence": [{"paragraph_id": "P0001", "quote": paragraphs[0]["text"]}]}, paragraphs)


@pytest.mark.parametrize("text,expected", [
    ("Either party shall give 60 calendar days written notice.", "符合"),
    ("Either party shall give 15 calendar days written notice.", "不符合"),
    ("Either party shall give reasonable notice.", "待核验"),
    ("Either party shall give 60 business days written notice.", "待核验"),
])
def test_policy_units(text, expected):
    row = {"category": "automatic_renewal", "status": "found", "missing": [],
           "evidence": [{"paragraph_id": "P1", "quote": text}]}
    assert annotate(row, {"policy": "supplier-demo@1"})["assessment"] == expected


def test_timeout_and_invalid_json_become_review(sample):
    class Broken(Model):
        def __init__(self): super().__init__(lambda *args: None, "tacc")
        def call(self, *args, **kwargs): raise TimeoutError("test")
    result = review(parse("sample.txt", sample), {**DEFAULT_SKILL, "explicit": True}, "", Broken())
    assert result["state"] == "failed"
    assert all(row["status"] == "review" for row in result["rows"])
