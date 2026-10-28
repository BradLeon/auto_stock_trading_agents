## Purpose

将基本面/PEAD 研究明确拆分为例行预期更新和事件后现实差评估，使财报前基线、Surprise Scorecard、指引和建议均可版本化与追溯。

## ADDED Requirements

### Requirement: Fundamental 必须提供例行预期更新模式

例行模式 SHALL 使用有效 InformationBrief、共享的公司数据、Consensus 和产业链中性事实，更新财报前预期、市场隐含预期、核心叙事、关键 KPI 和可证伪条件。

#### Scenario: 新信息确认原有叙事

- **WHEN** 新 InformationBrief 为现有基线提供独立确认证据
- **THEN** Fundamental SHALL 生成新 `FundamentalExpectationUpdate`
- **AND** 新版本 SHALL 保留原基线引用、新证据引用和变化解释

#### Scenario: 没有实质变化

- **WHEN** 新信息未改变任何预期、叙事或可证伪条件
- **THEN** 例行模式 SHALL 记录已评估但无实质变化
- **AND** SHALL NOT 伪造新的预期差或交易信号

### Requirement: Fundamental 必须提供事件后 PEAD 模式

事件模式 SHALL 对已确认公司事件的 actuals、公告、指引和电话会与冻结的财报前基线进行对比，并输出 Surprise Scorecard、指引变化、叙事转变和投资建议。

#### Scenario: 财报 actuals 已发布

- **WHEN** 财报期间、实体和文档完整性均通过准入
- **THEN** Fundamental SHALL 分别计算 actuals 相对冻结基线、Consensus 和市场隐含预期的差异
- **AND** SHALL 为每个 Scorecard 项保留数据口径和来源引用

### Requirement: 财报前基线必须在事件 cutoff 冻结

事件模式 SHALL 引用事件 cutoff 之前最后一个有效的预期基线。事件公布后出现的信息 SHALL NOT 被回写进财报前基线。

#### Scenario: 公布后 InformationBrief 修正了市场预期描述

- **WHEN** 新 InformationBrief 的事件或发布时间晚于财报 cutoff
- **THEN** 它 SHALL 可作为事件后解释材料
- **AND** SHALL NOT 改变用于 surprise 比较的冻结基线

### Requirement: 迟到材料必须生成新版本

电话会、完整指引或经验证 actuals 晚于首次评估到达时，系统 SHALL 生成引用新材料的新 `FundamentalEventReview`，SHALL NOT 覆盖首次版本。

#### Scenario: 电话会在初版 Scorecard 后到达

- **WHEN** 初版 EventReview 已发布后完整电话会通过准入
- **THEN** 系统 SHALL 生成新版本并链接到初版
- **AND** 两个版本 SHALL 共享同一冻结财报前基线

### Requirement: Fundamental 必须遵守观点隔离

Fundamental SHALL 只读取 Information Analyst 的观点投影及共享 Data Products，SHALL NOT 读取 Sector、Macro、Technical 或其他 Fundamental 运行的投资观点。产业链上下游信号 SHALL 以中性事实或数据产品输入。

#### Scenario: 运行上下文包含 Sector 配置结论

- **WHEN** Fundamental 输入组装检测到 SectorAllocation 或 MacroReview
- **THEN** 任务 SHALL 以跨角色依赖违规失败
- **AND** SHALL NOT 将这些观点渲染进 Fundamental 报告

### Requirement: Fundamental 输出不得绕过组合决策和风控

Fundamental 可产生投资建议和非执行性 sizing hint，但 SHALL NOT 对组合执行最终 pre-trade risk gate，SHALL NOT 直接产生券商订单或 Boss 审批请求。

#### Scenario: EventReview 建议增持

- **WHEN** FundamentalEventReview 建议增持某标的
- **THEN** 该建议 SHALL 作为 Chief 的分析输入
- **AND** 任何真实订单 SHALL 仍必须由 Chief 决策、Risk 批准和 Boss 批准
