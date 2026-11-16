"""M2 类型格：别名归一、值域/刻度/精确整数三件套、边界。"""

from __future__ import annotations

from decimal import Decimal

from app.modules.m2_types import coerce, lattice, range as value_range


def _compare(old: str, new: str) -> dict:
    return lattice.compare(coerce.parse_type(old), coerce.parse_type(new))


def test_type_aliases_normalized():
    assert coerce.parse_type("INTEGER")["base"] == "int"
    assert coerce.parse_type(" double precision ")["base"] == "double"
    assert coerce.parse_type("NUMERIC(12,3)")["precision"] == 12
    assert coerce.parse_type("NUMERIC(12,3)")["scale"] == 3
    assert coerce.type_text(coerce.parse_type("VARCHAR(64)")) == "varchar(64)"
    assert coerce.type_text(coerce.parse_type("string")) == "string"


def test_int_to_bigint_is_widen():
    verdict = _compare("int", "bigint")
    assert verdict["change"] == "widen"
    assert verdict["reason"] == "TYPE_WIDEN"
    assert verdict["detail"] is None


def test_bigint_to_double_loses_exactness():
    verdict = _compare("bigint", "double")
    assert verdict["change"] == "narrow"
    assert verdict["detail"] == "exactness_loss"
    assert verdict["evidence"]["exact_ok"] is False


def test_decimal_precision_and_scale_only_increase():
    widen = _compare("decimal(18,2)", "decimal(20,4)")
    assert widen["change"] == "widen"
    assert widen["evidence"]["exact_ok"] is True

    digits = _compare("decimal(18,2)", "decimal(18,4)")
    assert digits["change"] == "narrow"
    assert digits["detail"] == "int_digits_drop"

    scale = _compare("decimal(18,2)", "decimal(20,1)")
    assert scale["change"] == "narrow"
    assert scale["detail"] == "scale_drop"


def test_decimal_quantum_uses_decimal_math():
    spec = coerce.parse_type("decimal(18,2)")
    item = value_range.value_range(spec)
    assert item.quantum == Decimal("0.01")
    assert item.hi == Decimal("9999999999999999.99")
    assert item.exact == Decimal("9999999999999999.99")


def test_text_length_rules():
    assert _compare("varchar(64)", "varchar(96)")["change"] == "widen"
    narrow = _compare("varchar(64)", "varchar(32)")
    assert narrow["change"] == "narrow"
    assert narrow["detail"] == "length_narrow"
    assert _compare("varchar(64)", "string")["change"] == "widen"
    assert _compare("string", "varchar(64)")["change"] == "narrow"
    assert _compare("varchar(64)", "varchar(64)")["change"] == "same"


def test_temporal_rank_rules():
    assert _compare("date", "timestamp")["change"] == "widen"
    narrow = _compare("timestamp", "date")
    assert narrow["change"] == "narrow"
    assert narrow["detail"] == "temporal_narrow"
    assert _compare("date", "date")["change"] == "same"


def test_cross_family_and_unknown_are_breaking():
    cross = _compare("bigint", "varchar(32)")
    assert cross["change"] == "cross_family"
    assert lattice.is_breaking(cross["change"]) is True

    unknown = _compare("variant", "text")
    assert unknown["change"] == "unknown"
    assert unknown["reason"] == "TYPE_UNKNOWN"
    assert lattice.is_breaking(unknown["change"]) is True


def test_grid_rows_shape():
    rows = value_range.grid_rows()
    assert len(rows) == 15
    labels = [row["label"] for row in rows]
    assert "decimal(18,2)" in labels and "timestamp" in labels and "bool" in labels
    for row in rows:
        assert set(row) == {"label", "super", "lo", "hi", "quantum", "exact"}
