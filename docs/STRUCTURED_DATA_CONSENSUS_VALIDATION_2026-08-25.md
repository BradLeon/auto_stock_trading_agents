# 市场 Consensus 专项验收（2026-08-25）

> 后续状态：本报告记录阶段 8 当时的 `shadow` 决策；完成消费者重放与 Workflow 回归后，阶段 12 已将 PEAD / Sector Consensus 切至 `platform`。以消费者验收和机器目录为当前状态准绳。

## 决策

真实源专项通过，PEAD Consensus 默认读取模式从 `legacy` 进入 `shadow`。`shadow` 仍向 Workflow 返回旧适配器结果，同时采集、持久化并对账新路径，因此不会在本阶段改变 PEAD 的业务输入。

## 设计结果

- yfinance `0q` 不再作为期间入库；系统优先由最新已报公司财期推导下一财季，不可推导时才绑定具体 earnings event。两者都不存在时，预测不发布。
- 保存 reported EPS actual、EPS/收入均值及区间、目标价、评级分布、近 120 天最多 8 条评级变更。`target_current` 是实时股价，仍属于 runtime/excluded，不持久化。
- Yahoo 未提供这些表格的可靠发布时间，因此 `published_at` 保持空，首次可见时间严格等于真实抓取的 `known_at`。
- 每次抓取都保存独立 snapshot；即使数值未变，也保留新的可见时点，以支持严格 `as_of` 回放。

## 真实源结果

| 实体 | 最新 snapshot 记录 | 口径数 | 具体目标财期 | 旧 PEAD 标量对账 |
|---|---:|---:|---|---|
| AMZN | 42 | 17 | 2026-09-30 | 15/15 一致 |
| MSFT | 42 | 17 | 2026-09-30 | 15/15 一致 |
| KLAC | 42 | 17 | 2026-09-30 | 15/15 一致 |
| TSM | 42 | 17 | 2026-09-30 | 15/15 一致 |

质量门结果：

- 5 次真实采集均 `succeeded`，0 条 quarantined；MSFT 真实抓取两次形成两个不同 `known_at`。
- 早期 `as_of` 只返回第一次 MSFT EPS 4.72455，不会读到第二次快照；future leakage 为 0。
- 60 个可比标量与旧 PEAD dict 完全一致，mismatch 为 0。
- 4 个实体的必需覆盖、预测区间、目标期和 168 小时新鲜度检查均通过。
- v1 真实测试曾发现 pandas/numpy scalar 无法 JSON 序列化；v2 修复后通过，v3 进一步将发布的评级变更限定为旧契约的 120 天/8 条。

## 在哪里检查

- 机器报告：`var/structured_consensus_validation_20260825_v3/validation.json`
- 隔离数据库：`var/structured_consensus_validation_20260825_v3/consensus-smoke.sqlite`
- artifact 根目录：`var/structured_consensus_validation_20260825_v3/artifacts/`
- AMZN snapshot：`artifacts/ac/acb56133e99b40da0f78cf6e7663057c0adf9425373b574695a1866e47d055b3.json`
- MSFT 第一次 snapshot：`artifacts/4b/4b0f2fb0386a3b37d2d3dd9f920f1b9613122184e0726d6049ee67c3974c1495.json`
- KLAC snapshot：`artifacts/0b/0b1b27af8b8ddb95cfb552e212b3c2b5f09dd6c2ca01bba1cbdc2fb315110eef.json`
- TSM snapshot：`artifacts/3e/3ee92ca63babdc25aa95f095bd1a068ff04610307c9d774109e93538f46dd8a7.json`
- MSFT 第二次 snapshot：`artifacts/9a/9a790f1e0653b7785276b590cda3621a657d59bbc2833fe6f6a958409d3318ef.json`

以上文件位于 `var/`（Git 忽略）且与生产数据隔离。最终检查以 `_v3` 为准。
