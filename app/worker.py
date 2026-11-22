import hashlib
import json
import re
import threading
import time
import traceback
from sqlalchemy import select, update, text
from .config import settings
from .db import session, initialize, Job, Contract, File, Event, Checkpoint, Delivery, Receipt, Ticket, User, MailCursor, AssistantRun, dump, identifier
from .security import digest
from .services import ACTIVE_REVIEW_STATES, write_file, ingest, create_job, graph_client, deliver_pending, remove_deleted_files
from .schemas import JobInput
from .retrieval import vector_matches
from .tacc_sdk import SERVICE_RECHECK_SECONDS, TaccServicePending, TaccClientPending, queued_service_base
from engine import parse, review, Model, report_text, docx_report

ACTIVE = ACTIVE_REVIEW_STATES[1:]


def defer_for_tacc(task, state="waiting_tacc"):
    task.state, task.claim, task.lease = state, "", time.time() + SERVICE_RECHECK_SECONDS
    task.error = ""
    task.attempts -= 1  # Waiting for the scheduler does not consume an execution attempt.


def claim_job():
    now = time.time()
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = db.scalar(select(Job).where((Job.state == "queued") | (Job.state.in_(ACTIVE) & (Job.lease < now))).order_by(Job.created))
        if not job:
            return None
        if job.attempts >= 3:
            job.state, job.error = "failed", "任务恢复超过上限，请创建新任务。"
            return None
        job.claim, job.lease = identifier(), now + 45
        job.attempts += 1
        job.state, job.updated = "parsing", now
        return job.id, job.claim


def heartbeat(job_id, claim, stop):
    while not stop.wait(10):
        with session() as db:
            db.execute(update(Job).where(Job.id == job_id, Job.claim == claim, Job.state.in_(ACTIVE)).values(lease=time.time() + 45))


