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
from .services import write_file, ingest, create_job, graph_client, deliver_pending
from .schemas import JobInput
from engine import parse, review, Model, report_text, docx_report

ACTIVE = ["parsing", "surveying", "extracting", "validating", "rendering"]


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
                   "expand": "extracting", "validate": "validating", "render": "rendering"}
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
        event("parse", "读取合同并建立段落编号", contract.name)
        if not paragraphs:
            paragraphs = parse(contract.name, (settings.data / file.path).read_bytes())
            with session() as db:
                current = db.get(Job, job_id)
                if current.claim != claim:
                    raise RuntimeError("任务已被重新领取")
                db.get(Contract, contract.id).paragraphs = dump(paragraphs)
        event("parse", f"正文已解析 · {len(paragraphs)} 个段落", "")
        if settings.model_mode == "tacc" and settings.tacc_protocol == "sdk":
            from .tacc_sdk import run_remote
            result = run_remote("review", event, checkpoint=checkpoint, paragraphs=paragraphs,
                                skill=skill, instruction=job.instruction)
        else:
            model = Model(event, settings.model_mode)
            model.calls = checkpoint("model_calls") or []
            model.save_calls = lambda calls: checkpoint("model_calls", calls)
            result = review(paragraphs, skill, job.instruction, model, checkpoint=checkpoint)
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
                                      contract.name.rsplit(".", 1)[0] + "-审查报告.docx", raw, "report", job_id)
                current.report_file = artifact.id
            current.report, current.result = report, dump(result)
            current.state = "completed" if result["state"] == "complete" else result["state"]
            current.updated = time.time()
            if job.source == "email" and not db.scalar(select(Delivery).where(Delivery.job_id == job_id)):
                db.add(Delivery(job_id=job_id, owner_id=job.owner_id))
            db.add(Event(job_id=job_id, stage="done", title="任务处理结束", detail=current.state))
    except Exception as exc:
        with session() as db:
            job = db.get(Job, job_id)
            if job and job.claim == claim:
                job.state = "failed"
                if isinstance(exc, (ValueError, RuntimeError, TimeoutError)) and not str(exc).startswith("TACC SDK failed:"):
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
    message_id = settings.graph_mailbox + ":" + message["id"]
    receipt_id = digest(message_id)
    with session() as db:
        if db.get(Receipt, receipt_id):
            return
    sender = message.get("from", {}).get("emailAddress", {}).get("address", "").lower()
    if sender == settings.graph_mailbox.lower():
        return
    subject = message.get("subject", "")
    if subject.startswith("合同审查结果"):
        return
    with session() as db:
        user = db.scalar(select(User).where(User.email == sender, User.role == "legal"))
        if not user:
            db.add(Receipt(id=receipt_id, state="ignored", note="未登记发件人"))
            return
    attachments = list(client.attachments(message["id"]))
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if db.get(Receipt, receipt_id):
            return
        if len(attachments) != 1:
            db.add(Receipt(id=receipt_id, state="rejected", note="每封邮件需要一份有效合同"))
            return
        token_match = re.search(r"\[TOKEN=([A-Za-z0-9_-]+)\]", subject)
        ticket = db.get(Ticket, digest(token_match[1])) if token_match else None
        authorized = bool(ticket and ticket.owner_id == user.id and ticket.expires > time.time() and not ticket.used)
        _, name, raw = attachments[0]
        contract = ingest(db, user, name, raw, "邮件导入")
        request = str(message.get("body", {}).get("content", ""))[:4000]
        job = create_job(db, user, contract.id, JobInput(instruction=request, request_key=receipt_id),
                         "email", "queued" if authorized else "awaiting_confirmation")
        if authorized:
            ticket.used = 1
        db.add(Receipt(id=receipt_id, state="accepted", job_id=job.id))
    client.mark_read(message["id"])


def mail_loop(stop):
    client = graph_client()
    while not stop.is_set():
        try:
            if not settings.app_demo and client.enabled:
                with session() as db:
                    record = db.get(MailCursor, settings.graph_mailbox)
                    cursor = record.cursor if record else ""
                for _ in range(12):
                    batch = client.delta(cursor)
                    for message in batch.get("value", []):
                        if "@removed" not in message and message.get("hasAttachments"):
                            receive_message(client, message)
                    cursor = batch.get("@odata.nextLink") or batch.get("@odata.deltaLink")
                    if not cursor:
                        raise RuntimeError("Outlook 增量响应缺少游标")
                    with session() as db:
                        record = db.get(MailCursor, settings.graph_mailbox)
                        if record:
                            record.cursor = cursor
                        else:
                            db.add(MailCursor(mailbox=settings.graph_mailbox, cursor=cursor))
                    if "@odata.nextLink" not in batch:
                        break
            deliver_pending()
        except Exception as exc:
            print("mail service:", type(exc).__name__, flush=True)
        stop.wait(max(2, settings.graph_poll))


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
                        ((AssistantRun.state == "running") & (AssistantRun.lease < time.time()))).order_by(AssistantRun.created))
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
    except Exception as exc:
        with session() as db:
            current = db.get(AssistantRun, run_id)
            if current.claim == claim:
                current.state = "failed"
                current.error = str(exc)[:500] if isinstance(exc, ValueError) else type(exc).__name__ + "：助手执行失败，请检查模型或网络连接"
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
