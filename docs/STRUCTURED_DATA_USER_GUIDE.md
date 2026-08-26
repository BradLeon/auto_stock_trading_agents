# 结构化数据层使用手册

> 读者：研究者、Agent / Workflow 使用者、临时分析脚本作者
> 目标：知道当前有什么数据，并正确地查询、计算和追溯
> 当前状态：统一数据层迁移期，2026-08-26

## 1. 这本手册负责什么

本手册只回答数据消费问题：

1. 当前实际有哪些 dataset、metric 和 entity 可以查询？
2. 如何查询最新值、全部修订和历史 `as_of`？
3. 如何做同比、环比、滚动计算和多实体比较？
4. 如何解释返回值中的来源、质量、冲突和血缘？
5. Agent 和确定性 Workflow 应调用哪个接口？

新增来源、采集、QPS、质量验收、发布、回滚和自动任务属于运维职责，见[结构化数据层运维指南](STRUCTURED_DATA_OPERATIONS.md)。本手册不再重复 `sources`、`datasets`、`metrics`、`ingest`、`publish` 等运维命令。`data health` 和 `data quality` 也是运维诊断命令；使用者只消费查询结果中随行的质量字段，发现异常时把 dataset、entity、metric、`known_at` 和 observation ID 交给运维排查。

数据层统一后，使用者只需记住三个消费入口：

| 需求 | 入口 | 数据是否持久化 |
|---|---|---:|
| 财务、Consensus、区域/行业指标、融资/ARR 等数值 | `ats.data.products` / `data series` | 是，支持 `as_of` 和 vintage |
| 财报、公告、纪要、新闻、研报、证据 | `ats.data.products` 的文档/证据方法 | 是，保存文档版本和血缘 |
| 当前股价、OHLCV、期权链、Greeks、IV | `ats.data.runtime` 或现有 IBKR/yfinance/ThetaData | 否，不能历史重放 |

结构化与非结构化共享来源、实体、质量和血缘语义，但使用者不应直接访问文件路径、SQLite 表或 Provider API。

## 2. 先理解数据边界

### 2.1 Persistent：可以历史重放的研究事实

结构化持久层登记的业务能力包括：

| dataset | 研究内容 | 典型指标 |
|---|---|---|
| `company_financials` | 官方财务和 defeatbeta `stock_statement` 补充 | 收入、利润、EPS、现金流、资产负债、派生 margin/FCF |
| `market_consensus` | 每次真实抓取的市场预期 snapshot | EPS、收入预测、目标价、评级分布 |
| `regional_tw_exports` | 台湾 IC/电子零组件月度出口 | 官方水平值及平台派生 yoy/mom |
| `regional_kr_exports` | 韩国半导体出口月度序列 | 官方水平/指数及平台派生 yoy/mom |
| `industry_dram_contract_price` | DRAM 合约价研究序列 | 合约价格及版本 |
| `private_company_events` | 经证据核验的私营公司事件 | 融资、估值、ARR |

这张表表示“系统注册了这些能力”，不表示当前数据库已经有数据。实际覆盖必须通过 `catalog` 和 `availability` 查询。

### 2.2 Runtime：按需查询、不能从结构化库重放

以下数据不在本结构化持久层：

- ticker 当前价格和 OHLCV；
- 期权链、Greeks、IV 和 skew；
- IBKR 账户、持仓和成交状态。

需要这些数据时继续使用 IBKR、yfinance 或 ThetaData 的现有运行时接口。Workflow 可以把 persistent 和 runtime 输入组合在一起，但 structured snapshot 只重放 persistent 部分。

## 3. 一次性设置命令环境

以下示例默认在 Bash 或 Zsh 中运行。每个终端会话只需设置一次：

```bash
export ATS_REPO="/absolute/path/to/auto_stock_trading_agents"
export ATS_PYTHON="$ATS_REPO/.venv/bin/python"
cd "$ATS_REPO"

ats_cli() {
  PYTHONPATH="$ATS_REPO/src" "$ATS_PYTHON" -m ats.runtime.cli "$@"
}
```

后文的：

```bash
ats_cli data catalog --format markdown
```

