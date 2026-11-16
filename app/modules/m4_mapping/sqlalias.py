"""从下游 SQL 里抽出字段引用。

只认 `别名.列名` 这种显式写法：无别名裸列名一律不算，宁可漏报也不猜。
真实取数脚本里出现子查询、CTE、函数调用的情况在文档里写明不覆盖。
"""

from __future__ import annotations

import re

from app import config

FROM_RE = re.compile(
    r"\b(?:from|join)\s+(?P<table>[A-Za-z_][\w.]*)"
    r"(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_]\w*))?",
    re.IGNORECASE,
)
REF_RE = re.compile(r"\b(?P<alias>[A-Za-z_]\w*)\.(?P<column>[A-Za-z_]\w*)\b")
STOP_WORDS = {
    "where", "on", "group", "order", "having", "limit", "union", "left", "right",
    "inner", "outer", "cross", "join", "select", "from", "as", "and", "or", "using",
    "full", "lateral", "window", "qualify", "fetch", "offset", "with", "values",
}


def strip_noise(sql: str) -> str:
    text = config.SQL_BLOCK_COMMENT_RE.sub(" ", sql or "")
    text = config.SQL_COMMENT_RE.sub(" ", text)
    text = config.SQL_STRING_RE.sub("''", text)
    return " ".join(text.split())


def extract_refs(sql: str) -> dict:
    text = strip_noise(sql)
    aliases: dict[str, str] = {}
    for match in FROM_RE.finditer(text):
        table = match.group("table")
        alias = (match.group("alias") or "").lower()
        if not alias or alias in STOP_WORDS:
            alias = table.split(".")[-1].lower()
        aliases[alias] = table

    refs: list[str] = []
    unresolved: list[str] = []
    for match in REF_RE.finditer(text):
        prefix = match.group("alias")
        column = match.group("column")
        if prefix.lower() in STOP_WORDS:
            continue
        table = aliases.get(prefix.lower())
        if table is None:
            unresolved.append(f"{prefix}.{column}")
            continue
        refs.append(f"{table}.{column}")
    return {
        "sql": sql,
        "aliases": aliases,
        "refs": sorted(set(refs)),
        "unresolved": sorted(set(unresolved)),
    }


def split_ref(ref: str) -> tuple[str, str]:
    table, _, column = ref.rpartition(".")
    if not table:
        return "", column
    return table, column
