# Spec Delta

## ADDED Requirements

### Requirement: 私有实体上市后证人身份统一
登记为私有实体的命题证人上市后，系统 SHALL 以上市实体为规范身份，原私有实体名作为别名。命题证人、结构化实体登记与治理型证据观察器写入的观测 SHALL 全部归到规范身份下；同一经济主体 SHALL NOT 同时以两个身份出现在命题的证人计数或证据簇中。统一前后，既有观测的命题判读 SHALL 可回归核对。

#### Scenario: Anthropic 上市为 ANT
- **WHEN** ANT 登记为规范实体、ANTHROPIC 为其别名
- **THEN** 来自 Sacra 的收入观测与来自 ANT 注册文件的观测 SHALL 归到同一证人 ANT
- **AND** L1 商业化命题的证人计数 SHALL 把它们计为一个说话人

#### Scenario: 统一后的回归核对
- **WHEN** 身份统一完成后重跑 L1 三个证据观察器
- **THEN** 除证人身份外，各观察器的状态与读数 SHALL 与统一前一致，差异 SHALL 逐项列出
