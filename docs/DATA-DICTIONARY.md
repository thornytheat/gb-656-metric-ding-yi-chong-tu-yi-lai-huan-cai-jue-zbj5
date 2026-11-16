# 定义 metricgraph

本文件是字段含义与计算方式的唯一出处；页面只显示字段名、状态与数值。

## 1 字段身份

- `field_id`：字段身份，字符串，跨版本不变。改名不产生新身份。
- 无 `field_id` 时按 `sha256(表名|列名)` 取前 12 位派生，前缀 `auto:`，与行序无关。
- 同一身份在一个版本里出现两次：第二次进隔离区，原因码 `FIELD_ID_DUP`。
- 同表列名比较不分大小写；保留首次出现的列，重复列进隔离区。
- 身份是 2^53 以上的整数时一律按字符串保存与导出，禁止过 `float`。

## 2 类型格

一个类型归一成 `{族, 基础类型, 精度, 标度, 长度}`，族为 numeric / text / temporal / bool / unknown。

每个数值类型带四个 Decimal 常量：

| 常量 | 含义 |
|---|---|
| `lo` / `hi` | 可表示的取值范围 |
| `quantum` | 最小刻度，例如 decimal(18,2) 为 0.01 |
| `exact` | 能不丢精度表示的最大整数绝对值，double 为 2^53 |

从旧类型到新类型判为放宽（`widen`）当且仅当四件事同时成立：`new.lo <= old.lo`、`new.hi >= old.hi`、`new.quantum <= old.quantum`、`new.exact >= old.exact`。否则判收窄（`narrow`），并给出细分：

| 细分 | 触发条件 |
|---|---|
| `int_digits_drop` | decimal 的整数位（精度减标度）变少 |
| `scale_drop` | decimal 的小数位变少 |
| `range_narrow` | 取值范围变小 |
| `precision_loss` | 刻度变粗 |
| `exactness_loss` | 精确整数范围变小 |
| `length_narrow` | 字符长度上限变小 |
| `temporal_narrow` | 时间精度回退（timestamp 到 date） |

跨族（数值到字符等）判 `cross_family`；任一端不在类型格内判 `unknown`。三种都算破坏。

## 3 必填与两向结论

- `required=true → false`：`REQUIRED_LOOSENED`，读者与写者都通过。
- `required=false → true`：`REQUIRED_TIGHTENED`，写者不通过，读者通过。
- 读者向看名字能否落到同一身份、类型能否解释；写者向看类型是否容纳与是否新增约束。
- 结论三档：`COMPATIBLE`、`COMPATIBLE_BY_MAPPING`（需要建别名视图）、`BREAKING`。

## 4 时间与区间

- 存储一律 UTC，页面按 `+08:00` 显示。
- 生效区间是半开区间 `[start, end)`；`end` 为空表示不设终点。
- 重叠判定：`left.start < right.end` 且 `right.start < left.end`，缺端点按无穷远。
- 端点相邻（左区间终点等于右区间起点）不算重叠。

## 5 引用

- 下游登记给出 `sql`、`upstream`、`downstream`；SQL 里只抽 `别名.列名`。
- 解析必须过身份：先按 `(表, 列名)` 找到旧版本字段，再按 `field_id` 找新版本字段。
- 名字还在但身份换了的，判断链 `REF_BROKEN`，不允许悄悄绑到新列。
- 改名同时换类型的字段不生成别名，引用同样判断链。
- 上下游任一边缺登记即 `REF_ONE_SIDED`。

## 6 采信

- 编码回退链：`utf-8-sig` → `utf-8` → `gb18030`，先剥字节序标记再探测。
- 逻辑行按 `\n` 切分并去掉行尾 `\r`，行号从 1 开始，保留原始行号。
- 阻断行（列定义损坏、重复列、重复身份）不进判定，提示行（派生身份、未知类型、编码回退）照常参算。
- 空文件、仅表头、全空行都返回 `EMPTY_INPUT` 提示，不当作成功采信。

## 7 原因码

原因码唯一来源是 `app/reasons.py`，级别 `info / warn / break`。主原因按 `app/modules/m3_compat/priority.py` 的优先级表取，同优先级按码字典序，保证重排输入结论不变。

## 8 账目

- 旧版本字段数 = 未变 + 改名 + 换类型 + 改必填 + 冲突 + 删除
- 新版本字段数 = 未变 + 改名 + 换类型 + 改必填 + 冲突 + 新增
- 登记引用 = 可达 + 断链；可达 = 直接 + 经别名
- 列定义行 = 接受字段 + 阻断行

