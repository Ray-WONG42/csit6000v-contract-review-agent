import hashlib
import hmac
import secrets
import time
import os
from fastapi import HTTPException, Request
from sqlalchemy import select, delete
from .db import session, User, Login
from .config import settings


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return salt + ":" + digest


def password_matches(password, encoded):
    salt, digest = encoded.split(":")
    actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, digest)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def code_digest(value):
    path = settings.data / ".verification-key"
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
            if os.name != "nt":
                path.chmod(0o600)
        except FileExistsError:
            pass
    return hmac.new(path.read_bytes(), value.encode(), hashlib.sha256).hexdigest()


def current_user(request: Request):
    token = request.cookies.get("session", "")
    with session() as db:
        login = db.get(Login, digest(token))
        if not login or login.expires < time.time():
            raise HTTPException(401, "请先登录")
        user = db.get(User, login.user_id)
        if not user:
            raise HTTPException(401, "账号不存在")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), login.csrf):
                raise HTTPException(403, "会话校验失败，请刷新后重试")
        request.state.csrf = login.csrf
        return user


def legal_user(request: Request):
    user = current_user(request)
    if user.role != "legal":
        raise HTTPException(403, "总账户提供输入与最终输出的只读访问")
    return user


def own(resource, user):
    if not resource or resource.owner_id != user.id:
        raise HTTPException(404, "记录不存在")
    return resource


def seed_users():
    accounts = []
    if settings.app_demo:
        accounts = [
            ("legal@example.com", "LegalDemo2026!", "legal"),
            ("other@example.com", "OtherDemo2026!", "legal"),
            ("admin@example.com", "AdminDemo2026!", "admin"),
        ]
    elif settings.admin_email and len(settings.admin_password) >= 12:
        accounts = [(settings.admin_email.lower(), settings.admin_password, "admin")]
    with session() as db:
        if not settings.app_demo:
            if db.scalar(select(User).where(User.email.in_(["legal@example.com", "other@example.com", "admin@example.com"]))):
                raise RuntimeError("真实模式需要使用全新的 APP_DATA 目录，避免保留演示账号")
            if not db.scalar(select(User).where(User.role == "admin")) and not accounts:
                raise RuntimeError("首次真实启动需要 ADMIN_EMAIL 与至少 12 位 ADMIN_PASSWORD")
        for email, password, role in accounts:
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, password=password_hash(password), role=role))


def expire_sessions():
    with session() as db:
        db.execute(delete(Login).where(Login.expires < time.time()))
