## Purpose

为结构化和非结构化研究数据提供统一的数据层组织、配置和兼容契约，使数据源接入、采集、存储、查询与迁移遵循同一套可验证的生命周期，同时保留两类数据各自的语义和存储特性。

## ADDED Requirements

### Requirement: 结构化与非结构化能力必须归属于统一的数据层命名空间

系统 SHALL 将结构化数据、非结构化文档、共享数据基础抽象、数据源适配器、采集管道、数据存储和数据产品组织在统一的数据层命名空间下。结构化与非结构化 SHALL 作为数据层内的领域子树，而不是相互独立的顶层平台。两者 SHALL 共享实体、来源、版本、质量、血缘和采集运行契约，同时保留各自的数据标准化、索引和存储语义。

#### Scenario: 新增结构化数据源

- **WHEN** 开发者为新的财务、区域或行业数值来源增加适配器
- **THEN** 适配器、结构化采集管道和结构化存储实现 SHALL 位于统一数据层的对应子树
- **AND** Agent 与 Workflow SHALL 通过数据产品访问该来源，而不是直接导入 Provider 适配器

#### Scenario: 新增非结构化数据源

- **WHEN** 开发者接入新的公告、研报、RSS 或电话会来源
- **THEN** 该来源 SHALL 使用与结构化来源相同的数据源注册、运行、质量和血缘契约
- **AND** 文档正文、版本、分块和证据 SHALL 继续使用适合非结构化内容的专用存储模型

### Requirement: 数据层组件必须遵守单向职责和依赖边界

系统 SHALL 区分共享核心、catalog、adapters、pipelines、stores、products 和 runtime 数据职责。适配器 SHALL 只负责外部来源访问和原始结果转换，SHALL NOT 直接写业务数据库；采集管道 SHALL 负责编排、准入、标准化和发布；存储组件 SHALL 负责持久化；数据产品 SHALL 负责面向消费者的查询和计算；runtime 组件 SHALL 负责不进入持久层的即时市场输入。结构化和非结构化领域之间 SHALL 只能通过共享核心或数据产品交互，SHALL NOT 形成反向循环依赖。

#### Scenario: Provider 暂时不可达

- **WHEN** 某个外部 Provider 请求失败
- **THEN** 适配器 SHALL 返回带有来源和失败状态的标准结果
- **AND** 适配器 SHALL NOT 自行修改结构化观测、文档资产或 Workflow 记忆

#### Scenario: Agent 请求研究数据

- **WHEN** Agent 查询财务指标、研报全文或证据事实
- **THEN** Agent SHALL 调用数据产品或统一兼容接口
- **AND** Agent SHALL NOT 依赖底层表名、文件路径或 Provider 特定 API

### Requirement: 数据源和数据集必须通过统一 catalog 配置和校验

系统 SHALL 提供统一的数据 catalog 配置入口，能够注册来源、数据集、实体范围、适配器、更新策略、保存限制、运行状态、质量门和回退关系。结构化数据配置与非结构化文档配置 SHALL 可以分域维护，但 SHALL 由同一个 loader 解析并执行引用、重复 ID、状态和契约校验。Workflow 配置 SHALL 与数据源配置分离。

#### Scenario: 配置引用不存在的适配器

- **WHEN** 数据集配置引用了未注册或无法加载的适配器
- **THEN** catalog 校验 SHALL 失败并指出数据源、数据集和适配器标识
- **AND** 该数据集 SHALL NOT 被标记为可发布

#### Scenario: 查询当前数据能力

- **WHEN** 运维者或使用者请求数据目录
- **THEN** 目录 SHALL 区分 registered、published、no-data、planned、disabled、runtime/excluded 和 failure 等状态
- **AND** 状态 SHALL 来自统一 catalog 与实际运行记录，而不是仅由手工文档决定

### Requirement: 旧入口必须在迁移期保持兼容并支持独立切换

系统 SHALL 在迁移期间继续支持 `ats.structured`、`ats.data_platform` 和现有 Workflow 的公开调用契约。旧入口 SHALL 通过兼容转发访问新实现，不得产生第二套业务逻辑。结构化来源、非结构化来源和消费者 SHALL 可独立切换；切换前必须完成结果对账，切换后必须保留可配置回退和回滚能力。

#### Scenario: 旧 Workflow 未完成迁移

- **WHEN** 某个 Workflow 仍导入旧模块路径
- **THEN** Workflow SHALL 能继续运行并获得与迁移前兼容的结果契约
- **AND** 新实现与旧实现 SHALL 不得因重复写入造成不可解释的数据差异

#### Scenario: 单独切换非结构化来源

