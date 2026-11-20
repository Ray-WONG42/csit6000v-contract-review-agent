import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from .config import settings
from .db import session, Contract, File, Job, Skill, Delivery, LocalMail, User, dump, identifier
from .security import own
from .schemas import JobInput, SkillInput
from engine import DEFAULT_SKILL, MAX_FILE, docx_report


def write_file(db, owner_id, contract_id, name, raw, kind, job_id=""):
    file_id = identifier()
    directory = settings.data / "files" / owner_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_id
    temporary = directory / (file_id + ".partial")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
    entry = File(id=file_id, owner_id=owner_id, contract_id=contract_id, job_id=job_id,
                 name=name, path=str(path.relative_to(settings.data)), kind=kind)
    db.add(entry)
    return entry


def ingest(db, user, name, raw, company):
    name = Path(name.replace("\\", "/")).name[:180]
    if not name.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(422, "支持文字版 PDF、DOCX 和 TXT")
    if not raw or len(raw) > MAX_FILE:
        raise HTTPException(422, "文件必须非空且不超过 12 MB")
    contract_id = identifier()
    file = write_file(db, user.id, contract_id, name, raw, "input")
    item = Contract(id=contract_id, owner_id=user.id, company=company.strip()[:100] or "未归类",
                    name=name, file_id=file.id, sha256=hashlib.sha256(raw).hexdigest())
    db.add(item)
    db.flush()
    return item


def create_job(db, user, contract_id, body: JobInput, source="web", state="queued"):
    contract = own(db.get(Contract, contract_id), user)
    existing = db.scalar(select(Job).where(Job.owner_id == user.id, Job.request_key == body.request_key))
    if existing:
        if existing.contract_id != contract_id or existing.instruction != body.instruction:
            raise HTTPException(409, "此提交标识已用于另一个任务")
        return existing
    active = db.scalars(select(Job).where(Job.owner_id == user.id, Job.state.in_(
        ["queued", "parsing", "surveying", "extracting", "validating", "rendering"]))).all()
    if len(active) >= 5:
        raise HTTPException(429, "当前任务较多，请等待任务结束")
    skill = {**DEFAULT_SKILL, "policy": "supplier-demo@1", "version": 1}
    if body.skill_id:
        stored = own(db.get(Skill, body.skill_id), user)
        skill = {**json.loads(stored.content), "version": stored.version, "explicit": True}
    parent_id = ""
    if body.parent_id:
        parent = own(db.get(Job, body.parent_id), user)
        if parent.contract_id != contract.id:
            raise HTTPException(422, "前序任务必须来自同一合同")
        parent_id = parent.id
        if not body.skill_id:
            skill = json.loads(parent.skill)
            previous = json.loads(parent.result)
            skill["previous_categories"] = [row["category"] for row in previous.get("rows", [])]
            skill["previous_question"] = parent.instruction[:1000]
    item = Job(owner_id=user.id, contract_id=contract.id, request_key=body.request_key,
               instruction=body.instruction, parent_id=parent_id, skill=dump(skill), source=source, state=state)
    db.add(item)
    db.flush()
    return item


def graph_client():
    from mailbox import Outlook
    return Outlook()


def send_mail(recipient, subject, body, file=None):
    if settings.app_demo:
        with session() as db:
            db.add(LocalMail(recipient=recipient, subject=subject, body=body, file_id=file.id if file else ""))
        return "demo"
    client = graph_client()
    if not client.enabled:
        raise RuntimeError("Outlook 尚未配置。请设置 Graph 授权参数")
    attachment = None
    if file:
        raw = (settings.data / file.path).read_bytes()
        if len(raw) > 2_500_000:
            body += f"\n报告较大，请登录 {settings.app_public_url} 查看并下载。"
        else:
            attachment = (file.name, raw)
    client.send(recipient, subject, body, attachment)
    return "accepted"


def deliver_pending():
    with session() as db:
        delivery = db.scalar(select(Delivery).where(Delivery.state == "pending").order_by(Delivery.updated))
        if not delivery:
            return
        delivery.state = "sending"
        delivery.attempts += 1
        delivery.updated = time.time()
        db.flush()
        user = db.get(User, delivery.owner_id)
        job = db.get(Job, delivery.job_id)
        file = db.get(File, job.report_file) if job.report_file else None
    try:
        result = json.loads(job.result)
        text = result.get("message") or result.get("answer") or "合同处理已完成，报告见附件。"
        status = send_mail(user.email, f"合同审查结果 [{job.id[:8]}]", text, file)
        error = ""
    except Exception as exc:
        import urllib.error
        status = "failed" if isinstance(exc, (RuntimeError, urllib.error.HTTPError)) else "uncertain"
        error = type(exc).__name__ + "：请检查邮箱连接或发件记录"
    with session() as db:
        current = db.get(Delivery, delivery.id)
        current.state, current.error, current.updated = status, error, time.time()


def contract_view(item, email=""):
    return {"id": item.id, "name": item.name, "company": item.company, "file_id": item.file_id,
            "created": item.created, "owner_email": email}


def job_view(db, job, detail=False):
    delivery = db.scalar(select(Delivery).where(Delivery.job_id == job.id))
    value = {"id": job.id, "contract_id": job.contract_id, "source": job.source, "state": job.state,
             "instruction": job.instruction, "created": job.created, "error": job.error,
             "report_file": job.report_file, "delivery": delivery.state if delivery else ""}
    if detail:
        value.update(result=json.loads(job.result) or None, report=job.report)
    return value