def run_job(job_id, claim):
    stop = threading.Event()
    heart = threading.Thread(target=heartbeat, args=(job_id, claim, stop), daemon=True)
    heart.start()
    def event(stage, title, detail=""):
        mapping = {"parse": "parsing", "survey": "surveying", "retrieve": "extracting",
                   "expand": "extracting", "validate": "validating", "render": "rendering", "queue": "tacc_client"}
        with session() as db:
            job = db.get(Job, job_id)
            if job.claim != claim:
                raise RuntimeError("任务已被重新领取")
            if stage in mapping:
                job.state = mapping[stage]
            job.updated = time.time()
            db.add(Event(job_id=job_id, stage=stage, title=title, detail=str(detail)[:1500]))
    def checkpoint(key, value=None):
        with session() as db:
            job = db.get(Job, job_id)
            if job.claim != claim:
                raise RuntimeError("任务已被重新领取")
            saved = db.scalar(select(Checkpoint).where(Checkpoint.job_id == job_id, Checkpoint.key == key))
            if value is None:
                if not saved and key == "survey":
                    saved = db.scalar(select(Checkpoint).join(Job, Job.id == Checkpoint.job_id).where(
                        Job.contract_id == job.contract_id, Job.owner_id == job.owner_id,
                        Job.state.in_(["completed", "partial"]), Checkpoint.key == key).order_by(Checkpoint.id.desc()))
                return json.loads(saved.value) if saved else None
            if saved:
                saved.value = dump(value)
            else:
                db.add(Checkpoint(job_id=job_id, key=key, value=dump(value)))
    started = time.perf_counter()
    try:
        with session() as db:
            job = db.get(Job, job_id)
            contract = db.get(Contract, job.contract_id)
            file = db.get(File, contract.file_id)
            paragraphs = json.loads(contract.paragraphs)
            skill = json.loads(job.skill)
        ollama_model = skill.get("ollama_model") or None
        mode = skill.get("model_mode") or ("ollama" if ollama_model else settings.model_mode)
        sdk_review = mode == "tacc" and settings.tacc_protocol == "sdk"
        if sdk_review and not checkpoint("tacc_client"):
            queued_service_base(checkpoint)
        event("parse", "读取合同并建立段落编号", contract.name)
        if not paragraphs:
            paragraphs = parse(contract.name, (settings.data / file.path).read_bytes())
            with session() as db:
                current = db.get(Job, job_id)
                if current.claim != claim:
                    raise RuntimeError("任务已被重新领取")
                db.get(Contract, contract.id).paragraphs = dump(paragraphs)
        event("parse", f"正文已解析 · {len(paragraphs)} 个段落", "")
        model = Model(event, mode, ollama_model=ollama_model)
        if model.mode == "ollama" and skill.get("requested_ollama_model"):
            model.require_local_ollama_model()
        vector_hits = checkpoint("vector_hits")
        if vector_hits is None:
            vector_hits = {}
            vector_enabled = settings.contract_vector_search
            if vector_enabled is None:
                vector_enabled = model.mode == "ollama"
            if model.mode != "demo" and vector_enabled:
                event("retrieve", "使用 Ollama 和 FAISS 检索合同段落", settings.ollama_embedding_model)
                try:
                    vector_hits = vector_matches(paragraphs, skill, job.instruction, model)
                except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    event("retrieve", "向量检索不可用，继续使用其他候选来源", type(exc).__name__)
            checkpoint("vector_hits", vector_hits)
        if sdk_review:
            from .tacc_sdk import run_remote
            result = run_remote("review", event, checkpoint=checkpoint, paragraphs=paragraphs,
                                skill=skill, instruction=job.instruction, vector_hits=vector_hits, auto_start=True)
        else:
            model.calls = checkpoint("model_calls") or []
            model.save_calls = lambda calls: checkpoint("model_calls", calls)
            result = review(paragraphs, skill, job.instruction, model, checkpoint=checkpoint, vector_hits=vector_hits)
        result["language"] = skill.get("language", "zh")
        if result["language"] == "en":
            from .localization import text_for
            for row in result.get("rows", []):
                for field in ("label", "assessment", "explanation", "finding", "question"):
                    row[field] = text_for(row.get(field, ""), "en")
                row["missing"] = [text_for(item, "en") for item in row.get("missing", [])]
                for item in row.get("checklist", []):
                    item["requirement"] = text_for(item["requirement"], "en")
                    item["assessment"] = text_for(item["assessment"], "en")
            result["answer"] = "\n\n".join(row["label"] + ": " + (row.get("finding") or ("No supporting source found." if row["status"] == "not_found" else "Human review required.")) for row in result.get("rows", []))
        result["seconds"] = round(time.perf_counter() - started, 3)
        report = report_text(contract.name, result) if result["rows"] else ""
        event("render", "保存审查结果", "")
        raw = docx_report(report, result) if report else None
        with session() as db:
            current = db.get(Job, job_id)
            if current.claim != claim:
                raise RuntimeError("任务已被重新领取")
            if raw:
                artifact = write_file(db, job.owner_id, job.contract_id,
                                      contract.name.rsplit(".", 1)[0] + ("-review-report.docx" if result.get("language") == "en" else "-审查报告.docx"), raw, "report", job_id)
                current.report_file = artifact.id
            current.report, current.result = report, dump(result)
            current.state = "completed" if result["state"] == "complete" else result["state"]
            current.updated = time.time()
            if job.source == "email" and not db.scalar(select(Delivery).where(Delivery.job_id == job_id)):
                db.add(Delivery(job_id=job_id, owner_id=job.owner_id))
            db.add(Event(job_id=job_id, stage="done", title="任务处理结束", detail=current.state))
    except TaccServicePending as exc:
        with session() as db:
            job = db.get(Job, job_id)
            if job and job.claim == claim:
                client_pending = isinstance(exc, TaccClientPending)
                defer_for_tacc(job, "tacc_client" if client_pending else "waiting_tacc")
                job.updated = time.time()
                stage = "tacc_client_wait" if client_pending else "tacc_service"
                if db.scalar(select(Event.id).where(Event.job_id == job_id, Event.stage == stage).limit(1)) is None:
                    db.add(Event(job_id=job_id, stage=stage, title=str(exc), detail=""))
    except Exception as exc:
        with session() as db:
            job = db.get(Job, job_id)
            if job and job.claim == claim:
                job.state = "failed"
                if getattr(exc, "public_message", None):
                    job.error = exc.public_message
                elif isinstance(exc, (ValueError, RuntimeError, TimeoutError)) and not str(exc).startswith("TACC SDK failed:"):
                    job.error = str(exc)[:250]
                else:
                    job.error = f"{type(exc).__name__}：处理失败，请检查服务配置后重新提交"
                job.updated = time.time()
                db.add(Event(job_id=job_id, stage="error", title="处理失败", detail=job.error))
        print("job failed:", job_id, type(exc).__name__, flush=True)
    finally:
        stop.set()
        heart.join(timeout=2)


