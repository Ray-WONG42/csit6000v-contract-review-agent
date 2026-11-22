import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text, update
from .config import settings
from .db import (session, AssistantRun, Checkpoint, Contract, Delivery, Event, File, FileRemoval,
                 Job, LocalMail, Receipt, Skill, User, dump, identifier)
from .security import own
from .schemas import JobInput, SkillInput
from engine import DEFAULT_SKILL, MAX_FILE, docx_report, is_standard_request


ACTIVE_REVIEW_STATES = ("queued", "waiting_tacc", "tacc_client", "parsing", "surveying",
                        "extracting", "validating", "rendering")
ACTIVE_ASSISTANT_STATES = ("queued", "waiting_tacc", "running")
logger = logging.getLogger(__name__)


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


def tacc_configured():
    if settings.tacc_protocol == "sdk":
        from .tacc_sdk import get_base_url, sdk_executable
        if not settings.tacc_url:
            return bool(sdk_executable())
        try:
            return bool(get_base_url())
        except (ValueError, OSError):
            return False
    return bool((settings.tacc_url or settings.llm_base_url) and (settings.tacc_model or settings.model_name))


def resolve_model_selection(model_mode, ollama_model):
    mode = model_mode or settings.model_mode
    if settings.model_mode == "demo" and (model_mode or ollama_model):
        raise HTTPException(422, "当前为演示模式，无法选择真实模型。")
    if ollama_model and mode != "ollama":
        raise HTTPException(422, "请选择 Ollama 模式后再指定本地模型。")
    if model_mode == "tacc" and not tacc_configured():
        raise HTTPException(503, "工作台尚未配置 TACC 连接。")
    if mode == "api" and not (settings.llm_base_url and settings.model_name and settings.llm_api_key):
        raise HTTPException(503, "API 模型尚未配置")
    return {"model_mode": mode, "api_model": settings.model_name if mode == "api" else "", "ollama_model": (ollama_model or settings.ollama_model) if mode == "ollama" else ""}


def create_job(db, user, contract_id, body: JobInput, source="web", state="queued", model_snapshot=None):
    contract = own(db.get(Contract, contract_id), user)
    existing = db.scalar(select(Job).where(Job.owner_id == user.id, Job.request_key == body.request_key))
    if existing:
        saved_skill = json.loads(existing.skill)
        if (existing.contract_id != contract_id or existing.instruction != body.instruction
                or saved_skill.get("language", "zh") != body.language
                or saved_skill.get("requested_model_mode", "") != body.model_mode
                or saved_skill.get("requested_ollama_model", "") != body.ollama_model):
            raise HTTPException(409, "此提交标识已用于另一个任务")
        return existing
    active = db.scalars(select(Job).where(Job.owner_id == user.id, Job.state.in_(ACTIVE_REVIEW_STATES))).all()
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
            if not skill.get("explicit") and is_standard_request(body.instruction):
                skill["categories"] = DEFAULT_SKILL["categories"]
            previous = json.loads(parent.result)
            skill["previous_categories"] = [row["category"] for row in previous.get("rows", [])]
            skill["previous_question"] = parent.instruction[:1000]
    skill["language"] = body.language
    skill.update(model_snapshot or resolve_model_selection(body.model_mode, body.ollama_model))
    skill["requested_model_mode"] = body.model_mode
    skill["requested_ollama_model"] = body.ollama_model
    item = Job(owner_id=user.id, contract_id=contract.id, request_key=body.request_key,
               instruction=body.instruction, parent_id=parent_id, skill=dump(skill), source=source, state=state)
    db.add(item)
    db.flush()
    return item