- **WHEN** 新闻和研报来源已完成新管道对账，但公司财务来源尚未完成迁移
- **THEN** 非结构化消费者 SHALL 可独立切换到新数据产品
- **AND** 财务消费者 SHALL 保持当前稳定路径，直到其自身通过验收

### Requirement: 高频市场输入必须与持久化数据层明确隔离

系统 SHALL 将 ticker 股价、OHLCV、订单簿、期权链、Greeks、隐含波动率和其他快速变化的市场输入归入 runtime 数据边界。runtime 数据 SHALL 通过 IBKR、yfinance 或等价即时适配器按需获取，SHALL NOT 被误标为已沉淀的结构化数据集，SHALL NOT 进入本次数据层存储迁移。

#### Scenario: Workflow 请求当前股价

- **WHEN** Workflow 需要当前价格或近期行情
- **THEN** 系统 SHALL 路由到 runtime 查询入口
- **AND** 结构化持久层 SHALL NOT 因该请求新增观测版本或原始行情 artifact

### Requirement: 公司财务必须采用可审计的单一报表包来源链

系统 SHALL 将已由既有公司基本面读取使用的 defeatbeta、yfinance、SEC Facts 与发行人 IR
统一为 `company_financials` 的低频来源链，并按 `defeatbeta_stock_statement`、
`yfinance_financials`、`sec_companyfacts`、`company_disclosures` 的顺序尝试。来源链 SHALL
在首个为同一实体/报告期提供完整财务报表合同的来源后停止；完整合同至少包含收入、毛利、
营业利润、净利润、EPS、经营现金流、CapEx、现金、总资产、总负债、权益与债务，且实体、
报告期和币种/单位均可确认。毛利率、营业利润率、FCF SHALL 从该合同派生；P/E SHALL 由受管
EPS 和 runtime 价格按需计算而不得持久化。系统 SHALL NOT 将不同 Provider 的字段静默拼接为同一
报表，Provider 行 SHALL 保留 Provider 身份且不得冒充发行人原始披露。只有当 SEC Facts 与同一
实体、报告期、币种的发行人 IR 单独均不完整时，系统 MAY 组成带字段级 artifact lineage 和原始到派生
重算证据的 `official_disclosure_bundle`。此规定 SHALL NOT
授权持久化 yfinance 或其他 Provider 的股票行情、OHLCV、订单簿、期权链或其他 runtime 输入。

#### Scenario: 前序来源提供完整当季财务报表

- **WHEN** defeatbeta 或 yfinance 为美国发行人提供可确认的当季完整财务报表合同
- **THEN** 系统 SHALL 仅发布该来源的报表包、原始来源、报告期、币种、known-at 与质量状态
- **AND** SHALL NOT 再抓取或以 SEC/IR 字段补齐该报表包

#### Scenario: 前序来源无法提供完整财务报表

- **WHEN** 当前来源缺少核心字段、报告期不正确、单位不明或质量门失败
- **THEN** 系统 SHALL 记录该来源失败原因并尝试来源链中的下一个来源
- **AND** 只有来源链全部失败时，`company_financials` 才可保持无覆盖或不可发布

#### Scenario: 两个官方披露共同构成完整报表

- **WHEN** SEC Facts 与同实体、同报告期的发行人 IR 分别缺少不同核心字段，且二者均有可追溯官方 artifact
- **THEN** 系统 MAY 发布字段级标记为 `official_disclosure_bundle` 的报表包
- **AND** 系统 SHALL NOT 将任何 Provider 字段混入该包，并 SHALL 对所有派生 XBRL 行执行可复算校验

#### Scenario: 财务 fallback 无法确认币种或报告期

- **WHEN** fallback 返回的报表行缺少可确认的实体、币种、单位或报告期
- **THEN** 该行 SHALL 被隔离而不得发布到严格财务查询
- **AND** 该失败 SHALL NOT 使行情数据进入持久化结构化层

### Requirement: 每股与债务指标必须保留不可互换的经济口径

系统 SHALL 将普通股原始 EPS、ADR EPS、市场/Provider 调整后 EPS、长期债务、官方总债务及 Provider 报告总债务作为独立的受管指标或具有等效不可混淆维度的系列。ADR EPS SHALL 保存 ADR 单位与币种；发行人直接披露的 ADR EPS SHALL 优先于由普通股口径或镜像推断的值。Provider 的历史每股值（例如拆股调整）与其 total-debt 定义 SHALL NOT 覆盖或冒充官方 GAAP 指标。`LongTermDebt` 或其他明确排除当前到期债务/租赁的 XBRL 概念 SHALL NOT 被映射为总债务。跨来源核对 SHALL 仅在单位、期间和经济定义相同的指标之间进行。

