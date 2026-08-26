# 结构化数据层使用手册

> 适用变更：`build-structured-data-foundation`
> 文档状态：阶段二最终实现基线（2026-08-25）
> 读者：研究者、Agent / Workflow 使用者、临时分析脚本作者

## 1. 这套数据层解决什么问题

使用者不需要知道数据存在 SQLite 的哪张表，也不需要知道某个 Provider 的字段名。你应该能直接提出研究问题：

- AMZN 最近八季收入和经营现金流是什么？
- 截至某个历史时点，当时系统能看到的 MSFT 财务数据是什么？
- 当前 Consensus 对下一财季 EPS/收入的预期是什么，数据有多旧？
- 台湾 IC 出口同比如何，计算用了哪些原始月份？
- OpenAI 最近一轮融资金额和估值来自哪篇原文、哪个段落？
- PEAD 本次使用了哪些持久事实，哪些是即时市场输入？

数据层负责提供可查询、可计算、带质量状态和血缘的事实；Agent 负责解释这些事实对投资任务意味着什么。

注册语义以 `config/structured_data.yaml` 为准，但“当前实际有什么数据”必须通过动态目录确认。日常使用优先通过 DataProducts 或 `ats data` 发现能力，不直接依赖 YAML 的内部结构或本文的静态举例。

## 2. 先理解两类输入

### 2.1 Persistent：可重放的研究事实

进入结构化持久层的数据包括：

- 官方财务报表和修订。
- defeatbeta `stock_statement` 镜像切片及其来源信息。
- 每次真实抓取的 Consensus snapshot。
- 台湾/韩国官方月度出口水平。
- 经证据核验的融资、估值和 ARR 等事件数值。

这些数据可以查询 latest、全部 vintage 和历史 `as_of`，也可以进入 structured snapshot manifest。

### 2.2 Runtime：当下查询的市场输入

以下数据不会进入本结构化数据库：

- ticker 股价和 OHLCV。
- 期权链、Greeks、IV 和 skew。
- IBKR 实时账户、持仓和行情。

需要这些数据时继续直接使用现有 IBKR / yfinance / ThetaData 路径。它们可以与 persistent 数据在同一个 Workflow 中组合，但不能误以为 SQL 可以重放当时的完整市场状态。

## 3. 三分钟确认“现在能查什么”

按以下顺序执行，不需要先知道表名、Provider 字段或 metric ID：

```bash
# 1. 总览：区分 queryable、registered_no_data 与 runtime_excluded
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data catalog --format markdown

# 2. 解释一个 dataset、metric、source 或 entity
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data describe company_financials
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data describe AMZN

# 3. 确认某实体在某数据集中的实际指标、期间和最新可见时间
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data availability \
  --dataset company_financials --entity AMZN

# 4. 从 accepted observation 动态生成可复制命令；空库会明确返回 no_data
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data examples \
  --dataset company_financials
```

四个命令的分工：`catalog` 回答全局能力，`describe` 回答对象语义，`availability` 回答实际覆盖，`examples` 生成当前环境确实存在的数据查询。`sources/datasets/metrics` 仍保留为低层机器清单，但不能单独证明数据库已有数据。

### 3.1 当前产品面

| 能力 | 状态 |
|---|---|
| 查询现有 measurement 序列 | `CURRENT` |
| 基础 `as_of` 和全部 vintages | `CURRENT` |
| 输出 Pandas DataFrame | `CURRENT` Python API |
| 查询文档、公司包、命题包、健康和基础质量 | `CURRENT` |
| 统一财务、stock_statement、Consensus 查询 | `CURRENT` DataProducts + CLI |
| 多实体横截面和可比性检查 | `CURRENT` DataProducts + `data cross-section` |
| 版本化同比/环比/滚动计算 | `CURRENT` DataProducts + `data derive` |
| 受治理只读 SQL | `CURRENT` |
| 来源选择、冲突、pending mapping 和 snapshot replay | `CURRENT` DataProducts + 运维 CLI |

本节列出的查询和运维命令均已实现；planned 来源与未解除质量门会直接显示状态，不以假数据补齐。

## 4. 数据发现

### 4.1 当前来源与健康

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data health
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality \
  --dataset company_financials --format markdown
```

`health` 用来发现既有来源状态；不带 dataset 的 `quality` 同时返回文档和 structured 质量，带 dataset 时返回 Coverage、Accuracy / Reconciliation、Freshness、Completeness、Availability 五维结构化报告。

### 4.2 目录命令

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data sources
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data datasets
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data metrics
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data catalog --format markdown
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data describe market_consensus
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data availability --dataset market_consensus --entity MSFT
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data examples --dataset market_consensus
```

