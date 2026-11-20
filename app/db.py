import json
import time
import uuid
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy import String, Text, Float, Integer, UniqueConstraint
from .config import settings


def identifier():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    email: Mapped[str] = mapped_column(String, unique=True)
    password: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="legal")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Login(Base):
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)


class Code(Base):
    __tablename__ = "codes"
    email: Mapped[str] = mapped_column(String, primary_key=True)
    digest: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[float] = mapped_column(Float, default=time.time)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    company: Mapped[str] = mapped_column(String, default="未归类")
    name: Mapped[str] = mapped_column(String)
    file_id: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    paragraphs: Mapped[str] = mapped_column(Text, default="[]")
    parser_version: Mapped[str] = mapped_column(String, default="1")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class File(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    contract_id: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    contract_id: Mapped[str] = mapped_column(String, index=True)
    request_key: Mapped[str] = mapped_column(String)
    instruction: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str] = mapped_column(String, default="")
    skill: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, default="web")
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    result: Mapped[str] = mapped_column(Text, default="{}")
    report: Mapped[str] = mapped_column(Text, default="")
    report_file: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(String, default="")
    claim: Mapped[str] = mapped_column(String, default="")
    lease: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    stage: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("job_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    key: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(Text)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="received")
    job_id: Mapped[str] = mapped_column(String, default="")
    note: Mapped[str] = mapped_column(String, default="")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Ticket(Base):
    __tablename__ = "tickets"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)
    used: Mapped[int] = mapped_column(Integer, default=0)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    job_id: Mapped[str] = mapped_column(String, unique=True)
    owner_id: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String, default="")
    updated: Mapped[float] = mapped_column(Float, default=time.time)


class LocalMail(Base):
    __tablename__ = "local_mail"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    recipient: Mapped[str] = mapped_column(String, index=True)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    file_id: Mapped[str] = mapped_column(String, default="")
    created: Mapped[float] = mapped_column(Float, default=time.time)


class MailCursor(Base):
    __tablename__ = "mail_cursors"
    mailbox: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str] = mapped_column(Text, default="")


class AssistantRun(Base):
    __tablename__ = "assistant_runs"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    request_key: Mapped[str] = mapped_column(String)
    request: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    result: Mapped[str] = mapped_column(Text, default="{}")
    events: Mapped[str] = mapped_column(Text, default="[]")
    checkpoints: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(String, default="")
    claim: Mapped[str] = mapped_column(String, default="")
    lease: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time)


settings.data.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{settings.data / 'app.db'}", connect_args={"timeout": 30, "check_same_thread": False})


@event.listens_for(engine, "connect")
def pragmas(connection, _):
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")


@contextmanager
def session():
    with Session(engine, expire_on_commit=False) as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def initialize():
    Base.metadata.create_all(engine)
