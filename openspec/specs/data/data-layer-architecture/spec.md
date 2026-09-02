## Purpose

为结构化和非结构化研究数据提供统一的数据层组织、配置和兼容契约，使数据源接入、采集、存储、查询与迁移遵循同一套可验证的生命周期，同时保留两类数据各自的语义和存储特性。

## Requirements

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

#### Scenario: 行业评审读取成分股财务

- **WHEN** 行业评审为其配置的成分股读取财务快照
- **THEN** `sector_constituent_financials` SHALL 使用与 `pead_fundamentals` 相同的
  `company_financials` 完整报表包选择、来源优先级、报告期与血缘规则
- **AND** 毛利率、营业利润率和收入同比 SHALL 从选定报表包派生；市值、Trailing/Forward P/E
  与 Beta SHALL 保持 runtime 输入，不得作为持久化财报字段或财报对账对象
- **AND** 缺少合格报表包的实体 SHALL 显式返回 `no_coverage`，不得以 Provider TTM/网页字段
  冒充受管财务覆盖

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

### Requirement: 第三方非结构化来源必须先完成数据源级验收与发布

系统 SHALL 将 TrendForce 文章、SemiAnalysis（IMAP/RSS）和 IBKR News 作为独立注册的非结构化来源，以共享文档资产、版本、运行和质量契约保存。每个候选文档 SHALL 保留来源原生标识、canonical URL、发布时间、采集时间、正文提取状态、内容 hash、来源/实体或主题范围、质量结论和运行血缘。来源级发布 SHALL 仅以范围覆盖、来源真实性、正文可用性、时间、去重、时效、血缘和质量门决定，SHALL NOT 等待 Agent 或 Workflow 消费者切换、shadow 对账或稳定观察期；本阶段 SHALL NOT 删除 legacy 实现或修改消费者读取路由。

TrendForce 文章 SHALL 与 TrendForce DRAM 合约价结构化数据集分开注册、存储、运行和验收。SemiAnalysis SHALL 复用研究邮件/RSS 采集管道，以 IMAP `Message-ID` 或 UID、canonical URL 和内容 hash 去重，并枚举时间窗口内所有匹配的邮件及 RSS 项；可验证的邮件正文 SHALL 优先于 RSS 摘要。IBKR News SHALL 仅通过只读 TWS/IB Gateway 新闻访问采集，不得持久化行情或期权数据，且 SHALL 区分连接失败、权限或订阅缺失、provider/节流失败与确实不存在新闻。

当 SemiAnalysis 因未订阅仅可取得可验证的预览正文时，系统 MAY 将该资产发布到 platform，但 SHALL 将正文和 document version 的完整性标记为 `partial`、保留内容 hash、原生邮件标识和 canonical URL，并 SHALL 禁止将其表达为完整文章或覆盖其后的完整版本。IBKR News SHALL 是新闻新路径的第一优先级，并在历史补采中使用 `reqNewsProviders()` 返回的全部当次会话 provider。每条候选 SHALL 保留 provider/article ID、查询实体、原始精确新闻时间及其时区/会话标识；标题 SHALL 独立命中查询实体 ticker、公司名或登记别名，否则 SHALL 标为 `association_rejected`，不得消耗正文预算或作为该实体的覆盖。系统 SHALL 按 provider/article ID、标准化标题与精确时间、正文 hash 分层去重，并优先对最新的、标题主体通过的唯一候选获取正文。provider 枚举 SHALL 以事件循环安全的有界重试处理瞬时空响应，重试后仍为空时 SHALL 标记为 `provider_unavailable`，不得推断为零新闻或缺少 entitlement。只读诊断 MAY 用操作员显式给出的刚验证 provider 直接探测历史新闻，以区分 provider 枚举波动与历史接口故障；该结果 SHALL NOT 代替生产采集所需的当次动态 provider 枚举，或作为来源发布依据。若为延长超时直接调用底层 API，SHALL 使用与公共 `ib_async` API 相同的 TWS wire 日期格式；目标 historical-news request 收到明确 API 错误时，系统 SHALL 立即记录其错误码与文本，并归类为 provider 未订阅或请求拒绝，而非超时。系统 SHALL 使用历史头条原样返回的 `(providerCode, articleId)` 调用 `reqNewsArticle` 获取正文，并以事件循环安全的有界重试处理短暂正文空响应；二进制/PDF 或最终无正文 SHALL 保留为正文缺口。IBKR News SHALL 提供只读诊断，输出连接状态、server version、可用 provider、合约 conId、请求参数、API error 回调以及是否收到 `historicalNewsEnd`，以区分单个 provider、请求格式、权限和服务端无回调。

