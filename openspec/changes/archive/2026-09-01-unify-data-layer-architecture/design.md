## Context

当前代码的职责边界是按历史演进形成的：`ats.data` 同时包含 Provider 适配器和业务数据模块，`ats.structured` 聚合了结构化目录、采集、质量、存储和查询，`ats.data_platform` 提供消费者 facade，`ats.memory` 又同时管理 Workflow 记忆、文档资产、证据和测量表。配置也分散在 `structured_data.yaml`、`sources.yaml`、`news_sources.yaml` 和 Workflow 配置中。

本设计承接 `proposal.md` 和 `specs/data/data-layer-architecture/spec.md`。目标是先建立稳定的命名空间和责任契约，再逐步移动实现；不要求一次性重写已有 Provider 或数据库。

## Goals / Non-Goals

**Goals:**

- 让结构化和非结构化能力拥有同一个 `ats.data` 根命名空间，并按生命周期划分组件。
- 建立适配器、采集管道、存储、数据产品和 runtime 的单向依赖。
- 用统一 catalog 管理来源、数据集、状态、调度和质量门，同时保留结构化/非结构化配置的领域差异。
- 让 `ats.data_platform` 和 `ats.structured` 在迁移期成为兼容转发层。
- 拆分数据层 repository 的代码所有权，降低 `ats.memory` 的混合职责。
- 为开发者、运维者、使用者提供可执行且与代码同步的文档。
- 在 facade 阶段后，完成旧数据、旧调用方和旧实现的受控迁移与退役，而不是永久保留双路径。

**Non-Goals:**

- 本变更不引入新的外部数据 Provider；但可以将已经由 legacy 路径使用的 Provider 以受管 adapter 形式迁入，以修复既有消费者的字段或时效缺口。
- 本变更不把文档、数值观测和 Workflow 决策强行合并为同一数据模型或同一查询表。
- 本变更不持久化 ticker 股价、OHLCV、订单簿、期权链、Greeks 或隐含波动率。
- 本变更不在第一阶段删除旧表、旧配置或旧导入路径。
- 本变更不决定向量数据库、知识图谱或独立时序数据库的选型。
- 本变更不以 mock 或 facade 测试通过替代旧数据迁移、真实消费者切换或生产验收。

## Decisions

### 1. 以数据生命周期组织目录，而不是以信息类型建立两个平台

目标目录如下：

```text
src/ats/data/
├── core/                 # entity/source/lineage/quality/run 等共享契约
├── catalog/              # 配置模型、加载、引用校验
├── adapters/
│   ├── structured/       # SEC、Yahoo、defeatbeta、Consensus 等数值 Provider
│   └── unstructured/     # SEC 文档、release、transcript、RSS、研报等
├── pipelines/
│   ├── common/           # ingestion、admission、去重、发布
│   ├── structured/       # 数值标准化、准入、vintage
│   └── unstructured/     # 文档清洗、抽取、分块、证据准入
├── stores/
│   ├── structured/       # observations、artifacts、derived views
│   └── unstructured/     # documents、versions、chunks、evidence
├── products/             # structured、unstructured、combined、discovery
├── runtime/              # 即时行情和期权，不进入持久层
└── compat/               # 旧入口的转发和弃用标记
```

选择该方案是因为采集、存储、查询和运行时边界比“文章/表格”更能决定依赖方向。若按 `structured/` 和 `unstructured/` 各自复制一套完整平台，会重新产生两套 source registry、运行记录和质量机制。

### 2. `data_platform` 归入 products，`structured` 归入 data 实现

`DataProducts` 是消费者读模型，不是独立的数据域，因此正式实现迁移到 `ats.data.products`。`ats.structured` 当前包含多个生命周期职责，按职责拆到 `catalog`、`pipelines.structured`、`stores.structured` 和 `products.structured`。

迁移期保留：

```python
from ats.data.products import get_data_products       # 新入口
from ats.data_platform import get_data_products       # 兼容入口
from ats.data.structured import get_repository         # 新结构化入口
from ats.structured import get_repository              # 兼容入口
```

兼容模块只允许 re-export 或调用新实现，不允许继续添加业务逻辑。

### 3. 依赖方向由架构测试固定

依赖规则固定为：

```text
agents/workflows
        ↓
data.products / data.runtime
        ↓
data.pipelines
        ↓
data.adapters + data.stores
        ↓
external providers / filesystem / database
```

