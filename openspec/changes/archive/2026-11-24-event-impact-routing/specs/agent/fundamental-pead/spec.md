# Spec Delta

## ADDED Requirements

### Requirement: 每个 target 维护公司风险项
基本面 SHALL 为每个 PEAD target 维护一份公司风险项清单（例如关键人、治理、客户集中度、监管个案），每项附证伪条件。清单 SHALL 在上线时初始化，并在每次建立财报基线时刷新。风险项 SHALL 只描述公司特有的风险，整层或跨层的共同风险 SHALL NOT 写入。模型 SHALL NOT 修改配置文件；新增风险项以投影发布并留痕。

#### Scenario: 财报基线刷新
- **WHEN** NVDA 建立新一期财报基线
- **THEN** 基本面 SHALL 同时刷新 NVDA 的公司风险项，并在投影中保留上一版的引用

### Requirement: 重大事件评审只评估公司特有部分
收到公司成分的 `material_development` 事件时，基本面 SHALL 产出一份重大事件评审，回答：命中的论点、风险项或证伪条件；论点状态（稳定 / 承压 / 被证伪）；预期影响（改季度数字，或只改风险溢价）；待核验事项；以及 `direction`、`magnitude`、`confidence`。评审 SHALL 引用其认领的成分编号，SHALL 给出对该成分重要性的复核意见，SHALL 只评估公司特有的部分，并注明整层共同影响由层次分析负责。评审 SHALL NOT 含数量、仓位或风控结论。

#### Scenario: NVDA 关键人事件
- **WHEN** NVDA 的"董事会调查 CEO"成分触发重大事件评审
- **THEN** 评审 SHALL 标出命中的"管理层稳定"风险项与论点状态
- **AND** SHALL NOT 评估出口管制对 L5 整层的影响

#### Scenario: 无论点基线的标的
- **WHEN** 被触发的标的没有任何财报基线
- **THEN** 评审 SHALL 照常产出，标注"无论点基线"，且 `confidence` SHALL 不超过 0.3

### Requirement: 重大事件评审与财报口径基本面叠加且有有效期
重大事件评审 SHALL 作为独立角色发布，SHALL NOT 替代或遮蔽同一标的的例行更新或财报事件评审。每份重大事件评审的有效期 SHALL 为 14 天；同一事件簇出现新进展并再次触发时 SHALL 发布新版本并续期；过期的评审 SHALL NOT 再作为决策输入。

#### Scenario: 财报评审与重大事件评审并存
- **WHEN** NVDA 在重大事件评审有效期内发布财报并产生财报事件评审
- **THEN** 两份评审 SHALL 同时有效，任何一份 SHALL NOT 使另一份失效

#### Scenario: 14 天无进展
- **WHEN** 一份重大事件评审发布后 14 天内该事件簇没有新触发
- **THEN** 该评审 SHALL 过期，不再进入决策快照
