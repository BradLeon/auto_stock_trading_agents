# 数据层消费者切换状态

> 状态截至 2026-08-29。本文只记录**消费者读取路径**的切换状态，不替代动态检查命令。
> 消费者分类和 `data release-assessment` 不再是数据源或数据集发布门；数据发布只看原始数据、
> 血缘、报告期/单位、完整性、时效、质量和派生重算。feature flag 不是验收证据。

## 数据发布与消费者切换的边界

`company_financials` 的发布资格应使用：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data financial-package-check
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data release-check \
  --source defeatbeta_stock_statement --mode platform
```

前者只检查 AMZN、MSFT、KLAC、TSM 当前完整报表包的 artifact/lineage、实体、期间、币种、
核心字段、时效、资产负债表质量与派生 XBRL 重算；后者再检查来源注册和最近采集状态。两者均不
读取 consumer mode、shadow 记录或 Workflow 运行结果。2026-08-29 的当前结果为四个验收实体均
通过：AMZN 是 SEC Facts + 同期 IR 的受控官方披露包，MSFT/KLAC/TSM 使用优先级最高的
DefeatBeta 报表包。`market_consensus` 由权威 Provider snapshot 发布；分析师覆盖面或修订造成的
consumer 数值差异应保留其 source/known-at，而不阻塞数据发布。

## 已完成的阶段 10 实现

- 结构化读取：PEAD 的 Consensus/财务数据，以及 Sector/Macro 的 Consensus、台湾/韩国
  区域数列均通过 `ats.data.products` / `ats.data.runtime` 的可回退路径读取；runtime 行情
  和实时宏观指标仍不入库。
- 非结构化读取：PEAD 研究文章和 Evidence/Chain 报告通过
  `UnstructuredReadRouter` 读取已迁移的文档、版本、证据和投影。在 `shadow` 下会比对
  新旧结果并返回 legacy 结果；目标库不可达时同样安全回退。
- 工作流状态：dossier、决策、处理 lease、洞察、task projection、claim proposal/assessment、
  Chain/Chief 运行结果和交易记录均属于 `ats.memory`，不作为结构化或非结构化输入源迁移。
- 审计：`ats data cutover-check` 将每次消费者级新旧库对账写入
  `var/data.sqlite:data_consumer_cutover_records`。对账按实体血缘过滤 document/version/chunk、
  evidence fact/projection 和 structured series，而不是用全库总数冒充实体对账。

## 当前验收结果与 10.5 分类

| Consumer | 10.5 分类与真实库证据 | 当前 mode / 处理 |
|---|---|---|---|
| `pead_fundamentals` | `governed_upgrade`：AMZN 是完整官方报表补齐 legacy 缺字段；MSFT/KLAC 是 EPS 单位与官方债务定义；TSM 是 `TWD/ADR` 单位修正。四个真实 shadow 记录同日稳定。 | `shadow`。需要写入独立复核记录（原始 artifact/lineage、期间单位经济定义、freshness、PEAD 输出）后才可 platform。 |
| `sector_agent` | `equivalent`：台湾财政部与韩国 ECOS 的 2026-07 level、YoY、MoM 与单位相同；有效窗口无 mismatch。 | 数据证据已满足；仍是 `shadow`，等待发布负责人显式批准并修改 checked-in consumer 基线。 |
| `macro_agent` | `governed_upgrade` 候选：legacy 发生 `ConnectError` 并返回空值，platform 有已治理的 2026-07 结果；这不是数值 mismatch。 | `shadow`。网络失败本身不能放行；必须独立复核 raw official artifact、已知时点和 Macro 输出，随后再记录验证。 |
| `evidence_chain` | `equivalent`（仅迁移存储）：2026-08-26 的 document/version/chunk/evidence 实体血缘对账通过。 | `shadow`。表 hash 不足以证明 `UnstructuredReadRouter` 的正文/版本/证据输出等价；需补一次真实消费者输出对账。写入继续属于 legacy workflow memory。 |
| `sector_consensus` | `equivalent`：当前 consumer 输出对账稳定。 | 数据证据已满足；维持 `shadow`，待独立发布决策。 |
| `pead_consensus` | `platform_regression`：MSFT 的 EPS consensus signature 为 3.2（legacy）对 3.4（platform）。 | 已从历史 `platform` 基线降为 `shadow`；先解释 Provider 时点/定义差异并完成新的真实对账。 |
| `chain_regional` | `evidence_incomplete`：没有消费者级 shadow 记录。 | 已从历史 `platform` 基线降为 `shadow`；需完成真实 dual-read、coverage/freshness/output 验收。 |
| `sector_fundamentals`、`pead_research` | `evidence_incomplete`：没有消费者级 shadow 记录。 | 分别保持 `legacy`、`shadow`；不得发布。 |
| `chief_graph` | `orchestration_boundary`：它消费上游 Agent/Workflow memory 产物；旧的 `chief-graph` 表 hash 记录会被发布评估明确忽略。 | 不可发布为 data-consumer `platform`。验收应看上游消费者状态、Chief 端到端回归和 rollback drill。 |
| `runtime_scheduler` | `orchestration_boundary`：它协调 runtime 日历、产品和采集 pipeline，没有同一持久数据的 legacy/platform 替代读模型。 | 不可发布为 data-consumer `platform`。验收应看上游状态、scheduler 端到端回归和 rollback drill。 |

所有候选均在[`config/data/consumer_release.yaml`](../config/data/consumer_release.yaml) 中登记；没有
消费者级真实 shadow 记录时，评估会返回 `evidence_incomplete`，不得因历史 feature flag 直接发布。

本次 PEAD 验收：`test_structured_consumer_migration.py`、`test_consumer_cutover_records.py`、
`test_pead_data.py`、`test_pead_graph.py`、`test_pead_score.py` 与 `test_pead_report.py` 均通过；
覆盖 shadow 输入、单位/债务语义、CapEx/FCF 展示、失败回退、prep、score 与报告渲染。

## 消费者切换门与下一步（非数据发布门）

`config/data/migration.yaml` 要求 consumer 至少产生 **1 个自然日内 1 次**成功对账记录，且没有
未解决 mismatch。10.5 进一步要求 `config/data/consumer_release.yaml` 将入口标记为直接数据消费者，
并通过 coverage、freshness、输出证据门。若差异属于 `governed_upgrade`，还必须写入独立复核记录；
legacy 网络失败不能自动代替复核。重复检查仍会保留审计记录，但不以次数替代这些门。

注意：`company_financials` 全局质量报告仍显示 MRVL 的既存跨源冲突（CapEx/营业利润，约
1.2%）。它们不在 AMZN/MSFT/KLAC/TSM 的数据发布验收范围内，仍保留以待单独修复，但不会阻塞
已通过 `financial-package-check` 的公司财务数据发布；PEAD 是否切到 platform 是独立消费者决策。

本轮实际补齐的原始采集（均保存 artifact 与血缘）如下：SEC Company Facts 为 AMZN 1,102 条、
MSFT 1,314 条、KLAC 1,022 条、TSM 128 条；台湾财政部电子零组件出口 307 条（至 2026-07）；
韩国 ECOS 半导体出口金额指数 19 条（至 2026-07）。TSM 在 SEC Company Facts 中目前只映射到
2024-12-31；该缺口已由 `company_disclosures` 的官方 Q1/Q2 FY2026 earnings release 补齐。该来源
只承担季度披露，不以 20-F 年报覆盖作为 PEAD 的验收要求。

Sector/Macro 的 `shadow` 输出故意保留 legacy 数值；对应 platform 候选的 `known_at` 和
observation ID 已经在产品读取与对账记录中验证。受控装配会屏蔽与本项无关的逐个股实时行情、
新闻与证据扫描，但保留真实区域 dual-read，因此验证的是数据产品到报告上下文的实际链路，而非
mock 数据。完整 Sector live review 的全市场快照仍可能超过本轮 60 秒测试预算，不作为 platform
发布依据。

Chief 与 scheduler 的 10.4 验收只对数据**输入边界**做切换验证：Chief 的上游财务、文档、
证据和 Consensus 仍由已经 shadow 的 Agent 数据产品提供，而 Chief 报告、审批、交易和运行状态
留在 `ats.memory`。scheduler 的财报日历是 runtime 输入，SEC release/news/research 则经统一
products/pipelines 进入下游；两者都不能以“Workflow 成功运行”代替数据层新旧库对账。因而本轮
发布/回滚演练使用隔离 release overlay，留下可审计 history，但没有写入生产 overlay 或提升到
`platform`。

每天的只读检查：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data cutover-check \
  --consumer evidence_chain --entity NVDA \
  --source-db var/ats.sqlite --target-db var/data.sqlite

PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data release-assessment \
  --consumer macro_agent --target-db var/data.sqlite
```

`release-assessment` 是只读分类命令：`0` 表示直接数据消费者的数据证据完整，`2` 表示仍有明确
gap；对编排边界返回 `2` 是预期行为，不是数据错误。它通过后仍须先运行
`data release-check --consumer <ID> --mode platform`。只有 `ready=true`、且发布负责人已在
checked-in config 批准，才可使用 `data publish --consumer <ID> --mode platform --apply`。任意
mismatch 或运行异常都执行 `data rollback --consumer <ID> --mode legacy --apply`。

阶段 11（冻结 legacy 写入、删除旧实现及 schema）必须等待这些发布资格全部通过后才开始。
