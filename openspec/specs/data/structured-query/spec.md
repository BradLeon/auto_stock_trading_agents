# data/structured-query Specification

## Purpose
定义结构化数据面向 Agent、研究者和图表工具的统一消费能力，使同一组可信观测可以通过指标序列、横截面、SQL 与 Pandas 被一致查询、计算和历史重放。

## Requirements

### Requirement: 消费者通过稳定数据产品而非 Provider 接口查询

系统 SHALL 提供与底层 Provider 和存储实现解耦的结构化查询入口。消费者 SHALL 能按实体、指标、期间、来源策略和历史可见时点请求数据，且结果 SHALL 包含单位、口径、来源、新鲜度和质量状态；新增数据源 SHALL NOT 要求每个 Agent 重写取数逻辑。

#### Scenario: 更换财务数据回退源

- **WHEN** 平台将某财务指标的回退源从一个 Provider 调整为另一个 Provider
- **THEN** 使用共享指标查询的 Agent SHALL 无需修改调用方式
- **AND** 返回结果 SHALL 显示实际使用的来源及选择原因

### Requirement: 指标序列同时支持最新视图与历史时点视图

指标查询 SHALL 支持返回当前最新 vintage、全部修订历史，以及截至指定 `as_of` 时点当时已经可见的值。历史时点查询 SHALL 以数据首次可见时间约束结果，SHALL NOT 使用之后发布、抓取或修订的数据。

#### Scenario: 重放财报发布日前的分析

- **WHEN** 用户以财报发布前一日作为 `as_of` 查询季度收入
- **THEN** 系统 SHALL 不返回尚未发布的该季度收入
- **AND** SHALL 明确返回 not-yet-known 或等价缺口，而不是用当前最新值填充

#### Scenario: 查看修订历史

- **WHEN** 用户请求同一指标和期间的全部 vintages
- **THEN** 系统 SHALL 按可见时间返回原值与后续修订值
- **AND** 每个版本 SHALL 可追溯到各自来源快照

### Requirement: 横截面查询只比较口径兼容的数据

系统 SHALL 支持多实体、同指标或指标集合的横截面查询，并对财期、币种、单位、调整方式和来源口径执行可比性检查。可通过显式转换规则统一的结果 SHALL 标记转换版本；无法可靠比较的值 SHALL 保留但标记不可比，SHALL NOT 静默排序或合并。

#### Scenario: 比较不同财年结束日公司的季度收入

- **WHEN** 用户比较多家公司标记为 Q2 的收入，但其实际覆盖日期不同
- **THEN** 查询结果 SHALL 展示各自覆盖期间并给出可比性状态
- **AND** SHALL NOT 仅因季度标签相同而隐藏期间差异

#### Scenario: 跨币种比较

- **WHEN** 用户要求将美元与新台币收入转换到统一币种
- **THEN** 系统 SHALL 仅在指定汇率口径和换算日期后生成比较结果
- **AND** SHALL 保留原币值和换算血缘

### Requirement: SQL 与 Pandas 返回同一受治理数据视图

系统 SHALL 为研究者提供受控、只读的 SQL 查询能力和 Pandas DataFrame 输出。两种入口 SHALL 基于相同的实体、指标、vintage、来源选择和质量规则；SQL SHALL 限定在已发布的数据产品范围内，SHALL NOT 允许绕过准入状态读取隔离数据作为默认事实。

#### Scenario: SQL 与 DataFrame 对账

- **WHEN** 用户以相同过滤条件分别通过 SQL 和 DataFrame 查询某公司的季度收入
- **THEN** 两个结果 SHALL 在行集合、值、时间和来源选择上保持一致
- **AND** 差异 SHALL 被测试或健康检查识别

#### Scenario: 查询隔离候选

- **WHEN** 普通消费者执行默认 SQL 查询
- **THEN** 结果 SHALL 排除 quarantined 或未核验候选
- **AND** 只有显式审计入口 SHALL 能查看这些记录及失败原因

### Requirement: 派生指标可重算、可版本化且不污染原始观测

同比、环比、移动平均、滚动统计、价差、汇率换算和其他派生指标 SHALL 由明确版本的计算定义生成，并记录输入指标与版本。更新计算规则 SHALL 生成新的派生版本，SHALL NOT 修改原始观测或把旧计算结果冒充为来源发布值。