每个字段只进一个桶，所以四个等式成立是必要条件；差额不为 0 即判定漏项。

## 9 指标依赖裁决

指标依赖图服务排障人员，也留审计凭据；两条约束同时成立才有意义。

### 9.1 批次绑定输入版本

- 一条批次键 = `sha256(定义规范摘要 | 输入版本 | 谱系 | 关注项 | 参数 | 规则集)`。
  改定义、改输入版本、改参数都会得到新键，因此**修订生成新裁决，旧裁决整行保留**。
- `lineage_id` 是同一条指标线的谱系；`parent_run_id` 指向上一条已发布裁决，
  「相对上一版改了什么」按谱系比，不按提交先后比。
- 版本比较按数字段数值比：`v2026_10 > v2026_9`，`v2026_09` 与 `v2026_9` 视为同一版本。
- 同一输入重试命中批次键：`run_id` 不变、结果行数不增、结论不变（重试不新增结果）。

### 9.2 拒绝与续跑

| 场景 | 结果 |
|---|---|
| 输入版本落后于该谱系当前版本 | `409 VERSION_STALE`，`detail.reason=METRIC_STALE_SUBMIT` |
| 同一输入版本、定义内容不同 | `409 CONFLICT`，`detail.reason=INPUT_VERSION_CONFLICT` |
| 提交带陈旧 `base_version_seq` | `409 VERSION_STALE` |
| 裁决写入中断（`status=pending`） | 同输入重试或 `POST /api/metrics/verdicts/{run_id}/resume` 从检查点续跑，`run_id` 与结果行数都不变 |
| 读取未完成的裁决 | `409 VERDICT_PENDING`，`detail.resumable=true`，未发布结果不能当审计凭据 |

检查点按 `normalize → evaluate → graph → chains → finalize` 推进，每阶段结束写一次
`progress.done`。定义在绑定瞬间随检查点落盘，所以进程在第一个阶段前死掉也能续跑。

### 9.3 判定

- 同名同版本多定义先做表达式稳定签名：签名一致只是重复登记（`duplicates`），
  签名不同才是冲突（`METRIC_CONFLICT`）。
- 依赖只认 `depends_on` 声明：目标不存在记 `METRIC_BROKEN_DEP`，指向自己记 `METRIC_CYCLE`。
- 环检测复用 M5 的三色 DFS；一个环只取环上最小节点作链路锚点，其余成员作为下游出现。
- 链路从锚点沿「谁依赖我」展开，下游节点列入 `chains` 且标 `METRIC_CHAIN_AFFECTED`（WARN）。
- 指标状态三档：`BLOCK`（冲突 / 缺依赖 / 环）、`WARN`（有原因但非阻断）、`PASS`。

### 9.4 授权

| 视图 | 可见 | 不可见 |
|---|---|---|
| 未授权（默认，`viewer`） | 匿名链路、深度、原因码、计数、输入版本 | 指标名、公式、来源、签名、审计日志 |
| 授权审计（`auditor`） | 全部，含指标名、公式、来源、签名与审计日志 | — |

- 审计凭据来自请求头 `X-Audit-Token` 或查询参数 `audit_token`，与服务端
  `METRICGRAPH_AUDIT_TOKEN` 比对（默认 `audit-local`，仅本机演示用）。
- 匿名编号 `m-` + `sha256(小写名)[:10]`：同一指标跨版本稳定，便于对照链路。
- 匿名视图按白名单重建，不整体拷贝结果对象，内部键名也不下发。
- 自称 `role=auditor` 但无凭据、或凭据不对，一律 `403 AUDIT_DENIED`，不降级为匿名视图。
- 审计日志只记凭据摘要（`sha256[:16]`），原始凭据不落盘。

### 9.5 页面与导出

- `/metrics` 页面并列显示：裁决版本表、受影响链路、指标明细、冲突与环、授权范围、审计日志。
  给 `run_id` 看指定版本，给 `lineage_id` 看该谱系最新版，都不给看全局最新版。
- 导出全部按「所选版本」生成：链路 CSV 与页面链路表逐行对应；运行清单写明
  `run_id`、`input_version`、`input_sha256`、`completed_stages`。
- 未授权导出只给匿名链路 CSV；含指标名与公式的 CSV / Markdown 需要审计凭据。
- 未发布的裁决不可导出（`409 VERDICT_PENDING`）。
