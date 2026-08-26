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