def delete_history(user, *, contract_id=None, job_id=None):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job_scope = Job.id == job_id if job_id else Job.contract_id == contract_id
        file_scope = File.job_id == job_id if job_id else File.contract_id == contract_id
        if job_id:
            own(db.get(Job, job_id), user)
        else:
            contract = own(db.get(Contract, contract_id), user)
            runs = select(AssistantRun.id).where(
                AssistantRun.owner_id == user.id,
                func.json_extract(AssistantRun.request, "$.contract_id") == contract_id,
            )
            if db.scalar(runs.where(AssistantRun.state.in_(ACTIVE_ASSISTANT_STATES)).limit(1)):
                raise HTTPException(409, "请等待此合同的对话处理完毕后再删除")
            # Older documents created in contract chat use the run ID in File.job_id.
            chat_artifact = runs.where(File.job_id.like("agent-" + AssistantRun.id + "-%")).correlate(File).exists()
            file_scope = or_(file_scope, chat_artifact)

        reviews = select(Job.id).where(Job.owner_id == user.id, job_scope)
        if db.scalar(reviews.where(Job.state.in_(ACTIVE_REVIEW_STATES)).limit(1)):
            raise HTTPException(409, "请等待审查任务处理完毕后再删除")
        if db.scalar(select(Delivery.id).where(Delivery.job_id.in_(reviews), Delivery.state == "sending").limit(1)):
            raise HTTPException(409, "请等待报告邮件发送完毕后再删除")

        files = File.owner_id == user.id, file_scope
        file_ids = select(File.id).where(*files)
        paths = tuple(db.scalars(select(File.path).where(*files)))
        subjects = select("合同审查结果 [" + func.substr(Job.id, 1, 8) + "]").where(
            Job.id.in_(reviews), Job.source == "email",
        )
        db.execute(delete(LocalMail).where(LocalMail.recipient == user.email,
                                            or_(LocalMail.file_id.in_(file_ids), LocalMail.subject.in_(subjects))))
        db.execute(delete(File).where(*files))
        db.add_all(FileRemoval(path=path) for path in paths)
        for table in (Event, Checkpoint, Delivery):
            db.execute(delete(table).where(table.job_id.in_(reviews)))
        # Keep the mail message digest so the inbox cursor cannot re-import a deleted contract.
        db.execute(update(Receipt).where(Receipt.job_id.in_(reviews)).values(state="deleted", job_id="", note=""))
        db.execute(update(Job).where(Job.owner_id == user.id, Job.parent_id.in_(reviews)).values(parent_id=""))
        db.execute(delete(Job).where(Job.owner_id == user.id, job_scope))
        if not job_id:
            db.execute(delete(AssistantRun).where(AssistantRun.id.in_(runs)))
            db.delete(contract)
    remove_deleted_files(paths)


def remove_deleted_files(paths=None):
    if paths == ():
        return
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        pending = select(FileRemoval)
        if paths is not None:
            pending = pending.where(FileRemoval.path.in_(paths))
        root = (settings.data / "files").resolve()
        for entry in db.scalars(pending):
            path = settings.data / entry.path
            try:
                if not path.parent.resolve().is_relative_to(root) or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                    logger.error("Rejected invalid file removal path")
                    continue
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Deleted attachment could not be removed; will retry")
            else:
                db.delete(entry)


def graph_client():
    if settings.mail_provider == "gmail" and settings.gmail_transport == "imap":
        from gmail_imap import GmailIMAP
        return GmailIMAP()
    if settings.mail_provider == "gmail":
        from gmailbox import Gmail
        return Gmail()
    from mailbox import Outlook
    return Outlook()


def send_mail(recipient, subject, body, file=None, in_reply_to="", language="zh"):
    if settings.demo_mail:
        with session() as db:
            db.add(LocalMail(recipient=recipient, subject=subject, body=body, file_id=file.id if file else ""))
        return "demo"
    client = graph_client()
    if not client.enabled:
        raise RuntimeError("邮箱尚未配置。请完成邮箱授权或填写 Gmail 应用专用密码")
    attachment = None
    if file:
        raw = (settings.data / file.path).read_bytes()
        if len(raw) > 2_500_000:
            body += (f"\nThe report is too large to attach. Sign in at {settings.app_public_url} to view and download it."
                     if language == "en" else f"\n报告较大，请登录 {settings.app_public_url} 查看并下载。")
        else:
            attachment = (file.name, raw)
    if in_reply_to and hasattr(client, "send_reply"):
        client.send_reply(recipient, subject, body, attachment, in_reply_to=in_reply_to)
    else:
        client.send(recipient, subject, body, attachment)
    return "accepted"


def deliver_pending():
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
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
        language = json.loads(job.skill).get("language", "zh")
        message = result.get("message") or result.get("answer") or (
            "Contract review completed. See the attached report." if language == "en" else "合同处理已完成，报告见附件。")
        subject = f"Contract review result [{job.id[:8]}]" if language == "en" else f"合同审查结果 [{job.id[:8]}]"
        status = send_mail(user.email, subject, message, file, in_reply_to=json.loads(job.skill).get("mail_message_id", ""), language=language)
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
    skill = json.loads(job.skill)
    value = {"id": job.id, "contract_id": job.contract_id, "source": job.source, "state": job.state,
             "instruction": job.instruction, "created": job.created, "error": job.error,
             "report_file": job.report_file, "delivery": delivery.state if delivery else "",
             "model_mode": skill.get("model_mode") or ("ollama" if skill.get("ollama_model") else ""),
             "model_name": skill.get("api_model", "") if skill.get("model_mode") == "api" else skill.get("ollama_model", "")}
    if detail:
        value.update(result=json.loads(job.result) or None, report=job.report)
    return value
