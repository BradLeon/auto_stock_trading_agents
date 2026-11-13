# Spec Delta

## MODIFIED Requirements

### Requirement: L1 Observer 提供 Ramp 付费企业采用补充段落
现有 L1 固定命题及 BTOS/RPS/Anthropic 三轴 SHALL 保持不变。若 Ramp slice 可用，Observer SHALL 在三轴总览之后增加独立的 Ramp supplemental signal，覆盖 v2 已注册的 `adoption_overall`、`adoption_overall_models`、`adoption_sector`、`adoption_business_size`、`spend_per_employee_overall`、`token_spend_share_by_maker` 和 `token_spend_share_by_model`，且只读 `ramp_ai_index_v2` regime；地理 slice 不发布。该段落 SHALL 不参与既有三轴整体状态计算，除非另一个独立变更显式修改 claim contract。

#### Scenario: Ramp 可用
- **WHEN** Ramp Overall 和至少一个历史月份通过质量门
- **THEN** L1 报告 SHALL 展示 Ramp 最新值、月变化、真实期间和 source scope
- **AND** 三轴 judgement SHALL 与未接入 Ramp 时保持一致

#### Scenario: Ramp 不可用
- **WHEN** FRED 部分与网页部分都没有通过质量门的 v2 数据
- **THEN** Observer SHALL 保留三轴输出并增加 `ramp_unavailable` warning
- **AND** SHALL 不把缺失 Ramp 当作零采用或 evidence conflict

#### Scenario: 只有网页部分失败
- **WHEN** FRED 部分有最新 v2 数据，网页部分处于 `export_unreadable` 或 `export_stale`
- **THEN** Observer SHALL 照常展示总体、供应商、行业与规模 adoption
- **AND** SHALL 对人均支出与 token 花费份额标注不可用原因，不沿用过期值冒充最新

### Requirement: Ramp 方法卡必须披露网络样本和付费交易定义
L1 专属方法卡 SHALL 说明 Ramp 统计主体为 Ramp 网络中的企业 cohort，采用判定为当月 AI 产品/服务正向交易，覆盖 corporate card、invoice 和 ACH 等 Ramp 处理的付款；并披露 NAICS 分组、企业规模分档、Token Spend Management 独立 cohort、免费工具/个人账户漏计和 Ramp 客户选择偏差，同时注明地理 slice 未纳入、数据口径为 `ramp_ai_index_v2`。Ramp 公司收入、客户数或估值只能作为来源背景，不能进入 adoption 指标。

#### Scenario: 读者查看 Ramp 采用率
- **WHEN** 报告显示 Ramp 56.80% adoption
- **THEN** 方法卡 SHALL 同时显示月份、Ramp 企业分母定义和 paid-transaction 规则
- **AND** SHALL 禁止读者将其解读为全美国企业或员工采用率

## REMOVED Requirements

### Requirement: Ramp 固定第四个追踪命题与五个可视化 scope
**Reason**: Ramp 页面改版后，首版五个 scope 中的 `model_market_share_overall` 已停止更新；企业规模已经可以从 FRED 取得。“五个 scope”的固定清单不再成立。
**Migration**: 由新增要求“Ramp 固定第四个追踪命题与 v2 可视化 scope”取代：命题文本与 claim id 不变，scope 清单改为 v2 七个。

## ADDED Requirements

### Requirement: Ramp 固定第四个追踪命题与 v2 可视化 scope
L1 Observer SHALL 将 Ramp 固定为独立的第四个补充命题：“AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？”该命题 SHALL 有独立的 claim id、方法卡、可用性状态和文字化结论。报告 SHALL 逐一输出 v2 七个 scope 的历史序列和图表；周度 token 份额 SHALL 以周为横轴，不与月度 adoption 共轴。

#### Scenario: Ramp v2 scope 可用
- **WHEN** v2 七个 scope 均有至少一个通过质量门的期间
- **THEN** 报告 SHALL 生成七张同名 scope 图表、数据表和 sidecar
- **AND** 图表 SHALL 绘制 v2 全部可见历史期间，不混入 v1 期间
- **AND** 报告 SHALL 在图表前给出 Ramp adoption 的最新值、历史期数、变化和 source-native 统计含义

#### Scenario: Ramp 历史不足或某 scope 不可用
- **WHEN** 某 scope 只有一个期间，或其所属部分（FRED 或网页）不可用
- **THEN** 报告 SHALL 标记 `insufficient_history` 或带原因的不可用状态，不得伪造趋势或补值
