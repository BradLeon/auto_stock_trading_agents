# Spec Delta

## ADDED Requirements

### Requirement: 风险命题按独立来源计数
产业链配置 SHALL 支持 `kind: risk` 命题，只用于整层或跨层的共同风险。风险命题的证据 SHALL 按独立来源计数：同一出版方或同一原发报道的转载 SHALL 计为一个来源。风险命题被判为"承压"或"兑现" SHALL 至少有 2 个独立来源，且其中至少 1 个为一手或权威来源（官方文件、监管或立法机构文本、公司公告、主流通讯社原发）。经营命题（common / relative）现有的独立公司说话人门槛 SHALL 不变。公司特有的风险 SHALL NOT 写成风险命题。

#### Scenario: 只有转载
- **WHEN** 5 篇报道都转载同一篇通讯社原文
- **THEN** 它们 SHALL 计为 1 个独立来源，风险命题 SHALL NOT 因此被判为"承压"

#### Scenario: 两个独立来源含一个权威来源
- **WHEN** 一份立法草案文本与一篇独立媒体报道都指向出口限制收紧
- **THEN** 出口准入风险命题 SHALL 可被判为"承压"

#### Scenario: 经营命题不受影响
- **WHEN** 一条经营命题只有 2 家公司发声
- **THEN** 其判读 SHALL 仍按既有的独立公司说话人门槛处理
