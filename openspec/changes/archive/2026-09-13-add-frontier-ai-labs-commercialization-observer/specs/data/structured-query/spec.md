## ADDED Requirements

### Requirement: 查询必须按收入语义返回可比 Frontier AI Labs 序列
结构化查询 SHALL 支持按实验室、可选产品、计量口径、观察身份、来源、参考期间、known-at/as-of 和质量状态筛选收入 observations。返回结果 SHALL 包含币种、值、period、披露日、source citation、observation/artifact IDs、revision、methodology regime、comparability status 和 lineage；默认不得将不同收入口径聚合到同一序列。

#### Scenario: 查询 OpenAI 与 Anthropic 可比曲线
- **WHEN** 消费者请求两家公司相同 metric identity 与 observation identity 的历史序列
- **THEN** 查询 SHALL 返回按真实参考期间排序的离散观察和 period gaps
- **AND** SHALL NOT 自动插值、换算计量口径或使用较晚披露回填更早 as-of

### Requirement: 收入聚合必须保留来源与观察身份
收入 DataProduct MAY 计算同一可比序列的最新值、绝对变化、百分比变化和年化运行率趋势，但每个派生结果 SHALL 绑定底层 observation IDs、来源组合、公式和 derivation version。若来源或口径冲突使派生不成立，查询 SHALL 返回 `not_comparable` 及原因，而不是选择一个无披露的合成值。

#### Scenario: 计算收入变化
- **WHEN** 同一公司至少有两个可比、通过质量门的离散期间
- **THEN** DataProduct SHALL 返回变化值、公式和底层 observation IDs
- **AND** 报告、CSV/JSON 与图表 SHALL 能复用同一派生结果

