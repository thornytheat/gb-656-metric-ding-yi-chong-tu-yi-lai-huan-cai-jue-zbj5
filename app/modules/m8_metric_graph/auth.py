"""指标依赖裁决的授权与视图裁剪。

两条边界必须同时成立：
1. 未授权只能看到匿名链路 —— 指标名、公式、来源、内部键名都不能出现在响应里；
2. 自称审计角色但凭据不对，直接拒绝，不能悄悄降级成匿名视图。
"""

from __future__ import annotations

import hashlib
import hmac

from app import config, errors

VIEWER = config.VIEWER_ROLE
AUDITOR = config.AUDIT_ROLE

ROLE_ALIASES = {
    "": VIEWER,
    "anonymous": VIEWER,
    "anon": VIEWER,
    "viewer": VIEWER,
    "auditor": AUDITOR,
    "audit": AUDITOR,
}

VIEWER_SCOPE = {
    "role": VIEWER,
    "visible": ["chain_shape", "depths", "reason_codes", "counts", "input_version",
                "version_label", "status"],
    "redacted": ["metric_name", "expression", "source", "signature", "audit_log"],
}

AUDITOR_SCOPE = {
    "role": AUDITOR,
    "visible": ["chain_shape", "depths", "reason_codes", "counts", "input_version",
                "version_label", "status", "metric_name", "expression", "source",
                "signature", "audit_log"],
    "redacted": [],
}


def resolve_role(role_param: str | None, token_param: str | None) -> dict:
    """凭据优先于自称角色。返回 {"role", "token_fp"}，越权直接抛 403。"""
    presented = (token_param or "").strip()
    claimed = (role_param or "").strip().lower()
    if claimed not in ROLE_ALIASES:
        raise errors.BadInput("未知角色", detail={"role": role_param})

    if presented:
        if hmac.compare_digest(presented, config.audit_token()):
            return {"role": AUDITOR, "token_fp": config.token_fingerprint(presented)}
        raise errors.Forbidden(
            "审计凭据不正确，拒绝越权查看",
            detail={"role": claimed or AUDITOR, "reason": "AUDIT_DENIED",
                    "required_scope": AUDITOR},
        )

    if ROLE_ALIASES[claimed] == AUDITOR:
        raise errors.Forbidden(
            "审计视图需要审计凭据",
            detail={"role": AUDITOR, "reason": "AUDIT_DENIED",
                    "required_scope": AUDITOR},
        )
    return {"role": VIEWER, "token_fp": None}


def scope_of(role: str) -> dict:
    return dict(AUDITOR_SCOPE if role == AUDITOR else VIEWER_SCOPE)


def anon_id(name: str) -> str:
    """稳定匿名编号：同一指标在所有版本里都得到同一个值，便于对照链路。"""
    digest = hashlib.sha256((name or "").strip().lower().encode("utf-8")).hexdigest()
    return "m-" + digest[:10]


def _anon_map(result: dict) -> dict[str, str]:
    names: set[str] = set()
    for row in result.get("metrics") or []:
        names.add(row["name"])
    for row in result.get("removed") or []:
        names.add(row["name"])
    for item in result.get("conflicts") or []:
        names.add(item["name"])
    for item in result.get("duplicates") or []:
        names.add(item["name"])
    for item in result.get("broken_deps") or []:
        names.add(item["metric"])
        names.add(item["dependency"])
    for cycle in result.get("cycles") or []:
        names.update(cycle.get("nodes") or [])
    names.update(result.get("self_loops") or [])
    for chain in result.get("chains") or []:
        names.add(chain["anchor"])
        for node in chain.get("nodes") or []:
            names.add(node["name"])
    return {name.lower(): anon_id(name) for name in names}


def _anon(mapping: dict[str, str], name: str | None) -> str | None:
    if name is None:
        return None
    return mapping.get(str(name).lower(), anon_id(str(name)))


def _anonymize(result: dict) -> dict:
    """白名单式重建，绝不整体拷贝：内部键名也属于要保护的信息。"""
    mapping = _anon_map(result)
    out = {
        "lineage_id": result.get("lineage_id"),
        "input_version": result.get("input_version"),
        "input_sha256": result.get("input_sha256"),
        "definitions_version": result.get("definitions_version"),
        "parent_run_id": result.get("parent_run_id"),
        "counts": dict(result.get("counts") or {}),
        "overall": result.get("overall"),
        "verdict_codes": list(result.get("verdict_codes") or []),
        "changed_counts": {
            "changed": len((result.get("changed") or {}).get("changed") or []),
            "added": len((result.get("changed") or {}).get("added") or []),
            "removed": len((result.get("changed") or {}).get("removed") or []),
        },
    }
    out["metrics"] = [
        {
            "anon": _anon(mapping, row["name"]),
            "version": row["version"],
            "depends_on": [_anon(mapping, dep) for dep in row.get("depends_on") or []],
            "status": row["status"],
            "reasons": list(row.get("reasons") or []),
        }
        for row in result.get("metrics") or []
    ]
    out["removed"] = [
        {
            "anon": _anon(mapping, row["name"]),
            "status": row["status"],
            "reasons": list(row.get("reasons") or []),
        }
        for row in result.get("removed") or []
    ]
    out["conflicts"] = [
        {
            "anon": _anon(mapping, item["name"]),
            "version": item["version"],
            "reason": item["reason"],
            "reason_code": item["reason_code"],
            "definition_count": item["definition_count"],
            "signature_count": len(item.get("signatures") or []),
        }
        for item in result.get("conflicts") or []
    ]
    out["duplicates"] = [
        {
            "anon": _anon(mapping, item["name"]),
            "version": item["version"],
            "definition_count": item["definition_count"],
        }
        for item in result.get("duplicates") or []
    ]
    out["cycles"] = [
        {
            "nodes": [_anon(mapping, node) for node in cycle.get("nodes") or []],
            "length": cycle.get("length"),
            "reason": cycle.get("reason"),
        }
        for cycle in result.get("cycles") or []
    ]
    out["self_loops"] = [_anon(mapping, node) for node in result.get("self_loops") or []]
    out["broken_deps"] = [
        {
            "metric": _anon(mapping, item["metric"]),
            "dependency": _anon(mapping, item["dependency"]),
            "reason": item["reason"],
        }
        for item in result.get("broken_deps") or []
    ]
    out["chains"] = [
        {
            "anchor": _anon(mapping, chain["anchor"]),
            "reasons": list(chain.get("reasons") or []),
            "nodes": [
                {
                    "anon": _anon(mapping, node["name"]),
                    "depth": node["depth"],
                    "role": node["role"],
                    "via": _anon(mapping, node.get("via")),
                }
                for node in chain.get("nodes") or []
            ],
            "edges": [[_anon(mapping, left), _anon(mapping, right)]
                      for left, right in chain.get("edges") or []],
            "affected_count": chain.get("affected_count"),
        }
        for chain in result.get("chains") or []
    ]
    out["redacted"] = True
    out["redaction_note"] = "未授权视图只给匿名链路，指标名与公式不下发"
    return out


def view(result: dict, role: str) -> dict:
    payload = _anonymize(result) if role == VIEWER else _copy_for_audit(result)
    payload["scope"] = scope_of(role)
    payload["role"] = role
    return payload


def _copy_for_audit(result: dict) -> dict:
    out = dict(result)
    out["redacted"] = False
    out["redaction_note"] = None
    return out


def audit_event(run_id: str | None, action: str, role: str, outcome: str,
                token_fp: str | None, detail: str = "") -> None:
    from app import store

    store.append_audit(run_id, action, outcome, role, token_fp, detail)
