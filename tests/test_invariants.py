"""不变量：守恒、幂等、单调、可复算。"""

from __future__ import annotations

from app import store
from app.modules.m1_intake import service as intake
from app.modules.m6_batches import service as batch_service
from app.modules.m7_migration import service as migration
from tests.fixtures import expected


def test_field_account_is_conserved(run):
    ledger = run["ledger"]
    fields = ledger["fields"]
    buckets = fields["buckets"]
    old_side = sum(buckets[key] for key in
                   ("unchanged", "renamed", "retyped", "required", "conflict", "deleted"))
    new_side = sum(buckets[key] for key in
                   ("unchanged", "renamed", "retyped", "required", "conflict", "added"))
    assert old_side == fields["old_total"] == expected.OLD_TOTAL
    assert new_side == fields["new_total"] == expected.NEW_TOTAL
    assert fields["old_gap"] == fields["new_gap"] == 0


def test_ref_account_is_conserved(run):
    refs = run["ledger"]["refs"]
    assert refs["reachable"] + refs["broken"] == refs["refs_total"]
    assert refs["refs_total"] == sum(len(item["refs"]) for item in run["refs"]["consumers"])
    assert refs["gap"] == 0
    assert refs["direct"] + refs["aliased"] >= refs["reachable"]


def test_intake_account_is_conserved(client):
    for name in ("schema_v2026_03.sql", "schema_v2026_06.sql",
                 "schema_v2026_09.sql", "metadata_extra.json"):
        stats = intake.ingest(name)["stats"]
        assert stats["accepted"] + stats["blocked"] == stats["column_lines"]
        assert stats["parsed"] + stats["parse_failures"] == stats["column_lines"]


def test_rerun_keeps_rows_and_totals(client):
    first = store.latest_run()
    before = migration.fields_csv_text(first)
    again = batch_service.execute()
    after = migration.fields_csv_text(again)
    assert again["run_id"] == first["run_id"]
    assert before == after
    assert again["ledger"]["counts"] == first["ledger"]["counts"]
    assert again["buckets"] == first["buckets"]
    assert store.count_runs() == 1


def test_export_text_is_deterministic(client):
    run = store.latest_run()
    assert migration.spec_markdown(run) == migration.spec_markdown(run)
    assert migration.manifest_json_text(run) == migration.manifest_json_text(run)
    assert migration.refs_csv_text(run) == migration.refs_csv_text(run)
