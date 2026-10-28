## Purpose

为 Sector Analyst 建立行业、产业层和标的三级配置的唯一所有权，并使配置结论只依赖 LayerAnalysis 和受治理的共享事实。

## ADDED Requirements

### Requirement: Sector Analyst 必须拥有三级配置职责

Sector Analyst SHALL 为完整研究范围输出行业级、产业层级和标的级配置建议，并将每个结论绑定到对应 LayerAnalysis 和共享事实引用。Layer Analyst SHALL NOT 另行产生可与之冲突的配置结论。

#### Scenario: 生成层级和标的配置

- **WHEN** 所需 LayerAnalysis 和行业横截面数据已就绪
- **THEN** Sector Analyst SHALL 产生行业总体、各产业层和各标的的配置意见
- **AND** SHALL 保留从标的建议到层级、行业总体结论的一致性说明

### Requirement: Sector Analyst 只能消费 Layer 观点和共享事实

Sector Analyst SHALL 只读取 LayerAnalysis、行业横截面、产业结构、中性证据和其他 Data Products，SHALL NOT 读取 InformationBrief、Fundamental 评审、MacroReview 或 TechnicalReview。

#### Scenario: Sector 上下文包含 PEAD 结论

- **WHEN** 输入组装检测到 PEAD 或 Fundamental 投影
- **THEN** Sector 任务 SHALL 以跨分析师依赖违规失败
- **AND** SHALL NOT 使用该观点修正配置

### Requirement: Sector 的 Layer 依赖必须完整且有效

被请求范围内的 LayerAnalysis 失败、缺失、过期或 schema 不兼容时，Sector SHALL NOT 伪造对应层级配置。运行 SHALL 显式列出缺口并进入不完整或失败终态。

#### Scenario: 某层 LayerAnalysis 失败

- **WHEN** Sector 请求覆盖的某一产业层没有有效 LayerAnalysis
- **THEN** Sector SHALL 将该层标记为缺失上游分析
- **AND** SHALL NOT 对该层和其标的产生可下游消费的配置结论

### Requirement: SectorAllocation 必须是建议而非可执行订单

SectorAllocation SHALL 表达配置等级、建议权重或相对偏离、信心度、核心依据和失效条件，但 SHALL NOT 包含绕过 Chief 的券商订单或实时执行指令。

#### Scenario: Sector 建议清仓某标的

- **WHEN** SectorAllocation 对某标的给出清仓建议
- **THEN** 该结论 SHALL 作为 Chief 的研究输入
- **AND** 系统 SHALL NOT 仅因 SectorAllocation 而创建卖单

### Requirement: Sector 必须显示证据冲突和限额边界

Sector SHALL 保留不同层级或标的证据冲突，SHALL NOT 通过一个不透明总分数消除分歧。建议权重 SHALL 遵守研究阶段的配置上限，但最终交易后风险批准 SHALL 仍属于 Risk Officer。

#### Scenario: 层级景气与标的竞争位置冲突

- **WHEN** LayerAnalysis 显示本层总需求改善但某标的相对位置恶化
- **THEN** SectorAllocation SHALL 分别呈现层级和标的两条依据
- **AND** SHALL 解释三级配置如何保留该分歧

