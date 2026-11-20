import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from sqlalchemy import select
from conftest import login
from app.db import session, Job, Delivery, File, Ticket, User, Checkpoint
from app.worker import claim_job, run_job, receive_message
from app.services import deliver_pending
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


def test_end_to_end_and_cross_account_security(client, sample):
    login(client)
    cid, jid = submit(client, sample)
    execute()
    detail = client.get("/api/jobs/" + jid)
    assert detail.status_code == 200, detail.text
    job = detail.json()
    assert job["state"] == "completed", job
    assert len(job["result"]["rows"]) == 8
    renewal = next(r for r in job["result"]["rows"] if r["category"] == "automatic_renewal")
    assert renewal["assessment"] == "符合"
    assert len(renewal["evidence"]) == 2
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