`data.core` 和 `data.catalog` 是跨结构化/非结构化共享边界。适配器不得导入 products 或 memory；stores 不得发起网络请求；products 不得导入具体 Provider；`data.structured` 与 `data.unstructured` 不得互相导入实现模块。通过 import-linter 风格的静态检查或等价单元测试固定这些规则。

### 4. 配置采用“总目录 + 领域配置 + Provider 配置”

目标配置结构：

```text
config/
├── data/
│   ├── catalog.yaml          # source/dataset/entity/status 的总索引
│   ├── structured.yaml       # 指标、映射、vintage、质量门
│   ├── unstructured.yaml     # 文档类型、正文策略、分块和保留策略
│   ├── schedules.yaml        # 采集触发与预算
│   └── providers/             # Provider 认证、endpoint、限流、保存约束
├── workflows/                 # PEAD、Evidence、Sector 等消费者配置
└── runtime/                  # broker、market 等即时查询配置
```

`catalog.yaml` 只负责索引和引用，不复制全部 Provider 参数。统一 loader 返回经过 schema 校验的配置对象，并检查 source、dataset、adapter、entity 和 fallback 引用。现有配置在兼容期通过 loader 的 legacy overlay 读取，禁止同时出现两套独立状态。

### 5. 先拆代码所有权，再拆数据库物理边界

第一阶段不改变 SQLite 文件位置。把 `memory.store` 中的数据层表按 repository 接口迁移到 `data.stores`，由数据层 repository 通过兼容访问旧表；Workflow 记忆继续由 `ats.memory` 管理。这样可以先验证行为和职责，再决定是否拆库或迁移表，降低一次性数据迁移风险。

数据层 repository 的边界为：结构化观测、原始 artifact、文档元数据/版本/分块、证据、ingestion runs。交易、决策、性能和 agent run 仍属于 memory。

### 5.1 受管公司财务来源链与完整报表合同

`defeatbeta` 与 `yfinance` 已由既有读取路径使用；将它们和 SEC Facts、发行人 IR 收敛到
`company_financials` 不构成新增外部 Provider。每个 adapter 只读取低频的季度/年度财务
报表行，保留原始响应、查询时点、实体、报告期、币种和 source version；它不得读取或
写入价格、OHLCV、订单簿、期权链或其他 runtime 市场数据。

按可达性依次尝试 `defeatbeta_stock_statement`、`yfinance_financials`、`sec_companyfacts`、
`company_disclosures`。来源链在第一个提供同一实体、同一报告期、可确认币种/单位且覆盖收入、
毛利、营业利润、净利润、EPS、经营现金流、CapEx、现金、总资产、总负债、权益及债务的报表包
后停止；毛利率、营业利润率和 FCF 由该报表包派生。任何缺失、陈旧、单位不明或不完整的前序
来源才允许进入下一来源。不同 Provider 的结果只可保留作审计，不得逐字段拼接成一个“官方”报表。
唯一受控例外是：SEC Facts 与同实体、同报告期、同币种的发行人 IR 单独均不完整时，可组成
`official_disclosure_bundle`；每个字段仍须记录其 SEC 或 IR artifact，任何由 XBRL 输入推导的行
都必须可重算。Provider 数据须标记为 Provider-reported，SEC/IR 数据才可标记为 official。P/E 不持久化，因为价格属于
runtime，只以已发布 EPS 和即时价格按需计算。每次发布前，需在隔离库及当前 platform target
验证当前报告期、核心字段、币种/单位、来源停止条件、与既有路径的差异及退回 legacy 的行为。

`sector_constituent_financials` 是上述同一报表包的轻量消费者，而非独立的行业财务来源。
它按各行业配置中的成分股逐一读取最新完整 `company_financials` 报表包，并只从该包派生
毛利率、营业利润率与收入同比；不得把 Provider 的 TTM 或网页字段静默填入缺失的持久化
财报值。市值、Trailing/Forward P/E 与 Beta 继续由 runtime 查询提供，且不参与该消费者的
持久化财务对账。缺少合格报表包时，消费者必须显式暴露 `no_coverage`，而不是伪装为已受管
财务事实。

### 5.2 财务语义的边界：ADR EPS 与债务

