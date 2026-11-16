"""测试夹具：每个用例一套独立库，样板数据在会话级临时库上跑。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TZ", "UTC")


@pytest.fixture(scope="session", autouse=True)
def session_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("session") / "metricgraph.sqlite"
    os.environ["METRICGRAPH_DB"] = str(path)
    from app import store

    store.init_db()
    yield path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """带样例种子的客户端：启动即产生第一条批次。"""
    monkeypatch.setenv("METRICGRAPH_DB", str(tmp_path / "metricgraph.sqlite"))
    from app import store

    store.reset()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    """没有批次的客户端：验证空态与 404 分支。"""
    monkeypatch.setenv("METRICGRAPH_DB", str(tmp_path / "empty.sqlite"))
    from app import store

    store.reset()
    from fastapi.testclient import TestClient

    from app.modules.m6_batches import service as batch_service

    monkeypatch.setattr(batch_service, "ensure_seeded", lambda: None)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def run(client):
    """当前批次：多数断言直接看这一份。"""
    from app import store

    return store.latest_run()
