"""M8 指标依赖裁决：批次绑定输入版本、续跑、陈旧拒绝、重试幂等、授权与导出。"""

from __future__ import annotations

import csv
import io
import json

import pytest

from app import config, errors, store
from app.modules.m8_metric_graph import graph, service as verdict_service

TOKEN = {"audit_token": "audit-local"}


@pytest.fixture()
def lineage():
    """每个用例一条独立谱系：启动样例占的是 published-metrics，不能混进来数行。"""
    return "lineage-" + config.token_fingerprint(str(id(object())))


def _defs(version: str = "v2026_09") -> list[dict]:
    rows = [
        {"name": "pay_amt", "expression": "sum(pay_fee)", "depends_on": ["pay_fee"],
         "source": "finance", "priority": 10},
        {"name": "refund_amt", "expression": "sum(refund_fee)",
         "depends_on": ["refund_fee"], "source": "finance", "priority": 10},
        {"name": "gmv", "expression": "pay_amt + refund_amt",
         "depends_on": ["pay_amt", "refund_amt"], "source": "finance", "priority": 10},
        {"name": "report_daily", "expression": "gmv", "depends_on": ["gmv"],
         "source": "bi", "priority": 10},
    ]
    for row in rows:
        row["version"] = version
    return rows


def _submit(client, lineage, definitions=None, **extra):
    body = {"definitions": definitions or _defs(), "lineage_id": lineage}
    body.update(extra)
    return client.post("/api/metrics/verdicts", json=body)


def _rows(lineage: str) -> list[dict]:
    return verdict_service.listing(lineage_id=lineage)


def _result(client, body) -> dict:
    return store.metric_run_by_id(body["run_id"])["result"]


# ---------- 批次绑定输入版本 ----------

def test_new_revision_creates_new_verdict_and_keeps_old(client, lineage):
    """新修订生成新裁决，旧裁决整行保留（审计凭据不能被覆盖）。"""
    first = _submit(client, lineage, input_version="v2026_09")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "ready"

    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 1"
    second = _submit(client, lineage, revised, input_version="v2026_10")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["run_id"] != first_body["run_id"]
    assert second_body["parent_run_id"] == first_body["run_id"]
    assert len(_rows(lineage)) == 2

    old = store.metric_run_by_id(first_body["run_id"])
    assert old["status"] == "ready"
    assert old["input_sha256"] == first_body["input_sha256"]
    assert store.superseding_run(old["batch_key"])["run_id"] == second_body["run_id"]


def test_same_input_retry_does_not_add_results(client, lineage):
    """同一输入重试命中批次键：run_id 不变、行数不增、结论不变。"""
    first = _submit(client, lineage).json()
    again = _submit(client, lineage).json()
    assert again["run_id"] == first["run_id"]
    assert again["reused"] is True
    assert len(_rows(lineage)) == 1
    assert again["block"] == first["block"]


def test_stale_input_version_rejected(client, lineage):
    _submit(client, lineage, input_version="v2026_10")
    stale = _submit(client, lineage, _defs("v2026_09"), input_version="v2026_09")
    assert stale.status_code == 409
    assert stale.json()["error"] == "VERSION_STALE"
    assert stale.json()["detail"]["reason"] == "METRIC_STALE_SUBMIT"
    assert len(_rows(lineage)) == 1


def test_same_version_different_content_conflicts(client, lineage):
    _submit(client, lineage, input_version="v2026_09")
    changed = _defs("v2026_09")
    changed[0]["expression"] = "sum(other_fee)"
    response = _submit(client, lineage, changed, input_version="v2026_09")
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "INPUT_VERSION_CONFLICT"


def test_base_version_seq_stale_rejected(client, lineage):
    _submit(client, lineage)
    current = store.current_metric_version_seq()
    response = _submit(client, lineage, params={"x": 1}, base_version_seq=current - 1)
    assert response.status_code == 409
    assert response.json()["detail"]["current_version_seq"] == current
    assert len(_rows(lineage)) == 1


def test_version_ordering_is_numeric_not_lexicographic():
    """v2026_10 必须被当成比 v2026_9 新，不能按字符串比。"""
    assert verdict_service.compare_versions("v2026_10", "v2026_9") > 0
    assert verdict_service.compare_versions("v2026.11.1", "v2026.9") > 0
    assert verdict_service.compare_versions("v2026_09", "v2026_9") == 0


# ---------- 中断可续跑 ----------

def _break_graph(monkeypatch):
    original = graph.build_graph

    def explode(*args, **kwargs):
        raise RuntimeError("模拟写入中断")

    monkeypatch.setattr(verdict_service.graph, "build_graph", explode)
    return original