#### Scenario: 修改同比计算规则

- **WHEN** 某月度序列从简单月份匹配改为工作日对齐规则
- **THEN** 系统 SHALL 以新的计算版本生成结果
- **AND** 旧版本和底层原始月度水平值 SHALL 保持可查询

#### Scenario: 计算所需历史不足

- **WHEN** 序列缺少计算同比所需的去年同期值
- **THEN** 派生结果 SHALL 返回 insufficient-history 或空值及原因
- **AND** SHALL NOT 将缺失值当作零计算

### Requirement: 数据查询可以完整回溯血缘

任何返回值 SHALL 能追溯到来源、采集运行、原始快照或来源原生切片、标准化规则和已应用的派生计算。由文档证据产生的结构化观测 SHALL 进一步链接到 document/version/span 和核验记录。

#### Scenario: 检查 ARR 数值来源

- **WHEN** 用户从公司研究包打开某私营公司的 ARR 观测
- **THEN** 系统 SHALL 展示该数值的指标定义、覆盖日期、来源文档位置和核验状态
- **AND** SHALL 能区分公司一手披露与媒体转述

### Requirement: 结果显式表达新鲜度、覆盖缺口与来源冲突

查询结果 SHALL 携带与请求范围相关的新鲜度和覆盖信息。数据缺失、尚未发布、来源无覆盖、来源不可达、未授权、校验失败、陈旧和多来源冲突 SHALL 使用不同状态表达；消费者 SHALL 能选择严格模式拒绝不满足质量门槛的数据。

#### Scenario: 严格模式遇到陈旧 Consensus

- **WHEN** PEAD 请求最新 Consensus 且配置要求不超过七天，但现有快照已超过阈值
- **THEN** 严格查询 SHALL 返回 stale-data failure 而不是静默交付旧值
- **AND** 宽松查询若返回旧值 SHALL 明确标记快照年龄

#### Scenario: 部分实体缺失

- **WHEN** 行业横截面中五家公司有四家存在目标指标
- **THEN** 查询 SHALL 返回四家数据并列出第五家的具体缺口状态
- **AND** SHALL NOT 因部分缺失丢弃整个横截面

### Requirement: 查询支持数据发现而不要求使用者知道表结构

系统 SHALL 允许使用者发现可用数据集、指标定义、支持的实体、可用期间、更新频率和来源状态。发现结果 SHALL 由机器可读目录与当前数据库覆盖动态合成，区分注册能力、实际可查询数据、未核验候选、计划接入、runtime/excluded 和当前无覆盖，SHALL NOT 仅通过空查询结果或手工维护的静态清单表达能力边界。系统 SHALL 提供目录总览、对象说明、按实体/数据集的可用性和由实际能力生成的可复制查询示例。

#### Scenario: 查找 OpenAI 可用指标

- **WHEN** 用户查询 OpenAI 当前有哪些结构化数据
- **THEN** 系统 SHALL 列出已核验的融资、估值、ARR 等实际可用指标及覆盖期间
- **AND** 对尚未接入或只有未核验候选的指标 SHALL 显示相应状态

#### Scenario: 使用者查看一个数据集能查什么

- **WHEN** 使用者请求某数据集的说明或示例
- **THEN** 系统 SHALL 返回其来源、核心指标、当前实际指标、实体、覆盖期间、最新可见时间、质量状态和精确查询命令
- **AND** 示例中的数据集、指标和实体 SHALL 来自当前目录或数据库，SHALL NOT 引用不存在的静态样例

#### Scenario: 注册能力尚无实际数据

- **WHEN** 数据集和指标已注册但数据库中没有 accepted observation
- **THEN** 数据目录 SHALL 将其显示为 registered/no-data 或等价明确状态
- **AND** SHALL NOT 将“代码支持”描述成“当前可查询”

### Requirement: 自主 Agent 使用受治理的结构化数据消费指引

系统 SHALL 提供仓库内可发现的 Agent Skill，指导能够自主决定取数步骤的 Agent 先调用动态目录和可用性检查，再选择 DataProducts、CLI、SQL/Pandas 或运行时市场接口。Skill SHALL 要求保留质量、来源、时间和血缘语义，且 SHALL 从动态目录发现数据能力而不是复制易失真的指标清单。确定性 Workflow SHALL 继续通过稳定 API 和显式配置消费数据，SHALL NOT 依赖 Skill 文本才能正确运行。

