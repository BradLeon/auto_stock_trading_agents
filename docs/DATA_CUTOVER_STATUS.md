# 数据层消费者切换状态

> 状态截至 2026-08-28。本文记录当前发布资格，不替代动态检查命令。

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

## 当前验收结果

| Consumer | 真实库对账 | 端到端回归 | 当前 mode / 发布资格 |
|---|---|---|---|
| `pead_fundamentals` | 2026-08-28 对 AMZN/MSFT/KLAC/TSM 完成真实 shadow；最近 mismatch 后有 4 条同日 `reconciled`、0 条 active-window mismatch | PEAD prep/score/report 与结构化回归通过；shadow 始终返回 legacy DTO，平台异常也回退 legacy | `shadow`；10.1 已验收，尚未执行 10.5 的 platform 发布。AMZN 是完整官方报表对 legacy 缺字段的升级；MSFT/KLAC 是 EPS 单位与官方 debt 定义升级；TSM 是 `TWD/ADR` 单位修正 |
| `sector_agent` | 2026-08-28 对台湾财政部与韩国 ECOS 两个来源真实 shadow 对账；2 个自然日、active-window 0 mismatch | 区域产品的 source override、YoY/MoM、血缘、故障回退和 Sector 装配/输出渲染测试通过；真实装配确认输出带来源、期间和 observation ID | `shadow`；两个区域 dataset 五维质量均 `passed`，但尚未执行 10.5 的 platform 发布 |
| `macro_agent` | 2026-08-28 对同一两个来源真实 shadow 对账；最近网络瞬断后的有效窗口 1 个自然日、0 mismatch | Macro 装配/输出渲染及 runtime-macro 边界测试通过；`--no-llm` 不再隐式触发证据 LLM 判读 | `shadow`；两个区域 dataset 五维质量均 `passed`，但尚未执行 10.5 的 platform 发布 |
| `evidence_chain` | 2026-08-26 已对账 | Evidence/Chain 回归通过 | `shadow`；写入仍在 legacy memory |
| `chief_graph` | 不适用直接库双读：只消费上游 Agent 的 memory 产物 | Chief graph 端到端回归通过；统一输入边界与独立 shadow→legacy 回滚演练通过 | `shadow`；它不把 dossier、决策、交易或运行结果重分类为数据层输入，因此没有伪造“直接数据对账”记录 |
| `runtime_scheduler` | 不适用单一持久读模型：入口协调 runtime 日历、官方披露和采集 pipeline | 日常调度、PEAD 窗口、官方发布确认、新闻/研究采集及隔离 shadow→legacy 回滚演练通过 | `shadow`；已改为 `ats.data.runtime`、`ats.data.products` 和 `ats.data.pipelines.unstructured` 入口；尚未执行 10.5 的 platform 发布 |

本次 PEAD 验收：`test_structured_consumer_migration.py`、`test_consumer_cutover_records.py`、
`test_pead_data.py`、`test_pead_graph.py`、`test_pead_score.py` 与 `test_pead_report.py` 均通过；
覆盖 shadow 输入、单位/债务语义、CapEx/FCF 展示、失败回退、prep、score 与报告渲染。

## 发布门与下一步

`config/data/migration.yaml` 要求 consumer 至少产生 **1 个自然日内 1 次**成功对账记录，且没有
未解决 mismatch，才具备 `platform` 发布资格。重复检查仍会保留审计记录，但不以次数替代
coverage、时效和输出验收。

注意：`company_financials` 全局质量报告仍显示 MRVL 的 6 条既存跨源冲突（CapEx/营业利润，约
1.2%），它们不在 AMZN/MSFT/KLAC/TSM 的 10.1 发行人范围内，未被删除或忽略。它们会继续让
全局 source 发布检查失败，必须单独修复；PEAD 也尚未切到 platform。

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

PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data cutover-status \
  --consumer evidence_chain --target-db var/data.sqlite
```

第二条命令在尚无成功对账或存在 mismatch 时以退出码 `2` 表示“尚不可发布”，不是数据错误。条件满足后，先运行
`data release-check --consumer <ID> --mode platform`；只有 `ready=true` 才可使用
`data publish --consumer <ID> --mode platform --apply`。任意 mismatch 或运行异常都执行
`data rollback --consumer <ID> --mode legacy --apply`。

阶段 11（冻结 legacy 写入、删除旧实现及 schema）必须等待这些发布资格全部通过后才开始。