def test_interrupted_write_resumes_with_same_run_id(client, lineage, monkeypatch):
    """写入在 graph 阶段前中断：同输入重试从检查点续跑，不新增结果行。"""
    original = _break_graph(monkeypatch)
    with pytest.raises(RuntimeError):
        _submit(client, lineage)

    pending = store.latest_metric_run(lineage)
    assert pending["status"] == config.VERDICT_PENDING
    assert "evaluate" in pending["progress"]["done"]
    assert "graph" not in pending["progress"]["done"]
    assert pending["result"] is None
    assert len(_rows(lineage)) == 1

    # 未完成的裁决不能当审计凭据读
    pending_read = client.get(f"/api/metrics/verdicts/{pending['run_id']}")
    assert pending_read.status_code == 409
    assert pending_read.json()["error"] == "VERDICT_PENDING"
    assert pending_read.json()["detail"]["resumable"] is True

    monkeypatch.setattr(verdict_service.graph, "build_graph", original)
    resumed = _submit(client, lineage).json()
    assert resumed["run_id"] == pending["run_id"]
    assert resumed["resumed"] is True
    assert resumed["status"] == "ready"
    assert len(_rows(lineage)) == 1


def test_explicit_resume_endpoint(client, lineage, monkeypatch):
    original = _break_graph(monkeypatch)
    with pytest.raises(RuntimeError):
        _submit(client, lineage)
    pending = store.latest_metric_run(lineage)

    monkeypatch.setattr(verdict_service.graph, "build_graph", original)
    response = client.post(f"/api/metrics/verdicts/{pending['run_id']}/resume")
    assert response.status_code == 200
    assert response.json()["run_id"] == pending["run_id"]
    assert len(_rows(lineage)) == 1


def test_resume_rejects_when_checkpoint_definitions_missing(client, lineage):
    run = _submit(client, lineage).json()
    stored = store.metric_run_by_id(run["run_id"])
    stored["status"] = config.VERDICT_PENDING
    stored["result"] = None
    progress = dict(stored["progress"])
    progress["data"] = {}
    progress["done"] = []
    stored["progress"] = progress
    store.save_metric_run(stored)
    with pytest.raises(errors.Conflict) as caught:
        verdict_service.resume(run["run_id"])
    assert caught.value.detail["reason"] == "CHECKPOINT_INCOMPLETE"


def test_superseded_pending_verdict_cannot_be_resumed(client, lineage, monkeypatch):
    """旧版本中断后又被新修订顶掉：补跑旧裁决会误导审计，必须拒绝。"""
    original = _break_graph(monkeypatch)
    with pytest.raises(RuntimeError):
        _submit(client, lineage, input_version="v2026_09")
    pending = store.latest_metric_run(lineage)

    monkeypatch.setattr(verdict_service.graph, "build_graph", original)
    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 9"
    _submit(client, lineage, revised, input_version="v2026_10")

    response = client.post(f"/api/metrics/verdicts/{pending['run_id']}/resume")
    assert response.status_code == 409
    assert response.json()["error"] == "VERSION_STALE"
    assert response.json()["detail"]["reason"] == "METRIC_VERSION_SUPERSEDED"


# ---------- 冲突、环、缺依赖的判定 ----------

def test_conflict_cycle_and_broken_dependency(client, lineage):
    definitions = [
        {"name": "a", "expression": "x", "version": "v1", "depends_on": ["missing"]},
        {"name": "loop_a", "expression": "y", "version": "v1", "depends_on": ["loop_b"]},
        {"name": "loop_b", "expression": "z", "version": "v1", "depends_on": ["loop_a"]},
        {"name": "dup", "expression": "u", "version": "v1", "source": "one"},
        {"name": "dup", "expression": "v", "version": "v1", "source": "two"},
    ]
    body = _submit(client, lineage, definitions, input_version="v1").json()
    result = _result(client, body)
    assert result["conflicts"][0]["name"] == "dup"
    assert result["conflicts"][0]["reason"] == "DEFINITION_CONFLICT"
    assert result["cycles"][0]["nodes"] == ["loop_a", "loop_b"]
    assert result["broken_deps"][0]["dependency"] == "missing"
    assert result["overall"] == "BLOCK"


def test_cycle_has_single_anchor(client, lineage):
    definitions = [
        {"name": "a", "expression": "x", "version": "v1", "depends_on": ["b"]},
        {"name": "b", "expression": "y", "version": "v1", "depends_on": ["a"]},
    ]
    body = _submit(client, lineage, definitions, input_version="v1").json()
    anchors = [chain["anchor"] for chain in _result(client, body)["chains"]]
    assert anchors == ["a"]


