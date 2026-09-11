# ONS BICS AI 条件模块验收（OpenSpec Tasks 6）
验收日期：2026-09-09。来源为 ONS BICS 官方 dataset/workbook、workbook 内 questionnaire 链接，以及 ONS《Artificial intelligence in UK businesses: 2023 to 2026》官方 chart CSV。

## 主动发现

- 最新 BICS Wave 163 workbook 已发现并固定 SHA-256。
- parser 只检查当前 wave 的实际 AI 数据表，不再把 workbook 内历史 question log 误判为本期问卷。
- Wave 163 没有当前期 AI observations，结果为 `question_not_fielded`；不继承 Wave 159、不创建零值、不触发虚假 stale。
- questionnaire、release date、reference window、response rate 和 respondent count 从 workbook 首页提取。
- 问题全文、完整答案集合、routing 和 population universe 共同形成 regime fingerprint；只批准明确白名单问题，模糊或未知问题进入 drift 审核。

## Wave 159 隔离回填

Wave 159 官方 workbook 中的 AI time-series 表提供 12 个明确波次：92、98、105、111、117、123、129、135、141、147、153、159；深度问题只在 Wave 159 出现，因此按 snapshot 处理。

| 验收项 | 结果 |
|---|---:|
| observations | 5,081 |
| entities | 20 |
| artifacts | 3（workbook、Article Figure 1、Figure 2） |
| quarantined | 0 |
| 第二次采集 | `no_change`，5,081 unchanged |

Wave 159 全部企业口径的抽查值：采用至少一种 AI 技术 28.9%，平均采用技术数 1.585，extensive 16.3%，limited 58.2%，pilot 3.4%，过半员工日常使用 35.6%。这些值包含 0–9 人企业；Article 中约 35%/10% 等 headline 使用的是 10 人以上企业口径，不能混称。DataProduct 同时返回行业、规模和条件 universe，允许消费者选择同口径切片。

## 质量与解释

- workbook 比例从 0–1 原生 ratio 规范化为 percent；计数均值保留 count。
- `[c]` 抑制 cell 保存在 artifact 中但不变成零 observation。
- extensive/workforce 等互斥答案允许 0.25 个百分点官方舍入误差。
- workbook 与 Article Figure 1/2 对 10 人以上 headline 做自动对账；冲突时剔除相关 metric 的默认趋势并保留双 artifact failure lineage。
- BICS 是自愿调查、官方开发中统计，且排除若干行业；结果仅作为 UK supplement。
- ONS 与 BTOS 不默认相减、排名或融合。只有一个同 regime 深度 wave 时返回 `insufficient_history`。

专项测试：23 passed；真实连续双跑验证 observation 和 artifact 幂等。
