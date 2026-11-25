# Spec Delta

## ADDED Requirements

### Requirement: 模型发布日期只取自发布登记
成绩记录中的模型发布日期 SHALL 取自已确认的模型发布登记；登记中没有该模型时，发布日期 SHALL 为空并标注"发布日期未登记"。系统 SHALL NOT 用成绩日期、页面抓取日期或仓库创建时间填充发布日期。

#### Scenario: 成绩页没有发布日期
- **WHEN** 某模型的成绩来自 Artificial Analysis 页面，而发布登记中没有该模型
- **THEN** 成绩记录的发布日期 SHALL 为空并标注"发布日期未登记"
- **AND** 依赖发布日期的计算 SHALL 把该模型排除并披露原因

### Requirement: 每任务成本与成绩一起保存
来源公开提供完成某项评测的成本或单位任务成本时，系统 SHALL 与该成绩一起保存成本数值、币种、计量口径（每任务、每次完整评测）与来源。成本 SHALL 与成绩使用同一评测身份，SHALL NOT 跨评测或跨配置拼接。来源不提供成本时，成本 SHALL 为空，SHALL NOT 用 token 价格估算填充。

#### Scenario: 同一成绩带成本
- **WHEN** 某来源同时公布模型在某评测上的得分与完成该评测的成本
- **THEN** 系统 SHALL 保存两者，并绑定同一模型 release、评测版本与配置

### Requirement: 扩充评测按可得性逐项接入
在首版十一项之外新增评测时，系统 SHALL 先对每项做可得性检查：免费公开出口、历史深度、方法版本与可比组。通过检查的评测 SHALL 以独立评测身份接入；未通过的 SHALL 登记为"未覆盖"并写明原因。首批检查对象为 SWE-bench Pro、GDPval-AA、Artificial Analysis 智能指数、Epoch 能力指数、SemiAnalysis GDPx、Harvey LAB-AA 与 APEX-Agents-AA。

#### Scenario: 来源需要付费
- **WHEN** 某评测只能通过付费接口获得
- **THEN** 该评测 SHALL 登记为"未覆盖"，原因为需付费
- **AND** 系统 SHALL NOT 接入该付费接口，除非用户批准