def receive_message(client, message):
    message_id = settings.mailbox + ":" + message["id"]
    receipt_id = digest(message_id)
    def can_retry(receipt):
        return receipt and receipt.state == "ignored" and receipt.note == "未登记发件人"
    with session() as db:
        existing = db.get(Receipt, receipt_id)
        if existing and not can_retry(existing):
            return
    sender = message.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    if sender == settings.mailbox.lower():
        return
    subject = message.get("subject", "")
    if subject.startswith(("合同审查结果", "Contract review result")):
        return
    with session() as db:
        user = db.scalar(select(User).where(User.email == sender, User.role == "legal"))
        if not user:
            if not existing:
                db.add(Receipt(id=receipt_id, state="ignored", note="未登记发件人"))
            return
    attachments = list(client.attachments(message["id"]))
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        receipt = db.get(Receipt, receipt_id)
        if receipt and not can_retry(receipt):
            return
        if len(attachments) != 1:
            if receipt:
                receipt.state, receipt.note = "rejected", "每封邮件需要一份有效合同"
            else:
                db.add(Receipt(id=receipt_id, state="rejected", note="每封邮件需要一份有效合同"))
            return
        token_match = re.search(r"\[TOKEN=([A-Za-z0-9_-]+)\]", subject)
        ticket = db.get(Ticket, digest(token_match[1])) if token_match else None
        ticket_valid = bool(ticket and ticket.owner_id == user.id and ticket.expires > time.time() and not ticket.used)
        authorized = settings.mail_auto_review or ticket_valid
        _, name, raw = attachments[0]
        contract = ingest(db, user, name, raw, "邮件导入")
        request = "标准合同审查" if settings.mail_auto_review else str(message.get("body", {}).get("content", ""))[:4000]
        language_match = re.search(r"\[LANG=(en|zh)\]", subject, re.I)
        language = language_match[1].lower() if language_match else user.language
        job = create_job(db, user, contract.id, JobInput(instruction=request, request_key=receipt_id, language=language),
                         "email", "queued" if authorized else "awaiting_confirmation")
        snapshot = json.loads(job.skill)
        snapshot["mail_message_id"] = str(message.get("internetMessageId", ""))[:500]
        job.skill = dump(snapshot)
        if ticket_valid:
            ticket.used = 1
        if receipt:
            receipt.state, receipt.job_id, receipt.note = "accepted", job.id, ""
        else:
            db.add(Receipt(id=receipt_id, state="accepted", job_id=job.id))
    client.mark_read(message["id"])


