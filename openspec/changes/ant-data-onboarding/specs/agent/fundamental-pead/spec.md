# Spec Delta

## ADDED Requirements

### Requirement: 新上市 target 首份财报前以未覆盖状态参与
没有任何已发布季度财报的新上市 target，基本面 SHALL 发布明确的"未覆盖"状态，原因为"无财报历史"，并注明预计首份财报日期（若已知）。该状态 SHALL 按既有的基本面未覆盖规则参与决策：不阻断周期，但该标的只允许持有或减仓。首份财报入库并建立基线后，状态 SHALL 自动转为正常覆盖。

#### Scenario: ANT 首份 10-Q 之前
- **WHEN** ANT 在可交易名单中，但尚无任何季度财报
- **THEN** 基本面 SHALL 为 ANT 发布"未覆盖：无财报历史"
- **AND** 主理人周期 SHALL 照常进入，ANT 只允许持有或减仓

#### Scenario: 首份财报入库
- **WHEN** ANT 的首份 10-Q 与财报材料入库并建立基线
- **THEN** ANT 的基本面状态 SHALL 转为正常覆盖，不需要人工切换
