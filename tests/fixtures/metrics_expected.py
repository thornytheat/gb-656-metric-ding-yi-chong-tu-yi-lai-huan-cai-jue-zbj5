"""指标依赖样例的期望值。改样例必须同时改这里。"""

from __future__ import annotations

SAMPLE_VERSION = "v2026_09"
SAMPLE_LINEAGE = "published-metrics"

DEFINITIONS_TOTAL = 12
METRICS_TOTAL = 9

# 样例故意放了三类问题：同名冲突、依赖环、缺依赖
CONFLICTS = 1
DUPLICATES = 1
CYCLES = 1
CYCLE_NODES = ["cycle_a", "cycle_b"]
BROKEN_DEPS = 4

PASS = 1
WARN = 2
BLOCK = 6

CHAINS = 5
AFFECTED = 8
OVERALL = "BLOCK"

ANON_PREFIX = "m-"
