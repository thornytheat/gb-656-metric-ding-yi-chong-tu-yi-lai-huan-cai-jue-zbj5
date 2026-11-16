"""类型写法归一：原始 DDL 类型 → 基础名 + 精度/标度/长度。

只做归一，不做兼容判定；判定在 lattice.py。
"""

from __future__ import annotations

import re

from app import config

TYPE_ARG_RE = re.compile(r"^\s*(?P<head>[A-Za-z][A-Za-z0-9_ ]*?)\s*"
                         r"(?:\(\s*(?P<args>[^)]*)\s*\))?\s*$")
INT_ARG_RE = re.compile(r"^\d+$")


def _super_of(base: str) -> str:
    if base in config.NUMERIC_BASES:
        return "numeric"
    if base in config.TEXT_BASES:
        return "text"
    if base in config.TEMPORAL_BASES:
        return "temporal"
    if base in config.BOOL_BASES:
        return "bool"
    return "unknown"


def _parse_args(raw: str | None) -> list[int] | None:
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",")]
    out: list[int] = []
    for part in parts:
        if not INT_ARG_RE.match(part):
            return None
        out.append(int(part))
    return out


def parse_type(type_raw: str) -> dict:
    """返回统一结构；未知写法落到 unknown 家族，不做猜测。"""
    raw = (type_raw or "").strip()
    match = TYPE_ARG_RE.match(raw)
    if not match:
        return {"raw": raw, "super": "unknown", "base": raw.lower() or "unknown",
                "precision": None, "scale": None, "length": None, "known": False}
    base = config.base_of(match.group("head"))
    args = _parse_args(match.group("args"))
    spec = {"raw": raw, "super": _super_of(base), "base": base,
            "precision": None, "scale": None, "length": None,
            "known": base in (config.NUMERIC_BASES | config.TEXT_BASES
                              | config.TEMPORAL_BASES | config.BOOL_BASES)}

    if args is None:
        spec["known"] = False
        return spec

    if base == "decimal":
        precision = args[0] if len(args) >= 1 else 38
        scale = args[1] if len(args) >= 2 else 0
        if precision < 1 or scale < 0 or scale > precision:
            spec["known"] = False
            return spec
        spec["precision"] = precision
        spec["scale"] = scale
    elif base in ("varchar", "char"):
        if args:
            length = args[0]
            if length < 1:
                spec["known"] = False
                return spec
            spec["length"] = length
        else:
            spec["length"] = None
    return spec


def type_text(spec: dict) -> str:
    """把归一后的类型还原成规格单上的写法。"""
    base = spec["base"]
    if base == "decimal" and spec["precision"] is not None:
        return f"decimal({spec['precision']},{spec['scale']})"
    if base in ("varchar", "char") and spec["length"] is not None:
        return f"{base}({spec['length']})"
    return base
