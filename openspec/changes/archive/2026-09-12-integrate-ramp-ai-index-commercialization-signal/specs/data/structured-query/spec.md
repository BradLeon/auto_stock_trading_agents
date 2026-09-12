## ADDED Requirements

### Requirement: 查询层支持 Ramp source scope 与非融合比较

结构化查询 SHALL 暴露 Ramp adoption、spend 和 model market share 的 source scope、统计主体、分母、技术范围、期间和 methodology regime。默认跨源查询 SHALL 仅返回并列事实与 comparability diagnostics；不得将 Ramp、BTOS、RPS 或 Anthropic 的比例加权、求平均、相减或补值。

#### Scenario: 请求 Ramp 和 BTOS 并列

- **WHEN** 消费者显式请求同一期间 Ramp adoption 与 BTOS adoption
- **THEN** 查询 SHALL 返回两个独立 series、各自分母和 source scope
- **AND** SHALL 标记为 directional/contextual comparison，而非 level-comparable

#### Scenario: 未指定 Ramp scope

- **WHEN** 消费者请求“Ramp AI adoption”但未指定 `adoption_overall`、`adoption_overall_models` 或 `adoption_sector`
- **THEN** 查询 SHALL 返回 ambiguous-dimension failure 或要求选择 scope
- **AND** SHALL 不从多个 chart slice 自动拼接结果

### Requirement: Ramp 网页导出结果可形成离线快照与图表输入

查询结果 SHALL 能固定网页导出 payload 的 artifact ID、observation IDs、chart slug、rows hash、period、as_of 和 derivation version。图表与 Agent context SHALL 使用该快照，不在渲染或推理时再次访问 Ramp 页面。

#### Scenario: 重放 Ramp 月度报告

- **WHEN** 后续网页更新了 historical table 或模型说明
- **THEN** 以旧 manifest 重放 SHALL 返回旧 payload 和旧派生结果
- **AND** SHALL 不重新点击网页或使用当前最新值
