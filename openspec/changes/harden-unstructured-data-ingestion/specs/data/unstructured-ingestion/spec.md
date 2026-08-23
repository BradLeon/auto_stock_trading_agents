## Purpose

定义非结构化研究数据从来源发现、身份与期间校验、全文完整性判断、版本化存储到下游读取的可信契约，使多个 Agent 复用同一资产时只消费已验证且可追溯的内容。

## ADDED Requirements

### Requirement: 文档类型表达业务语义而非文件格式

系统 SHALL 区分 research article、news item、company release、investor presentation、earnings transcript 与 regulatory filing；HTML、PDF、email、structured text SHALL 作为载体格式单独表达。系统 SHALL 继续读取既有 `article|news|release|deck|transcript|filing` 类型，并 SHALL 为新旧类型提供确定性映射。

#### Scenario: 同为 PDF 的两类正式材料

- **WHEN** 一份 PDF 是公司季度业绩演示，另一份 PDF 是 10-K
- **THEN** 前者 SHALL 分类为 investor presentation，后者 SHALL 分类为 regulatory filing
- **AND** 两者的载体格式均 SHALL 为 PDF

#### Scenario: 读取旧类型

- **WHEN** 目录中存在历史 `deck` 或 `article` 文档
- **THEN** 统一读取接口 SHALL 继续返回该文档
- **AND** 系统 SHALL 按兼容映射给出新的业务语义，SHALL NOT 要求重写历史正文

### Requirement: 来源优先级按数据用途确定

系统 SHALL 为每种文档语义声明来源优先级。实时新闻 SHALL 优先使用 IBKR 等实时授权源；结构化电话会纪要 SHALL 优先使用 defeatbeta/Yahoo Finance 结构化数据并允许公司 IR/人工文件覆盖；正式公告与演示材料 SHALL 优先使用 SEC 或公司 IR；Tavily SHALL 仅产生候选 URL，不得单独使候选成为可信资产。

#### Scenario: Tavily 找到纪要页面

- **WHEN** Tavily 返回一个标题和正文均像电话会纪要的页面
- **THEN** 系统 SHALL 先执行公司身份、目标期间、结构和完整性校验
- **AND** 任一强校验未通过或无法判断时 SHALL 将其隔离，SHALL NOT 写为可信文档

#### Scenario: 实时与日级新闻源并存

- **WHEN** IBKR 正常连接且第三方 Parquet 快照也可用
- **THEN** 盘中增量新闻 SHALL 由 IBKR 提供
- **AND** Parquet 新闻 SHALL 用于日级回填、历史覆盖和缺口检查，SHALL NOT 冒充盘中实时源

### Requirement: 候选文档必须经过强制准入

所有自动发现的非结构化内容 SHALL 先成为候选，并基于预期实体、目标事件/期间、文档语义和完整性作出 accepted 或 quarantined 决定。未知、空值、`TODO` 或不可解析配置 SHALL NOT 使强校验自动通过。隔离记录 SHALL 保留来源、候选标识、失败原因和采集时间，且下游默认查询 SHALL 排除隔离内容。

#### Scenario: 公司身份不匹配

- **WHEN** 目标实体为 SKHY 而候选正文或来源标识指向 GSK
- **THEN** 候选 SHALL 被隔离并标记 identity mismatch
- **AND** 下游按 SKHY 查询 SHALL NOT 返回该正文

#### Scenario: 目标期间未知

- **WHEN** 目标财季为空、`TODO` 或只有财年而无季度
- **THEN** 自动搜索候选 SHALL NOT 因无法比较期间而被放行
- **AND** 系统 SHALL 要求从 earnings calendar、结构化来源或人工输入解析目标期间，否则记录 period unresolved

#### Scenario: 多个校验失败

- **WHEN** 候选同时存在身份和期间错误
- **THEN** 隔离记录 SHALL 保留全部失败原因
- **AND** 来源健康报告 SHALL 同时计入相应错误类别

### Requirement: 结构化电话会纪要可验证且正文纯净

系统 SHALL 优先消费带 symbol、fiscal year、fiscal quarter、report date、paragraph order、speaker 与 content 的结构化纪要。接受前 SHALL 核对实体、期间、财报日期、段落连续性、speaker 覆盖、开场/Q&A/结束结构与前端噪声比例。存储 SHALL 同时保留原始结构和标准化正文；任一强校验失败 SHALL 隔离。

#### Scenario: defeatbeta 提供完整季度纪要

- **WHEN** 结构化记录的 symbol、财年季度和报告日与目标事件一致，且段落/speaker 结构完整
- **THEN** 系统 SHALL 按 paragraph order 生成标准化正文并保存来源结构
- **AND** 下游 SHALL 可按 document id 读到无 JavaScript、CSS、导航和广告的正文

#### Scenario: 网页正文包含电话会结构但公司错误

- **WHEN** 网页正文含 Operator、Prepared Remarks 和 Q&A，但公司身份不匹配
- **THEN** 结构校验成功 SHALL NOT 覆盖身份校验失败
- **AND** 文档 SHALL 被隔离

#### Scenario: 结构化主源缺失

- **WHEN** 某实体在结构化主源没有目标季度纪要
- **THEN** 系统 SHALL 依次尝试公司 IR/人工文件和其他已声明的结构化来源
- **AND** Tavily 候选若无法完成同等级校验 SHALL 只记录缺口，不得降级放行

### Requirement: 正式公告和演示材料必须绑定公司与事件