等价于：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data catalog --format markdown
```

如果要查询另一套数据库，显式设置：

```bash
export ATS_STRUCTURED_DB_PATH="/absolute/path/to/structured.sqlite"
```

如果只想确认数据层配置和来源状态，不需要打开数据库：

```bash
ats_cli data config
```

这是只读校验命令；实际可查询覆盖仍以 `data catalog` 和 `data availability` 为准。

CLI 默认输出 JSON；`catalog`、`describe`、`availability`、`examples` 支持 `--format markdown`。数值查询 `series`、`derive`、`cross-section` 和 `lineage` 输出 JSON。

## 4. 使用者命令地图

| 你的问题 | 命令 | 返回什么 |
|---|---|---|
| 整个库现在有什么？ | `catalog` | dataset 可查询状态、observation/metric/entity 数量、质量 |
| 一个 dataset/metric/entity 是什么？ | `describe VALUE` | 注册语义、实际覆盖、来源、示例 |
| 某实体在某数据集有什么？ | `availability --dataset ... --entity ...` | 实际指标、期间和最后可见时间 |
| 给我当前库可直接复制的命令 | `examples --dataset ...` | 从 accepted observation 动态生成的命令 |
| 查询时间序列 | `series --metric ... --entity ...` | 值、期间、来源、known_at、质量和血缘 |
| 查询历史时点或全部修订 | `series --as-of ...` / `--vintages` | 当时可见值或所有 vintage |
| 计算同比、环比、滚动值 | `derive --operation ...` | 带计算版本和输入血缘的派生结果 |
| 多实体同期间比较 | `cross-section` | 值、可比性、缺失和冲突 |
| 追溯一个返回值 | `lineage OBSERVATION_ID` | observation → artifact → Provider 的链路 |

正确顺序是：先 `catalog`，再 `availability/examples`，最后查询。不要从静态文档猜 metric ID 或假设某个 ticker 一定有数据。

## 5. 三分钟确认当前有什么数据

### 5.1 全局目录

```bash
ats_cli data catalog --format markdown
```

预期格式：

```text
# Structured Data Catalog

Accepted observations: <count>

| Dataset | Availability | Observations | Metrics | Entities | Quality |
| `company_financials` | `registered_no_data` | 0 | 0 | 0 | `warning` |
| `market_consensus` | `queryable` | 84 | 17 | 2 | `passed` |
```

数值会随数据库变化。关键是 `Availability`：

| 状态 | 含义 | 下一步 |
|---|---|---|
| `queryable` | 有 accepted observation | 继续 availability/examples/series |
| `registered_no_data` | 代码和配置支持，但当前库没有 accepted 数据 | 不要查询假值；联系运维确认是否未采集或未发布 |
| `runtime_excluded` | 不属于持久结构化数据 | 改用 IBKR/yfinance/ThetaData runtime |
| `planned` / `deferred` | 尚未成为当前可查询能力 | 不应在 Workflow 中依赖 |

### 5.2 对象说明

```bash
ats_cli data describe market_consensus --format markdown
```

它回答数据集的业务含义、主源/回退源、实际 entities/metrics、期间范围、最后 `known_at`、质量状态，并附带当前库生成的查询示例。

### 5.3 实体覆盖

```bash
ats_cli data availability \
  --dataset market_consensus \
  --entity MRVL
```

预期 JSON 结构：

```json
{
  "entity_filter": "MRVL",
  "dataset_filter": "market_consensus",
  "datasets": [
    {
      "dataset_id": "market_consensus",
      "entity_id": "MRVL",
      "status": "queryable",
      "accepted_observations": 42,
      "metrics": ["consensus.eps.mean", "consensus.eps.high"],
      "period_start": "2025-07-31",
      "period_end": "2026-08-31",
      "latest_known_at": "2026-08-25T13:30:51+00:00"
    }
  ]
}
```

数量和时间只是格式示例。若 `status=no_coverage`，表示当前库没有该实体的 accepted 数据，不应继续把空结果当作 0。

### 5.4 动态示例

```bash
ats_cli data examples --dataset market_consensus
```

预期返回：

```json
{
  "dataset_id": "market_consensus",
  "status": "ok",
  "selected_from_actual_coverage": {
    "metric_id": "consensus.eps.high",
    "entity_id": "MRVL",
    "period": "2026-07-30",
    "observation_id": "151c96f69498d1c1a641493e"
  },
  "examples": [
    "ats data series --dataset market_consensus --metric consensus.eps.high --entity MRVL",
    "ats data lineage 151c96f69498d1c1a641493e"
  ],
  "python": "from ats.data_platform import ..."
}
```

`examples` 只从当前 accepted observation 生成示例。返回 `status=no_data` 时，不要照抄手册里的固定实体，先让运维确认数据是否已经采集和发布。

## 6. 完整使用 Demo：查询 MRVL Consensus EPS 上界

### 6.1 使用场景

研究问题：当前系统认为 MRVL 目标财季 EPS Consensus 的高位预期是多少？这个数何时被系统看到、来自哪个来源、是否存在冲突？

当前验收库在 2026-08-26 的动态目录中选择了：

- dataset：`market_consensus`
- entity：`MRVL`
- metric：`consensus.eps.high`

如果你的 `data examples` 返回别的实体或指标，使用它生成的当前值替换下面参数。

### 6.2 查询

```bash
ats_cli data series \
  --dataset market_consensus \
  --metric consensus.eps.high \
  --entity MRVL
