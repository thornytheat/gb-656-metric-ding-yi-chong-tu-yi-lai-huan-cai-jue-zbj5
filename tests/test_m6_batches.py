"""M6 版本批次：幂等重跑、陈旧拒绝、半开区间、重叠标记、历史。"""

from __future__ import annotations

import pytest

from app import errors, store
from app.modules.m6_batches import guard, history, service as batch_service, window
from tests.fixtures import expected


def test_seeded_run_matches_expected(run):
    assert run["version_seq"] == 1
    assert run["version_label_old"] == "v2026_06"
    assert run["version_label_new"] == "v2026_09"
    assert run["effective_from"] == expected.DEFAULT_WINDOW_FROM
    assert run["effective_to"] is None
    assert run["run_id"].startswith("fm-")
    assert run["ruleset"]


def test_idempotent_rerun_reuses_run(client):
    first = store.latest_run()
    again = batch_service.execute()
    assert again["run_id"] == first["run_id"]
    assert again["reused"] is True
    assert store.count_runs() == 1
    assert again["ledger"]["counts"] == first["ledger"]["counts"]


def test_trap26_stale_submission_rejected(client):
    """TRAP-26：基线落后于当前版本号的提交必须被拒，不能覆盖新结果。"""
    response = client.post("/api/runs", json={"base_version_seq": 0})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "VERSION_STALE"
    assert body["detail"]["current_version_seq"] == 1
    assert store.count_runs() == 1


def test_current_baseline_accepted(client):
    response = client.post("/api/runs", json={"base_version_seq": 1})
    assert response.status_code == 200
    assert response.json()["reused"] is True


def test_stale_guard_unit():
    guard.check_stale(None, 3)
    guard.check_stale(3, 3)
    with pytest.raises(errors.StaleVersion):
        guard.check_stale(2, 3)
    with pytest.raises(errors.BadInput):
        guard.check_stale(-1, 3)
    with pytest.raises(errors.BadInput):
        guard.check_stale("abc", 3)


def test_trap25_overlap_flagged_but_adjacent_clean(client):
    """TRAP-25：生效区间重叠要标记；端点相邻（左闭右开）不算重叠。"""
    adjacent = client.post("/api/runs", json={
        "effective_from": "2026-08-01T00:00:00Z",
        "effective_to": "2026-09-01T00:00:00Z",
        "params": {"wave": "adjacent"},
    })
    assert adjacent.status_code == 200
    assert adjacent.json()["warnings"] == []

    overlapping = client.post("/api/runs", json={
        "effective_from": "2026-08-01T00:00:00+08:00",
        "effective_to": "2026-09-02T00:00:00Z",
        "params": {"wave": "overlap"},
    })
    assert overlapping.status_code == 200
    codes = [item["code"] for item in overlapping.json()["warnings"]]
    assert codes.count("VERSION_OVERLAP") == 2


def test_window_normalizes_offsets_to_utc():
    utc = window.normalize("2026-09-01T00:00:00Z", None, "v2026_09")
    local = window.normalize("2026-09-01T08:00:00+08:00", None, "v2026_09")
    assert utc["from"] == local["from"] == "2026-09-01T00:00:00+00:00"
    assert window.overlaps(utc, local) is True


def test_window_half_open_edges():
    left = window.normalize("2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z", "v2026_08")
    right = window.normalize("2026-09-01T00:00:00Z", None, "v2026_09")
    assert window.overlaps(left, right) is False
    shifted = window.normalize("2026-08-31T23:59:59Z", None, "v2026_08")
    assert window.overlaps(left, shifted) is True


def test_inverted_window_rejected():
    with pytest.raises(errors.BadInput) as caught:
        window.normalize("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "v2026_09")
    assert caught.value.detail["reason"] == "WINDOW_INVERTED"


def test_history_listing_and_balance(run):
    rows = history.listing()
    assert rows[0]["run_id"] == run["run_id"]
    assert rows[0]["balanced"] is True
    assert rows[0]["old_total"] == expected.OLD_TOTAL
    assert rows[0]["new_total"] == expected.NEW_TOTAL
    reconciled = history.reconcile(run)
    assert reconciled["balanced"]["all"] is True
    assert reconciled["window"]["semantics"] == "[start, end)"


def test_version_seq_is_monotonic(client):
    first = store.latest_run()["version_seq"]
    client.post("/api/runs", json={"params": {"wave": "one"}})
    second = store.latest_run()["version_seq"]
    client.post("/api/runs", json={"params": {"wave": "two"}})
    third = store.latest_run()["version_seq"]
    assert first < second < third