每股指标的经济单位不能由 entity 的报告币种推断。TSM 的官方 release 同时给出
普通股 `NT$/share` 和 ADR `US$/ADR`，因此二者作为不同 metric 发布；基础面 DTO
优先 ADR metric，缺失时才退到带 `TWD/ADR` 单位的镜像 fallback 或普通股原值，绝不
把 5:1 ADR 比例当作跨源数据错误。镜像或 fallback 返回的历史每股值还可能经过市场
调整（KLAC 的历史 EPS 即显示 10:1 拆股调整）；该值发布到独立的
`financial.eps.diluted.market_adjusted`，而不是覆盖发行人的原始 GAAP 每股值。

债务也不以字段名称相同就视为同一概念。`us-gaap:LongTermDebt` 明确排除资本租赁且
不包含全部当前到期债务，必须单列为长期债务。若存在
`us-gaap:DebtLongtermAndShorttermCombinedAmount`，它才是总债务的优先官方事实。
镜像或 fallback 的 `total_debt` 发布为 `financial.total_debt.provider_reported`；在未能
证明其是否包含融资/经营租赁、短债或其他项目以前，它只可作产品 fallback，不可与官方
总债务自动对账。这样既保留镜像的补缺价值，也不会用错误的 SEC alias 或 Provider
定义制造 3%–43% 的假冲突。

已写入旧库的错误语义不能靠追加新 observation 自动修复。历史修复必须先建立 SQLite
一致性备份，再以事务重分类旧镜像 series、保留原 observation/artifact ID、更新 candidate
的 metric 血缘，并以最新 vintage 与 dataset 配置的对账容差重算冲突。旧 TSM release 的
“NT$ million”未缩放值已经被缩放后的新 vintage 取代；重算不得把该被取代的历史版本重新
当作有效来源。迁移结果必须分别报告受影响实体的未解决冲突和范围外既存冲突，后者不能被
静默掩盖。

PEAD 的 shadow 对账以完整的报表合同和经济语义为门槛，而不是 DTO 字节相等。相同期间的
收入、利润率、净利润、EPS、CapEx 或 FCF 数值变化，缺少核心字段，或平台期间更旧，均阻断
发布。平台完整但 legacy 缺字段、平台提供完整的更新期间，及同期间仅发生显式单位修正或
Provider-reported debt 至官方总债务定义的切换，可作为带字段级审计的 governed upgrade。
CapEx 在持久层以正投资额保存，在 PEAD DTO 中按现金流出显示为负；FCF 因而保持为 CFO +
显示的 CapEx。这个转换只发生在产品展示层，不改变原始 observation。

### 6. 按阶段迁移，每阶段设置回滚点

每个阶段都先引入新入口，再迁移调用方，最后删除重复实现。每阶段必须通过导入兼容、配置校验、数据产品回归、受影响来源 smoke test 和受影响 Workflow 回归；失败时只回退当前阶段的 feature flag 或兼容转发，不删除已有数据。

### 7. 将“兼容 facade 完成”与“旧实现退役”分成独立里程碑

现有代码仅完成 facade、配置总目录和代码所有权边界；这些措施允许新旧入口共享底层资产，但不构成数据迁移或消费者切换。后续里程碑必须以可审计的 migration manifest 驱动，按下列顺序推进：

1. 建立 legacy inventory：每个旧模块、配置 alias、表、artifact 目录、文档索引及其 Agent/Workflow 调用方都要有稳定 ID、owner、数据量、血缘和回退路径。
2. 为每个数据域建立可续跑迁移单元。结构化迁移必须同时覆盖历史 `measurement_*` 表和已经受管但仍位于旧 SQLite 的 `structured_*` 表；观测以 dataset/entity/period/vintage 为单位，文档以 document/version/chunk/alias/evidence 为单位，artifact 以内容 hash 与关联记录为单位。
3. 每个迁移单元在写入前备份或保留原始资产，写入后比较计数、主键、内容 hash、期间范围、版本/known_at、血缘和质量状态。差异必须显式记录，不得静默跳过。
4. 新 repository 完成读写验收后，数据源/数据集的发布只以数据层证据决定：原始 artifact/lineage、实体与报告期、单位/币种、完整性、时效、质量状态，以及原始指标到派生指标的可复算对账均须通过。数据源/数据集发布不得等待 Agent/Workflow shadow 观察期。消费者仅在其自身读取实现或输出逻辑变更时执行一次即时 smoke/regression；其结果是调用方回归证据，不是数据层发布门。旧路径的网络错误不能替代数据质量证据；任一异常可按 source/consumer 独立切回 legacy。
5. 全部调用方迁移且达到上述发布门后，删除旧实现和 alias。删除前冻结 legacy 路径的新写入，并保留可恢复的备份、迁移 manifest 和发布记录。