```

预期 JSON 结构：

```json
{
  "status": "ok",
  "metric_id": "consensus.eps.high",
  "entity_id": "MRVL",
  "dataset_id": "market_consensus",
  "as_of": null,
  "include_vintages": false,
  "source_strategy": "selected",
  "quality_mode": "strict",
  "rows": [
    {
      "observation_id": "151c96f69498d1c1a641493e",
      "period": "2026-07-30",
      "value": 0.95707,
      "unit": "USD/share",
      "currency": "USD",
      "known_at": "2026-08-25T13:30:51+00:00",
      "source_id": "yfinance_consensus",
      "selected_source": "yfinance_consensus",
      "selection_reason": "primary_source_available",
      "quality_status": "accepted",
      "conflict": false,
      "lineage": {
        "observation_id": "151c96f69498d1c1a641493e",
        "artifact_id": "800cf680b3a45d2fd88bace1"
      }
    }
  ],
  "rejected": [],
  "conflicts": [],
  "missing": null
}
```

实际数值会变化。读取顺序：

1. `status=ok` 且 `rows` 非空，查询才有结果；
2. `period` 是预测绑定的具体目标财期，不是抓取日期；
3. `known_at` 是系统首次可用时间，历史 `as_of` 受它约束；
4. `unit` 和 `currency` 决定数值如何解释；
5. `quality_status=accepted` 表示通过准入，不表示投资观点正确；
6. `selected_source` 和 `selection_reason` 解释为何选该来源；
7. `conflict=true` 或 `conflicts` 非空时，不得静默平均；
8. `observation_id` 用于血缘查询和 snapshot manifest。

### 6.3 查血缘

```bash
ats_cli data lineage 151c96f69498d1c1a641493e
```

预期能追到 observation、series、ingestion run、raw artifact/query slice、Provider 和获取时间。文档型数值还应包含 document/version/span 与核验记录。

### 6.4 重放历史时点

```bash
ats_cli data series \
  --dataset market_consensus \
  --metric consensus.eps.high \
  --entity MRVL \
  --as-of 2026-08-26T00:00:00+00:00
```

若 `as_of` 早于 observation 的 `known_at`，预期返回：

```json
{
  "status": "no_coverage",
  "rows": [],
  "missing": {
    "status": "no_coverage",
    "reason": "no_accepted_observation"
  }
}
```

这不是错误，也不能用当前最新值回填；它表示该历史时点系统尚不知道该数据。

## 7. 查询模板

### 7.1 最新序列

```bash
ats_cli data series \
  --dataset DATASET_ID \
  --metric METRIC_ID \
  --entity ENTITY_ID
```

如果不确定三项参数，先运行：

```bash
ats_cli data examples --dataset DATASET_ID
```

### 7.2 限定起始期间

```bash
ats_cli data series \
  --dataset DATASET_ID \
  --metric METRIC_ID \
  --entity ENTITY_ID \
  --since 2024Q1
```

`since` 是期间字符串下界；财务来源的实际 period 格式以 `availability` 和返回结果为准，不要假设所有公司都使用自然年 `Q1`。

### 7.3 查看全部修订版本

```bash
ats_cli data series \
  --dataset DATASET_ID \
  --metric METRIC_ID \
  --entity ENTITY_ID \
  --vintages
```

默认只返回当前选择视图；`--vintages` 返回同一期间的多个可见版本，用于重述和 Consensus snapshot 分析。

### 7.4 历史 `as_of`

```bash
ats_cli data series \
  --dataset DATASET_ID \
  --metric METRIC_ID \
  --entity ENTITY_ID \
  --as-of 2026-08-01T00:00:00+00:00
```

`as_of` 必须是带时区的 ISO 8601。它回答“当时系统能够知道什么”，不是简单的 `period <= date`。

### 7.5 严格指定来源

```bash
ats_cli data series \
  --dataset company_financials \
  --metric financial.revenue.gaap \
  --entity AMZN \
  --source sec_companyfacts
