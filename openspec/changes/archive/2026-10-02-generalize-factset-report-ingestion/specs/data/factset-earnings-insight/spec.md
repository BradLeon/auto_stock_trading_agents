## MODIFIED Requirements

### Requirement: The structured dataset has explicit index and GICS-sector semantics
系统 SHALL 注册持久数据集 `sp500_earnings_insight`。指数级观测 SHALL 使用实体 `SP500`；行业级观测 SHALL 使用 `GICS_10`、`GICS_15`、`GICS_20`、`GICS_25`、`GICS_30`、`GICS_35`、`GICS_40`、`GICS_45`、`GICS_50`、`GICS_55` 和 `GICS_60`。新候选的估计状态 SHALL 仅为 `estimated|actual`；原文中的 blended 等措辞 SHALL 作为证据保留，不原地改写历史观测。每条观测 SHALL 明确报告日期、目标季度/年度或 snapshot 日期、单位、来源、`known_at` 和质量状态。

核心指数范围 SHALL 包括披露进度、EPS/营收 above-inline-below 分布与 surprise 幅度、EPS/营收同比增长、净利率、正负 guidance 数量、行业盈利修正广度 `earnings.revision.improved_sector_count`、bottom-up EPS 端点、forward/trailing P/E 及 5/10 年参照、评级分布和目标价上行空间。核心行业范围 SHALL 包括报告提供的对应 scorecard、surprise、增长、净利率与 margin breadth、guidance、forward P/E、美国/国际收入暴露、评级分布和目标价上行空间。

#### Scenario: A report covers multiple target periods
- **WHEN** 同一期报告同时提供已披露季度、下一季度、当前日历年和下一日历年的估计
- **THEN** 系统 SHALL 将每个数值绑定到其真实目标期间和估计状态
- **AND** SHALL NOT 以报告日期替代目标期间或默认所有字段属于同一季度

#### Scenario: The earnings season changes terminology
- **WHEN** FactSet 使用 `blended`、`estimated` 或 `actual` 等措辞
- **THEN** 仅本指标、本期间被明确说明为实际业绩的新候选 SHALL 标为 `actual`，否则 SHALL 标为 `estimated`；原始措辞 SHALL 保留为证据
- **AND** 系统 SHALL NOT 将缺少 Scorecard 或 guidance 误写为零，亦 SHALL NOT 原地修改旧状态历史

#### Scenario: Revision breadth is written as words
- **WHEN** 报告称 “Ten of eleven sectors” 的盈利增长相较某一日期改善
- **THEN** 系统 SHALL 将 `earnings.revision.improved_sector_count` 保存为整数 `10`，实体为 `SP500`，并保留真实目标季度、估计状态、`comparison_date`、`revision_direction` 和 `sector_total=11`
- **AND** 原始单词 token 与完整文字 span SHALL 作为证据保留
- **AND** 当原文未给出明确数量或总数时，系统 SHALL 保持 null 并记录原因，不得根据方向性描述猜测数量

#### Scenario: A chart contains a Top or Bottom company list
- **WHEN** 报告提供 Top/Bottom 10 EPS surprise、EPS revisions 或价格反应分桶
- **THEN** 这些内容 SHALL 保留在文档/图表资产中，但 SHALL NOT 成为 V1 核心数据集的完整公司截面

### Requirement: Migration is gated by current-report source quality and consumer regressions
系统 SHALL 在至少 2026-08-28 和 2026-09-18 两份真实官方 PDF 上，以同一生产解析逻辑、范围策略和策略版本完成核心正文、行业图表、准入、血缘、消费者及回滚验收。核心范围 SHALL 包含已登记且本期适用的S&P 500总体指标/有界宏观叙事，以及Information Technology行业八类指标的全部披露期间。核心清单 SHALL 独立于提取结果确定。发布 SHALL 按指标组及声明实体范围独立验证，范围内适用单元格与独立原文标注一致且完整性通过后方可发布；未通过范围 SHALL 保留候选和错误，不阻止其他独立通过范围。系统 SHALL NOT 将全11行业或双报告770格全部成功作为change完成门槛，也 SHALL NOT 按日期/hash选择定制坐标。

#### Scenario: An operator reviews the current-report sector golden dataset
- **WHEN** 任一期真实报告产生行业候选
- **THEN** 系统 SHALL 提供按组审阅包，包含 chart_id、页码、GICS entity、列、期间、状态、值、单位、原始标签/token及真实证据区域
- **AND** 审阅者 SHALL 能接受、修正或标记不可判读，SP500 行 SHALL NOT 写入行业golden

