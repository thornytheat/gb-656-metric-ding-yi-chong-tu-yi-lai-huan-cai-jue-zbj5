"""M4 字段映射：别名 SQL、冲突、跨版本改名链、SQL 抽取、引用身份解析。"""

from __future__ import annotations

from app.modules.m4_mapping import mapping, rename, service as map_service, sqlalias


def test_alias_sql_for_renamed_field(run):
    rows = [row for row in run["mapping"]["rows"] if row["old_name"] == "order_no"]
    assert len(rows) == 1
    assert rows[0]["status"] == "renamed"
    assert rows[0]["alias_sql"] == "order_code as order_no"
    assert run["mapping"]["aliases"] == [{"table": "dim_order",
                                          "sql": "order_code as order_no"}]


def test_conflict_row_has_no_alias(run):
    conflicts = run["mapping"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["old_names"] == ["channel"]
    assert conflicts[0]["new_name"] == "pay_channel"
    assert conflicts[0]["reason"] == "RENAME_AND_RETYPE"
    rows = [row for row in run["mapping"]["rows"] if row["old_name"] == "channel"]
    assert rows[0]["status"] == "conflict"
    assert rows[0]["alias_sql"] is None


def test_trap22_alias_chain_resolves_multi_hop(client):
    """TRAP-22：同一字段连续改两次名，最早的名字也要能解析到最终名字。"""
    from app.modules.m1_intake import service as intake

    base = intake.ingest("schema_v2026_03.sql")["fields"]
    old = intake.ingest("schema_v2026_06.sql")["fields"]
    new = intake.ingest("schema_v2026_09.sql")["fields"]
    graph = rename.merge_edges(rename.build_edges(base, old), rename.build_edges(old, new))
    resolved = rename.resolve(graph, "dim_order", "ord_no")
    assert resolved["canonical"] == "order_code"
    assert resolved["path"] == ["ord_no", "order_no", "order_code"]
    assert resolved["cycle"] is False
    assert resolved["hops"] == 2

    shuffled = dict(reversed(list(graph.items())))
    assert rename.resolve(shuffled, "dim_order", "ord_no")["canonical"] == "order_code"


def test_alias_cycle_is_reported():
    graph = {
        "t|a": {"b"},
        "t|b": {"a"},
    }
    resolved = rename.resolve(graph, "t", "a")
    assert resolved["cycle"] is True
    assert resolved["reason"] == "ALIAS_CYCLE"
    assert resolved["path"] == ["a", "b", "a"]


def test_sqlalias_extracts_alias_qualified_refs():
    parsed = sqlalias.extract_refs(
        "select o.order_id, p.pay_amt from dim_order o join fct_pay p on p.order_id = o.order_id"
    )
    assert parsed["aliases"] == {"o": "dim_order", "p": "fct_pay"}
    assert parsed["refs"] == ["dim_order.order_id", "fct_pay.order_id", "fct_pay.pay_amt"]


def test_sqlalias_ignores_comments_and_strings():
    parsed = sqlalias.extract_refs(
        "select o.order_id -- o.order_amt\n"
        "from dim_order o where o.status = 'o.note' /* p.channel */"
    )
    assert parsed["refs"] == ["dim_order.order_id", "dim_order.status"]


def test_reference_identity_blocks_rebound_name(run):
    resolution = run["resolution"]
    broken = {(item["consumer"], item["ref"]): item for item in resolution["broken"]}
    assert len(broken) == 3
    status_item = broken[("dw.mv_order_stat", "dim_order.status")]
    assert status_item["via"] == "rebound"
    assert status_item["detail"] == "名字已被别的身份占用"


def test_unsafe_alias_is_broken(run):
    broken = {(item["consumer"], item["ref"]): item
              for item in run["resolution"]["broken"]}
    channel = broken[("dw.mv_pay_daily", "fct_pay.channel")]
    assert channel["via"] == "unsafe_alias"
    assert channel["field_id"] is None
    aliased = {(item["consumer"], item["ref"]) for item in run["resolution"]["aliased"]}
    assert ("dw.mv_order_stat", "dim_order.ord_no") in aliased


def test_mapping_conflict_when_two_old_names_hit_one_new_name():
    diff = {"rows": [
        {"field_id": "1", "table": "dim_x", "old_name": "a", "new_name": "c",
         "old_type": "bigint", "new_type": "bigint", "bucket": "renamed",
         "change": "same", "verdict": "COMPATIBLE_BY_MAPPING", "primary": "RENAME_ONLY",
         "reasons": ["RENAME_ONLY"], "reader_ok": True, "writer_ok": True},
        {"field_id": "2", "table": "dim_x", "old_name": "b", "new_name": "c",
         "old_type": "bigint", "new_type": "bigint", "bucket": "renamed",
         "change": "same", "verdict": "COMPATIBLE_BY_MAPPING", "primary": "RENAME_ONLY",
         "reasons": ["RENAME_ONLY"], "reader_ok": True, "writer_ok": True},
    ]}
    built = mapping.build([], [], diff)
    assert built["stats"]["conflicts"] == 1
    assert built["conflicts"][0]["reason"] == "MAPPING_CONFLICT"
    assert all(row["alias_sql"] is None for row in built["rows"])
    assert all(row["status"] == "conflict" for row in built["rows"])


def test_ref_account_is_balanced(run):
    ledger = run["ledger"]
    assert ledger["refs"]["refs_total"] == 17
    assert ledger["refs"]["reachable"] == 14
    assert ledger["refs"]["broken"] == 3
    assert ledger["refs"]["aliased"] == 2
    assert ledger["refs"]["balanced"] is True


def test_load_refs_rejects_bad_name(client):
    import pytest

    from app import errors

    with pytest.raises(errors.BadInput):
        map_service.load_refs("../etc/passwd")
