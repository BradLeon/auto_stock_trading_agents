# 数据层消费者切换状态

> 状态截至 2026-08-27。本文记录当前发布资格，不替代动态检查命令。

## 已完成的阶段 10 实现

- 结构化读取：PEAD 的 Consensus/财务数据和 Sector 的 Consensus/区域数列已通过
  `ats.data.products` / `ats.data.runtime` 的可回退路径读取；runtime 行情仍不入库。
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
| `pead_fundamentals` | 2026-08-27 已完成新旧双读；`data_consumer_cutover_records` 记录 1 个 EPS/货币口径 mismatch | PEAD 与结构化回归通过 | `shadow`；TSM Q1/Q2 FY2026 由官方 SEC 6-K/EX-99.1 入库（TIFRS，TWD），旧 yfinance ADR/报表口径不等价，未达 platform 发布门 |
| `sector_agent` | 2026-08-26 已对账、无 mismatch | Sector 回归通过 | 台湾/韩国区域数列已采集并可查询；仍为 `shadow`，待输出与 freshness 验收 |
| `evidence_chain` | 2026-08-26 已对账 | Evidence/Chain 回归通过 | `shadow`；写入仍在 legacy memory |
| `chief_graph` | 2026-08-26 已对账 | Chief 回归通过 | `shadow`；它消费 Agent 产物而非直接迁移决策状态 |

本次回归：消费者/迁移读取用例 20 项通过；PEAD、Sector、Evidence/Chain、Chief 用例 130 项通过。

## 发布门与下一步

`config/data/migration.yaml` 要求 consumer 至少产生 **1 个自然日内 1 次**成功对账记录，且没有
未解决 mismatch，才具备 `platform` 发布资格。重复检查仍会保留审计记录，但不以次数替代
coverage、时效和输出验收。

本轮实际补齐的原始采集（均保存 artifact 与血缘）如下：SEC Company Facts 为 AMZN 1,102 条、
MSFT 1,314 条、KLAC 1,022 条、TSM 128 条；台湾财政部电子零组件出口 307 条（至 2026-07）；
韩国 ECOS 半导体出口金额指数 19 条（至 2026-07）。TSM 在 SEC Company Facts 中目前只映射到
2024-12-31；该缺口已由 `company_disclosures` 的官方 Q1/Q2 FY2026 earnings release 补齐。该来源
只承担季度披露，不以 20-F 年报覆盖作为 PEAD 的验收要求。

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
