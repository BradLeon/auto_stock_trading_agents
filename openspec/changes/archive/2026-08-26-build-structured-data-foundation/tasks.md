## 1. 基线、范围与验收门

- [x] 1.1 盘点现有财务、Consensus、`stock_statement`、台湾/韩国出口的调用方、返回契约、覆盖实体和关键指标，形成迁移矩阵；另将 IBKR/yfinance 股价与期权路径登记为 runtime/excluded
- [x] 1.2 从 PEAD、Sector、Chain 和现有图表/报告实际消费中确定首批核心指标清单，并把未纳入字段定义为待映射而非静默丢弃
- [x] 1.3 为各首批数据集配置覆盖、时间连续性、新鲜度、跨源差异和切换容差，明确真实源验收样本与回滚条件
- [x] 1.4 补充旧路径 characterization tests，固定当前正常返回、缺失语义和错误降级行为
- [x] 1.5 运行当前结构化相关测试并保存基线报告；测试未通过时先修复或记录既有失败，不进入平台改造

## 2. 结构化平台核心底座

- [x] 2.1 定义与 Provider 无关的来源、数据集、原始快照、指标、序列、观测 vintage、派生定义、证据链接和数据快照领域对象
- [x] 2.2 以加法迁移扩展 SQLite 结构化目录和观测账本，保持现有 `measurement_series` / `measurement_points` 与旧数据库可读取
- [x] 2.3 抽出独立 structured repository 接口，并支持默认同库部署与可配置独立数据库路径
- [x] 2.4 实现内容寻址的原始 artifact/query-slice 存储、哈希去重、来源保留策略和可复现元数据
- [x] 2.5 实现核心指标注册、Provider 别名映射、单位/币种族、财期语义和待映射池
- [x] 2.6 实现 observation vintage 的幂等写入、修订追加、首次可见时间和原始 artifact 血缘
- [x] 2.7 为旧 measurement 记录提供兼容读取/审计迁移，禁止在无语义依据时猜测历史发布时间或改写旧值
- [x] 2.8 完成底座单元与迁移测试：空库、旧库升级、重复运行、修订、重启、并发读和 `as_of` 无前视；全部通过后进入下一阶段

## 3. 统一采集与准入生产线

- [x] 3.1 定义适配器批次契约，使适配器返回来源原生数据、原始 artifact、查询范围和 Provider 状态而不直接写业务表
- [x] 3.2 实现采集运行状态机，区分有新数据、无变化、零匹配、未发布、陈旧、不可达、未授权、解析失败和校验失败
- [x] 3.3 实现结构化中央准入，校验实体、指标、期间、数值、单位、来源时间和重复/冲突，并保留全部 reason codes
- [x] 3.4 实现单来源、单实体和单切片失败隔离，保证局部故障不阻断其他数据集
- [x] 3.5 实现按数据集和用途配置的主源/回退源策略，保留多来源并列值及可解释的实际选择原因
- [x] 3.6 编写统一适配器 contract tests 和采集集成 fixture，覆盖无单位、错实体、错期间、未发布、未授权、修订和局部失败
- [x] 3.7 在隔离数据库运行端到端假来源采集，核对 raw → candidate → accepted/quarantined → query 全血缘；全部通过后进入查询产品开发

## 4. 查询、计算与研究接口

- [x] 4.1 扩展 DataProducts 指标查询，支持最新 vintage、全部 vintages、指定来源和严格 `as_of`
- [x] 4.2 实现主源选择、回退原因、多源冲突及缺失状态随查询结果返回
- [x] 4.3 实现多实体横截面查询和财期、单位、币种、调整口径的可比性检查
- [x] 4.4 实现版本化派生注册与查询时计算，首批覆盖同比、环比、移动统计和显式汇率换算，缺少历史时不得按零计算
- [x] 4.5 基于同一查询结果提供 Pandas DataFrame 输出，保留值、期间、来源、vintage、质量和血缘列
- [x] 4.6 提供 accepted 数据产品的只读 SQL 入口/视图，默认排除隔离候选和内部 Workflow 表
- [x] 4.7 实现数据集/指标发现、来源健康、血缘钻取和 structured snapshot manifest 创建/重放
- [x] 4.8 编写查询测试，覆盖 SQL/Pandas 一致性、历史重放无前视、跨币种显式转换、不可比项、严格/宽松质量模式和快照重放
- [x] 4.9 使用合成大样本运行查询与写入基准，记录 SQLite 数据量、延迟和锁等待基线；未达到首期可接受阈值时先优化 repository，不提前引入外部服务

## 5. 第一批接入：台湾与韩国官方出口序列