#### Scenario: TSM 同时披露普通股与 ADR EPS

- **WHEN** TSMC 的官方季度 release 同时给出 NT$/普通股与 US$/ADR unit EPS
- **THEN** 系统 SHALL 发布两个可追溯的观测，并使面向 ADR 消费者的基本面产品优先选择 US$/ADR unit
- **AND** 当直接 ADR 值不可用时，系统 SHALL 保留带原始币种与 ADR 单位的 fallback，而不得把它与普通股 EPS 视作冲突

#### Scenario: 美国发行人同时披露总债务与长期债务

- **WHEN** SEC Facts 同时存在 `DebtLongtermAndShorttermCombinedAmount` 和 `LongTermDebt`
- **THEN** 前者 SHALL 映射为总债务，后者 SHALL 映射为长期债务
- **AND** 镜像 `total_debt` SHALL 以 Provider 报告总债务单列保存；只有书面确认其数值定义相同后才可加入与官方总债务的对账

#### Scenario: Provider 对历史每股或债务作出不同定义

- **WHEN** 镜像或受管 fallback 返回与发行人原始披露不同的拆股调整 EPS，或包含租赁等额外项目的 total debt
- **THEN** 系统 SHALL 以独立 market-adjusted EPS 或 Provider-reported debt 指标保存，并保留 provider field 与 adjustment 血缘
- **AND** 严格质量模式 SHALL NOT 将其与原始 EPS 或官方总债务判为同一指标的冲突

### Requirement: Workflow 运行结果必须与数据层输入明确隔离

系统 SHALL 将 PEAD、Sector、Evidence、Chain、Chief 和 scheduler 产生的任务投影、命题提议/判断、报告、运行状态、决策及交易结果保留在 `ats.memory`。这些记录 MAY 引用数据层的 observation、document 或 artifact lineage，但 SHALL NOT 被登记为结构化或非结构化输入数据集，也 SHALL NOT 被放入数据迁移 domain、平台读取路由或消费者数据对账范围。

#### Scenario: Workflow 完成一次分析

- **WHEN** Workflow 写入 task projection、claim assessment 或 Chain/Chief 报告
- **THEN** 记录 SHALL 写入 workflow memory
- **AND** 数据层只能保存其所引用的原始观测、文档和可复用中性事实，不得把分析结论重新发布为输入数据

### Requirement: 数据层架构文档必须覆盖开发、运维和消费视角

系统 SHALL 提供相互链接的数据层架构文档、开发者指南、运维指南和使用手册。文档 SHALL 说明目标目录、组件职责、依赖规则、配置位置和字段语义、来源增删流程、采集触发方式、验证与发布标准、命令分类、数据发现和 Agent/Workflow 消费方式。文档中的命令和配置示例 SHALL 能通过检查或测试验证，且 SHALL 与实际兼容期状态保持一致。

#### Scenario: 运维者增加数据源

- **WHEN** 运维者阅读运维文档并准备接入新 Provider
- **THEN** 文档 SHALL 指向实际配置文件、适配器注册位置、隔离采集命令、质量验收命令和发布/回滚命令
- **AND** SHALL 明确哪些操作只读、哪些操作会改变运行状态

#### Scenario: 使用者发现可用数据

- **WHEN** Agent 作者或研究者想知道某实体当前可以查询哪些数据
- **THEN** 使用手册 SHALL 指向动态目录、可用性查询、数据产品入口和结构化/非结构化示例
- **AND** SHALL 明确何时使用持久化数据，何时使用 runtime 市场查询

### Requirement: 旧持久化数据必须在旧实现退役前完成可恢复迁移与对账

系统 SHALL 为 legacy `measurement_*` 及既有 `structured_*` 结构化观测、artifact、catalog/run records，以及文档、版本、分块、别名和证据建立可审计的迁移范围与 manifest。每个迁移批次 SHALL 可安全重试或续跑，并 SHALL 对比记录数量、稳定标识、内容 hash（适用时）、期间/vintage、血缘、质量状态和消费者可见查询结果。系统 SHALL 在任何差异未解释前阻止对应 legacy 存储或实现的删除。

#### Scenario: 完成一个结构化迁移批次

- **WHEN** 运维者迁移一个 dataset/entity/period 范围的历史 observation 与 artifact
- **THEN** 系统 SHALL 记录迁移范围、来源和目标计数、成功/跳过/冲突条目以及对账结果
- **AND** 所有未解释的计数、vintage、血缘或查询差异 SHALL 使该范围保持不可退役状态

#### Scenario: 旧 SQLite 已含受管 structured 表

