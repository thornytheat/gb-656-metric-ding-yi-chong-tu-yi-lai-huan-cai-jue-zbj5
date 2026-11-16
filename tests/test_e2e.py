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
    ("/metrics", "指标依赖"),
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


def test_metric_page_checks_conflicts_cycles_and_scope(client):
    """页面要能核对冲突、环与授权范围，并且匿名视图不泄漏指标名。"""
    anonymous = client.get("/metrics")
    assert anonymous.status_code == 200
    assert "未授权视图只给匿名链路" in anonymous.text
    assert "授权范围" in anonymous.text
    assert "pay_amt" not in anonymous.text
    # 冲突与环在匿名视图里照样可见（只是对象匿名）
    assert "METRIC_CONFLICT" in anonymous.text or "定义冲突" in anonymous.text
    assert "依赖环" in anonymous.text

    authorized = client.get("/metrics", params={"audit_token": "audit-local"})
    assert authorized.status_code == 200
    assert "pay_amt" in authorized.text
    assert "审计日志" in authorized.text

    denied = client.get("/metrics", params={"role": "auditor"})
    assert denied.status_code == 200
    assert "越权查看被拒" in denied.text


def test_metric_export_matches_selected_version(client):
    listing = client.get("/api/metrics/verdicts").json()
    run_id = listing["runs"][0]["run_id"]
    manifest = client.get("/api/export/metrics-manifest.json",
                          params={"run_id": run_id}).json()
    assert manifest["run_id"] == run_id
    assert manifest["input_version"] == listing["runs"][0]["input_version"]
    chains = client.get("/api/export/chains.csv", params={"run_id": run_id})
    assert chains.status_code == 200
    assert "node_anon" in chains.text

    authorized = client.get("/api/export/chains.csv",
                            params={"run_id": run_id, "audit_token": "audit-local"})
    assert authorized.status_code == 200
    assert "node" in authorized.text.splitlines()[0].split(",")
    assert "pay_amt" in authorized.text
