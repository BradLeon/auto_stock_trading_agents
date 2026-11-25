# Spec Delta

## ADDED Requirements

### Requirement: 新上市发行人可以人工补充财季标签
自动来源给出财报日期但没有财年与财季标签时，人工入口 SHALL 允许为该事件补充财季标签，并按人工覆盖规则记录操作者、依据与旧版本。补充财季标签之前，该事件 SHALL 保持计划状态，SHALL NOT 被标记为已发布，也 SHALL NOT 触发财报事件分析。

#### Scenario: Finnhub 只给日期
- **WHEN** Finnhub 给出 ANT 的首份财报日期，但没有财年与财季
- **THEN** 日历 SHALL 发布该计划事件并标注"缺财季标签"
- **AND** 操作者补充财季标签后，日历 SHALL 发布标记为人工覆盖的新版本，后续发布核对按该标签进行