`pead_research` SHALL 仅从共享 `research_article` 资产读取第三方研究材料；它 SHALL NOT 将 earnings release、SEC filing、transcript、IBKR News 或 Yahoo News 当作研究输入。除 `full` 文档外，它 MAY 处理来源为 SemiAnalysis 的可验证 `partial` 文档，但 SHALL 保留该文档 version 的 `completeness`、`truncation_reason`、article ID 和 document/version 血缘，且提取结果 SHALL 仅陈述预览正文中可见的信息，不得将其称为完整文章。其他 `partial` 或 `teaser` 研究资产 SHALL NOT 被该消费者处理。其 platform 发布 SHALL 由隔离处理 smoke、选中输入的 document/version/完整性检查、Workflow memory insight/projection/event 输出血缘、失败隔离及 rollback drill 验收；SHALL NOT 要求 legacy 输出逐项等价。

`evidence_chain` SHALL 以实际 platform Chain report 的非结构化读取合同验收：documents、immutable versions/chunks、facts、evidence observations/projections 与 failures。`structured_observations` SHALL NOT 被作为该消费者的 comparison 输入或 platform 发布门，因为它不属于该报告的非结构化读取合同。验收 SHALL 在隔离 Workflow memory 中将该消费者路由到 platform，以真实行业配置和固定时点生成 no-LLM 报告；报告 SHALL 非空，命题所引用的 observation IDs SHALL 对应 platform evidence observation。当前受管文档的引用 SHALL 解析至 immutable document/version；早于 document-version 存储的历史证据观测 SHALL 显式标为 `evidence_snapshot`，并至少保留 observation ID、document ID、source URL、实体、来源实体、观测时间及原文证据片段，不得冒充完整文档版本。no-LLM 渲染 SHALL 以显式 unknown/未裁决表示未运行的语义 adjudication，SHALL NOT 调用外部 LLM 或把未裁决证据表达成已裁决结论。通过后的 release SHALL 保存 platform 输入、报告摘要/hash、claim assessment memory 输出、失败处理和 rollback evidence，且 SHALL NOT 要求 legacy 报告逐字等价。

`yfinance_live_news` SHALL 与 defeatbeta Yahoo 日级镜像、IBKR News 作为三个独立来源管理。它 SHALL 仅在 IBKR 的 TWS 不可达、权限/订阅不足、动态 provider 不可用、请求受限/拒绝或指定标的/切片失败时，作为失败范围的 fallback；IBKR 正常完成但无新闻 SHALL NOT 触发 Yahoo。系统 SHALL 按当前 PEAD targets 读取 `Ticker.news` 的候选，保留 Yahoo 原生 ID、publisher、原始发布时间、canonical URL、查询实体和抓取时点，并以 Yahoo ID 或 canonical URL 去重。Yahoo 的 ticker 推荐关系 SHALL NOT 单独构成主体正确性证据：候选标题必须命中查询实体的 ticker、注册公司名或已登记别名，才可标为 `title_verified` 并进入正文抓取；未命中的候选 SHALL 标记为 `association_rejected`，出现在验收报告中但不得作为文档资产或来源覆盖成功。正文 SHALL 由候选 URL 直接取得，并验证标题锚点仍在正文中；正文不可得、页面壳、视频或标题错配 SHALL 作为可追溯缺口。该来源的来源级验收 SHALL 输出 PEAD 实体、标题、URL、publisher、发布时间和关联判定的完整清单，且在人工审阅该清单前 SHALL NOT 更新其 source release overlay 或消费者路由。

