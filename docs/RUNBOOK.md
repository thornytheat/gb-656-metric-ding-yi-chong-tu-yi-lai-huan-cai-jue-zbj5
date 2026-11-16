# 运行手册 metricgraph

## 1 启动

```bash
docker compose up -d --build
curl -s localhost:18215/api/health
```

宿主端口 `18215`，容器内 `8000`。启动时如果库是空的，服务会自动把 `sample_data/` 里的样例跑一遍，页面打开即有数据。

## 2 截图与验收命令

```bash
docker compose config --quiet
docker compose exec -T app python -m pytest -q
```

## 3 主流程

1. 打开 `http://localhost:18215/`，看规格单的字段清单与采信诊断。
2. `/types` 比较两个类型写法，看读者 / 写者两向结论。
3. `/mapping` 看旧名到新名、别名 SQL、冲突项。
4. `/impact` 看受影响节点、环、断链、单边登记。
5. `/batches` 点「跑一次」，再点「陈旧提交」验证 409。
6. `/metrics` 看指标依赖裁决：默认匿名链路，填审计凭据后看指标名与公式。
7. `/spec` 导出迁移单与三份账目 CSV。

## 4 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

命名卷 `metricgraph-data` 挂着 `/data/metricgraph.sqlite`。删卷即清空批次、快照与隔离区。

## 5 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 服务与库状态、当前批次号 |
| POST | `/api/schemas/import` | 采信样例文件，返回快照摘要与诊断 |
| GET | `/api/schemas/quarantine` | 隔离区 JSON |
| GET | `/api/schemas/quarantine.csv` | 隔离区 CSV |
| GET | `/api/types` | 类型格与别名表 |
| POST | `/api/types/compare` | 两个类型写法比较 |
| POST | `/api/compat` | 一对版本的两向判定 |
| GET | `/api/mapping` | 映射表、别名、冲突 |
| GET | `/api/impact` | 影响面、环、断链 |
| POST | `/api/runs` | 跑批次，可带基线版本号与生效区间 |
| GET | `/api/batches` | 批次历史 |
| GET | `/api/reconcile` | 四账对账 |
| GET | `/api/export/spec.md` | 迁移单 Markdown |
| GET | `/api/export/fields.csv` | 字段账 CSV |
| GET | `/api/export/refs.csv` | 引用账 CSV |
| GET | `/api/export/ledger.csv` | 账目 CSV |
| GET | `/api/export/manifest.json` | 运行清单 |
| POST | `/api/metrics/verdicts` | 提交指标定义，生成或复用裁决 |
| GET | `/api/metrics/verdicts` | 裁决历史（可按 `lineage_id` 过滤） |
| GET | `/api/metrics/verdicts/{run_id}` | 单条裁决；未授权只返回匿名链路 |
| POST | `/api/metrics/verdicts/{run_id}/resume` | 中断的裁决从检查点续跑 |
| GET | `/api/metrics/verdicts/{run_id}/audit` | 审计日志（需要审计凭据） |
| GET | `/api/export/chains.csv` | 链路 CSV，与所选版本一致 |
| GET | `/api/export/metrics.csv` | 指标 CSV（授权后含指标名与公式） |
| GET | `/api/export/metrics.conflicts.csv` | 冲突清单 CSV（需要审计凭据） |
| GET | `/api/export/metrics.md` | 裁决摘要 Markdown（需要审计凭据） |
| GET | `/api/export/metrics-manifest.json` | 裁决运行清单 |

授权：请求头 `X-Audit-Token` 或查询参数 `audit_token`，服务端比对
`METRICGRAPH_AUDIT_TOKEN`（默认 `audit-local`，仅本机演示）。凭据不对返回
`403 AUDIT_DENIED`，不会降级成匿名视图。

## 6 失败处置

| 现象 | 原因 | 处置 |
|---|---|---|
| 409 `VERSION_STALE` | 提交带的基线序号落后 | 去掉 `base_version_seq` 或填当前序号 |
| 409 `VERSION_STALE`（`METRIC_STALE_SUBMIT`） | 输入版本落后于该谱系当前版本 | 换更新的 `input_version`，旧版裁决保留只读 |
| 409 `CONFLICT`（`INPUT_VERSION_CONFLICT`） | 同一输入版本换了定义内容 | 升 `input_version` 再提交 |
| 409 `VERDICT_PENDING` | 裁决尚未写完 | 同输入重试或调用 `resume` 续跑 |
| 403 `AUDIT_DENIED` | 无凭据却要审计视图，或凭据不对 | 提供正确 `audit_token`；只需链路时去掉角色 |
| 400 `WINDOW_INVERTED` | 生效区间起点不早于终点 | 检查 `effective_from` / `effective_to` |
| 400 `BAD_INPUT` | 文件名、参数或 JSON 结构不对 | 看返回的 `detail` |
| 404 `NOT_FOUND` | 还没跑过批次 | 先在 `/batches` 点「跑一次」 |

## 7 端口与并发

本服务固定 18215。同时起多个栈时改 `docker-compose.yml` 的端口映射与 `metricgraph-data` 卷名，避免与本机其他项目抢端口、串数据。
