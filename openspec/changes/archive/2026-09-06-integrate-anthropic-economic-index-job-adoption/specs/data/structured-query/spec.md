## ADDED Requirements

### Requirement: 查询支持版本化实体层级与 taxonomy as-of 语义

结构化查询 SHALL 支持按 dataset、source、关系类型、父实体、子实体和 `as_of` 遍历版本化实体关系。`as_of` 查询 SHALL 仅使用当时已经可见的 taxonomy version；结果 SHALL 返回关系来源版本、known time、artifact 和质量状态。

#### Scenario: 查询职业的全部任务
- **WHEN** 消费者以 occupation 和 `as_of` 请求其 child tasks
- **THEN** 系统 SHALL 返回该时点已知的有效 `has_task` 关系及 task entities
- **AND** SHALL 为每条关系提供 taxonomy lineage

#### Scenario: taxonomy 后续改变任务归属
- **WHEN** 当前 taxonomy 与历史 `as_of` 时点对同一 task 的归属不同
- **THEN** 最新查询 SHALL 使用当前已知关系，历史查询 SHALL 使用当时已知关系
- **AND** SHALL NOT 用新关系重写旧分析快照

### Requirement: source product 是不可省略的系列与比较边界

当一个数据集包含多个 source products 时，series identity SHALL 包含 source product。领域 snapshot、profile、排名和变化查询 SHALL 要求明确 source product；系统 SHALL NOT 默认跨产品合并、补值、排名或计算变化。跨产品比较只有在消费者显式选择并接受口径说明时才可返回并列结果。

#### Scenario: 未指定 source product
- **WHEN** 消费者对多产品数据集请求职业排名但未指定 source product
- **THEN** 查询 SHALL 返回 ambiguous-dimension failure 或要求显式选择
- **AND** SHALL NOT 混合 Claude.ai 与 1P API 生成单一排名

#### Scenario: 显式跨产品比较
- **WHEN** 消费者显式请求同一职业、月份的 Claude.ai 与 1P API 并列比较
- **THEN** 系统 SHALL 保留两个独立值、单位、methodology 和 lineage
- **AND** SHALL 标记该比较不代表同一总体的可直接加总序列

### Requirement: 领域派生结果公开公式版本和完整输入身份

领域派生指标 SHALL 在查询时或数据产品层计算，返回 derivation id/version、公式、所有输入 observation IDs、输入 taxonomy relation IDs、适用口径和缺失原因。派生值 SHALL NOT 被保存或展示为 Provider 原始发布值，且不同 methodology regime、source product 或非连续期间 SHALL NOT 被静默连接。

#### Scenario: 计算自动化使用贡献
- **WHEN** 同产品、同期间、同职业同时具有 Usage Share 和 Automation Share
- **THEN** `automated_usage_share` SHALL 按 `usage_share × automation_share / 100` 计算
- **AND** 结果 SHALL 返回两个输入 observation IDs 和 derivation version

#### Scenario: 计算职业环比变化
- **WHEN** 同产品、同 methodology 的两个 observation 位于连续自然月
- **THEN** Usage、Automation 或 collaboration change MAY 按百分点差计算
- **AND** 若月份不连续或跨 methodology regime，结果 SHALL 返回不可计算原因

#### Scenario: 历史不足以计算同比
- **WHEN** 数据集尚无足够去年同期历史
- **THEN** 同比派生 SHALL 返回 `insufficient_history`
- **AND** SHALL NOT 以零或抽样周数据代替缺失历史

### Requirement: 职业任务覆盖派生保留缺失与隐私过滤语义

系统 SHALL 以指定 taxonomy version 的任务总数作为职业任务覆盖分母，并区分有公开 AEI cell、无公开 cell、automation 大于 augmentation、augmentation 大于 automation及两者相等的任务。缺失 cell SHALL 计入 unobserved proxy，SHALL NOT 被解释为零使用或确定未使用。

#### Scenario: 计算 observed task coverage
- **WHEN** occupation 在指定 taxonomy 中有十个任务且六个具有合格 AEI observation
- **THEN** `observed_task_coverage` SHALL 为 `6/10`
- **AND** 结果 SHALL 标注该值是公开可见任务覆盖代理而非员工采用率

#### Scenario: automation 与 augmentation 相等
- **WHEN** 某 task 的 Automation Share 与 Augmentation Share 相等
- **THEN** 该 task SHALL 进入 balanced bucket
- **AND** SHALL NOT 被强行归入 mostly automated 或 mostly augmented

### Requirement: SOC 大类指标优先使用 Provider 原始 level 1 值

职业大类 Usage Share 和 Automation Share SHALL 使用对应产品、期间的 Provider SOC level 1 observation。系统 SHALL NOT 以隐私过滤后的可见详细职业求和或简单平均替代官方大类值；job share of major group 的派生结果 SHALL 显示分子分母和不完全加总提示。

#### Scenario: 详细职业加总不等于大类值
- **WHEN** 可见 level 0 jobs 的 Usage Share 合计与 Provider level 1 值不同
- **THEN** 大类展示 SHALL 返回 Provider level 1 observation
- **AND** 结果 SHALL 说明差异可能包含未发布或隐私过滤 cells

### Requirement: 领域 DataProducts 与通用查询返回同一受治理 observation 集合

Work-adoption snapshot、job profile、metric series、cross section、只读 SQL 和 DataFrame 在相同过滤、quality、source product、period、taxonomy version 与 `as_of` 条件下 SHALL 基于同一 observation 集合。默认查询 SHALL 包含 accepted/warning，quarantined 记录仅可由显式审计入口读取。

#### Scenario: 多入口对账
- **WHEN** 测试以相同条件通过 snapshot、job profile、SQL 和 DataFrame 查询同一职业指标
- **THEN** 返回的 observation IDs、值、期间和来源选择 SHALL 一致
- **AND** 差异 SHALL 触发验收失败

### Requirement: 分析快照固定观测、关系与派生版本

一次消费者运行的 snapshot manifest SHALL 固定 dataset/source、source product、查询参数、`as_of`、observation IDs、taxonomy relation IDs、artifact IDs、derivation versions、质量状态和生成时间。重放 SHALL 不访问外部来源，且 SHALL 不因后续 observation vintage、taxonomy 或默认规则变化而改变。

#### Scenario: 重放 L1 月度观察
- **WHEN** 后续 release 修订历史 observation 或 taxonomy，但用户以旧 manifest 重放
- **THEN** 系统 SHALL 返回 manifest 固定的 observation、relation 和 derivation versions
- **AND** SHALL NOT 自动替换为当前最新版本
