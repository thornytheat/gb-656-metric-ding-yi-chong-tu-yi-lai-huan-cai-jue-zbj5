"""M3 兼容判定：两向结论、必填变化、改名冲突、优先级。"""

from __future__ import annotations

import itertools

from app.modules.m3_compat import direction, judge, priority
from tests.fixtures import expected


def _pair(old_rows, new_rows, ref_counts=None):
    old_fields = expected.make_fields(old_rows)
    new_fields = expected.make_fields(new_rows)
    return judge.diff_pair(old_fields, new_fields, ref_counts or {})


def test_widen_is_compatible_both_ways():
    diff = _pair([{"field_id": "1", "name": "amount", "type": "decimal(18,2)"}],
                 [{"field_id": "1", "name": "amount", "type": "decimal(20,4)"}])
    row = diff["rows"][0]
    assert row["change"] == "widen"
    assert row["verdict"] == "COMPATIBLE"
    assert row["reader_ok"] and row["writer_ok"]
    assert row["bucket"] == "retyped"


def test_narrow_breaks_both_sides():
    diff = _pair([{"field_id": "1", "name": "pay_amt", "type": "decimal(18,2)"}],
                 [{"field_id": "1", "name": "pay_amt", "type": "decimal(18,4)"}])
    row = diff["rows"][0]
    assert row["change"] == "narrow"
    assert row["verdict"] == "BREAKING"
    assert row["reader_ok"] is False and row["writer_ok"] is False
    assert row["primary"] == "TYPE_NARROW"
    assert row["type_detail"] == "int_digits_drop"


def test_required_tightening_breaks_writer_only():
    diff = _pair([{"field_id": "1", "name": "paid_at", "type": "timestamp",
                   "required": False}],
                 [{"field_id": "1", "name": "paid_at", "type": "timestamp",
                   "required": True}])
    row = diff["rows"][0]
    assert row["required_change"] == "tightened"
    assert row["reader_ok"] is True
    assert row["writer_ok"] is False
    assert row["verdict"] == "BREAKING"
    assert row["primary"] == "REQUIRED_TIGHTENED"
    assert row["writer"]["repair"] == "default_value"


def test_required_loosening_is_compatible():
    diff = _pair([{"field_id": "1", "name": "note", "type": "varchar(32)",
                   "required": True}],
                 [{"field_id": "1", "name": "note", "type": "varchar(32)",
                   "required": False}])
    row = diff["rows"][0]
    assert row["required_change"] == "loosened"
    assert row["verdict"] == "COMPATIBLE"
    assert row["bucket"] == "required"


def test_rename_only_needs_alias():
    diff = _pair([{"field_id": "1", "name": "order_no", "type": "varchar(64)"}],
                 [{"field_id": "1", "name": "order_code", "type": "varchar(64)"}])
    row = diff["rows"][0]
    assert row["bucket"] == "renamed"
    assert row["verdict"] == "COMPATIBLE_BY_MAPPING"
    assert row["reader"]["repair"] == "alias"
    assert row["reader_ok"] is True


def test_trap17_rename_is_not_delete_plus_add(run):
    """TRAP-17：改名必须按身份对齐，不能算成一次删除加一次新增。"""
    rows = [row for row in run["diff"] if row["old_name"] == "order_no"]
    assert len(rows) == 1
    assert rows[0]["bucket"] == "renamed"
    assert rows[0]["new_name"] == "order_code"
    deleted = [row for row in run["diff"] if row["bucket"] == "deleted"]
    assert len(deleted) == 1
    assert deleted[0]["old_name"] == "status"


def test_rename_and_retype_is_conflict():
    diff = _pair([{"field_id": "2005", "table": "fct_pay", "name": "channel",
                   "type": "varchar(16)", "required": False}],
                 [{"field_id": "2005", "table": "fct_pay", "name": "pay_channel",
                   "type": "varchar(8)", "required": False}])
    row = diff["rows"][0]
    assert row["bucket"] == "conflict"
    assert row["primary"] == "RENAME_AND_RETYPE"
    assert row["verdict"] == "BREAKING"
    assert "RENAME_ONLY" in row["reasons"] and "TYPE_NARROW" in row["reasons"]


def test_trap19_rule_order_does_not_change_primary():
    """TRAP-19：三条规则同时命中，主原因不能取决于书写顺序。"""
    codes = ["RENAME_AND_RETYPE", "TYPE_NARROW", "REQUIRED_TIGHTENED"]
    results = {priority.primary(list(order)) for order in itertools.permutations(codes)}
    assert results == {"RENAME_AND_RETYPE"}
    assert priority.primary(["TYPE_NARROW", "REQUIRED_TIGHTENED"]) == "TYPE_NARROW"


def test_deleted_referenced_vs_unreferenced():
    referenced = _pair(
        [{"field_id": "1", "name": "status", "type": "varchar(16)"}], [],
        {("dim_order", "status"): 2},
    )["rows"][0]
    assert referenced["bucket"] == "deleted"
    assert referenced["verdict"] == "BREAKING"
    assert referenced["primary"] == "FIELD_DELETED_REFERENCED"
    assert referenced["ref_count"] == 2

    plain = _pair([{"field_id": "2", "name": "legacy_flag", "type": "bool"}], [])["rows"][0]
    assert plain["verdict"] == "COMPATIBLE"
    assert plain["primary"] == "FIELD_DELETED"


def test_sample_buckets_match_expected(run):
    assert run["buckets"] == expected.BUCKETS
    assert run["verdicts"] == expected.VERDICTS
    assert run["counts"]["old_total"] == expected.OLD_TOTAL
    assert run["counts"]["new_total"] == expected.NEW_TOTAL
    assert run["counts"]["breaking"] == expected.VERDICTS["BREAKING"]


def test_direction_helpers_directly():
    assert direction.required_change(True, False) == "loosened"
    assert direction.required_change(False, True) == "tightened"
    assert direction.required_change(True, True) == "same"
    assert direction.combine({"ok": True}, {"ok": False}, False) == "BREAKING"
    assert direction.combine({"ok": True}, {"ok": True}, True) == "COMPATIBLE_BY_MAPPING"
