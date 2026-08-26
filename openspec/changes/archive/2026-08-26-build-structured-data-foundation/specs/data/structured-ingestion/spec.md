## Purpose

定义数值型研究数据从外部来源进入共享平台时的可信边界，使公司、地区与行业研究数据能够统一沉淀、保留历史修订，并可追溯到当时可见的来源版本，同时明确高频运行时行情不属于本持久层。

## ADDED Requirements

### Requirement: 数值只有满足结构化准入契约后才成为共享观测

系统 SHALL 只发布能够明确绑定经济实体、指标口径、覆盖期间或事件时间、数值及单位、来源和系统可见时间的结构化观测。字段缺失、单位含糊、实体无法归一或期间冲突的记录 SHALL 保留为候选或隔离记录，SHALL NOT 进入默认查询。

#### Scenario: Provider 返回缺少单位的数值

- **WHEN** 数据源返回某公司的指标值，但来源口径无法确定该值是美元、当地货币还是百分比
- **THEN** 系统 SHALL 将记录标记为 unit unresolved 并排除在默认指标序列之外
- **AND** SHALL 保留来源记录和失败原因供后续修正

#### Scenario: 私营公司也可作为经济实体

- **WHEN** 来源披露 OpenAI 或 Anthropic 的融资、估值或 ARR，且实体、时间、单位和证据均可确定
- **THEN** 系统 SHALL 使用稳定的非上市公司实体标识保存该观测
- **AND** SHALL NOT 要求该实体必须拥有股票代码

### Requirement: 所有结构化来源遵守统一的注册与运行契约

每个结构化来源 SHALL 声明其数据用途、提供者、适配器、覆盖范围、更新频率、来源优先级、授权或保存约束及允许的回退来源。每次采集 SHALL 形成独立运行记录，并区分成功有新数据、成功无变化、零匹配、未到发布时间、陈旧、不可达、未授权、解析失败和校验失败。

#### Scenario: 来源可访问但本期尚未发布

- **WHEN** 台湾或韩国官方接口可正常访问，但目标月份数据尚未到法定发布时间
- **THEN** 运行状态 SHALL 为 not-yet-published 或等价明确状态
- **AND** SHALL NOT 被报告为来源故障或零值

#### Scenario: 单一来源失败

- **WHEN** `stock_statement` 镜像不可达，但 Consensus 和官方财务来源仍可用
- **THEN** 系统 SHALL 记录该数据集的局部失败并继续其他来源
- **AND** SHALL NOT 将整个结构化采集任务标记为完全成功或完全失败

### Requirement: 运维者可以通过统一生命周期接入、验收、发布和回滚来源

系统 SHALL 提供可执行的来源生命周期入口，至少覆盖注册校验、隔离采集、质量/发布预检、显式发布和回滚。注册校验 SHALL 同时核对来源配置、数据集引用、运行适配器、请求预算、质量阈值与验收样本；隔离采集 SHALL 可指定独立数据库和 artifact 目录。发布 SHALL 只在最近采集状态和关联数据集质量门满足声明规则时生效，并 SHALL 形成可查询的发布记录；发布来源 SHALL 实际控制统一采集入口的运行模式，不能仅改变展示标签。默认发布命令 SHALL 为只读预检，只有显式确认参数才能修改发布状态。

#### Scenario: 新来源在隔离环境通过验收后发布

- **WHEN** 运维者完成来源配置和适配器实现，并在隔离数据库运行注册校验、真实源采集与质量预检
- **THEN** 系统 SHALL 展示每个发布条件及其通过或失败原因
- **AND** 只有显式执行发布后，统一采集入口 SHALL 按新运行模式允许该来源进入平台采集

#### Scenario: 质量门失败阻止发布

- **WHEN** 来源最近运行不可达、没有成功运行，或关联数据集未达到声明的发布质量门
- **THEN** 发布预检和显式发布 SHALL 拒绝切换到 platform 模式并返回可操作原因
- **AND** 当前运行模式和消费者读取模式 SHALL 保持不变

#### Scenario: 回滚来源发布

- **WHEN** 已发布来源发生持续质量退化且运维者显式执行回滚
- **THEN** 系统 SHALL 将来源恢复到指定的安全运行模式并保存回滚记录
- **AND** SHALL NOT 删除已经保存的 artifact、观测 vintage 或失败记录

#### Scenario: runtime excluded 来源不能被误发布

- **WHEN** 运维者对 IBKR 或 yfinance 行情类 runtime/excluded 来源执行隔离采集或发布
- **THEN** 系统 SHALL 拒绝该操作并说明其不属于持久结构化数据集

### Requirement: 原始来源版本在标准化之前被不可变保留

系统 SHALL 在标准化前保存原始响应、来源原生切片或足以复现该次查询的不可变快照，并记录内容身份、来源快照版本和获取时间。相同内容可去重，修订内容 SHALL 形成新版本；若授权条款不允许保存完整响应，系统 SHALL 保存允许范围内的来源指针、查询条件、内容摘要和可复现元数据，并显式标记保存限制。