Workflow 的 task projection、claim proposal/assessment、PEAD/Sector/Chain/Chief 的报告和运行结果是
`ats.memory` 的内部状态：它们可以引用已发布数据的 lineage，但不是可供其他 Workflow 复用的
结构化或非结构化输入，也不属于数据迁移 domain 或 consumer 数据对账范围。

不采用一次性全库复制或全局开关切换：两种方式都会放大大文件、历史版本和 Workflow 输出差异的排查范围。

### 8. 第三方非结构化来源先以数据源门槛独立发布

TrendForce 文章、SemiAnalysis 订阅文章和 IBKR News 都是多个研究产品可能复用的原始输入，必须先进入共享的非结构化资产层；消费者只能在其上建立各自的检索、分类或实体关系投影，不能各自重新抓取、保存或判断同一篇原文。

本阶段的验收对象是单个数据源，而不是 Agent 或 Workflow 输出。每份候选资产必须保留来源原生标识、canonical URL、采集时间、发布时间、正文提取结果、内容 hash、实体/主题标签、质量状态和运行记录。发布门仅检查数据本身：范围覆盖、来源真实性、正文可用性、去重、时效、血缘以及可重放的质量判定；不要求消费者 shadow 对账，也不因尚未切换的消费者阻塞数据源发布。

- **TrendForce 文章**：以文章索引发现为主，RSS 仅作为辅助线索；每篇文档保存入口页与正文获取的状态。付费墙、摘要不足或 RSS 过期必须分别记录为 `partial` / `unreachable`，不能被“发现了 URL”误判为已覆盖。TrendForce DRAM 合约价属于结构化数据集，与文章资产分别注册、运行和验收。
- **SemiAnalysis（IMAP/RSS）**：复用 `data.research.ingest` 的邮件与 RSS 获取能力，不另建邮箱抓取器。以 IMAP `Message-ID` / UID 与 canonical URL 作为稳定去重键；同一窗口内的所有匹配邮件与 RSS 项都要进入候选账本，邮件正文优先于 RSS 摘要，二者可合并为同一文档版本但不可静默丢弃。未订阅导致的预览正文可以发布，但文档版本、质量结果和验收报告必须显式为 `partial`；它不是全文，也不得覆盖以后取得的完整版本。
- **PEAD Research 消费规则**：`pead_research` 的输入范围仅为 TrendForce、SemiAnalysis 等第三方 `research_article` 共享资产；官方 release/filing/transcript 由 PEAD Graph 消费，IBKR/Yahoo 新闻由 monitor/Graph 消费。除完整研究文章外，允许 SemiAnalysis 的 `partial` 预览正文进入提取；其他不完整研究资产仍不进入该消费者。提取提示和每项 Workflow memory 输出必须能经 `article_id → document → immutable version` 回溯至 `completeness` 与 `truncation_reason`，并明确限制为原文可见信息，不能把预览补全或标示为全文。发布验收以 platform 只读选择、隔离处理 smoke、输出的 article/version 血缘、失败隔离和 rollback drill 组成；legacy 结果只可作背景，非发布门。
- **IBKR News（第一优先级）**：使用只读 TWS/IB Gateway 新闻接口，不持久化行情或期权数据。历史补采恢复 legacy 语义：每次运行使用 `reqNewsProviders()` 返回的全部 provider，并为每篇资产保留 provider/article ID、查询实体、原始精确新闻时间及其时区/会话标识；正文预算和质量门再决定可发布范围。标题必须独立命中查询实体 ticker、公司名或登记别名；不命中的候选进入 `association_rejected` 账本而不取正文。去重按 provider/article ID、标准化标题与精确时间、正文 hash 分层进行，正文预算按最新、主体通过的唯一候选分配。provider 枚举为空时，以 `IB.sleep` 进行有界重试，最终空结果只能标记为当前会话 `provider_unavailable`，不得推断为无 entitlement。只读诊断可以显式探测刚刚已知的 provider，即使当次枚举暂时为空；这只能用于分辨枚举波动与历史新闻能力，生产采集仍必须使用新鲜的动态枚举，不能把该临时 provider 当作发布范围。扩展超时的底层请求必须复用 `ib_async` 的 TWS wire 时间格式，不能把 Python `datetime` 原样传入 socket；TWS 对该请求的明确拒绝会立即结束本次等待，并分类为订阅不足或请求被拒绝，而不会伪装成超时。对 `reqHistoricalNews` 返回的每个 `(providerCode, articleId)`，以原样参数调用 `reqNewsArticle`；短暂的空正文或异常可有界重试，二进制/PDF 返回仍明确记为正文缺口。验收按已配置的标的、provider 和时间切片逐项记录；TWS 未连接、缺少新闻权限、provider 不可用、请求受限和确实没有新闻是不同状态，只有最后一种才可计为“零篇但成功”。诊断入口还必须输出只读连接、server version、可用 provider、合约 conId、每个探针的 API 错误回调及是否收到 `historicalNewsEnd`，使无响应能区分为请求格式、单一 provider、权限或服务端无回调。
- **yfinance Yahoo News（仅可达性 fallback）**：该来源调用 `Ticker.news`，与 defeatbeta 的 Yahoo 日级镜像分别注册、运行和验收。它不是 IBKR 健康时的并行主来源；仅在 IBKR 的 TWS 不可达、权限/订阅不足、动态 provider 不可用、请求受限/拒绝，或指定标的/切片失败时，处理失败的范围。IBKR 正常完成但没有新闻时不得触发 Yahoo fallback。按 PEAD 当前 targets 逐一请求，保留 Yahoo content ID、publisher、原始发布时间、Yahoo canonical URL、查询实体与抓取时点；同一 Yahoo ID 或 canonical URL 只形成一个候选。主体准入严格采用 `title_verified`：标题须命中该实体 ticker、注册公司名或别名的完整词组；仅由 Yahoo 推荐关系带回、标题未命中的候选标为 `association_rejected`，保留在报告而不取正文/不发布。通过标题门的候选仅在直接取得正文且正文仍能验证标题锚点时才可接受；页面壳、导航、付费墙、视频或正文错配均记录为缺口。来源验收输出 PEAD 每个标的的全部标题、URL、publisher、时间和主体判定，供人工审阅；未获人工审阅前不写入 source release overlay。