#### Scenario: Golden cells are used for sector release validation
- **WHEN** 某报告某指标组的独立原文标注完成
- **THEN** 系统 SHALL 按指标、实体、列、期间、状态和单位逐格比较，并在缺失、额外、重复、数值或证据不符时阻断受影响组
- **AND** 通过组 SHALL 在声明实体范围内具有完整适用列和期间；仅全行业横截面要求11行业完整；生产解析器 SHALL NOT 读取golden或人工修正值作为提取输入

#### Scenario: Completion evidence is independent and report-specific
- **WHEN** 声称双报告验收完成
- **THEN** 系统 SHALL 提供两份PDF hash、相同解析器/策略/范围版本、独立核心清单及逐格对账结果；既有231格历史基线及770格标注 SHALL 保留，但不作为必须全部成功的门槛
- **AND** 每个候选 SHALL 有实际单元格/数值证据区域，SHALL NOT 使用整页、整行占位或用候选自身生成golden
- **AND** 存在未决核心适用单元格时 SHALL NOT 声称核心验收完成；仅有非核心延期项时 SHALL 允许核心范围验收，但 SHALL 明示全报告覆盖partial及未完成范围

#### Scenario: A sector chart omits an otherwise registered metric column
- **WHEN** 原文证据确认某期未披露某注册列
- **THEN** 系统 SHALL 记录 not_disclosed/not_applicable及证据，不补零或估算，不将其列为该期必需列
- **AND** 无法定位或解析 SHALL 分别标记 not_located/extraction_failed，SHALL NOT 被改称未披露

#### Scenario: A sector growth chart includes a prior comparison date
- **WHEN** 行业增长图包含Today及较早比较日
- **THEN** 系统 SHALL 发布当前列为正式增长观测，保留真实目标期间和估计状态，comparison_date按该报告实际日期规范化
- **AND** 较早列 SHALL 保留为修正证据，不伪造第二条当前行业观测

#### Scenario: Index metrics pass but chart extraction does not
- **WHEN** Index通过且部分行业指标组失败
- **THEN** 系统 SHALL 发布Index及其他独立验证的行业组，失败组保留shadow/待审及错误
- **AND** 全报告覆盖 SHALL 报告partial及各范围缺口；科技核心存在缺口时核心验收 SHALL 为blocked，仅非核心缺口时不阻断核心验收；消费者 SHALL NOT 使用未发布字段或将Index成功视为科技核心通过

#### Scenario: The platform consumer is rolled back
- **WHEN** 新解析或产品读取出现回归
- **THEN** 操作者 SHALL 能通过解析/产品发布选择及consumer开关回退稳定版本，不删除PDF、版本、候选、审阅及vintage
- **AND** 隔离回放 SHALL 证明as-of与known_at未被重处理篡改

## ADDED Requirements

### Requirement: Monthly production selection is managed independently of consumer reads
系统 SHALL 以受管队列在纽约时间月末触发 FactSet 采集及核心准入，并以目标报告月份去重；手动与补跑 SHALL 使用相同的准入和版本规则。自动发布 SHALL 只选择目标月份的报告原件，SHALL NOT 将后来月份的稳定 URL 内容冒充目标月。采集和消费者读取 SHALL 分离；读取 SHALL NOT 触发网络采集。最新已发布月报 SHALL 保持可用，不能仅因日龄降级。

#### Scenario: Month-end refresh or sleep recovery runs
- **WHEN** 月末任务按时运行，或 Mac 休眠后在补跑窗口恢复
- **THEN** 系统 SHALL 以目标月份和策略身份幂等入队，并核对官方报告日期；同月重复触发 SHALL NOT 重复发布

#### Scenario: The stable URL has rolled into the next month
- **WHEN** 补跑目标为上月，而稳定 URL 已返回次月报告
- **THEN** 系统 SHALL 阻断错月发布并保留失败原因；已有且 hash 验证通过的目标月官方原件 MAY 经同一受管入口重处理

#### Scenario: Latest released month is older than a fixed day threshold
- **WHEN** 上游尚未发布新的可用月报，当前最新已发布月报超过固定日龄阈值
- **THEN** 消费者 SHALL 继续以该版本和真实报告日期读取，不产生仅基于日龄的 stale 警告或降级；采集故障 SHALL 单独留在运维状态

