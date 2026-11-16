"""M1 元数据采信：编码、标记、行不齐、重复列、派生身份、大整数身份。"""

from __future__ import annotations

import hashlib

from app.modules.m1_intake import reader, service, sheet
from tests.fixtures import expected


def _derived(table: str, name: str) -> str:
    seed = f"{table.strip().lower()}|{name.strip().lower()}".encode("utf-8")
    return "auto:" + hashlib.sha256(seed).hexdigest()[:12]


def test_trap02_bom_stripped_before_decode(client):
    """TRAP-02：标记先剥、再探测编码，否则带标记的 GB18030 一件都解不出来。"""
    snapshot = service.ingest("schema_v2026_06.sql")
    names = [field["name"] for field in snapshot["fields"]]
    assert "\ufefforder_id" not in names
    assert "order_id" in names
    assert snapshot["bom"] is True
    assert any(row["reason"] == "BOM_STRIPPED" for row in snapshot["diagnostics"])


def test_encoding_fallback_is_recorded(client):
    snapshot = service.ingest("schema_v2026_06.sql")
    assert snapshot["encoding"] == "gb18030"
    assert snapshot["fallback"] is True
    assert any(row["reason"] == "ENCODING_FALLBACK" for row in snapshot["diagnostics"])


def test_crlf_keeps_logical_lines_and_original_numbers(client):
    raw = reader.read_meta_file(service.config.resolve_sample("schema_v2026_06.sql"))
    assert raw.line_count == 23
    assert all("\r" not in line for _, line in raw.lines)
    assert raw.lines[0][0] == 1
    assert raw.lines[1] == (2, "CREATE TABLE dim_order (")

    snapshot = service.ingest("schema_v2026_06.sql")
    order_id = next(f for f in snapshot["fields"] if f["name"] == "order_id")
    assert order_id["line"] == 3
    assert order_id["type_raw"] == "BIGINT"


def test_trap05_extra_separator_quarantined(client):
    """TRAP-05：行尾多一个分隔符，整行进隔离区，不能补空列混进判定。"""
    snapshot = service.ingest("schema_v2026_06.sql")
    blocked = [row for row in snapshot["diagnostics"] if row["blocking"]]
    reasons = {row["reason"] for row in blocked}
    assert "LINE_MALFORMED" in reasons
    malformed = next(row for row in blocked if row["reason"] == "LINE_MALFORMED")
    assert malformed["line"] == 7
    assert "order_amt" in malformed["raw"]
    assert "1005" not in {field["field_id"] for field in snapshot["fields"]}


def test_duplicate_column_keeps_first_by_line(client):
    snapshot = service.ingest("schema_v2026_06.sql")
    dup = [row for row in snapshot["diagnostics"] if row["reason"] == "DUPLICATE_COLUMN"]
    assert len(dup) == 1
    assert dup[0]["column"] == "ORDER_NO"
    assert dup[0]["line"] == 5
    kept = next(f for f in snapshot["fields"] if f["name"].lower() == "order_no")
    assert kept["field_id"] == "1002"
    assert kept["line"] == 4


def test_missing_field_id_derived_is_position_independent():
    columns = [
        {"table": "dim_a", "name": "x", "type_raw": "bigint", "required": True,
         "field_id": None, "line": 1},
        {"table": "dim_a", "name": "y", "type_raw": "bigint", "required": True,
         "field_id": None, "line": 2},
    ]
    first = sheet.fold_columns(columns, "v1", "t")
    second = sheet.fold_columns([dict(item, line=item["line"] + 100) for item in columns],
                                "v1", "t")
    ids_first = {field["name"]: field["field_id"] for field in first["fields"]}
    ids_second = {field["name"]: field["field_id"] for field in second["fields"]}
    assert ids_first == ids_second
    assert ids_first["x"] == _derived("dim_a", "x")
    assert ids_first["x"] != ids_first["y"]


def test_trap09_big_integer_field_id_kept_as_string(client):
    """TRAP-09：2^53+1 的身份过一遍浮点就被改末位，必须按字符串保形。"""
    snapshot = service.ingest("metadata_extra.json")
    ids = [field["field_id"] for field in snapshot["fields"]]
    assert expected.BIG_ID in ids
    assert str(expected.BIG_ID_AS_FLOAT) != expected.BIG_ID
    assert all(not str(field_id).endswith(".0") for field_id in ids)


def test_json_metadata_diagnostics(client):
    snapshot = service.ingest("metadata_extra.json")
    reasons = {(row["reason"], row["blocking"]) for row in snapshot["diagnostics"]}
    assert ("FIELD_ID_DUP", True) in reasons
    assert ("FIELD_ID_DERIVED", False) in reasons
    assert ("REQUIRED_TOKEN_UNKNOWN", False) in reasons
    assert ("UNKNOWN_TYPE", False) in reasons
    assert snapshot["stats"]["accepted"] == 4
    assert snapshot["stats"]["blocked"] == 1


def test_intake_account_is_balanced(client):
    snapshot = service.ingest("schema_v2026_06.sql")
    stats = snapshot["stats"]
    assert stats["column_lines"] == expected.OLD_TOTAL + stats["blocked"]
    assert stats["accepted"] + stats["blocked"] == stats["column_lines"]
    assert stats["accepted"] == len(snapshot["fields"]) == expected.OLD_TOTAL
    assert stats["parsed"] == 14
    assert stats["parse_failures"] == 1