- [x] 5.1 重构台湾财政部和韩国 ECOS 适配器，使其返回官方原始水平、单位、覆盖月份、发布时间/可见时间和原始来源 artifact
- [x] 5.2 将现有适配器提前计算的 yoy/mom 迁移为平台派生定义，并保持兼容输出可对账
- [x] 5.3 在隔离数据库回填声明窗口，验证月份连续性、重复运行幂等、来源修订和未到发布时间状态
- [x] 5.4 增加官方序列 fixture tests 和网络关闭测试，确保接口变化、空响应和局部不可达不会产生伪零值
- [x] 5.5 运行台湾/韩国真实源专项 smoke test，输出原始水平、派生变化率、覆盖/时效报告和与旧 Chain 结果的差异
- [x] 5.6 只有在专项测试与对账通过后，按 feature flag 单独切换 Chain 区域序列读取；执行一次回滚演练再进入运行时市场数据边界验证

## 6. 边界验证：股价与期权保持运行时查询

- [x] 6.1 在来源目录中将 IBKR/yfinance ticker 股价、OHLCV、期权链、Greeks 和隐含波动率标记为 runtime/excluded，而不是 planned 或 missing dataset
- [x] 6.2 审计调度器、适配器注册和 repository，确认本变更不会为行情或期权建立采集任务、raw artifact、measurement series、vintage 或历史回填
- [x] 6.3 保持现有 `market_data`、IBKR 与 options 运行时接口不变，并让组合查询结果能区分 persistent 研究事实与 runtime 市场输入
- [x] 6.4 增加边界测试：执行股价和期权查询后断言结构化数据库与 artifact 目录没有新增行情记录，同时既有运行时返回仍正常
- [x] 6.5 在隔离 Workflow 中组合一次持久财务/Consensus 输入与即时市场输入，验证 structured snapshot 只记录持久层输入
- [x] 6.6 将边界测试、来源分类和用户路由说明纳入验收；全部通过后进入财务数据迁移

## 7. 第二批接入：官方财务与 defeatbeta stock_statement

- [x] 7.1 为首批核心财务指标建立定义和 XBRL/Provider 映射，明确单季/累计、GAAP/non-GAAP、币种、单位缩放和公司财期
- [x] 7.2 实现官方公司财务适配器，优先消费 SEC/XBRL 或等价公司披露，并保存申报、报告期、发布时间和来源版本
- [x] 7.3 实现 defeatbeta `stock_statement` 适配器，通过远程 Parquet predicate pushdown 获取目标实体/期间切片并保存托管快照与上游 provenance
- [x] 7.4 将 `stock_statement` 可映射行项目写入共享指标体系，未知行项目进入待映射池而不形成平行 statement 数据库
- [x] 7.5 实现官方/镜像多来源并列、默认官方优先和跨源 reconciliation，差异不得通过覆盖或平均隐藏
- [x] 7.6 实现财务质量规则：财期连续性、覆盖起止、累计/单季一致性、基本 statement 恒等关系和单位突变
- [x] 7.7 增加 domestic、foreign private issuer、不同财年结束日、重述、镜像缺行、未知项目和官方/镜像冲突 fixture tests
- [x] 7.8 对 AMZN、MSFT、KLAC、TSM 及至少一个镜像缺失实体运行隔离专项，输出覆盖、映射率、差异、vintage 和 `as_of` 重放报告
- [x] 7.9 专项未达到阈值时保持 fundamentals 旧路径；达标后只开启影子读取和消费者对账，不在本任务中删除旧财务逻辑

## 8. 第三批接入：市场 Consensus

- [x] 8.1 将现有 Consensus 适配器接入统一批次契约，并把 `0q` 等相对标签在采集时绑定到具体公司财期/目标事件
- [x] 8.2 分别注册 reported actual、EPS/收入预测、预测区间、目标价、评级分布和评级变更等实际可用口径
- [x] 8.3 按每次真实抓取保存 Consensus snapshot 和 `known_at`，Provider 未提供发布时间时禁止构造更早历史
- [x] 8.4 实现 Consensus 覆盖、关键字段完整性、目标期间冲突、快照年龄和来源不可达质量状态
- [x] 8.5 增加季度滚动、快照修订、部分字段缺失、NaN、无覆盖和严格新鲜度 fixture tests
- [x] 8.6 在隔离数据库连续抓取/模拟至少两个可见时点，验证早期 `as_of` 不使用后期预测，并与现有 PEAD 输入对账
- [x] 8.7 对账达标后按 feature flag 切换 PEAD Consensus 读取并完成回滚测试；旧适配器保持兼容直至后续退役变更

## 9. 第四批验证：证据型融资、估值与 ARR