推荐先发现 dataset 和 metric，再查询数值。不要从 Provider 字段名猜全局指标名。

目录会告诉你：

- 业务含义和分类。
- 可用实体与期间范围。
- 单位族、币种和周期口径。
- 当前首选来源和可用回退来源。
- 数据新鲜度、覆盖和质量状态。
- 是否为 persistent、runtime/excluded 或 pending。

## 5. CLI 取数

### 5.1 当前序列查询

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT
```

指定最早期间：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT \
  --since 2025-01
```

查看所有修订版本：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT \
  --vintages
```

查看历史时点当时可见的数据：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT \
  --as-of 2026-08-01T00:00:00+00:00
```

### 5.2 统一指标查询

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --metric financial.revenue.gaap \
  --entity AMZN \
  --since 2024Q1
```

严格指定来源：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series \
  --metric financial.revenue.gaap \
  --entity AMZN \
  --source sec_companyfacts
```

如果不指定来源，平台按 dataset/用途配置选择主源或回退源，并在结果中给出选择原因。它不会把不同来源的冲突值平均成一个数。

### 5.3 CLI 派生与横截面

```bash
# 同比；mom、rolling 同理，rolling 另加 --window N
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data derive \
  --dataset regional_tw_exports \
  --metric regional.tw_ic_exports.value \
  --entity TW_IC_EXPORT --operation yoy

# 多实体比较；period 必须是 availability/series 中实际返回的期间
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data cross-section \
  --dataset company_financials \
  --metric financial.revenue.gaap \
  --entities AMZN,MSFT,KLAC --period 2026-06-30
```

若示例实体或期间在当前库中不存在，先运行 `data examples` 和 `data availability`，不要照抄本文的说明性占位值。

## 6. Python API

### 6.1 当前 API

```python
from datetime import datetime, timezone
from ats.data_platform import get_data_products

products = get_data_products()

catalog = products.catalog()
company_financials = products.describe("company_financials")
amzn_coverage = products.availability(
    dataset="company_financials", entity="AMZN")
copyable_examples = products.examples(dataset="company_financials")

latest = products.indicator_series(
    source_id="tw_ic_exports",
    entity="TW_IC_EXPORT",
    since="2025-01",
)

historical = products.indicator_series(
    source_id="tw_ic_exports",
    entity="TW_IC_EXPORT",
    as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
)

all_vintages = products.indicator_series(
    source_id="tw_ic_exports",
    include_vintages=True,
)
```

### 6.2 Pandas

```python
frame = products.indicator_series(
    source_id="tw_ic_exports",
    entity="TW_IC_EXPORT",
    as_frame=True,
)

print(frame[["period", "value", "unit", "source_id", "fetched_at"]])
```

DataFrame 不是另一条取数路径，而是同一个查询结果的表格表达。结构化 `metric_series(..., as_frame=True)` 会保留 period、source、vintage/known_at、quality 和 lineage 列，使 Pandas 和对象查询语义一致。

### 6.3 统一产品

```python
series = products.metric_series(
    metric="financial.revenue.gaap",
    entity="AMZN",
    since="2024Q1",
    as_of=datetime(2026, 8, 1, tzinfo=timezone.utc),
    quality="strict",
)

peers = products.cross_section(
    metric="financial.gross_margin.gaap",
    entities=["AMZN", "MSFT", "GOOG"],
    period="FY2026Q2",
)
```

严格质量模式遇到单位不明、期间冲突或来源 conflict 时不返回伪装成正常的值；宽松模式可以返回候选状态供研究排查，但必须携带警告。

## 7. SQL

SQL 是只读分析面，只暴露 accepted 观测和选择视图。它不会开放 Workflow 内部表，也不允许 SQL 写入平台。

概念查询：

```sql
SELECT
    entity_id,
    metric_id,
    period,
    value,
    currency,
    source_id,
    known_at,
    quality_status
FROM structured_observations_accepted
WHERE entity_id = 'AMZN'
  AND metric_id = 'financial.revenue.gaap'
ORDER BY period, known_at;
```

SQL 和 DataProducts 的 accepted/as_of 语义必须通过一致性测试。Agent 不应依赖 SQL 物理视图名；SQL 面主要供临时研究、核对和图表工具使用。

## 8. `latest`、`vintages` 和 `as_of` 的区别

假设某公司 Q1 收入：

