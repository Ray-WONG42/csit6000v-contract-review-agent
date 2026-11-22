import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, Depends, UploadFile, File as Upload, Form, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, text
from sqlalchemy.exc import IntegrityError
from .config import settings, ROOT
from .db import initialize, session, User, Login, Code, Contract, File, Job, Event, Skill, Ticket, Delivery, Receipt, LocalMail, AssistantRun, dump
from .security import current_user, legal_user, own, password_hash, password_matches, digest, code_digest, seed_users
from .schemas import EmailInput, RegisterInput, LoginInput, LanguageInput, SkillInput, JobInput, CompanyInput, ReportInput, ContractView, JobView, EventView, AssistantInput
from .services import (ACTIVE_ASSISTANT_STATES, ingest, create_job, contract_view, job_view, send_mail,
                       write_file, resolve_model_selection, tacc_configured, delete_history, graph_client)
from engine import RULES, MAX_FILE, Model, docx_report


@asynccontextmanager
async def lifespan(app):
    initialize()
    seed_users()
    if not settings.app_demo and settings.model_mode == "demo":
        raise RuntimeError("真实运行需要启用 Ollama 或 TACC 模型")
    yield


app = FastAPI(title="合同审查工作台", version="1.0.0", lifespan=lifespan)
rate_limits = {}