### Requirement: New estimate states use explicit actual or default estimated
新候选估计状态 SHALL 仅使用actual和estimated；只有原文明确说明对应指标和期间为实际业绩时 SHALL 使用actual，否则 SHALL 使用estimated，包括blended及未说明状态。原始措辞 SHALL 保留为证据，历史记录 SHALL NOT 原地改写。未说明估计状态 SHALL NOT 单独阻断发布，数值、实体、单位、期间和审阅要求 SHALL 保持不变。

#### Scenario: Source omits a state or reports blended performance
- **WHEN** 本组原文没有明确实际业绩说明，或使用blended措辞
- **THEN** 新候选 SHALL 标为estimated并保留可用原文证据，不增加第三种估计状态

#### Scenario: Actual belongs to another period or a conditional statement
- **WHEN** actual仅出现在其他指标/期间、条件句或报告中部分公司的发布进度说明中
- **THEN** 系统 SHALL NOT 据此将本组标为actual；actual证据 SHALL 绑定候选身份并经独立核验

### Requirement: Core scope covers all disclosed target periods within the eight metric groups
系统 SHALL 在核心Information Technology范围内覆盖八类指标在每份报告中实际提供的全部目标季度、日历年、财年和预测窗口，包括营收增长与营收surprise。核心数量 SHALL 由独立原文清单确定，非核心范围 SHALL 允许记录延期而不阻断核心验收。组发布身份 SHALL 区分目标期间、期间基础、估计状态、实体范围及范围版本。

#### Scenario: The same metric appears for several periods
- **WHEN** 报告提供核心范围Q2、Q3、CY年度或FY年度的同类指标
- **THEN** 系统 SHALL 分别保存适用行业数值及期间证据，不按chart_id覆盖另一期间，不将CY与FY互换
- **AND** 前期比较列 SHALL 保留为比较证据，不误认成另一当前目标期

### Requirement: Report extraction follows semantic labels rather than dated layouts
系统 SHALL 从每期原文识别标题、行业标签、表头、期间、单位与估计状态，输出可定位的原始证据。系统 SHALL NOT 用固定行业顺序、页码、图像编号、量程或报告日期分支代替本期识别。既有原文获取与不可变存储 SHALL 被复用，数值提取失败 SHALL NOT 丢弃已准入的完整报告。

#### Scenario: Pages and sector order change
- **WHEN** 两份报告或布局扰动样本的页码、图像顺序和行业排序不同
- **THEN** 同一逻辑 SHALL 保持数值与真实行业/期间对应，不能识别时明确拒绝而不是按旧位置归属

#### Scenario: OCR produces ambiguous numbers
- **WHEN** 数字负号、小数点、单位或标签有歧义
- **THEN** 系统 SHALL 保留候选、原始token和证据并要求独立验证，SHALL NOT 单凭模型置信度或柱长估算发布精确数值

#### Scenario: Authorized visual assistance transcribes source crops
- **WHEN** 在用户授权及配置预算内调用OpenRouter视觉辅助解析
- **THEN** 系统 SHALL 仅发送必要图表裁剪，保留原图/crop hash、bbox、模型/prompt/策略版本及usage审计，不向模型提供golden或预期值
- **AND** OCR/视觉不一致 SHALL 保留冲突；一致 SHALL NOT 自动成为发布批准；截断、ID不匹配、预算耗尽或隐私路由不可用 SHALL 失败关闭

### Requirement: Group release policies and review decisions are governed and versioned
系统 SHALL 集中登记指标组适用性、实体范围、优先级、完整性、关系约束、预算/停止规则和审阅策略，并关联每次运行的配置版本。已登记核心范围的证据、数值及完整性检查均通过时，系统 SHALL 自动准入并持久化绑定报告hash、候选集合、提取器/策略版本、组及实体范围/范围版本的策略审批；异常 SHALL 保持未发布并进入人工审阅。人工结论 SHALL 同样保存身份、时间、结论及证据；裸布尔值和测试审批 SHALL NOT 作为生产批准。来源获取、提取、发布结果 SHALL 分层可观测。

#### Scenario: Core candidate passes governed policy
- **WHEN** 本期 Index 或科技核心组的全部适用检查通过且绑定材料与策略均有效
- **THEN** 系统 SHALL 记录可追溯的策略审批并发布该范围，无需每月例行人工批准
- **AND** 非核心行业未完成 SHALL 保持全报告覆盖 partial，不阻断已通过的核心范围