| 系统看到时间 | 值 | 含义 |
|---|---:|---|
| 5 月 1 日 | 100 | 首次披露 |
| 6 月 15 日 | 102 | 后续修订 |

- latest 返回 102。
- all vintages 返回 100 和 102。
- `as_of=5 月 20 日` 返回 100。
- `as_of=4 月 30 日` 返回 missing，即使来源后来把该数据标注为 Q1。

`as_of` 同时受发布时间和系统首次获取时间约束。它不是简单的 `period <= date`。

## 9. 派生计算

平台支持版本化同比、环比、移动统计和显式汇率换算。

```python
base = products.metric_series(
    metric="regional.tw_ic_exports.value",
    entity="TW_IC_EXPORT",
    as_of=cutoff,
)
yoy = products.derive(
    operation="yoy",
    query_result=base,
    output_metric="regional.tw_ic_exports.yoy",
)
```

使用规则：

- 原始水平与派生变化率分开查询。
- 缺少上一期/去年同期时返回 missing，不按 0 计算。
- 跨币种比较前显式指定换算币种和汇率时间。
- 财务单季不能直接与累计年初至今数值做环比。
- 返回结果应显示公式版本和输入 observation IDs。

## 10. 横截面对比

横截面查询不是简单地把不同公司同名字段拼成一列。平台会检查：

- 指标语义是否一致。
- GAAP / non-GAAP 是否一致。
- 单季 / 累计 / 年度口径是否一致。
- 公司财期是否已对齐。
- 币种和单位是否一致或已显式换算。
- 来源质量是否满足查询模式。

不可比项会返回状态和原因，不会静默进入排名。Sector 因此可以明确区分“数据较差”和“公司表现较差”。

## 11. 来源选择、回退与冲突

默认规则示例：

- 公司财务：官方 SEC/XBRL 或等价公司披露优先。
- defeatbeta `stock_statement`：补充、回填和交叉检查。
- 台湾/韩国出口：官方源优先。
- Consensus：按真实抓取 snapshot；未来其他 Provider 与其并列。

结果中的来源状态可能是：

```json
{
  "selected_source": "sec_companyfacts",
  "selection_reason": "primary_source_available",
  "alternatives": [
    {"source": "defeatbeta_stock_statement", "status": "conflict"}
  ]
}
```

如果主源不可达而使用回退，结果必须写明 `fallback_reason`。如果两个来源冲突，平台保留两个值并提示差异，不替你决定“取平均”。

## 12. 数据分类与查找思路

遇到一个数字时，可按下面顺序判断去哪里取：

| 问题 | 应走路径 |
|---|---|
| 是当前股价、期权、Greeks 或 IV 吗？ | runtime：IBKR / yfinance / ThetaData |
| 是公司/官方 API、CSV、XBRL 中的低频事实吗？ | structured persistent |
| 是新闻/研报中的数字吗？ | 先查 document；只有带 evidence 且核验后才查 structured |
| 是同比、环比、移动均值吗？ | structured derivation，不把它当 Provider 原始值 |
| 是 Agent 的利多/利空判断吗？ | task projection / report，不是共享结构化事实 |
| 是某次交易使用的即时价格吗？ | decision / Journal 审计，不建设行情副本 |

## 13. 查看血缘和质量

`CURRENT` 文档/投影血缘：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data lineage <projection-id>
```

结构化血缘与质量：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data lineage <observation-id>
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data conflicts --dataset company_financials
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data pending-mappings
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality --dataset company_financials --format markdown
```

一条完整血缘应能追到：查询结果 → 选中的 observation vintage → 标准化 mapping 版本 → raw artifact/query slice → Provider 和获取运行。文档型数值还应追到 document/version/span 和核验历史。

## 14. Agent 和 Workflow 如何使用

### 14.1 基本原则

- Agent 只消费 DataProducts 或由 Workflow 组装的上下文，不直接调用 structured repository。
- Provider Adapter 只由采集层调用。
- persistent 数据和 runtime 数据在上下文中分区并标明来源。
- 对质量敏感的 Agent 使用 strict 模式；缺失时明确降级，不自行填 0。
- 每次重要分析记录 structured snapshot manifest，便于重放持久输入。
- 会自主决定取数步骤的 Agent 使用仓库 Skill：`.agents/skills/structured-data-consumer/SKILL.md`。Skill 会要求先发现、再检查、后查询，并动态路由 persistent/runtime。
- 确定性 Workflow 不依赖 Skill；PEAD、Sector、Chain 直接调用 DataProducts/兼容接口与 feature flags。Skill 是 Agent 的操作规程，不是运行时 API。

