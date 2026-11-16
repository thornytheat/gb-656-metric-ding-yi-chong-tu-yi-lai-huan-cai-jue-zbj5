"""全局常量：编码链、类型别名、样例定位、显示时区。

只放跨模块共享的事实，不放业务判定；判定在各功能模块的规则文件里。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import errors

APP_NAME = "metricgraph"
APP_TITLE = "metricgraph 规格单"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "sample_data"
DEFAULT_DB = PROJECT_ROOT / "data" / "metricgraph.sqlite"

RULESET_VERSION = os.environ.get("METRICGRAPH_RULESET", "fm-2026.11.1")

# 采信层：编码回退链，首个能解码的胜出
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")

# 显示时区：存储一律 UTC，页面按 +08:00 显示
DISPLAY_TZ_NAME = "Asia/Shanghai"
DISPLAY_OFFSET = timedelta(hours=8)
UTC = timezone.utc

# 样例数据：默认一旧一新一对，外加下游登记
DEFAULT_OLD = "schema_v2026_06.sql"
DEFAULT_NEW = "schema_v2026_09.sql"
BASE_OLD = "schema_v2026_03.sql"
DEFAULT_REFS = "refs_downstream.json"
EXTRA_METADATA = "metadata_extra.json"

ALLOWED_SUFFIX = {".sql", ".json"}
SAMPLE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# 类型别名：原始写法 -> 基础类型名
TYPE_ALIASES = {
    "tinyint": "tinyint",
    "smallint": "smallint",
    "int": "int",
    "integer": "int",
    "int32": "int",
    "long": "bigint",
    "bigint": "bigint",
    "int64": "bigint",
    "float": "float",
    "real": "float",
    "float32": "float",
    "double": "double",
    "float64": "double",
    "double precision": "double",
    "decimal": "decimal",
    "numeric": "decimal",
    "number": "decimal",
    "varchar": "varchar",
    "varchar2": "varchar",
    "nvarchar": "varchar",
    "char": "char",
    "string": "string",
    "text": "text",
    "clob": "text",
    "date": "date",
    "timestamp": "timestamp",
    "datetime": "timestamp",
    "bool": "bool",
    "boolean": "bool",
}

NUMERIC_BASES = {"tinyint", "smallint", "int", "bigint", "float", "double", "decimal"}
TEXT_BASES = {"varchar", "char", "string", "text"}
TEMPORAL_BASES = {"date", "timestamp"}
BOOL_BASES = {"bool"}

# required 列的取值记号，三态靠这套常量区分
REQUIRED_TRUE = {"1", "y", "yes", "true", "t", "是", "必填", "非空", "not null"}
REQUIRED_FALSE = {"0", "n", "no", "false", "f", "否", "可空", "空", "null", "nullable"}

# 空值哨兵只在这里定义，别处不得复制
NULL_SENTINELS = {"", "-", "--", "n/a", "na", "null", "none", "nil", "未知", "待定"}

# 版本标签形如 v2026_09，用于推出生效起点
VERSION_LABEL_RE = re.compile(r"v(?P<year>\d{4})[_\-]?(?P<month>\d{2})", re.IGNORECASE)
SQL_COMMENT_RE = re.compile(r"--[^\n]*")
SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
SQL_STRING_RE = re.compile(r"'(?:[^']|'')*'")

# 半开区间：生效窗口 [start, end)
WINDOW_SEMANTICS = "[start, end)"

# 影响面最多展开层数，防止异常登记把页面拖死
MAX_IMPACT_DEPTH = 8

# ---------- 指标依赖裁决 ----------
# 指标批次绑定输入版本：一条批次键对应一组输入摘要，修订后是新键而不是覆盖旧键
METRIC_BATCH_PARAMS_LIMIT = 12

# 审计角色：未授权只给匿名链路；授权审计才可看指标名与公式
AUDIT_ROLE = "auditor"
VIEWER_ROLE = "viewer"

# 审计凭据可在请求头 X-Audit-Token 或查询参数 audit_token 里给
AUDIT_TOKEN_ENV = "METRICGRAPH_AUDIT_TOKEN"
DEFAULT_AUDIT_TOKEN = "audit-local"

# 裁决写入状态：pending 表示有输入版本已绑定但结果尚未发布
VERDICT_READY = "ready"
VERDICT_PENDING = "pending"

# 依赖环 / 断链 / 授权的判定出口只有这些，页面与导出共用
VERDICT_CODES = ("PASS", "WARN", "BLOCK")

# 指标依赖裁决的样例与谱系
METRIC_SAMPLE = "metrics_v2026_09.json"
METRIC_SAMPLE_LINEAGE = "published-metrics"


def resolve_sample(name: str) -> Path:
    """把样例文件名限定在 sample_data/ 内，拒绝穿越与绝对路径。"""
    raw = (name or "").strip()
    if not raw:
        raise errors.BadInput("未给出样例文件名", detail={"field": "file"})
    if "/" in raw or "\\" in raw or ".." in raw:
        raise errors.BadInput("样例名只接受文件名", detail={"file": name})
    if not SAMPLE_NAME_RE.match(raw):
        raise errors.BadInput("样例名含非法字符", detail={"file": name})
    path = (SAMPLE_DIR / raw).resolve()
    if SAMPLE_DIR.resolve() not in path.parents:
        raise errors.BadInput("样例名越出样例目录", detail={"file": name})
    if not path.is_file():
        raise errors.NotFound(f"样例不存在：{raw}", detail={"file": raw})
    if path.suffix.lower() not in ALLOWED_SUFFIX:
        raise errors.BadInput("只接受 .sql 与 .json 样例", detail={"file": raw})
    return path


def base_of(type_raw: str) -> str:
    """取类型的基础名，带参数时去掉括号部分。"""
    head = (type_raw or "").strip().split("(", 1)[0].strip().lower()
    head = re.sub(r"\s+", " ", head)
    return TYPE_ALIASES.get(head, head)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def audit_token() -> str:
    """审计凭据：环境变量优先，默认值只用于本机演示。"""
    return os.environ.get(AUDIT_TOKEN_ENV, DEFAULT_AUDIT_TOKEN).strip() or DEFAULT_AUDIT_TOKEN


def token_fingerprint(token: str) -> str:
    """凭据摘要进审计日志，原始凭据不落盘。"""
    import hashlib

    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:16]


def parse_params(raw: str | None) -> dict:
    """批次参数在库里存成 JSON 文本，读回时坏数据返回空对象而不是抛错。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def to_local_text(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.astimezone(timezone(DISPLAY_OFFSET)).strftime("%Y-%m-%d %H:%M:%S +08:00")
