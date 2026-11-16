"""端到端：样例 → 判定 → 对账 → 导出，页面与接口一致。"""

from __future__ import annotations

import csv
import io

from tests.fixtures import expected

PAGES = [
    ("/", "规格单"),
    ("/types", "类型与兼容"),
    ("/mapping", "字段映射"),
    ("/impact", "影响面"),
    ("/batches", "版本历史"),
    ("/spec", "迁移单"),
]


def test_health_and_home(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["runs"] == 1
    assert body["window_semantics"] == "[start, end)"

    home = client.get("/")
    assert home.status_code == 200
    assert "规格单" in home.text


def test_all_pages_render(client):
    for path, marker in PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text, path
        assert "/static/theme.css" in response.text
        assert "http://" not in response.text.split("<main")[0]


def test_full_flow_matches_ledger(client):
    reconcile = client.get("/api/reconcile").json()
    fields = reconcile["fields"]
    assert fields["old_total"] == expected.OLD_TOTAL
    assert fields["new_total"] == expected.NEW_TOTAL
    assert fields["old_gap"] == 0 and fields["new_gap"] == 0
    assert reconcile["refs"]["balanced"] is True
    assert reconcile["balanced"]["all"] is True

    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/catalog.csv").text)))
    assert len(rows) == expected.OLD_TOTAL + 4
    assert sum(1 for row in rows if row["verdict"] == "BREAKING") == 6


def test_quarantine_csv_export(client):
    response = client.get("/api/schemas/quarantine.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == expected.QUARANTINE_BLOCKED + expected.QUARANTINE_WARNINGS
    reasons = {row["reason"] for row in rows}
    assert {"LINE_MALFORMED", "DUPLICATE_COLUMN", "BOM_STRIPPED"} <= reasons
    assert sum(1 for row in rows if row["blocking"] == "Y") == expected.QUARANTINE_BLOCKED


def test_home_renders_field_rows(client):
    home = client.get("/")
    assert "order_code" in home.text
    assert "pay_channel" in home.text
    assert "破坏" in home.text
