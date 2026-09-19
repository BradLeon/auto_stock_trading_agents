## ADDED Requirements

### Requirement: 退役来源以机器可读墓碑登记且不可被静默复活

系统 SHALL 在机器配置中维护 `retired_sources` 注册表。每条墓碑 SHALL 记录 `source_id`、退役时间、退役原因、原定级、数据处置结果（明确区分「已清除」与「保留为孤儿」）与替代来源建议。注册校验 SHALL 对已退役 `source_id` fail-closed：它 SHALL NOT 被重新注册为活跃来源、SHALL NOT 出现在任何 discovery group、SHALL NOT 被默认全来源检查或统一采集入口选中；重新启用 SHALL 需要显式改写墓碑并走独立变更流程。墓碑 SHALL NOT 被当作活跃来源配置读取，也 SHALL NOT 因墓碑存在而使已清除数据重新出现在默认查询或可用来源统计中。

#### Scenario: 尝试把已退役来源重新注册为活跃来源

- **WHEN** 开发者把已有墓碑的 `source_id` 重新写入来源配置
- **THEN** 注册校验 SHALL 失败并指出该 id 的退役时间、退役原因与数据处置结果
- **AND** SHALL 提示必须显式改写墓碑并提交独立变更，而不是直接恢复注册

#### Scenario: 全来源检查跳过退役来源

- **WHEN** 运维者运行不带 source 过滤的统一 release check 或目录总览
- **THEN** 输出 SHALL NOT 包含任何退役来源的 due、状态或可用覆盖行
- **AND** 墓碑 SHALL 能独立查询并返回退役原因与数据处置结果

#### Scenario: 墓碑解释库内仍有该来源的数据

- **WHEN** 某退役来源的数据处置结果为「保留为孤儿」，而审计在库中仍查到该 `source_id` 的观测
- **THEN** 目录与审计视图 SHALL 把该来源标注为已退役且数据未清除
- **AND** SHALL NOT 把该来源计入活跃覆盖、可用数据集或可查询来源

#### Scenario: 退役来源不参与 Observer 与报告

- **WHEN** 任意 Evidence Observer、DataProduct 或人类报告枚举受治理输入
- **THEN** 退役来源 SHALL NOT 出现在输入清单、方法卡或 coverage 统计中
- **AND** 退役 SHALL NOT 改变任何在役来源的命题、状态或数值

### Requirement: 来源数据清除必须显式确认且不隐式触发

系统 SHALL 提供独立的来源数据清除入口，用于已退役来源的观测、序列、artifact 与来源检查记录的物理删除。该入口 SHALL 默认只读运行并报告待删除的行数、artifact 数量与字节数；只有同时提供目标 `source_id` 与显式确认参数时才执行删除。清除 SHALL 要求目标来源已登记退役墓碑，SHALL NOT 在采集、发布、回滚、目录同步或生命周期校验路径中被隐式触发。执行 SHALL 形成可查询的清除记录，至少包含 `source_id`、执行时间、操作者、各表删除行数、释放字节数、artifact 数量与是否已导出留存。清除 SHALL 同时移除该来源独占的 artifact blob 文件，与在役来源共享同一内容身份的 blob SHALL 保留。回滚与清除 SHALL 为不同语义：回滚保留 artifact、观测 vintage 与失败记录，清除则使其不可恢复。

#### Scenario: 不提供确认参数时只做干跑

- **WHEN** 运维者对一个已退役来源调用清除入口但不提供显式确认参数
- **THEN** 系统 SHALL 只读返回待删除的观测数、序列数、artifact 数、artifact 字节数与来源检查记录数
- **AND** SHALL NOT 修改任何行、任何 blob 或任何发布状态

#### Scenario: 没有退役墓碑的来源不能被清除

- **WHEN** 运维者对仍在役的来源调用清除入口并提供了确认参数
- **THEN** 系统 SHALL 拒绝执行并说明该来源没有退役墓碑
- **AND** SHALL NOT 删除该来源的任何行或 artifact

#### Scenario: 显式确认后完成清除并留下审计记录

- **WHEN** 运维者对已登记墓碑的来源提供显式确认参数
- **THEN** 系统 SHALL 删除该来源的观测、序列、artifact 与来源检查记录，并移除其独占的 artifact blob
- **AND** SHALL 写入包含各表删除行数、释放字节数与 artifact 数量的清除记录，供后续审计

#### Scenario: 常规运行路径不隐式清除数据

- **WHEN** 统一采集、发布、回滚、并发探测或目录同步路径遇到一个已退役来源
- **THEN** 这些路径 SHALL 只跳过或拒绝该来源并记录可解释状态
- **AND** SHALL NOT 删除其任何已保存的观测、artifact 或来源检查记录

## MODIFIED Requirements

### Requirement: 来源注册驱动主动发现与增量更新

结构化数据平台 SHALL 允许来源声明 discovery adapter、检查频率、业务 cadence、release identity、可变 metadata 指针、不可变 artifact 身份和条件模块规则。统一调度入口 SHALL 对到期来源执行发现，只有在新 release、new period 或历史修订出现时才拉取与准入内容；同一机制 SHALL 覆盖全部已注册的活跃来源，包括 Anthropic Economic Index、Census BTOS、RPS/FRED 等分属不同 cadence 的来源，并 SHALL 完全跳过已退役来源。

#### Scenario: 多来源按各自 cadence 到期

- **WHEN** 每周统一 discovery 运行，而 Anthropic 为 release-event、BTOS 为双周、RPS 为季度、frontier capability 为每 7 天
- **THEN** 系统 SHALL 分别执行各来源的 due-check 和 source-native discovery
- **AND** SHALL NOT 将低频或未承诺发布日期的来源仅因没有新数据标记为 stale

#### Scenario: 退役来源不被统一发现选中

- **WHEN** 统一 discovery 按 due-check 枚举到期来源
- **THEN** 已登记墓碑的来源 SHALL NOT 进入候选集合，也 SHALL NOT 产生新的 artifact、观测或 vintage
- **AND** 其余活跃来源的发现结果 SHALL NOT 受该跳过影响

### Requirement: 统一 release check 可独立运行和按来源过滤

运维者 SHALL 能通过同一 discovery/release-check 入口检查全部已启用来源或只检查指定 source/dataset，并能选择仅发现或发现后采集。只读检查 SHALL NOT 改变发布状态；自动采集 SHALL 仍执行来源 schema、质量和 lineage 门。

#### Scenario: 只检查 AI adoption 来源

- **WHEN** 运维者请求仅检查 `ai_adoption` group 的来源
- **THEN** 系统 SHALL 返回该 group 中全部已注册活跃来源（当前为 Anthropic Economic Index、Census BTOS、RPS/FRED、Ramp AI Index）的 due、latest upstream、latest ingested 和可操作状态
- **AND** 已退役来源 SHALL NOT 出现在任何 group 检查结果、默认全来源检查或可操作状态列表中
- **AND** SHALL NOT 运行或改变无关财务、行情或其他 Observer 来源
