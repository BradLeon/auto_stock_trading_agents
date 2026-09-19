## MODIFIED Requirements

### Requirement: Observer 使用固定且隔离的生产化命题

Observer SHALL 保留稳定 claim identity，并以 `claim_definition_version=v2` 使用命题：“AI 的企业采用广度、员工持续使用和任务生产化深度是否同步扩大，从局部试验走向可重复的生产工作流？”它 SHALL 只消费受治理的 AI adoption evidence bundle，分别读取 BTOS 企业广度、RPS 员工持续使用和 Anthropic 1P API 任务生产化，输出 snapshot manifest 与 lineage；不得直接读取 Provider、物理表或自行拼接跨源数值。该 Observer SHALL 只消费已注册且未退役的来源，已登记退役墓碑的来源 SHALL NOT 出现在输入清单、方法卡、manifest 或 lineage 中。

#### Scenario: 运行固定命题

- **WHEN** L1 AI 应用层运行该 Observer
- **THEN** 输出 SHALL 具有固定命题和可重放的受治理输入清单
- **AND** SHALL NOT 声称 Usage Share 是员工或企业采用率

#### Scenario: 运行扩展后的固定命题

- **WHEN** L1 AI 应用层运行该 Observer
- **THEN** 输出 SHALL 具有 v2 命题、三条证据轴及可重放的受治理输入清单
- **AND** SHALL NOT 将企业比例、就业人口比例或 Claude Usage Share 互相改写或融合

#### Scenario: 来源退役不改变 Observer 输出

- **WHEN** 一个从未进入本 Observer 的来源正式退役并清除其数据
- **THEN** Observer 的命题、claim version、三轴输入、状态判定、manifest 与图表 SHALL 与退役前完全一致
- **AND** SHALL NOT 因该来源退役而引入新的轴、新的覆盖率扣减或新的 `insufficient_history` 标记
