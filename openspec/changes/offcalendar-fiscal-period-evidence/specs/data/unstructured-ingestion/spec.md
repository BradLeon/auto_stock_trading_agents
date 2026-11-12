# Spec Delta

## ADDED Requirements

### Requirement: 非日历财年公司的目标财期必须按其财年口径解析
系统 SHALL 以公司登记的财年结束月，把报告期末日期换算成该公司的财年与季度，作为准入比对的目标财期。未登记时默认财年结束于 12 月。财期比对 SHALL 按财年与季度的数值比较，不比较原始标签字符串。

#### Scenario: ORCL 财年截至 5 月
- **WHEN** ORCL 报告期末为 2026-08-31，财年结束月登记为 5
- **THEN** 目标财期 SHALL 解析为 FY2027 Q1
- **AND** 文档声明的财期为 FY2027 Q1 时 SHALL 通过财期检查

#### Scenario: 标签写法不同但财期相同
- **WHEN** 文档写“2026 年第 3 季度”，事件财期为 FY2026 Q3
- **THEN** 财期检查 SHALL 通过

### Requirement: 未通过准入的文档不得进入模型抽取
证据抽取 SHALL 在发出任何模型请求前，确认文档版本已通过准入。未通过或被隔离的文档 SHALL 被跳过，记录跳过原因与隔离原因，且 SHALL NOT 发出模型请求。

#### Scenario: 被隔离的财报
- **WHEN** 一份财报文档因 `period_unresolved` 被隔离
- **THEN** 抽取运行 SHALL 记录 `skipped_unadmitted` 和隔离原因
- **AND** 该文档的模型请求数 SHALL 为零