@app.middleware("http")
async def security_headers(request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in (str(request.base_url).rstrip("/"), settings.app_public_url.rstrip("/")):
            return Response("Origin rejected", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


def throttle(request, action, limit=15):
    now = time.time()
    key = (request.client.host, action)
    hits = [x for x in rate_limits.get(key, []) if x > now - 60]
    if len(hits) >= limit:
        raise HTTPException(429, "请求过于频繁，请稍后重试")
    rate_limits[key] = hits + [now]
    if len(rate_limits) > 10000:
        rate_limits.clear()


@app.get("/api/config")
def config():
    shortcuts = [("法务", settings.login_legal_email), ("另一法务", settings.login_other_legal_email),
                 ("总账户", settings.login_admin_email)]
    if settings.app_demo and not any(email for _, email in shortcuts):
        shortcuts = [("法务", "legal@example.com"), ("另一法务", "other@example.com"), ("总账户", "admin@example.com")]
    return {"demo": settings.app_demo, "model_mode": settings.model_mode,
            "login_accounts": [{"label": label, "email": email} for label, email in shortcuts if email],
            "model_name": settings.ollama_model if settings.model_mode == "ollama" else settings.model_name if settings.model_mode == "api" else settings.tacc_model,
            "api_model_name": settings.model_name, "api_configured": bool(settings.llm_base_url and settings.llm_api_key and settings.model_name),
            "categories": [{"id": key, "label": value[0]} for key, value in RULES.items()],
            "mailbox": settings.mailbox,
            "mail_auto_review": settings.mail_auto_review, "mail_poll_seconds": settings.mail_poll_seconds,
            "mail_demo": settings.demo_mail, "mail_configured": graph_client().enabled,
            "tacc_configured": tacc_configured()}


@app.get("/api/health")
def health():
    return {"status": "ok", "mode": settings.model_mode}


@app.get("/api/ollama/models", response_model=list[str])
def ollama_models(user=Depends(legal_user)):
    if settings.model_mode == "demo":
        return []
    try:
        return Model.local_ollama_models()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/assistant/runs")
def assistant_start(body: AssistantInput, user=Depends(legal_user)):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if body.contract_id:
            own(db.get(Contract, body.contract_id), user)
        previous = db.scalar(select(AssistantRun).where(AssistantRun.owner_id == user.id,
                                                       AssistantRun.request_key == body.request_key))
        if previous:
            if AssistantInput.model_validate_json(previous.request) != body:
                raise HTTPException(409, "此提交标识已用于其他消息")
            return {"id": previous.id, "state": previous.state}
        active = db.scalars(select(AssistantRun).where(AssistantRun.owner_id == user.id,
                                                      AssistantRun.state.in_(ACTIVE_ASSISTANT_STATES))).all()
        if len(active) >= 3:
            raise HTTPException(429, "请等待当前对话完成")
        snapshot = resolve_model_selection(body.model_mode, body.ollama_model)
        request = dump({**body.model_dump(), "model_snapshot": snapshot})
        run = AssistantRun(owner_id=user.id, request_key=body.request_key, request=request)
        db.add(run)
        db.flush()
        return {"id": run.id, "state": run.state}


@app.get("/api/assistant/runs/{run_id}")
def assistant_status(run_id: str, user=Depends(legal_user)):
    with session() as db:
        run = own(db.get(AssistantRun, run_id), user)
        return {"id": run.id, "state": run.state, "result": json.loads(run.result),
                "events": json.loads(run.events), "error": run.error}


@app.post("/api/auth/send-code")
def send_code(body: EmailInput, request: Request):
    throttle(request, "code", 5)
    domains = [x.strip().lower() for x in settings.allowed_email_domains.split(",") if x.strip()]
    if domains and body.email.split("@")[1] not in domains:
        raise HTTPException(403, "此邮箱域名不在注册范围")
    value = str(secrets.randbelow(900000) + 100000)
    salt = secrets.token_hex(16)
    with session() as db:
        previous = db.get(Code, body.email)
        if previous and previous.sent > time.time() - 60:
            raise HTTPException(429, "请等待 60 秒后再次发送")
        if previous:
            db.delete(previous)
            db.flush()
        db.add(Code(email=body.email, digest=salt + ":" + code_digest(salt + value),
                    expires=time.time() + 600, attempts=0))
    try:
        send_mail(body.email, "Verification code" if body.language == "en" else "注册验证码",
                  f"Your verification code is {value}. It expires in 10 minutes." if body.language == "en" else f"验证码 {value}，10 分钟内有效。")
    except Exception:
        with session() as db:
            db.execute(delete(Code).where(Code.email == body.email))
        raise HTTPException(503, "验证码邮件发送失败，请检查邮箱 OAuth 授权")
    return {"message": "验证码已发送", "demo_code": value if settings.demo_mail else None}


@app.post("/api/auth/register")
def register(body: RegisterInput, request: Request):
    throttle(request, "register")
    valid = False
    with session() as db:
        code = db.get(Code, body.email)
        if code and code.expires > time.time() and code.attempts < 5:
            salt, expected = code.digest.split(":")
            valid = hmac.compare_digest(code_digest(salt + body.code), expected)
            code.attempts += 1
            if valid:
                if db.scalar(select(User).where(User.email == body.email)):
                    raise HTTPException(409, "此邮箱已注册")
                db.add(User(email=body.email, password=password_hash(body.password), role="legal", language=body.language))
                db.delete(code)
    if not valid:
        raise HTTPException(422, "验证码无效、过期或尝试次数过多")
    return {"message": "注册成功，请登录"}


@app.post("/api/auth/login")
def login(body: LoginInput, request: Request, response: Response):
    throttle(request, "login")
    with session() as db:
        user = db.scalar(select(User).where(User.email == body.email))
        if not user or not password_matches(body.password, user.password):
            raise HTTPException(401, "邮箱或密码错误")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(Login(token=digest(token), csrf=csrf, user_id=user.id, expires=time.time() + 86400))
        response.set_cookie("session", token, httponly=True, samesite="lax", secure=settings.app_secure_cookie, max_age=86400)
        return {"id": user.id, "email": user.email, "role": user.role, "language": user.language, "csrf": csrf}


@app.get("/api/auth/me")
def me(request: Request, user=Depends(current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "language": user.language, "csrf": request.state.csrf}


@app.put("/api/auth/language")
def update_language(body: LanguageInput, user=Depends(current_user)):
    with session() as db:
        db.get(User, user.id).language = body.language
    return {"language": body.language}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    with session() as db:
        db.execute(delete(Login).where(Login.token == digest(request.cookies.get("session", ""))))
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/contracts", response_model=list[ContractView])
def contracts(user=Depends(legal_user)):
    with session() as db:
        return [contract_view(item) for item in db.scalars(select(Contract).where(Contract.owner_id == user.id).order_by(Contract.created.desc()))]


@app.post("/api/contracts", response_model=ContractView)
async def upload(file: UploadFile = Upload(), company: str = Form(default="未归类"), user=Depends(legal_user)):
    raw = await file.read(MAX_FILE + 1)
    with session() as db:
        return contract_view(ingest(db, user, file.filename or "contract", raw, company))


@app.patch("/api/contracts/{contract_id}", response_model=ContractView)
def classify(contract_id: str, body: CompanyInput, user=Depends(legal_user)):
    with session() as db:
        contract = own(db.get(Contract, contract_id), user)
        contract.company = body.company.strip() or "未归类"
        return contract_view(contract)


@app.delete("/api/contracts/{contract_id}")
def remove_contract(contract_id: str, user=Depends(legal_user)):
    delete_history(user, contract_id=contract_id)
    return {"ok": True}


@app.get("/api/contracts/{contract_id}/paragraphs")
def paragraphs(contract_id: str, user=Depends(legal_user)):
    with session() as db:
        return json.loads(own(db.get(Contract, contract_id), user).paragraphs)


@app.get("/api/contracts/{contract_id}/jobs", response_model=list[JobView])
def jobs(contract_id: str, user=Depends(legal_user)):
    with session() as db:
        own(db.get(Contract, contract_id), user)
        return [job_view(db, j) for j in db.scalars(select(Job).where(Job.contract_id == contract_id).order_by(Job.created))]


@app.post("/api/contracts/{contract_id}/jobs", response_model=JobView)
def start_job(contract_id: str, body: JobInput, user=Depends(legal_user)):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = create_job(db, user, contract_id, body)
        return job_view(db, job)


@app.get("/api/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, user=Depends(legal_user)):
    with session() as db:
        return job_view(db, own(db.get(Job, job_id), user), True)


@app.get("/api/jobs/{job_id}/events", response_model=list[EventView])
def events(job_id: str, after: int = 0, user=Depends(legal_user)):
    with session() as db:
        own(db.get(Job, job_id), user)
        return [{"id": e.id, "stage": e.stage, "title": e.title, "detail": e.detail, "created": e.created}
                for e in db.scalars(select(Event).where(Event.job_id == job_id, Event.id > after).order_by(Event.id).limit(300))]


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: str, user=Depends(legal_user)):
    delete_history(user, job_id=job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/confirm")
def confirm(job_id: str, user=Depends(legal_user)):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = own(db.get(Job, job_id), user)
        if job.state != "awaiting_confirmation":
            raise HTTPException(409, "此任务无需确认")
        job.state = "queued"
    return {"ok": True}


@app.post("/api/jobs/{job_id}/report-versions")
def save_report(job_id: str, body: ReportInput, user=Depends(legal_user)):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        job = own(db.get(Job, job_id), user)
        if not job.report_file:
            raise HTTPException(409, "审查报告尚未完成")
        entry = write_file(db, user.id, job.contract_id, "合同审查-编辑稿.docx", docx_report(body.content), "draft", job.id)
        return {"file_id": entry.id}


@app.post("/api/jobs/{job_id}/resend")
def resend(job_id: str, user=Depends(legal_user)):
    with session() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        own(db.get(Job, job_id), user)
        delivery = db.scalar(select(Delivery).where(Delivery.job_id == job_id))
        if not delivery or delivery.state not in ("failed", "uncertain"):
            raise HTTPException(409, "只有失败或状态不确定的交付可以重试")
        delivery.state, delivery.updated = "pending", time.time()
    return {"ok": True}


@app.get("/api/files/{file_id}")
def file_content(file_id: str, download: bool = False, user=Depends(current_user)):
    with session() as db:
        file = db.get(File, file_id)
        if not file or (user.role == "admin" and file.kind not in ("input", "report")) or (user.role != "admin" and file.owner_id != user.id):
            raise HTTPException(404, "文件不存在")
        path = (settings.data / file.path).resolve()
        if not path.is_relative_to(settings.data.resolve()) or not path.is_file():
            raise HTTPException(404, "文件不存在")
        media = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media, filename=file.name,
                            content_disposition_type="attachment" if download or not file.name.lower().endswith(".pdf") else "inline")


@app.get("/api/skills")
def skills(user=Depends(legal_user)):
    with session() as db:
        return [{"id": s.id, "version": s.version, **json.loads(s.content)}
                for s in db.scalars(select(Skill).where(Skill.owner_id == user.id))]


@app.post("/api/skills")
def add_skill(body: SkillInput, user=Depends(legal_user)):
    with session() as db:
        item = Skill(owner_id=user.id, name=body.name, content=dump(body.model_dump()))
        db.add(item)
        db.flush()
        return {"id": item.id, "version": item.version}


@app.put("/api/skills/{skill_id}")
def edit_skill(skill_id: str, body: SkillInput, user=Depends(legal_user)):
    with session() as db:
        item = own(db.get(Skill, skill_id), user)
        item.name, item.content, item.version = body.name, dump(body.model_dump()), item.version + 1
        return {"id": item.id, "version": item.version}


@app.get("/api/admin/contracts", response_model=list[ContractView])
def admin_contracts(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要总账户")
    with session() as db:
        rows = db.execute(select(Contract, User.email).join(User, User.id == Contract.owner_id).order_by(Contract.created.desc()))
        return [contract_view(c, email) for c, email in rows]


@app.get("/api/admin/contracts/{contract_id}/artifacts")
def admin_artifacts(contract_id: str, user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要总账户")
    with session() as db:
        if not db.get(Contract, contract_id):
            raise HTTPException(404, "合同不存在")
        return [{"id": f.id, "name": f.name, "kind": f.kind, "created": f.created}
                for f in db.scalars(select(File).where(File.contract_id == contract_id, File.kind.in_(["input", "report"])).order_by(File.created))]


@app.get("/api/mail/inbox")
def recent_inbox(page: str = Query(default="", max_length=2048), user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "仅总账户可查看收件箱")
    if settings.demo_mail:
        return {"items": [], "next_page": "", "demo": True}
    client = graph_client()
    if not client.enabled or not hasattr(client, "recent_inbox"):
        raise HTTPException(503, "收件箱预览需要已授权的 Gmail HTTPS API")
    try:
        result = client.recent_inbox(page=page)
    except Exception:
        raise HTTPException(503, "暂时无法读取收件箱，请稍后刷新或检查邮箱连接")
    fields = ("id", "subject", "sender", "sender_name", "received_at", "unread")
    result = {"items": [{k: item[k] for k in fields if k in item} for item in result["items"]],
              "next_page": result.get("next_page", ""), "window_start": result.get("window_start"), "demo": False}
    # Only mail metadata is exposed. Never return bodies, verification codes or private job traces.
    with session() as db:
        for item in result["items"]:
            receipt = db.get(Receipt, digest(settings.mailbox + ":" + item["id"]))
            item["intake_state"] = receipt.state if receipt else ""
            item["intake_note"] = receipt.note if receipt else ""
    return result


@app.post("/api/mail/ticket")
def ticket(request: Request, user=Depends(legal_user)):
    token = secrets.token_urlsafe(24)
    with session() as db:
        db.add(Ticket(digest=digest(token), owner_id=user.id, expires=time.time() + 86400))
    english = request.headers.get("accept-language", "").startswith("en")
    return {"ticket": token, "subject": f"[TOKEN={token}] [LANG=en] Contract review" if english else f"[TOKEN={token}] 合同审查", "mailbox": settings.mailbox, "expires_in": 86400}


@app.get("/api/mail/local")
def local_mail(user=Depends(legal_user)):
    if not settings.app_demo:
        raise HTTPException(404)
    with session() as db:
        return [{"id": m.id, "subject": m.subject, "body": m.body, "file_id": m.file_id, "created": m.created}
                for m in db.scalars(select(LocalMail).where(LocalMail.recipient == user.email).order_by(LocalMail.created.desc()).limit(30))]


@app.post("/api/demo/mail")
async def demo_mail(file: UploadFile = Upload(), instruction: str = Form(default=""), user=Depends(legal_user)):
    if not settings.app_demo:
        raise HTTPException(404)
    raw = await file.read(MAX_FILE + 1)
    with session() as db:
        contract = ingest(db, user, file.filename or "contract.txt", raw, "邮件导入")
        job = create_job(db, user, contract.id, JobInput(instruction=instruction, request_key=secrets.token_hex(16)), "email")
        return {"contract_id": contract.id, "job_id": job.id}


@app.get("/api/demo/sample")
def sample():
    return FileResponse(ROOT / "fixtures" / "Acme-Service-Agreement.docx", filename="Acme-Service-Agreement.docx")


dist = ROOT / "web" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="web")


@app.get("/api/templates/{language}/{name}")
def builtin_template(language: str, name: str):
    if language not in ("zh", "en") or name not in ("payment-skill.json", "review-request.txt"):
        raise HTTPException(404, "Template not found")
    return FileResponse(ROOT / "templates" / language / name, filename=language + "-" + name)
