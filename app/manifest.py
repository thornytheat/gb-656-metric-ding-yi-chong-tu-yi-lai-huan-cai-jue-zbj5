"""运行清单：输入摘要、批次键、run_id 与导出用 manifest。

run_id 由批次键推导，所以同一输入重跑必然得到同一个 run_id，行数不会增长。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from app import config


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def input_digests(names: list[tuple[str, Path]]) -> dict[str, str]:
    """有序的 (角色, 路径) -> {角色: 摘要}，顺序固定便于复算。"""
    return {role: sha256_file(path) for role, path in names}


def batch_key(digests: dict[str, str], params: dict, ruleset: str) -> str:
    payload = json.dumps(
        {"digests": digests, "params": params, "ruleset": ruleset},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return sha256_bytes(payload.encode("utf-8"))


def run_id_for(key: str) -> str:
    return "fm-" + key[:12]


def build_manifest(run: dict, created_at: datetime) -> dict:
    return {
        "run_id": run["run_id"],
        "batch_key": run["batch_key"],
        "ruleset": run["ruleset"],
        "version_seq": run["version_seq"],
        "input_sha256": run["input_sha256"],
        "input_digests": run["digests"],
        "params": run["params"],
        "effective_from": run["effective_from"],
        "effective_to": run["effective_to"],
        "window_semantics": config.WINDOW_SEMANTICS,
        "created_at_utc": created_at.astimezone(config.UTC).isoformat(),
        "created_at_local": config.to_local_text(created_at),
        "counts": run["ledger"].get("counts", {}),
        "balanced": run["ledger"].get("balanced", {}),
    }
