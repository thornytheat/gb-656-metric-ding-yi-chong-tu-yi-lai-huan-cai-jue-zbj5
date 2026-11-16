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
