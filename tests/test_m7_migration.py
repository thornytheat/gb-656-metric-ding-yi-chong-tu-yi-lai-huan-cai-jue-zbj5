"""M7 迁移单：条款号、四份导出、大整数保形、账目守恒。"""

from __future__ import annotations

import csv
import io
import json

from app.modules.m7_migration import ledger as ledger_mod, service as migration
from tests.fixtures import expected


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _pairs(text: str) -> dict:
    """两列 CSV 转字典，避免把表头当成数据。"""
    rows = list(csv.reader(io.StringIO(text)))
    return {row[0]: row[1] for row in rows[1:] if len(row) >= 2}


def test_spec_markdown_has_clauses_and_footnotes(run):
    text = migration.spec_markdown(run)
    assert text.startswith(f"# 迁移单 {run['run_id']}")
    for index in range(1, 7):
        assert f"### 3.{index} " in text
    assert "[^1]" in text and "[^4]" in text
    assert "## 2 字段账" in text
    assert "## 6 回滚" in text
    assert "| 旧版本字段 | 13 |" in text
    assert "| 新版本字段 | 16 |" in text


def test_spec_clauses_match_breaking_rows(run):
    clauses = migration.clauses(run)
    breaking = [row for row in run["diff"] if row["verdict"] == "BREAKING"]
    assert len(clauses) == len(breaking) == 6
    assert [item["no"] for item in clauses] == [f"3.{i}" for i in range(1, 7)]
    assert all(item["primary_label"] for item in clauses)


def test_spec_contains_alias_sql(run):
    text = migration.spec_markdown(run)
    assert "order_code as order_no" in text
    assert "无改名" not in text


def test_fields_csv_rows_and_reasons(run):
    rows = _rows(migration.fields_csv_text(run))
    assert len(rows) == expected.OLD_TOTAL + 4
    assert set(rows[0]) == {
        "field_id", "table", "old_name", "new_name", "old_type", "new_type",
        "change", "type_detail", "required_change", "reader", "writer", "verdict",
        "primary_reason", "bucket", "ref_count", "line_old", "line_new",
    }
    by_name = {row["old_name"]: row for row in rows if row["old_name"]}
    assert by_name["pay_amt"]["verdict"] == "BREAKING"
    assert by_name["pay_amt"]["type_detail"] == "int_digits_drop"
    assert by_name["order_no"]["reader"] == "Y"
    assert by_name["status"]["primary_reason"] == "FIELD_DELETED_REFERENCED"


def test_big_field_id_survives_csv(client):
    from app.modules.m1_intake import service as intake

    snapshot = intake.ingest("metadata_extra.json")
    csv_text = migration.fields_csv_text({"diff": [
        {"field_id": field["field_id"], "table": field["table"],
         "old_name": field["name"], "new_name": field["name"],
         "old_type": field["type_raw"], "new_type": field["type_raw"],
         "change": "same", "type_detail": None, "required_change": "same",
         "reader_ok": True, "writer_ok": True, "verdict": "COMPATIBLE",
         "primary": "TYPE_SAME", "bucket": "unchanged", "ref_count": 0,
         "line_old": field["line"], "line_new": field["line"]}
        for field in snapshot["fields"]
    ]})
    assert expected.BIG_ID in csv_text
    assert str(expected.BIG_ID_AS_FLOAT) not in csv_text


def test_refs_csv_marks_broken(run):
    rows = _rows(migration.refs_csv_text(run))
    assert len(rows) == expected.REFS_TOTAL
    broken = [row for row in rows if row["reason"] == "REF_BROKEN"]
    assert len(broken) == expected.REFS_BROKEN
    aliased = [row for row in rows if row["via"] == "alias"]
    assert len(aliased) == expected.REFS_ALIASED


def test_ledger_csv_values(run):
    rows = _pairs(migration.ledger_csv_text(run))
    assert rows["old_total"] == "13"
    assert rows["new_total"] == "16"
    assert rows["unchanged"] == "4"
    assert rows["conflict"] == "1"
    assert rows["refs_total"] == "17"
    assert rows["broken"] == "3"


def test_manifest_json_fields(run):
    payload = json.loads(migration.manifest_json_text(run))
    assert payload["run_id"] == run["run_id"]
    assert set(payload["input_digests"]) == {"base", "new", "old", "refs"}
    assert len(payload["input_sha256"]) == 64
    assert payload["window_semantics"] == "[start, end)"
    assert payload["balanced"]["all"] is True
    assert payload["counts"]["breaking"] == 6
    assert payload["input_names"]["old"] == "schema_v2026_06.sql"


def test_ledger_conservation_from_diff():
    diff = {
        "buckets": expected.BUCKETS,
        "counts": {"old_total": expected.OLD_TOTAL, "new_total": expected.NEW_TOTAL},
        "verdicts": expected.VERDICTS,
    }
    consumers = [{"refs": ["a.b"]}, {"refs": ["c.d", "e.f"]}]
    resolution = {"reachable": 2, "broken_count": 1, "aliased_count": 0, "direct_count": 2}
    built = ledger_mod.build(diff, consumers, resolution)
    assert built["fields"]["old_gap"] == 0
    assert built["fields"]["new_gap"] == 0
    assert built["refs"]["gap"] == 0
    assert built["balanced"]["all"] is True


def test_ledger_flags_unbalanced():
    diff = {
        "buckets": {"unchanged": 3, "renamed": 1, "retyped": 5, "required": 1,
                    "conflict": 1, "deleted": 1, "added": 4},
        "counts": {"old_total": 13, "new_total": 99},
        "verdicts": {},
    }
    built = ledger_mod.build(diff, [], {"reachable": 0, "broken_count": 0})
    assert built["fields"]["new_gap"] == 99 - 15
    assert built["balanced"]["fields_new"] is False
    assert built["balanced"]["all"] is False
