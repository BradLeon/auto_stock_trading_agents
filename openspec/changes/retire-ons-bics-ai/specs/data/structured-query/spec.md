## MODIFIED Requirements

### Requirement: AI adoption evidence bundle 保持三条证据轴独立

系统 SHALL 提供受治理的 AI adoption evidence bundle，分别返回企业采用广度、员工持续使用和任务生产化结构。每条轴 SHALL 保留自己的来源、统计主体、分母、地区、技术范围、period、methodology、质量和 lineage；系统 SHALL NOT 对不同轴加权、平均或生成统一 penetration score。bundle SHALL 只消费已注册且未退役的来源；已登记退役墓碑的来源 SHALL NOT 出现在任何证据轴、comparability matrix、coverage 统计或 lineage 中。

#### Scenario: 三条轴同时存在

- **WHEN** 消费者请求最新 L1 AI adoption evidence bundle
- **THEN** 系统 SHALL 返回三条独立 axis results 及各自 status
- **AND** SHALL NOT 将企业百分比、就业人口百分比和 Claude 流量份额转换成同一数值

#### Scenario: 来源退役后 bundle 不出现空轴或残影

- **WHEN** 某个曾注册的企业采用来源退役，且其数据已按显式清除入口删除
- **THEN** bundle SHALL 仍返回三条轴及其在役来源的 status，SHALL NOT 为退役来源生成空轴、占位行或 `unavailable` 条目
- **AND** 任何轴的 lineage 与 comparability matrix SHALL NOT 引用退役来源的 observation identity、artifact 或 dataset
