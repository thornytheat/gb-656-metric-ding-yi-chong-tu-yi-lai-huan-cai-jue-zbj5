"""值域与刻度：用 Decimal 算「新类型能否无损容纳旧类型的全部取值」。

三个维度一起比，缺一个就会得出错误结论：
取值范围（lo/hi）、刻度（quantum）、可精确表示的最大整数（exact）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app import config


@dataclass(frozen=True)
class ValueRange:
    lo: Decimal
    hi: Decimal
    quantum: Decimal
    exact: Decimal

    def as_dict(self) -> dict:
        return {
            "lo": str(self.lo),
            "hi": str(self.hi),
            "quantum": str(self.quantum),
            "exact": str(self.exact),
        }


FIXED: dict[str, ValueRange] = {
    "tinyint": ValueRange(Decimal("-128"), Decimal("127"), Decimal("1"), Decimal("128")),
    "smallint": ValueRange(Decimal("-32768"), Decimal("32767"), Decimal("1"),
                           Decimal("32768")),
    "int": ValueRange(Decimal("-2147483648"), Decimal("2147483647"), Decimal("1"),
                      Decimal("2147483648")),
    "bigint": ValueRange(Decimal("-9223372036854775808"), Decimal("9223372036854775807"),
                         Decimal("1"), Decimal("9223372036854775808")),
    "float": ValueRange(Decimal("-3.4028235E+38"), Decimal("3.4028235E+38"),
                        Decimal("1E-45"), Decimal("16777216")),
    "double": ValueRange(Decimal("-1.7976931348623157E+308"), Decimal("1.7976931348623157E+308"),
                         Decimal("1E-323"), Decimal("9007199254740992")),
}


def value_range(spec: dict) -> ValueRange | None:
    if spec.get("super") != "numeric":
        return None
    base = spec.get("base")
    if base == "decimal":
        precision = spec.get("precision") or 38
        scale = spec.get("scale") or 0
        step = Decimal(1).scaleb(-scale)
        hi = (Decimal(10) ** (precision - scale)) - step
        return ValueRange(-hi, hi, step, hi)
    return FIXED.get(base)


def capacity(old_spec: dict, new_spec: dict) -> dict:
    """返回是否无损容纳，以及收窄时的细分原因。"""
    old_range = value_range(old_spec)
    new_range = value_range(new_spec)
    if old_range is None or new_range is None:
        return {"fits": False, "detail": "range_narrow",
                "evidence": {"note": "非数值类型"}}

    fits = (
        new_range.lo <= old_range.lo
        and new_range.hi >= old_range.hi
        and new_range.quantum <= old_range.quantum
        and new_range.exact >= old_range.exact
    )
    evidence = {
        "old": old_range.as_dict(),
        "new": new_range.as_dict(),
        "lo_ok": new_range.lo <= old_range.lo,
        "hi_ok": new_range.hi >= old_range.hi,
        "quantum_ok": new_range.quantum <= old_range.quantum,
        "exact_ok": new_range.exact >= old_range.exact,
    }
    if fits:
        return {"fits": True, "detail": None, "evidence": evidence}
    return {"fits": False, "detail": _narrow_detail(old_spec, new_spec, old_range, new_range),
            "evidence": evidence}


def _narrow_detail(old_spec: dict, new_spec: dict, old_range: ValueRange,
                   new_range: ValueRange) -> str:
    if old_spec["base"] == "decimal" and new_spec["base"] == "decimal":
        old_int = old_spec["precision"] - old_spec["scale"]
        new_int = new_spec["precision"] - new_spec["scale"]
        if new_int < old_int:
            return "int_digits_drop"
        if new_spec["scale"] < old_spec["scale"]:
            return "scale_drop"
    if new_range.hi < old_range.hi or new_range.lo > old_range.lo:
        return "range_narrow"
    if new_range.quantum > old_range.quantum:
        return "precision_loss"
    return "exactness_loss"


def grid_rows() -> list[dict]:
    """类型格展示行：写死的常量表，页面与导出共用。"""
    rows: list[dict] = []
    for base in ("tinyint", "smallint", "int", "bigint", "float", "double"):
        item = FIXED[base]
        rows.append({
            "label": base,
            "super": "numeric",
            "lo": str(item.lo),
            "hi": str(item.hi),
            "quantum": str(item.quantum),
            "exact": str(item.exact),
        })
    for precision, scale in ((18, 2), (20, 4), (18, 4)):
        spec = {"super": "numeric", "base": "decimal", "precision": precision, "scale": scale}
        item = value_range(spec)
        rows.append({
            "label": f"decimal({precision},{scale})",
            "super": "numeric",
            "lo": str(item.lo),
            "hi": str(item.hi),
            "quantum": str(item.quantum),
            "exact": str(item.exact),
        })
    for label in ("varchar(64)", "varchar(96)", "string"):
        length = None if label == "string" else int(label.split("(")[1].rstrip(")"))
        rows.append({
            "label": label,
            "super": "text",
            "lo": "—",
            "hi": "—" if length is None else str(length),
            "quantum": "字符",
            "exact": "—",
        })
    for label, rank in (("date", 0), ("timestamp", 1)):
        rows.append({
            "label": label,
            "super": "temporal",
            "lo": "—",
            "hi": str(rank),
            "quantum": "日" if rank == 0 else "秒",
            "exact": "—",
        })
    rows.append({"label": "bool", "super": "bool", "lo": "0", "hi": "1",
                 "quantum": "1", "exact": "—"})
    return rows


TEMPORAL_RANK = {"date": 0, "timestamp": 1}


def temporal_rank(base: str) -> int:
    return TEMPORAL_RANK.get(base, -1)


def text_length(spec: dict) -> int | None:
    """None 表示不限长（string/text）。"""
    if spec["base"] in ("string", "text"):
        return None
    return spec.get("length")


def alias_count() -> int:
    return len(config.TYPE_ALIASES)
