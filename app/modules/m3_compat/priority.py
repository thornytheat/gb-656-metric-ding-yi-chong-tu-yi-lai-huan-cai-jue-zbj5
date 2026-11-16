"""判定优先级：多条规则同时命中时，结论只认优先级，不认书写顺序。"""

from __future__ import annotations

from app import reasons

# 数值越小越优先；同一对象命中多条时取最小的那个作为主原因
PRIORITY: dict[str, int] = {
    "ID_REUSED": 5,
    "RENAME_AND_RETYPE": 10,
    "MAPPING_CONFLICT": 15,
    "FIELD_DELETED_REFERENCED": 18,
    "NAME_DUP_DIFF_ID": 20,
    "TYPE_CROSS_FAMILY": 30,
    "TYPE_UNKNOWN": 35,
    "TYPE_NARROW": 40,
    "REQUIRED_TIGHTENED": 50,
    "ALIAS_CYCLE": 55,
    "TYPE_WIDEN": 70,
    "RENAME_CASE_ONLY": 72,
    "RENAME_ONLY": 75,
    "REQUIRED_LOOSENED": 80,
    "FIELD_DELETED": 85,
    "FIELD_ADDED": 90,
    "TYPE_SAME": 99,
}

FALLBACK = 500


def rank(code: str) -> int:
    return PRIORITY.get(code, FALLBACK)


def primary(codes: list[str]) -> str:
    """按 (优先级, 码字典序) 取主原因，保证重排输入得到同一结论。"""
    if not codes:
        return "COMPATIBLE"
    return sorted(set(codes), key=lambda code: (rank(code), code))[0]


def ordered(codes: list[str]) -> list[str]:
    return sorted(set(codes), key=lambda code: (rank(code), code))


def severity_of(codes: list[str]) -> str:
    level = 0
    for code in codes:
        level = max(level, reasons.SEVERITY_ORDER.get(reasons.severity(code), 0))
    return {0: "info", 1: "warn", 2: "break"}[level]