所有真实来源运行必须先通过确定性单元测试，并在隔离数据库/资产目录中执行。来源级报告将每个来源分类为 `equivalent`、`governed_upgrade`、`platform_regression`、`partial` 或 `unreachable`；只有无未解释平台回归且满足该来源覆盖/质量门的范围才可标记为 `platform`。失败范围保持 `legacy` 或 `shadow`，并保留可重试游标和缺口原因。本子阶段明确不修改消费者路由、不删除旧采集逻辑；这些是后续统一消费者验收与 legacy retirement 的独立工作。

### 8.1 Evidence Chain 以实际 platform 报告验收，而非通用表哈希

`evidence_chain` 的可切换读取合同是非结构化 document/evidence 产品：官方 release、filing、transcript、第三方 research/RSS/news 的 immutable document/version/chunk，及其 facts、evidence observations、projections 与失败记录。Chain report 基于这些观测产生命题覆盖、证据簇和可追溯原文；claim assessment、提案和报告文件则是 Workflow memory 输出或展示，不成为数据层输入。

通用数据库迁移比较覆盖 `structured_observations`，但该表不是 `evidence_chain` 的读取合同；例如 NVDA 的 84/252 行差异必须留给结构化数据消费者处置，不能把已验证的非结构化报告错误地降级为 platform regression。发布验收必须在复制的 Workflow memory 中强制 `evidence_chain=platform`，用真实行业配置和固定 `as_of` 渲染 no-LLM 报告，验证报告非空、命题输出可追溯到 platform evidence observation 与 immutable document/version，且失败记录仍被展示。no-LLM 路径必须输出明确的未裁决/unknown，而不是调用外部 LLM 或伪造语义结论。完成该验收后，以 `governed_platform_evidence_report_upgrade` 保存 comparison/independent verification，执行 `platform → legacy → platform` 演练；legacy 报告字节等价不是门槛。