- [x] 9.1 建立非上市公司稳定实体和证券可选映射，首批包含 OpenAI 与 Anthropic
- [x] 9.2 实现文档数值候选、document/version/span 血缘、来源等级、提取方式、核验状态和纠错历史
- [x] 9.3 实现实体、指标、事件/期间、单位和证据完整性准入，模型置信度不得单独通过发布门
- [x] 9.4 实现融资事件去重，使多篇转述绑定同一事件，并分别保存融资金额、估值和交易日期等观测
- [x] 9.5 使用阶段一 accepted 文档或固定公开样本建立 OpenAI/Anthropic fixture，覆盖一手披露、媒体转述、摘要不完整、单位歧义和数值冲突
- [x] 9.6 实现最小人工核验入口和审计日志，允许 accepted、rejected、needs-evidence 与 superseded 状态转换
- [x] 9.7 运行隔离专项并人工抽查全部发布样本的实体、数值、单位、时间和原文位置；未达到 100% 抽样正确率时不得进入默认查询

## 10. 面向角色的数据层文档

- [x] 10.1 创建 `docs/STRUCTURED_DATA_DEVELOPER.md`，说明设计哲学、系统边界、领域对象、组件职责、组件架构图、采集/准入/查询数据流图、存储选择和迁移策略
- [x] 10.2 在开发者文档中加入适配器契约、指标映射、扩展新来源的完整示例、测试分层、常见错误和禁止绕过的准入/血缘规则
- [x] 10.3 创建 `docs/STRUCTURED_DATA_OPERATIONS.md`，维护已接入、计划接入、未接入和 runtime/excluded 来源矩阵，并列明认证、更新频率、保存限制、依赖和责任边界
- [x] 10.4 为每个来源记录官方或可验证的 QPS/限流、内部请求预算、并发、缓存、重试/退避和超时；无权威 QPS 时标记 unknown 并说明保守配置，禁止编造限制
- [x] 10.5 在运维文档中写明新增来源的注册、凭证、fixture、隔离真实源测试、质量门、上线、监控、回滚和退役步骤，以及每一步的通过标准
- [x] 10.6 创建 `docs/STRUCTURED_DATA_USER_GUIDE.md`，提供数据发现、CLI、Python、SQL、Pandas、最新值、全部 vintage、`as_of`、横截面、派生计算、分类和血缘查询示例
- [x] 10.7 在使用者手册中说明 Agent/Workflow 如何消费 DataProducts、如何组合 persistent 与 runtime 输入，以及何时直接调用 IBKR/yfinance 获取股价或期权
- [x] 10.8 更新 `docs/DATA_ARCHITECTURE.md` 作为三份角色文档的总入口，并增加文档链接、命令和来源矩阵一致性测试；全部通过后才视为文档交付完成

## 11. 质量、运维与可观察性

- [x] 11.1 扩展 `ats data` 运维入口，提供 structured sources、datasets、metrics、coverage、quality、lineage、conflicts、pending mappings 和 ingestion history
- [x] 11.2 按 coverage、accuracy/reconciliation、freshness、completeness、availability 五个维度生成机器可查和 Markdown 质量报告
- [x] 11.3 让每个数据集的质量阈值可配置，并在报告中区分未发布、无覆盖、不可达、未授权、解析失败、校验失败和陈旧
- [x] 11.4 增加原始 artifact 存储用量、去重率和来源保留限制报告，为后续确定保留期限提供真实依据
- [x] 11.5 增加 CLI/报告 fixture tests，验证空库、部分成功、多源冲突、待映射、隔离候选、来源不可达和 runtime/excluded 展示
- [x] 11.6 在隔离库运行全来源质量报告，确认报告总数能与采集运行、accepted/quarantined 和查询覆盖逐项对账，并与运维来源矩阵一致

## 12. 消费者切换、历史重放与回滚

- [x] 12.1 为持久来源和消费者建立独立 feature flags，支持 legacy、shadow、platform 和 fallback 模式；runtime 股价/期权路径不参与数据迁移开关
- [x] 12.2 让现有财务、Consensus 和 Chain 兼容接口可从 DataProducts 组装旧返回对象，保持业务调用契约
- [x] 12.3 为 PEAD、Sector、Chain 和图表查询记录 structured snapshot manifest，包含持久观测、来源选择、vintage 和计算版本，不包含未持久化行情
- [x] 12.4 实现 snapshot manifest 离线重放测试，证明后来新增数据、来源优先级变化和公式升级不会改变历史持久输入
- [x] 12.5 为每个持久数据消费者执行 shadow reconciliation，按预设容差核对关键值、缺失语义、期间和来源，并生成切换清单
- [x] 12.6 分消费者切换且观察至少一个完整数据更新周期；任一质量门失败时回退对应消费者，不影响已通过来源
- [x] 12.7 运行 PEAD、Sector、Chain 及相关 Agent/Workflow 的完整回归，既验证任务能运行，也比较关键持久输入、runtime 组合和输出差异，不以纯 mock 测试代替真实隔离数据测试

