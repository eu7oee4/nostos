"""ACCESS_TOKEN 那道门。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(data_dir):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(settings, "access_token", "s3cret")


def test_no_token_configured_means_no_gate(client):
    assert client.get("/health").status_code == 200
    assert client.get("/messages").status_code == 200


def test_gate_blocks_everything_including_health(client, gated):
    assert client.get("/messages").status_code == 401
    assert client.get("/health").status_code == 401
    assert client.post("/chat", json={"content": "hi"}).status_code == 401
    assert client.get("/").status_code == 401


def test_static_pwa_assets_stay_open(client, gated):
    assert client.get("/manifest.json").status_code == 200
    assert client.get("/sw.js").status_code == 200


def test_bearer_header(client, gated):
    assert client.get("/messages", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/messages", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_query_token_sets_cookie_then_redirects_clean(client, gated):
    r = client.get("/?token=s3cret", follow_redirects=False)
    assert r.status_code == 303
    assert "token=" not in r.headers["location"]
    assert r.cookies.get("nostos_token") == "s3cret"
    # 之后同一个浏览器不用再带
    assert client.get("/messages").status_code == 200


def test_wrong_query_token_sets_nothing(client, gated):
    r = client.get("/?token=wrong", follow_redirects=False)
    assert r.status_code == 401
    assert "nostos_token" not in r.cookies
