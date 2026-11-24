# Spec Delta

## ADDED Requirements

### Requirement: 风险与冲击只能下调层使用率
板块配置 SHALL 只依据层次分析的下调建议向下调整层的配置结论与使用率，SHALL NOT 依据风险命题或论点外冲击上调。由 `material_development` 触发的临时复盘，单次 SHALL 最多下调一档；周六例行复盘 SHALL 不受档数限制。层的 `weight_cap` 与风控读取的静态上限 SHALL 不变。每次下调 SHALL 在该层的配置结果中记录下调原因，并引用风险命题或冲击编号。

#### Scenario: 临时复盘建议下调两档
- **WHEN** 临时复盘中 L5 层次分析建议从"超配"下调两档
- **THEN** 板块配置 SHALL 只下调一档至"标配"，并记录被截断的建议

#### Scenario: 周六例行复盘
- **WHEN** 周六例行复盘中层次分析建议下调两档且理由成立
- **THEN** 板块配置 SHALL 可下调两档

#### Scenario: 风险解除
- **WHEN** 风险命题由"承压"变为"解除"
- **THEN** 临时复盘 SHALL NOT 据此上调；恢复配置 SHALL 由例行复盘按经营命题判断