- **WHEN** 旧 SQLite 同时含有 `measurement_*` 和已受管的 `structured_*` 表
- **THEN** 迁移清单 SHALL 将两类结构化资产作为独立、可对账的批次记录
- **AND** 任一批次未迁移或未对账通过时，结构化数据域 SHALL NOT 被标记为已完成

#### Scenario: 迁移中断后恢复

- **WHEN** 一个文档或 observation 迁移任务在部分批次后中断
- **THEN** 后续运行 SHALL 从 manifest 的已确认边界恢复或幂等重放该批次
- **AND** SHALL NOT 覆盖原始 document version、artifact 或 accepted observation 的历史记录

### Requirement: Agent 与 Workflow 必须以可回滚方式切换到统一数据产品

系统 SHALL 盘点所有直接导入 legacy 数据模块或读取 legacy 数据表/路径的 Agent 与 Workflow，并为每个直接数据消费者记录目标数据产品、依赖 source、回滚入口和必要的调用方 smoke/regression。数据源或数据集是否可发布 SHALL 只由原始 artifact/lineage、实体/报告期、单位/币种、完整性、时效、质量状态以及原始到派生计算的可复算对账决定，SHALL NOT 等待 Agent/Workflow shadow 对账或稳定观察期。消费者只有在自身读取实现或输出逻辑变更时才需要即时回归；未切换的消费者 SHALL 保持原 mode，而不是阻塞其依赖数据源的发布。只编排上游结果、只触发采集或只写入 Workflow memory 的入口 SHALL NOT 被伪装成数据发布门。

#### Scenario: 切换一个 Workflow 消费者

- **WHEN** 一个 Workflow 的读取实现或输出逻辑发生切换
- **THEN** 运维者 SHALL 运行一次即时 smoke/regression，并能为该消费者独立回滚
- **AND** 该回归 SHALL NOT 阻塞已通过数据完整性、血缘和派生计算对账的数据源/数据集发布

#### Scenario: 消费者输出发生不一致

- **WHEN** shadow 或端到端验收发现新路径与 legacy 路径的结果超出该消费者定义的容差
- **THEN** 系统 SHALL 阻止该消费者发布到 platform
- **AND** SHALL 提供将该消费者切回 legacy 或 fallback 的独立回滚路径

#### Scenario: 旧路径暂时不可达而新路径有可追溯数据

- **WHEN** shadow 比较发现 legacy 请求失败、过期或返回空结果，而平台路径返回候选数据
- **THEN** 系统 SHALL 记录 legacy 失败的错误、平台实体/期间/单位/经济定义、artifact 或 observation lineage、known-at/freshness 及下游输出差异
- **AND** 只有这些证据证明平台结果完整、正确且不劣于旧路径时，评估 SHALL 将该差异标记为 `governed_upgrade` 而不是 `platform_regression`
- **AND** legacy 失败本身 SHALL NOT 自动使该消费者获得 platform 发布资格

#### Scenario: Chief 或 scheduler 仅编排数据消费者

- **WHEN** Chief 读取 Agent/Workflow memory 产物，或 scheduler 协调 runtime 输入和采集 pipeline 而没有同一持久数据的 legacy/platform 可替换读模型
- **THEN** 系统 SHALL 将该入口记录为 `orchestration_boundary`
- **AND** SHALL 以其上游数据消费者的发布状态、该入口端到端回归和独立回滚演练作为验收证据
- **AND** SHALL NOT 写入伪造的 data-consumer reconciliation 或将 mode 改为 platform 作为验收替代

### Requirement: 旧实现只能在全量迁移和稳定验收后退役

系统 SHALL 将 legacy 模块、配置 alias、重复 repository 实现和不再需要的旧 schema 视为独立退役对象。每个对象在删除前 SHALL 证明其持有的数据已完成迁移与对账、所有调用方已切换、回滚/恢复步骤已演练并通过约定的稳定观察期。OpenSpec change SHALL NOT 被归档，直到所有退役对象的验收状态为通过或被用户明确接受为长期保留。

#### Scenario: 尝试删除旧数据模块

- **WHEN** 开发者准备删除一个 legacy 数据模块或配置 alias
- **THEN** 退役清单 SHALL 显示其调用方、迁移 manifest、最近对账和回滚演练均已通过
- **AND** 若任一项缺失，系统 SHALL 阻止删除并保留兼容路径

#### Scenario: 判断变更是否可以归档

- **WHEN** 发布负责人请求归档统一数据层架构变更
- **THEN** 验收报告 SHALL 包含每个数据域迁移、每个 Agent/Workflow 切换和每个 legacy 退役对象的通过证据
- **AND** 任一未完成迁移、未切换消费者或未退役对象 SHALL 使变更保持 active 状态