SEC/IR 文档 SHALL 通过公司身份、来源域名或 CIK、form/exhibit 类型和目标事件日期校验。SEC 8-K 路径 SHALL 只在确认目标 Exhibit 99.x 及业绩语义后接受 release；无匹配 Exhibit 时 SHALL 记录缺口，不得回退到最大 HTML。Investor presentation SHALL 确认公司、期间和演示语义。

#### Scenario: 最新 8-K 是融资事件

- **WHEN** 目标公司的最新 8-K 正文是股票发行、warrant 或其他非业绩事件
- **THEN** 系统 SHALL NOT 将其标记为 earnings release
- **AND** SHALL 继续按目标财报事件寻找匹配 filing，或记录 release missing

#### Scenario: 搜索结果来自相似公司名

- **WHEN** TSM 的 presentation 候选来自非 TSMC 的 Taiwan Semiconductor 公司站点
- **THEN** 公司身份或官方域名校验 SHALL 失败
- **AND** 候选 SHALL 被隔离

#### Scenario: Earnings Presentation 分类

- **WHEN** 已验证材料的标题为 Earnings Presentation
- **THEN** 文档语义 SHALL 为 investor presentation
- **AND** 标题中的 earnings 一词 SHALL NOT 使其分类为 company release

### Requirement: IBKR 新闻连接和分页失败可局部降级

IBKR 历史新闻请求 SHALL 使用 API 接受的带时区或 UTC 日期格式，并 SHALL 将连接、provider、合约、时间切片和正文失败分别记录。单个 symbol/切片返回 `None`、超时或报错 SHALL 产生该局部缺口并继续其他 symbol，SHALL NOT 令整个来源被误报为零新闻。

#### Scenario: TWS 已开启

- **WHEN** readonly 客户端成功连接且账户暴露可用新闻 provider
- **THEN** 系统 SHALL 使用合法日期格式请求声明窗口内的标题
- **AND** SHALL 在获取正文前保留 provider code、article id、时间与目标 symbol

#### Scenario: 单个切片超时返回 None

- **WHEN** 某个历史新闻请求超时并返回 `None`
- **THEN** 系统 SHALL 将该切片视为空结果并记录 timeout
- **AND** SHALL 继续其余切片和 symbol

### Requirement: Newsletter 采集使用增量游标并识别完整性

Newsletter 首次接入 SHALL 支持可配置历史回填，后续 SHALL 按邮箱 UID/Message-ID 或等价稳定游标增量采集，并保留有限重叠窗口。采集层 SHALL 保存所有新资产；下游 `max_articles_per_run` SHALL 仅限制处理数量。正文 SHALL 标识 full、partial 或 teaser，出现付费解锁/截断信号时不得声明为完整研报。

#### Scenario: 首次同步已有多封历史邮件

- **WHEN** 邮箱中最近 30 天存在十封来自已配置发布者的邮件且尚无游标
- **THEN** 首次回填 SHALL 发现并去重保存这十封邮件
- **AND** 本轮下游处理上限为 4 SHALL NOT 阻止其余六封进入资产目录

#### Scenario: 增量运行

- **WHEN** 上次游标之后新增两封邮件，重叠窗口内另有一封已保存邮件
- **THEN** 系统 SHALL 保存两封新邮件并按 Message-ID/规范 URL 跳过旧邮件
- **AND** 成功完成后 SHALL 原子推进游标

#### Scenario: 邮件只含付费预览

- **WHEN** 邮件正文包含明确的 unlock/subscribe 提示且正文在提示处结束
- **THEN** 文档 SHALL 标记为 partial 或 teaser
- **AND** 消费者请求完整研报时 SHALL 能区分该状态与 full

### Requirement: 文档版本与统一读取必须字节一致

对任一 accepted 文档，目录记录的字符数、不可变版本正文和统一读取 API 返回 SHALL 一致。Frontmatter 解析 SHALL 只识别文件开头紧邻的闭合边界，正文中的 Markdown `---` SHALL 保留。内容更新 SHALL 生成新不可变版本而不覆盖历史版本。

#### Scenario: 正文包含 Markdown 横线

- **WHEN** accepted 正文中间包含一行 `---`
- **THEN** 统一读取 SHALL 返回横线前后的完整正文
- **AND** 返回字符数 SHALL 与 catalog 和内容哈希对应的版本一致

#### Scenario: 同一文档正文更新

- **WHEN** 同一逻辑文档获得不同内容哈希的新正文
- **THEN** 可见路径 SHALL 指向最新接受版本
- **AND** 旧哈希对应的不可变版本 SHALL 继续可读取和追溯

### Requirement: 数据质量按覆盖、正确、时效与完整性报告

系统 SHALL 按来源与文档语义报告覆盖率、accepted/quarantined 数、身份/期间/类型/完整性失败分布、最新成功时间、来源快照时间和延迟。无数据、来源不可达、无权限、没有匹配内容和校验失败 SHALL 是不同状态。

#### Scenario: 来源在线但没有目标内容

- **WHEN** IBKR 可连接且 provider 正常，但目标窗口内没有匹配新闻
- **THEN** 来源状态 SHALL 为 reachable with zero matches
- **AND** SHALL NOT 报为 unreachable

#### Scenario: 第三方快照落后

- **WHEN** defeatbeta 的 spec 更新时间超过配置的最大允许延迟
- **THEN** 来源健康报告 SHALL 标记 stale 并显示延迟
- **AND** 最新数据查询 SHALL 优先尝试更及时的来源或显式返回陈旧状态