```

指定 `--source` 时返回该来源的并列事实；不指定时按 dataset 的主源/回退规则选择并返回 `selection_reason`。平台不会把冲突数值平均。

## 8. 派生计算

CLI 当前支持 `yoy`、`mom` 和 `rolling`：

```bash
ats_cli data derive \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT \
  --operation yoy
```

滚动窗口：

```bash
ats_cli data derive \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT \
  --operation rolling \
  --window 3
```

预期返回字段包括：`derivation_id`、`derivation_version`、`operation`、派生值、输入 observation IDs 和质量状态。若历史不足，返回 missing/insufficient-history，不按 0 计算。

使用约束：

- 原始水平和派生变化率分开查询；
- 财务单季不能直接与累计 YTD 数值做环比；
- 跨币种比较前必须显式选择汇率口径和日期；
- 派生值不是 Provider 原始披露，不得改写底层 observation。

## 9. 多实体横截面

```bash
ats_cli data cross-section \
  --dataset company_financials \
  --metric financial.revenue.gaap \
  --entities AMZN,MSFT,KLAC \
  --period 2026-06-30
```

`period` 必须来自 `availability/series` 的实际返回值。结果会检查单位、币种、财期、period basis 和 adjustment。不可比项保留并给出原因，不会静默进入排名；部分实体缺失也不会让整个横截面失败。

## 10. 返回值如何判断能不能用

| 字段/状态 | 含义 | 使用规则 |
|---|---|---|
| `status=ok` | 查询有可交付结果 | 继续检查 rows 和质量 |
| `no_coverage` | 当前库没有该请求的 accepted 数据 | 不补 0；检查 catalog/availability |
| `registered_no_data` | 注册能力存在但尚无实际数据 | 联系运维，不应在 Workflow 中假定存在 |
| `quality_status=accepted` | 通过结构化准入 | 仍需结合 freshness/conflict |
| `quality_status=warning` | 可查看但有质量提示 | 严格业务不得静默使用 |
| `quality_status=conflict` | 多源可比值不一致 | 展示并调查，不取平均 |
| `known_at` | 系统何时首次能使用该版本 | `as_of` 不能越过它 |
| `published_at` | 来源何时对外发布 | 与 known_at 共同决定可见性 |
| `selected_source` | 实际使用来源 | 必须随研究输出保留 |
| `selection_reason` | 主源/回退选择理由 | 回退时确认是否符合用途 |
| `lineage` | observation/artifact 标识 | 重要结论应可回溯 |

`not_yet_published`、`no_coverage`、`unreachable`、`unauthorized`、`stale` 和 `validation_failed` 是不同的缺口，不能统一解释为“没有数据”。

## 11. Python API

### 11.1 发现和查询

```python
from datetime import datetime, timezone
from ats.data_platform import get_data_products

products = get_data_products()

catalog = products.catalog()
coverage = products.availability(
    dataset="market_consensus",
    entity="MRVL",
)

latest = products.metric_series(
    dataset="market_consensus",
    metric="consensus.eps.high",
    entity="MRVL",
    quality="strict",
)

historical = products.metric_series(
    dataset="market_consensus",
    metric="consensus.eps.high",
    entity="MRVL",
    as_of=datetime(2026, 8, 26, tzinfo=timezone.utc),
    quality="strict",
)
```

返回对象与 CLI JSON 使用同一受治理语义。确定性 Workflow 应调用 DataProducts，而不是解析 CLI 文本。

### 11.2 Pandas

```python
frame = products.metric_series(
    dataset="market_consensus",
    metric="consensus.eps.high",
    entity="MRVL",
    quality="strict",
    as_frame=True,
)

columns = [
    "period", "value", "unit", "currency", "source_id",
    "known_at", "quality_status", "observation_id",
]
print(frame[columns])
```

预期是一行一个 observation/vintage 的 DataFrame。Pandas 不是另一套数据源；它保留与对象查询相同的 period、source、known_at、quality 和 lineage 字段。

### 11.3 只读 SQL

```python
with products.read_only_sql() as conn:
    rows = conn.execute(
        """
        SELECT entity_id, metric_id, period, value, currency,
               source_id, known_at, quality_status
        FROM structured_observations_accepted
        WHERE entity_id = ? AND metric_id = ?
        ORDER BY period, known_at
        """,
        ("MRVL", "consensus.eps.high"),
    ).fetchall()