## 13. 最终验收与交付

- [x] 13.1 运行全部确定性测试、数据库迁移测试、适配器 contract tests、查询一致性测试、文档检查和 Workflow 回归，并保存测试清单与结果
- [x] 13.2 在隔离目录和隔离数据库运行首批真实持久源端到端采集，不覆盖生产数据，生成逐来源覆盖、准确性、完整性、时效性和失败原因报告
- [x] 13.3 人工抽查财务、Consensus、台湾/韩国出口及证据型事件样本，记录原始来源与标准化值对照；另验证股价/期权查询没有写入持久层
- [x] 13.4 运行历史 `as_of` 和 structured snapshot 重放验收，证明至少一组财务修订与 Consensus 快照不存在前视，并注明 runtime 市场输入不在重放范围
- [x] 13.5 分别以开发者、运维者和使用者身份走查三份文档，验证架构图、数据流、来源状态、QPS/预算、扩源步骤、取数计算示例和 Agent/Workflow 指引可用
- [x] 13.6 汇总仍未覆盖的数据源、待映射指标、授权限制、SQLite 性能和存储用量，作为后续小型 OpenSpec 变更输入
- [x] 13.7 在所有验收通过后标记本变更完成；旧持久取数代码只标记待退役，不在本变更中删除，删除另行提案并再次做完整 Workflow 验收

## 14. 可执行的数据源接入与发布闭环

- [x] 14.1 建立受控来源运行注册表，将配置中的 adapter key 映射到可验证的工厂和请求约束，并拒绝未注册与 runtime/excluded 来源
- [x] 14.2 实现统一来源注册校验，检查来源/数据集引用、适配器、请求预算、质量阈值、验收样本和发布边界，返回机器可读 reason codes
- [x] 14.3 实现可指定独立数据库与 artifact 根目录的统一隔离采集命令，支持实体、期间和 query scope，并让 source mode 实际控制非强制采集
- [x] 14.4 实现 release check，联合最近采集状态和数据集五维质量门判断来源是否可发布，失败时列出逐项原因
- [x] 14.5 实现可配置 release overlay、默认只读的 publish、显式 `--apply` 和非破坏 rollback；source/consumer 模式独立且保留审计历史
- [x] 14.6 增加生命周期测试，覆盖有效新来源、配置缺口、未注册适配器、隔离采集、质量门拦截、显式发布、回滚和 runtime/excluded 拒绝

## 15. 动态数据目录与可复制查询

- [x] 15.1 实现由 StructuredCatalog 与 repository 当前状态联合生成的 catalog 服务，区分注册能力、实际 accepted 覆盖、质量、发布模式和 runtime 边界
- [x] 15.2 实现 describe、availability 和 examples 数据产品，返回来源/数据集/指标/实体/期间/最新可见时间及从真实覆盖生成的精确命令
- [x] 15.3 扩展 `ats data` CLI，提供 `catalog`、`describe`、`availability`、`examples` 及 JSON/Markdown 输出，并在空库时明确 no-data
- [x] 15.4 增加 DataProducts/CLI 一致性与动态性测试，证明目录随实际观测变化、示例不引用不存在的数据、注册但无数据不会误报可查询

## 16. Agent/Workflow 结构化数据消费 Skill

- [x] 16.1 使用 skill-creator 在仓库内创建 `structured-data-consumer` Skill，规定自主 Agent 先发现、检查质量/时点、再查询，并动态路由 persistent 与 runtime 数据
- [x] 16.2 在 Skill 中说明 CLI、DataProducts、SQL/Pandas 和 structured snapshot 的选择边界，禁止复制静态指标清单或让确定性 Workflow 依赖 Prompt
- [x] 16.3 为 Skill 增加 UI metadata，运行 quick validation，并以公司数据发现、历史 `as_of` 和 runtime 行情路由场景做可观察行为检查

## 17. 角色文档补强与重新验收

- [x] 17.1 重写运维手册的实操路径，精确指出配置、运行注册、凭证、隔离目录、评测、发布、监控和回滚位置及逐条命令/通过标准
- [x] 17.2 重写用户手册的数据能力发现路径，列出 catalog/describe/availability/examples/series/derive/lineage 的命令和 Agent/Workflow 使用方式
- [x] 17.3 更新开发者文档与 DATA_ARCHITECTURE 入口，补充来源生命周期、release overlay、动态目录和 Skill/API 分工的组件图与数据流
- [x] 17.4 运行 OpenSpec strict validation、文档/Skill 契约测试、结构化专项测试和完整测试套件，生成补充验收结果；全部通过后才重新标记变更完成