## Risks / Trade-offs

- [循环依赖在移动期间短暂增加] → 先建立 core/interfaces 和架构 import test，再移动实现；禁止通过局部动态 import 掩盖长期循环。
- [旧入口与新入口出现双写差异] → 兼容层只转发到单一实现，增加新旧返回值对账测试，禁止复制业务逻辑。
- [配置迁移导致来源状态分裂] → 统一 loader 采用明确优先级和 legacy overlay，并输出配置来源；旧配置在过渡期只读。
- [memory.store 拆分影响旧 Workflow] → 先按 repository 接口隔离代码所有权，保留旧表和回退路径，完成消费者迁移后再做物理迁移。
- [目录重排导致导入路径破坏] → 为所有公开旧路径保留兼容模块，加入全仓库 import smoke test，并设置弃用日志而非立即删除。
- [新目录过度设计] → 第一阶段只建立边界和 facade，只有在现有职责明确时才移动文件；不为未来的向量库或知识图谱预建无用抽象。
- [Provider 报表语义与 SEC/IR 不一致] → 选择单一完整报表包、保留 provider 原始行与报告期和身份；缺失币种、时点不清、核心字段不全或同包内口径不明时继续后续来源，不把 Provider 字段混合后发布。SEC Facts 与同期间 IR 的受控官方包必须保留字段级来源且通过原始到派生重算。
- [TrendForce 付费墙或 RSS 过期造成假覆盖] → 分离发现、正文获取和质量准入状态，并在报告中按原因标记缺口。
- [SemiAnalysis 邮件与 RSS 重复或只处理最新一封] → 以 Message-ID/UID 与 canonical URL 去重，并以窗口候选数、准入数、重复数和失败数验收。
- [IBKR TWS 连接、权限与限流被误判为无新闻] → 对每个标的/provider/时间切片记录可区分的运行状态，只读访问并遵守 provider 节流。

## Migration Plan

1. **阶段一：基线与 facade**：建立目标目录、依赖规则、compatibility facade 与基础回归。该阶段已完成，但不改变生产数据或消费者默认路径。
2. **阶段二：legacy inventory 与迁移准备**：补全旧模块、配置、表、artifact、文档资产和消费者矩阵；为每个数据域定义迁移 manifest、备份、范围、对账键和 rollback 条件。
3. **阶段三：数据迁移与双读**：先迁移历史 `measurement_*` 及既有 `structured_*` observations/artifacts/catalog/run records，再迁移非结构化 documents/versions/chunks/evidence；每批迁移均执行计数、hash、vintage、血缘和查询结果对账，并保留可续跑状态。仅完成其中一类结构化表不得将结构化域标记为完成。
4. **阶段四 A：第三方非结构化来源级发布**：在不修改消费者路由的前提下，分别完成 TrendForce 文章、SemiAnalysis（IMAP/RSS）和 IBKR News 的共享资产、覆盖账本、隔离采集和来源质量报告；满足门槛的 source 范围可发布为 `platform`，其余保持 `legacy`/`shadow` 并留下缺口记录。
5. **阶段四 B：Agent/Workflow 切换**：逐个将 PEAD、Sector、Evidence/Chain、Chief 和其他直接使用 legacy data 路径的调用方改为 `ats.data.products` / `ats.data.runtime`；仅在调用方自身变更时执行即时 smoke/regression 与回滚演练，不把它当作已通过数据源发布的前置条件。
6. **阶段五：旧逻辑退役**：旧数据和全部消费者通过验收并完成稳定观察后，冻结旧路径写入，删除旧模块、配置 alias 与重复实现；最后执行全仓导入扫描、数据库/文件恢复演练和最终验收报告。

回滚策略：阶段二至四的任一失败只回退相应 source 或 consumer 的 mode，保留旧读取路径与已保存的原始资产。阶段五删除前必须具备经验证的备份和恢复步骤；一旦删除开始，恢复通过备份/manifest 进行，不得依赖已移除代码。

## Deferred Decisions

- SQLite 是否最终拆为结构化库、非结构化库和 Workflow memory 三个物理文件：不属于本变更，先完成代码所有权隔离并继续共用现有 SQLite 文件。
- 是否引入专门的依赖检查工具：不作为本变更新依赖，先使用现有测试框架和静态导入检查；只有现有检查不足时再单独提案。
