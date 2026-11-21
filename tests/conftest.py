import os
import tempfile
from pathlib import Path
import pytest

TEST_DATA = tempfile.mkdtemp(prefix="contract-review-test-")
os.environ["APP_DATA"] = TEST_DATA
os.environ["APP_DEMO"] = "true"
os.environ["MODEL_MODE"] = "demo"
os.environ["TACC_PROTOCOL"] = "chat"
os.environ["TACC_EXCLUDE_NODES"] = ""
os.environ["MAIL_AUTO_REVIEW"] = "false"
os.environ["MAIL_DEMO"] = "true"
os.environ["GMAIL_HTTPS_PROXY"] = ""

from app.main import app, rate_limits
from app.db import Base, engine, initialize
from app.security import seed_users
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    initialize()
    seed_users()
    rate_limits.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def login(client, name="legal"):
    accounts = {"legal": ("legal@example.com", "LegalDemo2026!"),
                "other": ("other@example.com", "OtherDemo2026!"),
                "admin": ("admin@example.com", "AdminDemo2026!")}
    email, password = accounts[name]
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return response.json()


@pytest.fixture
def sample():
    return (Path(__file__).resolve().parent.parent / "fixtures" / "Acme-Service-Agreement.txt").read_bytes()
