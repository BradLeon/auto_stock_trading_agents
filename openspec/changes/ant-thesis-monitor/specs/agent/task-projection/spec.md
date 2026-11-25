# Spec Delta

## ADDED Requirements

### Requirement: 新增持仓论点监测投影
系统 SHALL 新增 `ThesisMonitor` 投影（角色 `thesis_monitor`），携带：标的、配置版本哈希、每个指标的读数、状态（正常、恶化、未覆盖）与依据、E/R/A 各条规则的结果、信号优先级的应用说明、是否复核点、是否处于加仓禁止窗口及原因、模型判读条目。它 SHALL 拒绝目标权重、数量、金额等仓位与数量类字段；规则结果只表达"哪条规则成立"，SHALL NOT 表达具体下单动作。

#### Scenario: 草稿含目标权重
- **WHEN** 一份 `ThesisMonitor` 草稿含 `target_weight`
- **THEN** 发布 SHALL 被拒绝

#### Scenario: 规则结果的表达
- **WHEN** E1 成立
- **THEN** 投影 SHALL 记录"E1 成立"及其依据读数，SHALL NOT 记录卖出数量或订单