#### Scenario: Provider 回溯修订历史财务数据

- **WHEN** 同一来源在后续采集中返回了内容不同的历史期间数据
- **THEN** 系统 SHALL 保留修订前后的来源版本
- **AND** SHALL 能指出每个标准化观测来自哪个原始版本

#### Scenario: 来源禁止长期保存完整文件

- **WHEN** 来源条款只允许保存研究所需字段而不允许重新分发完整数据集
- **THEN** 系统 SHALL 按来源政策保存最小必要切片和复现元数据
- **AND** 健康与血缘查询 SHALL 显示该来源的保存限制

### Requirement: 标准化观测按修订版本保存而不覆盖历史

同一来源、实体、指标和覆盖期间可以存在多个 vintage。完全相同的重复采集 SHALL 幂等去重；内容发生变化时 SHALL 追加新 vintage，并保留发布时间、首次可见时间和获取时间。最新视图 SHALL 选择当前有效 vintage，历史时点视图 SHALL 只选择该时点已经可见的 vintage。

#### Scenario: 财务报表被重述

- **WHEN** 公司重述某季度收入，重述值在原报告数月后发布
- **THEN** 最新视图 SHALL 返回重述值
- **AND** 以重述发布前日期查询 SHALL 返回原始值而不是后来值

#### Scenario: 重复运行未发现变化

- **WHEN** 同一来源切片的内容和标准化结果均未变化
- **THEN** 系统 SHALL 记录一次无变化运行而不新增重复观测版本

### Requirement: 指标、实体、期间和单位遵守明确语义

系统 SHALL 通过共享注册表管理指标定义、经济实体、证券映射、财期制度、单位与币种。Provider 原始名称 SHALL 映射到平台指标而不丢失原字段；不同口径、不同调整方式或不可比单位 SHALL 保持为不同系列，除非存在显式、可版本化的转换规则。

#### Scenario: 自然年与公司财年季度不同

- **WHEN** MSFT 的 fiscal quarter 与自然年季度不一致
- **THEN** 财务观测 SHALL 按公司财期保存并同时保留实际覆盖起止日期
- **AND** 跨公司比较 SHALL NOT 仅凭 `Q1` 标签把不同覆盖期间视为完全同口径

### Requirement: 多来源可以共存且主源选择可解释

平台 SHALL 允许多个 Provider 对同一指标和期间分别保存观测，并依据可配置的数据用途、权威等级、时效、完整性和健康状态生成首选视图。来源冲突 SHALL 可见，SHALL NOT 通过覆盖较旧记录或取平均值静默消除。

#### Scenario: 官方财务数据与第三方镜像不一致

- **WHEN** SEC/XBRL 与 defeatbeta `stock_statement` 对同一公司、期间和可比口径返回不同数值
- **THEN** 系统 SHALL 同时保留两项带来源的观测并报告差异
- **AND** 美国发行人的默认官方财务视图 SHALL 按声明的优先级选择官方披露，而不是最近抓到的记录

#### Scenario: 主源陈旧而回退源更新

- **WHEN** 声明的主源超过新鲜度阈值且回退源已有更新值
- **THEN** 查询结果 SHALL 明确说明是否启用回退源及其原因
- **AND** SHALL 保留主源缺口供健康报告展示

### Requirement: 首批公司级数据通过同一平台沉淀

首批迁移 SHALL 覆盖公司财务报表、defeatbeta `stock_statement` 和市场 Consensus。`stock_statement` SHALL 作为来源适配器映射到共享指标体系而非形成独立业务数据库；Consensus SHALL 以每次可见快照保存估值、盈利预测、收入预测、目标价或评级等来源实际提供的数据，SHALL NOT 伪造历史发布时间。

#### Scenario: 接入 stock_statement

- **WHEN** defeatbeta 为某公司提供多个季度的 statement 行项目
- **THEN** 系统 SHALL 将可映射项目发布为带 Provider provenance 的共享财务观测
- **AND** 未知项目 SHALL 保留来源字段并进入待映射报告，SHALL NOT 被静默丢弃或错误归类

#### Scenario: Consensus 随时间变化

- **WHEN** 系统在两个日期抓到不同的下一季度 EPS Consensus
- **THEN** 两个快照 SHALL 分别保存各自的系统可见时间
- **AND** 早期历史时点查询 SHALL NOT 使用后一次抓取的预测值

#### Scenario: 首批公司某一数据集缺失

- **WHEN** 某个 covered entity 没有 Consensus 或 `stock_statement` 覆盖
- **THEN** 系统 SHALL 报告该实体与数据集的 coverage gap
- **AND** 财务或区域序列等其他已有数据 SHALL 继续发布

### Requirement: Ticker 股价与期权行情保持运行时查询且不进入持久层