```

SQL 仅适合临时分析、核对和图表工具。Agent 不应依赖物理表结构；默认查询只使用 accepted 只读视图，不读取 quarantined candidate。

## 12. Agent 和 Workflow 如何使用

### 12.1 自主 Agent

会自主决定取数步骤的 Agent 使用仓库 Skill：

```text
.agents/skills/structured-data-consumer/SKILL.md
```

标准过程：

1. `catalog/availability/examples` 发现当前能力；
2. 检查 persistent/runtime 边界；
3. 使用 DataProducts 或 CLI 查询；
4. 保留 `as_of`、quality、source 和 lineage；
5. 需要实时行情时路由到 IBKR/yfinance，并声明不可由 structured snapshot 重放。

Skill 不保存静态指标清单；它指导 Agent 使用动态目录。

### 12.2 确定性 Workflow

PEAD、Sector、Chain 等 Workflow 不依赖 Prompt 或 Skill 才能正确运行。它们直接调用 DataProducts/兼容 DTO，并由 feature flag 控制 legacy/shadow/platform。

| Workflow | persistent 输入 | runtime 输入 |
|---|---|---|
| PEAD | 财务 actual、历史财期、Consensus snapshot | 当前价格、run-up、期权 expected move |
| Sector | 可比财务和 Consensus 横截面 | momentum、当前市场状态 |
| Chain | 台湾/韩国官方水平及平台派生 yoy/mom | 当前无结构化行情依赖 |
| Evidence | 已核验融资、估值、ARR observation | 新闻解释和 Agent 命题不作为共享事实 |

### 12.3 组合 persistent 与 runtime

```python
from ats.data import market_data
from ats.data_platform import get_data_products
from ats.schemas.market import Ticker

products = get_data_products()

persistent = products.metric_series(
    dataset="company_financials",
    metric="financial.revenue.gaap",
    entity="MSFT",
    quality="strict",
)["rows"]

price_now = market_data.fetch_snapshot(Ticker(symbol="MSFT"))

workflow_inputs = products.compose_inputs(
    persistent=persistent,
    runtime=[{"symbol": "MSFT", "price": price_now}],
)
```

输出明确分为 `persistent` 和 `runtime`。只有 persistent observation IDs 进入 structured snapshot；即时价格按既有 Journal/任务记录保存本次使用结果。

## 13. 常见问题

### 为什么 `config/structured_data.yaml` 有这个 dataset，但 catalog 显示 no data？

YAML 表示注册能力，数据库 observation 才表示实际数据。查看 `availability`；采集与发布问题由运维手册处理。

### 为什么找不到 MSFT，但 MRVL 有数据？

当前库的实际 coverage 可能只包含部分实体。`examples` 会从真实 accepted observation 选择实体；不要用静态 ticker 清单推断覆盖。

### 为什么 HuggingFace 不是最终业务来源？

HuggingFace 是 defeatbeta 数据集的托管/传输渠道；业务 provenance 仍需记录 defeatbeta 及其声明的 Yahoo Finance 上游。

### 为什么 Consensus 不能自动补出长期历史？

部分 Provider 没有可靠历史发布时间。平台只能从实际抓取的 `known_at` 开始建立可信 snapshot，不能把今天看到的预测伪装成过去已知。

### 为什么有值但 `conflict=true`？

两个来源对同一实体、指标、期间和口径返回了不同值。平台保留并列事实，不覆盖、不平均。

### 为什么找不到股价和期权表？

这是明确的产品边界。股价和期权变化快，继续通过 IBKR/yfinance/ThetaData runtime 查询，不进入结构化持久层。

### 为什么 Provider 字段在 pending mapping？

平台尚不能证明它与某个统一 metric 语义一致。原字段和 artifact 会保留，但不会为了“有数据”而错误发布。

## 14. 使用者自检清单

在把数据用于报告、图表、Agent 或 Workflow 前，确认：

- 通过 `catalog/availability/examples` 证明数据当前实际存在；
- metric、period basis、单位、币种和 adjustment 与研究问题一致；
- 使用 latest、vintages 或 `as_of` 的选择正确；
- `quality_status`、freshness、conflict 和 missing reason 已处理；
- 重要数值保留 source、known_at 和 observation ID；
- 多实体比较已通过可比性检查；
- 派生计算保留公式版本和输入 observation IDs；
- runtime 行情没有被误称为可重放的 structured 数据；
- 确定性 Workflow 调用 DataProducts，而不是解析 Prompt 或 CLI 文本。

## 15. 相关文档

- [结构化数据层运维指南](STRUCTURED_DATA_OPERATIONS.md)
- [结构化数据开发者指南](STRUCTURED_DATA_DEVELOPER.md)
- [总体数据架构](DATA_ARCHITECTURE.md)
- [数据目录补充验收](STRUCTURED_DATA_PRODUCT_SURFACES_ACCEPTANCE_2026-08-26.md)