def mail_loop(stop):
    client = graph_client()
    while not stop.is_set():
        cycle_started = time.monotonic()
        try:
            remove_deleted_files()
            if not settings.demo_mail and client.enabled:
                with session() as db:
                    record = db.get(MailCursor, settings.mailbox)
                    cursor = record.cursor if record else ""
                for _ in range(12):
                    batch = client.delta(cursor)
                    for message in batch.get("value", []):
                        if "@removed" not in message and message.get("hasAttachments"):
                            receive_message(client, message)
                    cursor = batch.get("@odata.nextLink") or batch.get("@odata.deltaLink")
                    if not cursor:
                        raise RuntimeError("邮箱响应缺少游标")
                    with session() as db:
                        record = db.get(MailCursor, settings.mailbox)
                        if record:
                            record.cursor = cursor
                        else:
                            db.add(MailCursor(mailbox=settings.mailbox, cursor=cursor))
                    if "@odata.nextLink" not in batch:
                        break
            deliver_pending()
        except Exception as exc:
            print("mail service:", type(exc).__name__, flush=True)
        stop.wait(max(0, max(10, settings.mail_poll_seconds) - (time.monotonic() - cycle_started)))


def main():
    initialize()
    with session() as db:
        db.execute(update(Delivery).where(Delivery.state == "sending").values(state="uncertain", error="服务重启，请核对发件记录"))
    stop = threading.Event()
    thread = threading.Thread(target=mail_loop, args=(stop,), daemon=True)
    thread.start()
    assistant_thread = threading.Thread(target=assistant_loop, args=(stop,), daemon=True)
    assistant_thread.start()
    print("Contract review worker ready", flush=True)
    try:
        while True:
            claimed = claim_job()
            if claimed:
                run_job(*claimed)
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
        thread.join(timeout=3)


def run_assistant_once():
    from .assistant import run_assistant
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        run = db.scalar(select(AssistantRun).where((AssistantRun.state == "queued") |
                        (AssistantRun.state.in_(["running", "waiting_tacc"]) & (AssistantRun.lease < time.time())))
                        .order_by(AssistantRun.created))
        if not run:
            return False
        if run.attempts >= 3:
            run.state, run.error = "failed", "对话恢复超过上限，请重新发送"
            return True
        run.claim, run.state, run.lease = identifier(), "running", time.time() + 45
        run.attempts += 1
        run_id, claim, owner, request = run.id, run.claim, run.owner_id, json.loads(run.request)
    stop = threading.Event()
    def heartbeat_run():
        while not stop.wait(10):
            with session() as db:
                db.execute(update(AssistantRun).where(AssistantRun.id == run_id, AssistantRun.claim == claim,
                           AssistantRun.state == "running").values(lease=time.time() + 45))
    heartbeat_thread = threading.Thread(target=heartbeat_run, daemon=True)
    heartbeat_thread.start()
    def emit(stage, title, detail=""):
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim != claim:
                raise RuntimeError("对话已被重新领取")
            events = json.loads(current.events)
            events.append({"stage": stage, "title": title, "detail": detail, "created": time.time()})
            current.events = dump(events)
    def checkpoint(key, value=None):
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim != claim:
                raise RuntimeError("对话已被重新领取")
            data = json.loads(current.checkpoints)
            if value is None:
                return data.get(key)
            data[key] = value
            current.checkpoints = dump(data)
    try:
        result = run_assistant(owner, request, run_id, emit, checkpoint)
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim == claim:
                current.state, current.result = "completed", dump(result)
    except TaccServicePending as exc:
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim == claim:
                defer_for_tacc(current)
                current.updated = time.time()
                events = json.loads(current.events)
                stage = "tacc_client_wait" if isinstance(exc, TaccClientPending) else "tacc_service"
                if not any(event["stage"] == stage for event in events):
                    events.append({"stage": stage, "title": str(exc), "detail": "", "created": time.time()})
                    current.events = dump(events)
    except Exception as exc:
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim == claim:
                current.state = "failed"
                current.error = getattr(exc, "public_message", None) or (
                    str(exc)[:500] if isinstance(exc, ValueError) else type(exc).__name__ + "：助手执行失败，请检查模型或网络连接")
    finally:
        stop.set()
        heartbeat_thread.join(timeout=2)
    return True


def assistant_loop(stop):
    while not stop.is_set():
        try:
            if not run_assistant_once():
                stop.wait(1)
        except Exception as exc:
            print("assistant worker:", type(exc).__name__, flush=True)
            stop.wait(2)


if __name__ == "__main__":
    main()