#### Scenario: TrendForce RSS 过期或文章正文受付费墙限制

- **WHEN** 文章索引可发现候选 URL，但 RSS 过期、正文不足或付费墙阻止获取
- **THEN** 系统 SHALL 分别记录发现、正文获取与质量准入结果，并将该范围标记为 `partial` 或 `unreachable`
- **AND** 系统 SHALL NOT 因仅获得 URL、标题或摘要而将该文档计为完整覆盖或已发布资产

#### Scenario: SemiAnalysis 在同一窗口出现多封邮件和重复 RSS 项

- **WHEN** IMAP 与 RSS 在同一采集窗口返回多篇 SemiAnalysis 候选，且部分候选代表同一篇文章
- **THEN** 系统 SHALL 将全部候选记录进覆盖账本，并以邮件标识、canonical URL 和内容 hash 执行可审计去重
- **AND** 系统 SHALL 保存候选数、准入数、重复数、失败数与缺口原因，且优先保存可验证的邮件正文

#### Scenario: SemiAnalysis 只有未订阅预览正文

- **WHEN** IMAP 邮件或 RSS 证明来源、标识和时间有效，但正文在订阅边界截断
- **THEN** 系统 MAY 发布该文档为 `partial`，并在文档版本、血缘与验收报告中保留这一完整性状态
- **AND** 系统 SHALL NOT 将它计为全文、移除内容截断提示或覆盖之后取得的完整版本

#### Scenario: IBKR TWS 不可连接或没有新闻权限

- **WHEN** IBKR News 采集因 TWS/IB Gateway 未连接、新闻权限不足、provider 不可用或请求节流而无法完成
- **THEN** 系统 SHALL 记录与“零篇新闻”不同的失败状态、标的、provider 与时间切片
- **AND** 该失败范围 SHALL NOT 被标记为已通过覆盖验收或 `platform`

#### Scenario: IBKR 历史新闻请求无响应

- **WHEN** 只读 TWS 连接和 provider 枚举成功，但 `reqHistoricalNews` 在限定等待时间内既未回调结果也未回调错误
- **THEN** 诊断 SHALL 记录 server version、conId、provider、起止时间、请求 ID、API errors 与 `historicalNewsEnd` 缺失
- **AND** 系统 SHALL 将该范围标记为服务端无回调或待确认权限，而不是“零新闻”

#### Scenario: Yahoo 推荐新闻与查询实体不一致

- **WHEN** `Ticker.news` 为某个 PEAD 标的返回新闻，但标题不包含该标的的 ticker、公司名或实体别名
- **THEN** 系统 SHALL 将候选记录为 `association_rejected`，保留标题、URL、publisher、发布时间和查询实体以供审阅
- **AND** 系统 SHALL NOT 获取其正文、将其计入覆盖或发布为该标的的新闻资产

#### Scenario: Yahoo 候选标题正确但正文错误或不可得

- **WHEN** Yahoo 候选通过标题主体门，但 URL 对应页面正文无法取得或不包含可验证的标题锚点
- **THEN** 系统 SHALL 将候选记录为正文缺口或 `title_body_mismatch`
- **AND** 该候选 SHALL NOT 作为完整新闻文档发布

#### Scenario: 单个来源仅有部分范围达到发布门

- **WHEN** 某来源只有部分实体、主题或时间范围通过正文、时间、去重、质量与血缘检查
- **THEN** 系统 SHALL 仅将通过范围发布为 `platform`，并为其余范围保留 `legacy` 或 `shadow` 状态及可重试游标
- **AND** 系统 SHALL 输出来源级验收报告，而不得因消费者尚未切换而阻止通过范围发布

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
