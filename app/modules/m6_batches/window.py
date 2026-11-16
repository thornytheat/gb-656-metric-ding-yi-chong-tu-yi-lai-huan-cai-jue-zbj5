"""生效区间：一律归一成 UTC 再比较，区间按半开处理。"""

from __future__ import annotations

from datetime import datetime

from app import config, errors


def parse_instant(value: str, field: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise errors.BadInput("时间不能为空", detail={"field": field})
    text = raw.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise errors.BadInput("时间格式无法识别", detail={"field": field, "value": raw})
    if moment.tzinfo is None:
        # 朴素时间按 UTC 解释，避免同一列出现两个时间桶
        moment = moment.replace(tzinfo=config.UTC)
    return moment.astimezone(config.UTC)


def version_start(label: str) -> datetime:
    match = config.VERSION_LABEL_RE.search(label or "")
    if not match:
        raise errors.BadInput("版本标签里没有年月", detail={"value": label})
    year = int(match.group("year"))
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        raise errors.BadInput("版本标签月份非法", detail={"value": label})
    return datetime(year, month, 1, tzinfo=config.UTC)


def normalize(from_value: str | None, to_value: str | None, label: str) -> dict:
    start = parse_instant(from_value, "effective_from") if from_value else version_start(label)
    end = parse_instant(to_value, "effective_to") if to_value else None
    if end is not None and end <= start:
        raise errors.BadInput(
            "生效区间起点不早于终点",
            detail={"reason": "WINDOW_INVERTED", "start": start.isoformat(),
                    "end": end.isoformat()},
        )
    return {
        "from": start.isoformat(),
        "to": end.isoformat() if end else None,
        "from_dt": start,
        "to_dt": end,
        "semantics": config.WINDOW_SEMANTICS,
    }


def overlaps(left: dict, right: dict) -> bool:
    """半开区间相交：左起 < 右终 且 右起 < 左终；None 视为无穷远。"""
    left_from = left.get("from_dt") or parse_instant(left["from"], "from")
    left_to = left.get("to_dt") or (parse_instant(left["to"], "to") if left.get("to") else None)
    right_from = right.get("from_dt") or parse_instant(right["from"], "from")
    right_to = right.get("to_dt") or (parse_instant(right["to"], "to") if right.get("to") else None)

    if left_to is not None and right_from >= left_to:
        return False
    if right_to is not None and left_from >= right_to:
        return False
    return True


def find_overlaps(window: dict, existing: list[dict]) -> list[dict]:
    hits: list[dict] = []
    for item in existing:
        other = {"from": item["effective_from"], "to": item.get("effective_to")}
        if overlaps(window, other):
            hits.append({
                "run_id": item["run_id"],
                "from": item["effective_from"],
                "to": item.get("effective_to"),
                "reason": "VERSION_OVERLAP",
            })
    return hits


def describe(window: dict) -> str:
    end = window.get("to") or "∞"
    return f"[{window['from']}, {end})"