### 14.2 PEAD

目标用法：

- 从 DataProducts 获取财务 actual、历史季度、Consensus snapshot 和质量状态。
- 从现有 market/options 运行时接口获取当前价格、run-up 和 expected move。
- 记录财务/Consensus 的 snapshot manifest；市场数据按既有 dossier/Journal 记录本次使用结果。

在新平台完成 shadow reconciliation 前，PEAD 继续使用旧 `fundamentals.fetch()` 和 `consensus.fetch()` 外部契约。

### 14.3 Sector

目标用法：

- 用 cross-section 获取可比财务和 Consensus 指标。
- runtime 获取当前 momentum。
- 不可比或缺失项从数据质量状态进入报告，不按 cohort-neutral 的 0 掩盖问题。

### 14.4 Chain / Evidence

目标用法：

- 台湾/韩国 Adapter 只提供官方水平，yoy/mom 由平台派生。
- Chain 将派生结果转成证据观察，但不拥有采集副本。
- 融资/ARR 等文档数字只有 accepted + verified 后才能成为共享 observation；Agent 的命题支持/反驳仍是任务解释。

## 15. 组合 persistent 与 runtime 的示例

```python
from ats.data import market_data, options
from ats.data_platform import get_data_products
from ats.schemas.market import Ticker

products = get_data_products()

fundamentals = products.metric_series(
    metric="financial.revenue.gaap",
    entity="MSFT",
    dataset="company_financials",
    as_of=analysis_time,
)
consensus = products.consensus_snapshot(entity="MSFT", as_of=analysis_time)

price_now = market_data.fetch_snapshot(Ticker(symbol="MSFT"))
option_setup = options.fetch("MSFT")

workflow_input = products.compose_inputs(
    persistent=fundamentals["rows"] + consensus["rows"],
    runtime=[price_now.model_dump(mode="json"), option_setup],
)
manifest = products.snapshot_manifest(
    consumer="pead", purpose="manual-analysis", as_of=analysis_time,
    rows=workflow_input["snapshot_eligible"],
)
```

不要为了让一次分析“全都能重放”，把 `price_now` 或整个期权链写进 structured repository。真正需要审计的单次市场输入可由 Workflow 决策记录保存。

## 16. 常见问题

### 为什么 stock_statement 不是唯一财务源？

它是 defeatbeta 对上游结构化数据的镜像，适合便捷切片、补充和交叉检查，但不能取代官方披露的来源身份、申报时间和修订语义。平台会把它与官方值并列，并默认官方优先。

### 为什么 HuggingFace 不是业务来源？

HuggingFace 是数据集托管/传输渠道。血缘需要同时写清 defeatbeta 数据集、其声明的上游来源和具体 HuggingFace snapshot/query slice，不能只写 `source=huggingface`。

### 为什么 Consensus 不能回填很长的历史？

如果 Provider 不给可靠历史发布时间，系统只能从首次真实抓取起保存可信 snapshot。用今天看到的 `0q` 去构造过去的预测会产生前视偏差。

### 为什么查询结果可能有值但状态是 conflict？

因为多个来源都提供了值但差异超过配置容差。平台可以按主源规则给出 selected value，同时保留冲突；严格模式可以拒绝使用该值。

### 为什么找不到股价和期权表？

这是明确的产品边界，不是接入遗漏。请使用 IBKR/yfinance/ThetaData 的运行时接口。

### 为什么某个 Provider 字段只在 pending mappings？

平台无法证明它与现有核心指标语义相同。原始数据没有丢失；映射确认后可以从 artifact 重做标准化，无需重新下载。

## 17. 使用者验收清单

在认为一个数据产品“可用”前，检查：

- 实体、指标、期间和单位是否符合研究问题。
- source、known_at 和 quality 是否可见。
- `as_of` 是否符合分析时点，而不是只看 period。
- 多来源是否存在 conflict 或 fallback。
- 横截面是否通过可比性检查。
- 派生值是否有公式版本和完整输入。
- 文档型数值是否有原文位置和 verified 状态。
- 市场输入是否明确标记 runtime。

## 18. 相关文档

- [总体数据架构](DATA_ARCHITECTURE.md)
- [结构化数据开发者指南](STRUCTURED_DATA_DEVELOPER.md)
- [结构化数据运维指南](STRUCTURED_DATA_OPERATIONS.md)
- [工作流说明](WORKFLOWS.md)
- [数据源说明](DATA_SOURCES.md)
