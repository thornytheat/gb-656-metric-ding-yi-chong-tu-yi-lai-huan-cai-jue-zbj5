"""建表语句解析：把 DDL 拆成列定义，保留行号与身份标记。

朴素实现的两个死法在这里被挡住：直接 split(",") 会被类型括号里的逗号切坏，
按行号当身份会在换行/重排后全部错位。
"""

from __future__ import annotations

import re

from app import config

CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?`?(?P<name>[A-Za-z_][\w.]*)`?\s*\(",
    re.IGNORECASE,
)
FID_RE = re.compile(r"fid\s*=\s*(?P<fid>[0-9]+)", re.IGNORECASE)
VERSION_RE = re.compile(r"v(?P<year>\d{4})[_\-]?(?P<month>\d{2})", re.IGNORECASE)
COLUMN_RE = re.compile(
    r"^(?P<name>`[^`]+`|\"[^\"]+\"|[A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5$]*)\s+"
    r"(?P<type>[A-Za-z][A-Za-z0-9_]*(?:\s+PRECISION)?"
    r"(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)\s*(?P<rest>.*)$"
)
ONE_LINE_TABLE_RE = re.compile(r"create\s+table\s+[^(]+\((?P<body>.*)\)\s*;?\s*$", re.IGNORECASE | re.S)


def top_level_commas(text: str) -> int:
    depth = 0
    count = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def clean_name(raw: str) -> str:
    return raw.strip().strip("`").strip('"').strip()


def required_from_rest(rest: str) -> bool | None:
    body = (rest or "").upper()
    if "NOT NULL" in body or "PRIMARY KEY" in body:
        return True
    if "NULL" in body:
        return False
    return None


def _split_code_and_comment(line: str) -> tuple[str, str]:
    if "--" in line:
        code, comment = line.split("--", 1)
        return code, comment
    return line, ""


def parse_ddl(lines: tuple[tuple[int, str], ...]) -> dict:
    version: str | None = None
    tables: list[str] = []
    columns: list[dict] = []
    issues: list[dict] = []
    current: str | None = None

    for lineno, raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if version is None:
            found = VERSION_RE.search(stripped)
            if found:
                version = f"v{found.group('year')}_{found.group('month')}"

        code_part, comment_part = _split_code_and_comment(stripped)
        code = code_part.strip()

        if not code:
            # 纯注释行：只可能承载版本号
            continue

        create = CREATE_RE.search(code)
        if create:
            current = create.group("name")
            tables.append(current)
            tail = code[create.end():].strip()
            if tail and not tail.startswith("("):
                # 同行开始给列，交给下面的行内处理
                code = tail
            else:
                continue

        if current is None:
            continue
        if code.startswith(")"):
            current = None
            continue

        fid_match = FID_RE.search(comment_part) or FID_RE.search(code)
        fid = fid_match.group("fid") if fid_match else None
        body = code
        if body.endswith(","):
            body = body[:-1].rstrip()

        if top_level_commas(body) > 0:
            issues.append(
                {
                    "line": lineno,
                    "table": current,
                    "column": _guess_name(body),
                    "reason": "LINE_MALFORMED",
                    "blocking": True,
                    "detail": "顶层多余分隔符",
                    "raw": stripped,
                }
            )
            continue

        match = COLUMN_RE.match(body)
        if not match:
            issues.append(
                {
                    "line": lineno,
                    "table": current,
                    "column": _guess_name(body),
                    "reason": "LINE_MALFORMED",
                    "blocking": True,
                    "detail": "无法识别列名与类型",
                    "raw": stripped,
                }
            )
            continue

        rest = match.group("rest") or ""
        columns.append(
            {
                "table": current,
                "name": clean_name(match.group("name")),
                "type_raw": " ".join(match.group("type").split()),
                "required": required_from_rest(rest),
                "field_id": str(fid) if fid else None,
                "line": lineno,
            }
        )

    if not tables:
        issues.append(
            {
                "line": 0,
                "table": "",
                "column": "",
                "reason": "TABLE_UNPARSED",
                "blocking": True,
                "detail": "没有找到建表语句",
                "raw": "",
            }
        )
    return {"version": version, "tables": tables, "columns": columns, "issues": issues}


def _guess_name(body: str) -> str:
    token = body.strip().split(" ", 1)[0]
    return clean_name(token)[:40]


def detect_required_token(raw_value) -> bool | None:
    """把各种必填记号归一到三态；未知记号返回 None。"""
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return None
    token = str(raw_value).strip().lower()
    if token in config.REQUIRED_TRUE:
        return True
    if token in config.REQUIRED_FALSE:
        return False
    if token in config.NULL_SENTINELS:
        return False
    return None
