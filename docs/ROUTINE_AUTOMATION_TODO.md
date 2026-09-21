# 例行任务暂停—恢复清单

本清单是生产调度的唯一对照表。每次只恢复一个开关：先在非交易模式验证输入和输出，
再重载 `com.ats.schedule`。不要用「恢复每日级联」作为捷径；每日级联中的阶段必须逐项
恢复。暂停不会删除历史数据、报告、检查点或已有订单。

当前开关位于 `config/settings.yaml` 的 `schedule.daily_stages` 与 `schedule.jobs`。
缺少开关的旧部署默认开启，以维持向后兼容；本仓库的生产配置显式列出当前状态。

| 状态 | 配置项 | 例行时间 | 输入 → 输出 | 恢复前要完成的优化与验收 |
|---|---|---:|---|---|
| ✅ 运行 | `daily_stages.pead_event_triggers` | 交易日 10:30 ET | `events.yaml` 的 `pead:SYM` → 标的 monitor | 核对日历覆盖和 PEAD targets；运行 monitor 的非交易验证。 |
| ⏸ 暂停 | `daily_stages.macro_sector_event_triggers` | 交易日 10:30 ET | `events.yaml` 的 macro/sector → 对应复盘 | 明确事件清单、输入数据时点、报告消费者与失败告警。 |
| ⏸ 暂停 | `daily_stages.news_backfill` | 交易日 10:30 ET | Yahoo 新闻 → 共享新闻库 | 明确标的覆盖、去重规则、陈旧数据策略和下游消费者。 |
| ✅ 运行 | `daily_stages.pead_daily` | 交易日 10:30 ET | 研究源、财报日历 → PEAD 研究/monitor/prep | 核对研究源、PEAD targets 与财报前准备产物。 |
| ⏸ 暂停 | `daily_stages.technical_daily` | 交易日 10:30 ET | 价格数据 → 技术评分/报告 | 固化标的池、计算指标、报告格式与实际消费者。 |
| ⏸ 暂停 | `daily_stages.intel_digest` | 交易日 10:30 ET | 新闻/研究/上下文 → 情报摘要与推送 | 定义入选阈值、摘要模板、推送对象和静默条件。 |
| ⏸ 暂停 | `daily_stages.performance_snapshot` | 交易日 10:30 ET | IBKR 组合 → 净值/盈亏快照 | 验证 IBKR 可用性、快照口径、失败补跑与存储保留期。 |
| ⏸ 暂停 | `daily_stages.perf_risk_digest` | 交易日 10:30 ET | 组合/规则 → 风控快照与告警 | 明确风险阈值、告警通道、去重与静默窗口。 |
| ⏸ 暂停 | `daily_stages.journal_marks` | 交易日 10:30 ET | 预测/价格 → 评分与账本 | 固化预测 horizon、价格源、补跑策略和账本使用者。 |
| ⏸ 暂停 | `daily_stages.chief_daily` | 交易日 10:30 ET | 全部上游产物 → 非 PEAD Chief 决策/审批 | 确认允许的信号、零决策行为、审批策略和实盘边界。 |
| ✅ 运行 | `jobs.pead_score_bmo` | 交易日 11:00 ET | 已发布盘前财报 → PEAD 评分/Chief 审批 | 维持现有人工审批、评分账本与实盘安全开关。 |
| ✅ 运行 | `jobs.pead_score_amc` | 交易日 20:00 ET | 已发布盘后财报 → PEAD 评分/Chief 审批 | 维持隔夜限价逻辑、人工审批和实盘安全开关。 |
| ⏸ 暂停 | `jobs.pead_observe_window` | 随 PEAD 评分窗口 | 观察名单/目标财报 → 产业链证据 | 明确名单、证据质量门槛、成本上限、证据消费者和报告节奏。手动 `ats evidence observe` 不受影响。 |
| ✅ 运行 | `jobs.factset_weekly_ingest` | 周六 08:10 上海时间 | FactSet Earnings Insight → 受治理数据快照 | 每周核对来源版本、产物 hash、时效与下游读取结果。 |
| ⏸ 暂停 | `jobs.weekly_review` | 周六 08:50 上海时间 | 宏观/行业/证据 → 周报与截面排序 | 定义输入快照、报告模板、PEAD 消费边界、失败后的补跑方式。 |
| ⏸ 暂停 | `jobs.journal_reconcile` | 交易日 16:10 ET | 当日 IBKR 成交 → 交易记录回填 | 固化成交口径、当天缺失处理、Flex 补历史流程与验收对账。 |

## 恢复操作

1. 完成本表对应行的优化与非交易验证，并记录输入样本和预期输出。
2. 将该配置项设为 `true`；不要变更其他暂停项。
3. 运行该任务的安全验证，检查输出、日志和失败告警。
4. 重载 `com.ats.schedule`，确认日志只新增该任务；`com.ats.serve` 保持运行。
5. 在本表相应行标记已恢复，并写明验证日期与证据链接。

## 当前运行面

`com.ats.schedule` 应注册：`daily_cycle`、`factset_weekly_ingest`、
`pead_score_bmo`、`pead_score_amc`。`com.ats.serve` 是飞书审批回调服务，不是定时
任务，必须保持运行以完成 PEAD 的人工审批。