#### Scenario: Policy finds an anomaly
- **WHEN** 原文、数值、期间、关系、范围或证据检查失败或冲突
- **THEN** 受影响范围 SHALL 留在 shadow/待审，只有人工判断并按新候选重新验证后才可发布

#### Scenario: Another group fails
- **WHEN** 某指标组在声明实体范围内完整通过，但另一组或独立实体存在冲突
- **THEN** 系统 SHALL 独立发布前者、隔离后者，保留全部局部候选和失败原因
- **AND** 共享表头、期间或单位证据存在冲突时，系统 SHALL 阻断所有依赖该证据的范围，不通过拆分范围规避校验

#### Scenario: A previous approval is reused on changed material
- **WHEN** 报告、候选、实体范围或所绑定版本变化
- **THEN** 旧批准 SHALL 失效，系统 SHALL 重新验证或进入待审，不继承上一期人工批准

### Requirement: Current report gaps and historical valid groups remain distinguishable
产品 SHALL 保留同报告的当前视图，并提供明确分离的最近合格历史组视图；各组包含报告版本、known_at、实体范围、优先级、质量、覆盖及本轮失败。产品 SHALL 分开报告核心验收状态与全报告覆盖状态。最新已发布月报 SHALL NOT 仅因距今天数标为 stale 或触发下游降级；采集失败和较新报告尚未发布 SHALL 另行标记。系统 SHALL NOT 将上一期Sector冒充当前期完整截面，也 SHALL NOT 在存在历史发布时只报告从未有数据。

#### Scenario: New index exists without new sector release
- **WHEN** 9月Index通过、某行业组失败且8月该组已发布
- **THEN** 当前视图 SHALL 明示9月缺口，历史视图 SHALL 返回8月数据及报告日期；较新版本未发布和本次失败 SHALL 分别标记，不静默混合或按年龄降级

#### Scenario: A document is replayed or revised
- **WHEN** 相同PDF和提取版本重复运行，或新提取版本重处理历史PDF
- **THEN** 相同结果 SHALL 幂等，修订 SHALL 追加版本并保留真实可见时间；历史as-of SHALL 不受未来重处理污染

### Requirement: Investment relevance governs effort without weakening published accuracy
系统 SHALL 优先保障S&P 500总体宏观信息及Information Technology核心数据；原文明示的AI硬件、半导体、消费电子及上下游信息 SHALL 作为相关层优先保留；其他行业详细指标 SHALL 作为补充层低成本获取。所有已发布数据 SHALL 遵守相同准确性、证据、审阅及血缘要求。系统 SHALL 保留完整原文、已有标注和历史数据，不因降低优先级删除数据。

#### Scenario: A supplementary sector needs disproportionate repair
- **WHEN** 非核心行业通用解析失败，需要专门逐格人工补救、模型批次或专用解析调参
- **THEN** 系统 SHALL 记录失败/延期、实体与指标范围、原因及资源消耗，不继续专项攻关或阻塞核心交付
- **AND** 延期 SHALL NOT 标为not_disclosed、解析成功或已验收；可靠的已有补充数据 SHALL 保留

#### Scenario: Core succeeds while supplementary coverage is incomplete
- **WHEN** 双报告核心范围和必要可靠性/消费者测试通过，而非核心仍有延期项
- **THEN** 系统 SHALL 允许核心范围验收通过，同时输出全报告覆盖partial及延期清单
- **AND** 系统 SHALL NOT 宣称全行业完整或为消除partial删除未完成项

#### Scenario: Technology totals do not establish subsector facts
- **WHEN** 报告仅披露科技行业总量，没有明确AI硬件或消费电子子行业数据
- **THEN** 系统 SHALL 保留科技行业原口径，SHALL NOT 推算或伪造子行业数值
- **AND** 宏观总体 SHALL 使用报告原生S&P 500信息，不以科技行业或部分行业平均代替；其他行业中的相关实体 SHALL 保留原始行业归属

## REMOVED Requirements

### Requirement: Collection precedes weekly review through an explicit scheduled stage
**Reason**: 生产已改为与每周评审解耦的受管月末采集，旧的“本周失败即将上一期标 stale”契约会错误降级最新月报。
**Migration**: 使用“Monthly production selection is managed independently of consumer reads”契约；保留历史周度版本和受控回退能力，不把旧周调度视为当前生产入口。
