"""M5 影响面：反向闭包、环与自引用、单边登记、断链。"""

from __future__ import annotations

from app.modules.m5_impact import closure, cycle, service as impact_service
from tests.fixtures import expected


def _consumers(run):
    return run["refs"]["consumers"]


def test_trap20_cycle_and_self_loop(run):
    """TRAP-20：两个节点互相依赖成一个环，另一个节点指向自己。"""
    found = cycle.find_cycles(_consumers(run))
    assert found["cycle_count"] == 1
    assert found["cycles"][0]["nodes"] == ["dw.mv_pay_daily", "dw.mv_user_dim"]
    assert found["cycles"][0]["length"] == 2
    assert found["self_loop_count"] == 1
    assert found["self_loops"][0]["node"] == "dw.task_self"
    # 自引用不重复算进环
    assert all(len(item["nodes"]) > 1 for item in found["cycles"])


def test_trap21_one_sided_registration(run):
    """TRAP-21：上下游只登记一边，必须双向核验后列出缺边。"""
    sides = run["impact"]["one_sided"]
    assert len(sides) == expected.ONE_SIDED
    pairs = {(item["upstream"], item["downstream"]) for item in sides}
    assert ("dw.task_settle", "ads.rpt_pay_channel") in pairs
    assert ("dw.mv_order_daily", "dw.mv_order_stat") in pairs
    assert all(item["reason"] == "REF_ONE_SIDED" for item in sides)


def test_impact_closure_from_sample_run(run):
    graph = run["impact"]["impact"]
    assert graph["total"] == expected.IMPACT_TOTAL
    assert graph["depth_counts"] == expected.IMPACT_DEPTHS
    rows = {row["name"]: row for row in graph["rows"]}
    assert rows["dw.mv_pay_daily"]["depth"] == 1
    assert rows["dw.mv_user_dim"]["depth"] == 2
    assert rows["dw.mv_user_dim"]["via"] == "dw.mv_pay_daily"
    assert rows["dw.mv_order_stat"]["via"] == "broken"
    assert [row["depth"] for row in graph["rows"]] == [1, 1, 1, 2]


def test_broken_refs_carry_reason(run):
    broken = run["impact"]["broken"]
    assert len(broken) == expected.REFS_BROKEN
    assert {item["ref"] for item in broken} == {
        "dim_order.status", "fct_pay.channel",
    }
    assert all(item["reason"] == "REF_BROKEN" for item in broken)


def test_impact_empty_when_nothing_breaks():
    consumers = [{"name": "v.a", "kind": "view", "refs": [], "upstream": [],
                  "downstream": []}]
    graph = closure.impact([], {"1": ["v.a"]}, consumers)
    assert graph["total"] == 0
    assert graph["rows"] == []
    assert graph["depth_counts"] == {}


def test_impact_sorted_by_depth_then_name(run):
    rows = run["impact"]["impact"]["rows"]
    keys = [(row["depth"], row["name"]) for row in rows]
    assert keys == sorted(keys)


def test_closure_is_superset_with_broken_seeds(run):
    diff = {"rows": run["diff"]}
    changed = impact_service.changed_fields(diff)
    consumers = _consumers(run)
    by_field = {key: sorted(value) for key, value in run["resolution"]["by_field"].items()}
    plain = closure.impact(changed, by_field, consumers)
    broken_seeds = {}
    for item in run["resolution"]["broken"]:
        broken_seeds.setdefault(item["consumer"], set()).add(f"断链 {item['ref']}")
    widened = closure.impact(changed, by_field, consumers, broken_seeds=broken_seeds)
    assert {row["name"] for row in plain["rows"]} <= {row["name"] for row in widened["rows"]}
    assert widened["total"] > plain["total"]


def test_summary_is_balanced(run):
    summary = run["impact"]["summary"]
    assert summary["consumers"] == expected.CONSUMERS
    assert summary["refs_total"] == expected.REFS_TOTAL
    assert summary["reachable"] == expected.REFS_REACHABLE
    assert summary["broken"] == expected.REFS_BROKEN
    assert summary["one_sided"] == expected.ONE_SIDED
    assert summary["cycles"] == expected.CYCLES
    assert summary["self_loops"] == expected.SELF_LOOPS
    assert summary["balanced"] is True


def test_changed_fields_exclude_added(run):
    rows = impact_service.changed_fields({"rows": run["diff"]})
    assert len(rows) == 5
    assert all(row["bucket"] != "added" for row in rows)
    assert all(row["verdict"] == "BREAKING" for row in rows)
