## ADDED Requirements

### Requirement: L1 Observer 提供 Ramp 付费企业采用补充段落

现有 L1 固定命题及 BTOS/RPS/Anthropic 三轴 SHALL 保持不变。若 Ramp slice 可用，Observer SHALL 在三轴总览之后增加独立的 Ramp supplemental signal，覆盖已注册的 `adoption_overall`、`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall` 和 `model_market_share_overall`；企业规模与地理 slice 首版不发布。该段落 SHALL 不参与既有三轴整体状态计算，除非另一个独立变更显式修改 claim contract。

#### Scenario: Ramp 可用

- **WHEN** Ramp Overall 和至少一个历史月份通过质量门
- **THEN** L1 报告 SHALL 展示 Ramp 最新值、月变化、真实期间和 source scope
- **AND** 三轴 judgement SHALL 与未接入 Ramp 时保持一致

#### Scenario: Ramp 不可用

- **WHEN** API 未授权且网页导出不可读或质量门失败
- **THEN** Observer SHALL 保留三轴输出并增加 `ramp_unavailable` warning
- **AND** SHALL 不把缺失 Ramp 当作零采用或 evidence conflict

### Requirement: Ramp 方法卡必须披露网络样本和付费交易定义

L1 专属方法卡 SHALL 说明 Ramp 统计主体为 Ramp 网络中的企业 cohort，采用判定为当月 AI 产品/服务正向交易，覆盖 corporate card、invoice 和 ACH 等 Ramp 处理的付款；并披露 NAICS 分组、Token Spend Management 独立 cohort、免费工具/个人账户漏计和 Ramp 客户选择偏差，同时注明企业规模与地理 slice 未纳入首版。Ramp 公司收入、客户数或估值只能作为来源背景，不能进入 adoption 指标。

#### Scenario: 读者查看 Ramp 采用率

- **WHEN** 报告显示 Ramp 56.13% adoption
- **THEN** 方法卡 SHALL 同时显示月份、Ramp 企业分母定义和 paid-transaction 规则
- **AND** SHALL 禁止读者将其解读为全美国企业或员工采用率

### Requirement: Ramp 固定第四个追踪命题与五个可视化 scope

L1 Observer SHALL 将 Ramp 固定为独立的第四个补充命题：
“AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？”
该命题 SHALL 有独立的 claim id、方法卡、可用性状态和文字化结论；不得只在图表标题或 Agent context 中隐含表达。
首版 SHALL 逐一输出且一一对应以下五个 scope 的历史序列和图表：`adoption_overall`、
`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall`、
`model_market_share_overall`。若企业规模 slice 尚未进入采集白名单，报告 SHALL 明确将“企业规模”标为待观测缺口，
不得用 Overall 或行业值替代。

#### Scenario: Ramp 五个 scope 可用

- **WHEN** 五个 scope 均有至少一个通过质量门的官方导出
- **THEN** 报告 SHALL 生成五张同名 scope 图表、五张数据表和五个 sidecar
- **AND** adoption、spend 与 model share 图表 SHALL 优先绘制所有可见历史期间，而不是只绘制最新月份
- **AND** 报告 SHALL 在图表前给出 Ramp adoption 的最新值、历史期数、变化和 source-native 统计含义

#### Scenario: Ramp 历史不足或企业规模未发布

- **WHEN** 某 scope 只有一个期间，或企业规模不在首版白名单
- **THEN** 报告 SHALL 标记 `insufficient_history`/`not_published`，不得伪造趋势或补值