系统 SHALL 将 ticker 股价、OHLCV、期权链、Greeks、隐含波动率及其他快速变化的市场行情排除在本结构化持久层之外。Agent 与 Workflow SHALL 继续按需通过 IBKR、yfinance 或既有运行时适配器查询；结构化采集 SHALL NOT 为这些数据建立原始快照、标准化序列、历史回填或 observation vintage。运维来源目录 SHALL 将其标记为 runtime/excluded，而非尚未接入。

#### Scenario: Workflow 请求当前股价

- **WHEN** Agent 在分析流程中需要某 ticker 的当前价格或近期行情
- **THEN** 系统 SHALL 调用既有 IBKR/yfinance 运行时查询路径
- **AND** 本结构化平台 SHALL NOT 因该请求新增行情观测或原始行情 artifact

#### Scenario: Workflow 请求期权链

- **WHEN** Agent 需要期权合约、Greeks 或隐含波动率
- **THEN** 系统 SHALL 使用既有期权运行时数据源返回当次结果
- **AND** 结构化 SQL/Pandas 查询 SHALL NOT 声称能够提供持久化期权历史

### Requirement: 地区与行业数值使用相同观测契约

国家/地区官方序列和行业级指标 SHALL 与公司数据共用来源、指标、时间、版本和质量治理。台湾/韩国出口等来源 SHALL 首先保存发布的原始水平值；同比、环比等变化率 SHALL 作为可重算派生结果，不得取代原始观测。融资、估值和 ARR 等离散披露 SHALL 可表达为带事件时间和覆盖期间的结构化观测。

#### Scenario: 台湾出口月度数据入库

- **WHEN** 台湾官方来源发布新的月度 IC 出口金额
- **THEN** 系统 SHALL 保存该月原始金额、币种/单位、发布时间和来源版本
- **AND** 同比、环比 SHALL 能从已保存水平值重算

#### Scenario: 一轮融资包含多个数值

- **WHEN** 经核验来源同时披露融资金额、投后估值和交易日期
- **THEN** 系统 SHALL 将这些观测绑定到同一融资事件和来源证据
- **AND** SHALL 保留各数值自己的指标语义与单位

### Requirement: 文档提取数值必须经过证据型准入

从公告、研报或新闻提取的数值 SHALL 先成为候选，并关联原文 document/version/span、提取方式和置信状态。只有通过实体、指标、时间、单位和证据一致性核验的候选才可进入默认结构化查询；模型输出 SHALL NOT 单独构成准入依据。后续纠错 SHALL 保留旧候选及其状态历史。

#### Scenario: 新闻称 ARR 达到某个水平

- **WHEN** 模型从新闻摘要中识别出 Anthropic ARR，但正文不完整或原始出处不可访问
- **THEN** 候选 SHALL 标记 evidence incomplete 并排除在默认序列之外
- **AND** SHALL 可在补充完整来源后重新核验

#### Scenario: 人工核验公开披露

- **WHEN** 人工确认公司公告中的 ARR 数值、覆盖日期、单位和原文位置
- **THEN** 系统 SHALL 发布该结构化观测并保留核验者、核验时间及原文血缘

### Requirement: 结构化质量报告覆盖完整性、准确性、时效性和可用性

系统 SHALL 按来源、数据集、实体、指标和期间报告预期覆盖、实际覆盖、待发布、缺失、隔离、冲突、陈旧和不可达状态。质量报告 SHALL 提供可操作原因，并允许区分来源没有覆盖与适配器失败。首批迁移 SHALL 为关键数据集定义可自动验收的完整性和对账阈值。

#### Scenario: 采集成功但历史月份断档

- **WHEN** 来源返回最新月份但预期回看窗口中缺少一个已发布月份
- **THEN** 质量报告 SHALL 标记 period gap 并列出缺失期间
- **AND** 运行 SHALL NOT 仅因存在最新值而报告覆盖完整

#### Scenario: 新旧路径对账不一致

- **WHEN** 兼容期内新平台与旧 Workflow 对同一输入返回超出容差的结果
- **THEN** 该消费者 SHALL 保持旧路径并生成 reconciliation failure
- **AND** SHALL NOT 自动完成切换

### Requirement: 迁移按来源和消费者独立切换且可回滚

现有 Workflow SHALL 在迁移期继续运行。每个数据源 SHALL 依次经历历史回填、影子采集、结果对账、默认读取切换和旧取数退役；不同来源和消费者可独立推进。关闭新路径 SHALL 不删除已保存的原始快照与观测版本，旧接口在宣布退役前 SHALL 保持兼容。

#### Scenario: 仅切换区域出口序列

- **WHEN** 台湾/韩国出口序列已通过历史回填和对账，但公司财务迁移尚未完成
- **THEN** Chain 消费者 SHALL 可单独切换区域序列到新平台
- **AND** PEAD 财务读取 SHALL 继续使用其当前兼容路径

#### Scenario: 切换后发现质量退化

- **WHEN** 某来源切换后连续违反质量阈值
- **THEN** 系统 SHALL 能将该来源或消费者恢复到上一读取路径
- **AND** 新平台中已获取的版本和失败记录 SHALL 继续用于审计