def test_self_dependency_is_cycle(client, lineage):
    definitions = [
        {"name": "selfie", "expression": "x", "version": "v1", "depends_on": ["selfie"]},
    ]
    body = _submit(client, lineage, definitions, input_version="v1").json()
    result = _result(client, body)
    assert result["self_loops"] == ["selfie"]
    assert result["metrics"][0]["status"] == "BLOCK"


def test_downstream_chain_is_marked_affected(client, lineage):
    """排障人员要看到受影响链路：下游节点标 WARN，但上游破坏才是 BLOCK。"""
    body = _submit(client, lineage).json()
    result = _result(client, body)
    metrics = {row["name"]: row for row in result["metrics"]}
    assert metrics["pay_amt"]["status"] == "BLOCK"
    assert metrics["gmv"]["status"] == "WARN"
    assert "METRIC_CHAIN_AFFECTED" in metrics["gmv"]["reasons"]
    assert metrics["report_daily"]["status"] == "WARN"

    chain = next(item for item in result["chains"] if item["anchor"] == "pay_amt")
    assert [node["name"] for node in chain["nodes"]] == ["pay_amt", "gmv", "report_daily"]
    assert chain["affected_count"] == 2


# ---------- 授权 ----------

def test_unauthorized_view_is_anonymous(client, lineage):
    body = _submit(client, lineage).json()
    response = client.get(f"/api/metrics/verdicts/{body['run_id']}")
    assert response.status_code == 200
    verdict = response.json()["verdict"]
    assert verdict["role"] == "viewer"
    assert verdict["redacted"] is True
    text = json.dumps(verdict, ensure_ascii=False)
    for name in ("pay_amt", "refund_amt", "gmv", "sum(pay_fee)"):
        assert name not in text
    assert all(item["anon"].startswith("m-") for item in verdict["metrics"])
    assert verdict["chains"]


def test_audit_token_reveals_names_and_formulas(client, lineage):
    body = _submit(client, lineage).json()
    response = client.get(f"/api/metrics/verdicts/{body['run_id']}", params=TOKEN)
    verdict = response.json()["verdict"]
    assert verdict["role"] == "auditor"
    assert verdict["redacted"] is False
    names = {row["name"] for row in verdict["metrics"]}
    assert {"pay_amt", "gmv"} <= names
    expressions = {row["expression"] for row in verdict["metrics"]}
    assert "sum(pay_fee)" in expressions


def test_claimed_auditor_without_token_is_forbidden(client, lineage):
    body = _submit(client, lineage).json()
    response = client.get(f"/api/metrics/verdicts/{body['run_id']}",
                          params={"role": "auditor"})
    assert response.status_code == 403
    assert response.json()["error"] == "AUDIT_DENIED"


def test_wrong_token_is_forbidden_not_downgraded(client, lineage):
    body = _submit(client, lineage).json()
    response = client.get(f"/api/metrics/verdicts/{body['run_id']}",
                          params={"audit_token": "wrong"})
    assert response.status_code == 403
    assert response.json()["error"] == "AUDIT_DENIED"


def test_audit_log_requires_authorization(client, lineage):
    body = _submit(client, lineage).json()
    denied = client.get(f"/api/metrics/verdicts/{body['run_id']}/audit")
    assert denied.status_code == 403
    allowed = client.get(f"/api/metrics/verdicts/{body['run_id']}/audit", params=TOKEN)
    assert allowed.status_code == 200
    events = allowed.json()["events"]
    assert "verdict_ready" in {item["action"] for item in events}
    # 原始凭据不落盘
    assert all(item["token_fp"] != "audit-local" for item in events)


def test_anonymous_id_is_stable_across_versions(client, lineage):
    first = _submit(client, lineage, input_version="v2026_09").json()
    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 2"
    second = _submit(client, lineage, revised, input_version="v2026_10").json()
    first_view = client.get(f"/api/metrics/verdicts/{first['run_id']}").json()["verdict"]
    second_view = client.get(f"/api/metrics/verdicts/{second['run_id']}").json()["verdict"]
    # 两个版本的指标集合相同，排序也相同，匿名编号必须一一对应
    assert [row["anon"] for row in first_view["metrics"]] == \
           [row["anon"] for row in second_view["metrics"]]


# ---------- 导出与所选版本一致 ----------

