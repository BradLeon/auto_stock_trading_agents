# Spec Delta

## ADDED Requirements

### Requirement: 新增重大事件评审投影并扩展既有投影
系统 SHALL 新增 `FundamentalMaterialReview` 投影（角色 `fundamental_material_review`），携带：标的、认领的成分编号、论点状态、命中的风险项或证伪条件、`direction`、`magnitude`、`confidence`、预期影响类别、待核验事项、有效期截止、对成分重要性的复核意见；SHALL 拒绝动作与数量类字段；无论点基线时 `confidence` SHALL 不超过 0.3。系统 SHALL 新增 `FundamentalRiskRegister` 投影（角色 `fundamental_risk_register`），携带标的与至少一条公司风险项，每项 SHALL 带证伪条件，同样拒绝动作与数量类字段。既有投影 SHALL 新增：`InformationBrief.event_components`（每个成分含重要性与方向）；`LayerAnalysis` 的风险命题状态、论点外冲击、补认领与下调建议；`SectorAllocation` 每层的下调原因。所有新增字段 SHALL 在 v1 中可省略，既有投影 SHALL 无需重写即通过校验。

#### Scenario: 旧投影校验
- **WHEN** 读取一份上线前发布、没有新字段的 `LayerAnalysis` 投影
- **THEN** 它 SHALL 通过 v1 校验

#### Scenario: 风险项缺证伪条件
- **WHEN** 一份 `FundamentalRiskRegister` 草稿的某条风险项没有证伪条件
- **THEN** 发布 SHALL 被拒绝

#### Scenario: 重大事件评审含仓位字段
- **WHEN** 一份 `FundamentalMaterialReview` 草稿含 `target_weight`
- **THEN** 发布 SHALL 被拒绝
