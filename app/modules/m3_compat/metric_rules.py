"""指标表达式签名与同名定义冲突裁决。"""

from __future__ import annotations

import hashlib
import re


def canonical(expression: str, aliases: dict[str, str] | None = None) -> str:
    value = expression.lower()
    for old, new in sorted((aliases or {}).items(), key=lambda item: -len(item[0])):
        value = re.sub(rf"\b{re.escape(old.lower())}\b", new.lower(), value)
    value = re.sub(r"\s+", "", value)
    if "+" in value and "(" not in value:
        value = "+".join(sorted(part for part in value.split("+") if part))
    return value


def signature(expression: str, aliases: dict[str, str] | None = None) -> str:
    return hashlib.sha256(canonical(expression, aliases).encode()).hexdigest()[:16]


def resolve(definitions: list[dict], aliases: dict[str, str] | None = None) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for line, item in enumerate(definitions, 1):
        row = dict(item, line=line)
        groups.setdefault((str(item.get("name", "")).lower(), str(item.get("version", ""))), []).append(row)
    chosen, conflicts = [], []
    for (name, version), rows in sorted(groups.items()):
        ranked = sorted(rows, key=lambda row: (-int(row.get("priority", 0)), str(row.get("source", ""))))
        signatures = {signature(str(row.get("expression", "")), aliases) for row in rows}
        if not name or not version or any(not row.get("expression") for row in rows):
            conflicts.append({"name": name or "?", "version": version or "?", "reason": "DEFINITION_EMPTY"})
        elif len(signatures) > 1:
            conflicts.append({"name": name, "version": version, "reason": "DEFINITION_CONFLICT"})
        else:
            chosen.append({"name": name, "version": version, "source": ranked[0].get("source"), "signature": next(iter(signatures))})
    return {"chosen": chosen, "conflicts": conflicts, "input_count": len(definitions)}
