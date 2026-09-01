# 结构化消费者迁移与重放验收（2026-08-25）

> 变更：`build-structured-data-foundation`
> 环境：既有真实源专项数据库的隔离副本；未写生产数据库
> 机器结果：`var/structured_consumer_validation_20260825/validation.json`

## 结论

消费者兼容、持久/运行时边界和离线重放专项通过。达到质量门的 Chain 区域序列、PEAD Consensus 和 Sector Consensus 切换到 `platform`；财务数据因五维报告仍有核心覆盖与跨源冲突失败，PEAD fundamentals 保持 `legacy`。旧接口与回滚开关继续保留。

这次验收包含两组互补证据：

1. 复制真实源隔离库后的消费者数据专项，验证真实持久输入能够组装旧 DTO、记录 snapshot 并重放。
2. PEAD、Sector、Chain 的完整确定性回归组合，共 226 项通过，验证图、评分、报告、证据和消费者调用契约没有被重构破坏。

## 真实持久输入结果

| 消费面 | 实体 / Dataset | 结果 |
|---|---|---|
| PEAD fundamentals DTO | AMZN、MSFT、KLAC | 均组装 8 条旧 statement line；每实体 24 个持久输入 observation |
| PEAD fundamentals DTO | TSM | 组装 7 条旧 statement line；21 个持久输入 observation；缺失债务不填 0 |
| PEAD / Sector Consensus DTO | AMZN、MSFT、KLAC、TSM | 每实体最新 snapshot 42 条记录，目标期均为 `2026-09-30`，旧 dict 15 个非空标量 |
| Chain 台湾出口 | `regional_tw_exports` | 20 个水平值、20 个版本化派生结果 |
| Chain 韩国出口 | `regional_kr_exports` | 20 个水平值、20 个版本化派生结果 |

每组输出都创建 structured snapshot manifest 并立即离线重放；重放 observation 数量和 ID 与创建时一致。

## 不变性与边界测试

- 创建财务 manifest 后追加一个更新 vintage，再改变 `company_financials` 来源优先级，旧 manifest 的 observation ID 与值不变。
- 创建区域派生 manifest 后注册同一公式的 `v99`，旧 manifest 仍记录并重放创建时的输入与公式版本。
- 将持久财务输入与模拟 ticker price、option chain 组合后创建 manifest，重放只包含持久 observation，运行时市场输入为 0 条。
- 财务平台组装保留 `FundamentalData / FinancialStatements / StatementMetric` 形状；Consensus 平台组装保留固定键 dict、`None` 和 `[]` 缺失语义；Chain 保留 `SeriesPoint`。

## 切换与回滚决策

| 消费者 | 默认模式 | 理由 | 回滚 |
|---|---|---|---|
| `chain_regional` | `platform` | 台湾/韩国真实专项、旧值对账、切换与回滚演练通过 | `ATS_STRUCTURED_CHAIN_REGIONAL_MODE=legacy` |
| `pead_consensus` | `platform` | 真实专项 60/60 可比标量一致、两个可见时点无前视、消费者 DTO smoke 通过 | `ATS_STRUCTURED_PEAD_CONSENSUS_MODE=legacy` |
| `sector_consensus` | `platform` | 与 PEAD 共用持久 snapshot，但独立消费者开关和 manifest | `ATS_STRUCTURED_SECTOR_CONSENSUS_MODE=legacy` |
| `pead_fundamentals` | `legacy` | 平台可组装 DTO，但财务质量总门仍失败 | 可单次用 `shadow`；不切 platform |
| `sector_constituent_financials`（当时名为 `sector_fundamentals`） | `legacy` | 成分股财报指标现复用 PEAD 报表包；估值、beta 仍属于 runtime，不能用季度持久事实静默替代 | 保持现状 |

来源开关使用 `ATS_STRUCTURED_SOURCE_<SOURCE_ID>_MODE`，消费者开关使用 `ATS_STRUCTURED_<CONSUMER>_MODE`，单消费者/单来源组合还可使用 `ATS_STRUCTURED_<CONSUMER>_<SOURCE_ID>_MODE`。回退只影响相应消费者，不删除新表、artifact 或历史 manifest。

## 尚未解除的门

- `company_financials` 继续保留官方与 defeatbeta 并列值及冲突，不通过平均隐藏；在质量门达标前不切 PEAD fundamentals。
- TrendForce 本轮没有独立真实专项，仍显示 `no_coverage/no_run`，不纳入 Chain 区域切换结论。
- 这里验证的是结构化持久输入重放；即时价格、期权和 Greeks 按设计不可离线重放，其审计仍由 dossier / Journal 承担。