def test_export_matches_selected_version(client, lineage):
    first = _submit(client, lineage, input_version="v2026_09").json()
    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 3"
    second = _submit(client, lineage, revised, input_version="v2026_10").json()

    old_manifest = client.get("/api/export/metrics-manifest.json",
                              params={"run_id": first["run_id"]}).json()
    new_manifest = client.get("/api/export/metrics-manifest.json",
                              params={"run_id": second["run_id"]}).json()
    assert old_manifest["run_id"] == first["run_id"]
    assert old_manifest["input_version"] == "v2026_09"
    assert new_manifest["run_id"] == second["run_id"]
    assert new_manifest["input_version"] == "v2026_10"
    assert old_manifest["input_sha256"] != new_manifest["input_sha256"]

    old_csv = client.get("/api/export/metrics.csv",
                         params={"run_id": first["run_id"], **TOKEN}).text
    new_csv = client.get("/api/export/metrics.csv",
                         params={"run_id": second["run_id"], **TOKEN}).text
    assert "pay_amt + refund_amt" in old_csv
    assert "pay_amt + refund_amt + 3" in new_csv
    assert "pay_amt + refund_amt + 3" not in old_csv


def test_unauthorized_export_is_anonymous_csv(client, lineage):
    body = _submit(client, lineage).json()
    response = client.get("/api/export/chains.csv", params={"run_id": body["run_id"]})
    assert response.status_code == 200
    assert "pay_amt" not in response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows and all(row["node_anon"].startswith("m-") for row in rows)


def test_markdown_export_requires_auditor(client, lineage):
    body = _submit(client, lineage).json()
    denied = client.get("/api/export/metrics.md", params={"run_id": body["run_id"]})
    assert denied.status_code == 403
    allowed = client.get("/api/export/metrics.md",
                         params={"run_id": body["run_id"], **TOKEN})
    assert allowed.status_code == 200
    assert "pay_amt" in allowed.text


def test_pending_verdict_cannot_be_exported(client, lineage, monkeypatch):
    _break_graph(monkeypatch)
    with pytest.raises(RuntimeError):
        _submit(client, lineage)
    pending = store.latest_metric_run(lineage)
    response = client.get("/api/export/metrics-manifest.json",
                          params={"run_id": pending["run_id"]})
    assert response.status_code == 409
    assert response.json()["error"] == "VERDICT_PENDING"


# ---------- 不变量 ----------

def test_definitions_order_does_not_change_verdict(client, lineage):
    first = _submit(client, lineage, input_version="v2026_09").json()
    shuffled = list(reversed(_defs("v2026_09")))
    second = _submit(client, lineage, shuffled, input_version="v2026_09").json()
    assert second["run_id"] == first["run_id"]
    assert second["reused"] is True


def test_page_selects_requested_version(client, lineage):
    """页面给 run_id 就看那一版：两个版本的输入版本与摘要都要对得上。"""
    first = _submit(client, lineage, input_version="v2026_09").json()
    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 7"
    second = _submit(client, lineage, revised, input_version="v2026_10").json()

    old_page = client.get("/metrics", params={"run_id": first["run_id"],
                                              "audit_token": "audit-local"})
    assert old_page.status_code == 200
    assert first["run_id"] in old_page.text
    assert "v2026_09" in old_page.text
    assert "pay_amt + refund_amt + 7" not in old_page.text

    new_page = client.get("/metrics", params={"run_id": second["run_id"],
                                              "audit_token": "audit-local"})
    assert "pay_amt + refund_amt + 7" in new_page.text


def test_page_lists_previous_version_for_comparison(client, lineage):
    first = _submit(client, lineage, input_version="v2026_09").json()
    revised = _defs("v2026_10")
    revised[2]["expression"] = "pay_amt + refund_amt + 8"
    _submit(client, lineage, revised, input_version="v2026_10")
    page = client.get("/metrics", params={"run_id": first["run_id"]})
    assert first["run_id"] in page.text
    # 旧版仍在列表里，审计链没有断
    assert page.text.count("mv-") >= 2


def test_counts_add_up(run_verdict):
    result = run_verdict["result"]
    counts = result["counts"]
    assert counts["pass"] + counts["warn"] + counts["block"] == len(result["metrics"])
    assert counts["affected"] == len(
        {node["name"] for chain in result["chains"] for node in chain["nodes"]}
    )
    assert counts["chains"] == len(result["chains"])
    assert counts["conflicts"] == len(result["conflicts"])


def test_pending_and_resume_reasons_are_registered():
    from app import reasons

    for code in ("METRIC_CONFLICT", "METRIC_CYCLE", "METRIC_BROKEN_DEP",
                 "METRIC_STALE_SUBMIT", "METRIC_VERDICT_PENDING", "AUDIT_DENIED",
                 "METRIC_CHAIN_AFFECTED", "METRIC_REVISION_CHANGED"):
        assert code in reasons.known_codes()