#### Scenario: Agent 自主查询公司研究数据

- **WHEN** Agent 需要某公司可用的财务或 Consensus 指标但尚不知道覆盖情况
- **THEN** Skill SHALL 指导其先查询目录/可用性，再生成具体数据查询
- **AND** 若只有 runtime 行情满足请求，Skill SHALL 将其路由到 IBKR/yfinance 并说明该输入不可由 structured snapshot 重放

#### Scenario: Workflow 使用稳定契约

- **WHEN** PEAD、Sector 或 Chain 等确定性 Workflow 消费已知结构化输入
- **THEN** Workflow SHALL 直接调用 DataProducts/兼容接口并使用 feature flag 控制读取模式
- **AND** 即使 Agent Skill 未加载，Workflow 的结果契约 SHALL 保持成立

### Requirement: 查询结果可形成可复现的数据快照

系统 SHALL 能为一次分析记录实际使用的数据范围、来源选择、观测版本、计算规则版本和查询时点，使后续可以在不重新访问外部数据源的情况下重放该输入。重放 SHALL 不因默认主源或最新数据后来变化而改变结果。

#### Scenario: 重放一次 PEAD 分析

- **WHEN** 某次 PEAD 分析记录了结构化输入快照标识
- **THEN** 后续重放 SHALL 返回当次使用的财务和 Consensus 版本
- **AND** 新发布的财报或 Consensus 修订 SHALL 不进入该重放结果

### Requirement: 兼容接口与新查询结果在切换前完成对账

现有 Workflow 的兼容接口 SHALL 可由新平台提供数据而保持既有返回契约。每个消费者在切换默认读取路径前 SHALL 对关键指标、时间范围、缺失语义和容差完成自动对账；完成切换后仍 SHALL 保留可配置回退，直至旧路径正式退役。

#### Scenario: Consensus 兼容切换

- **WHEN** 新平台的 Consensus 在声明窗口内与旧 `consensus` 路径通过对账
- **THEN** 现有消费者 SHALL 可在不修改业务逻辑的情况下切换到共享数据产品
- **AND** 对账失败时 SHALL 保持旧路径并报告差异

### Requirement: 数据层文档按开发者、运维者和使用者角色交付

系统 SHALL 交付相互链接、与当前实现和来源配置同步的开发者文档、运维文档和使用者手册，并从总体数据架构文档提供稳定入口。文档示例、命令、来源状态和验收阈值 SHALL 可由测试或发布检查验证；ticker 股价与期权行情的 runtime/excluded 边界 SHALL 在三类文档中保持一致。

#### Scenario: 开发者理解并扩展数据层

- **WHEN** 开发者阅读结构化数据开发文档
- **THEN** 文档 SHALL 包含设计原则、组件职责、领域对象、组件架构图、采集与查询数据流图、适配器扩展契约及测试策略
- **AND** 开发者 SHALL 能据此新增来源而不绕过原始快照、准入、vintage、质量和血缘机制

#### Scenario: 运维者管理来源和验收

- **WHEN** 运维者阅读结构化数据运维文档
- **THEN** 文档 SHALL 列出已接入、计划接入、未接入和 runtime/excluded 来源，并说明认证、更新频率、保存约束、已知 QPS/限流、请求预算、重试/退避和故障处理
- **AND** 无官方 QPS 的来源 SHALL 标记为未知并采用可配置保守预算，SHALL NOT 编造确定限制
- **AND** 文档 SHALL 给出扩展来源的步骤、验收命令、通过标准和回滚条件
- **AND** 文档 SHALL 指向实际配置、适配器注册、发布状态和逐步可复制命令，并说明哪些命令只读、哪些命令会改变运行状态

#### Scenario: 使用者查询和计算研究数据

- **WHEN** 使用者阅读结构化数据使用手册
- **THEN** 文档 SHALL 提供数据发现、CLI、Python、SQL、Pandas、最新值、历史 `as_of`、横截面、派生计算、分类和血缘查询示例
- **AND** 文档 SHALL 说明 Agent/Workflow 如何消费数据产品，以及何时改用 IBKR/yfinance 运行时查询股价或期权
- **AND** 文档 SHALL 提供动态目录、对象说明、可用性和示例命令，使读者能从当前环境确认实际数据而非只阅读概念说明

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
