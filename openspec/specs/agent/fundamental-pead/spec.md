## Purpose

基本面分析师有两种运行模式：例行模式在信息、产业链事实或预期数据发生有效变化时更新预期基线；事件模式以财报或明确公司事件为触发器，在冻结的基线之上计算现实与预期的差异。两种模式都产出判断，都不产出可执行的订单，也不执行最终交易风控——那是风控主管与主理人的职责。

## Requirements

### Requirement: 例行模式只更新预期基线

例行模式 SHALL 在新 `InformationBrief`、产业链事实或预期数据发生有效变化时运行：读取上一个有效基线，把新信息分为确认、否定、新增或尚待验证，更新市场隐含预期、主要叙事、关键 KPI 与可证伪条件，产出 `FundamentalExpectationUpdate`。

例行模式 SHALL NOT 产出可执行订单、仓位或数量建议。

#### Scenario: 新信息分为四类

- **WHEN** 例行模式处理一批新信息简报
- **THEN** 每条信息 SHALL 被归为确认、否定、新增或尚待验证之一
- **AND** 归类结果 SHALL 随投影一并留痕，可事后复核

#### Scenario: 基线更新而非重算

- **WHEN** 例行模式完成一次运行
- **THEN** SHALL 产出相对上一基线的变化与变化驱动因素
- **AND** SHALL NOT 产出交易动作、目标仓位或买卖结论

### Requirement: 事件模式在 cutoff 冻结基线并只使用期间正确的材料

事件模式 SHALL 在事件 cutoff 冻结财报前预期基线与引用，并 SHALL 只使用报告期间正确且通过准入的 actuals、earnings release、指引与电话会材料。

#### Scenario: cutoff 后基线不再变动

- **WHEN** 事件模式已冻结某报告期的基线
- **THEN** 后续到达的信息 SHALL NOT 改写该基线
- **AND** 该报告期的差异计算 SHALL 始终以冻结基线为参照

#### Scenario: 期间不符的材料被排除

- **WHEN** 候选材料不属于该报告期或未通过准入
- **THEN** SHALL 被排除在事件模式输入之外
- **AND** 排除 SHALL 留痕，SHALL NOT 静默丢弃

### Requirement: 事件模式分别计算三类差异并产出 Scorecard

事件模式 SHALL 分别计算实际值相对**冻结基线**、**Consensus** 与**市场隐含预期**的差异，并产出 Surprise Scorecard、指引评估与叙事更新。三类差异 SHALL 分开呈现，SHALL NOT 被压成单一分数。

#### Scenario: 三类差异分列

- **WHEN** 一次财报事件完成评估
- **THEN** 输出 SHALL 分别给出对基线、Consensus 与市场隐含预期的差异
- **AND** 三者方向不一致时 SHALL 保留分歧并说明原因，SHALL NOT 取平均

#### Scenario: 电话会迟到产生新版本

- **WHEN** 电话会材料晚于财报稿到达
- **THEN** SHALL 生成该事件评审的新版本
- **AND** 早期版本 SHALL 保留，SHALL NOT 被覆盖

### Requirement: 事件模式保留非可执行投资观点，剥离数量与风控

事件模式 SHALL 保留方向性的投资观点（方向、幅度、信心、理由与可证伪条件），因为判断现实与预期的偏离属于基本面职责。

事件模式 SHALL NOT 计算目标仓位或下单数量，SHALL NOT 读取组合净值或持仓做 sizing，SHALL NOT 调用风险引擎、护栏复核或交易前风控；可执行性与风控结论 SHALL 只由风控主管与主理人在审批链内产生。

方向性观点 SHALL 以「预期差方向」表达（`FundamentalEventReviewPayload.direction` 取 `-1|0|1`），payload SHALL NOT 携带 action 词表取值（`buy|add|hold|trim|sell`）、目标股数、目标金额或组合权重字段。主理人 SHALL NOT 把 `direction` 直接映射为交易动作，动作与数量的产生 SHALL 经过主理人自身的综合判断与风控审查。

#### Scenario: 投资观点不含数量

- **WHEN** 事件模式给出方向性投资观点
- **THEN** 输出 SHALL 包含方向、幅度、信心、理由与可证伪条件
- **AND** SHALL NOT 包含目标股数、目标金额或组合权重

#### Scenario: 不调用风险引擎

- **WHEN** 事件模式产出评审结论
- **THEN** 流程 SHALL NOT 调用风险引擎、护栏复核或交易前风控
- **AND** SHALL NOT 生成结构化交易指令或券商侧动作

#### Scenario: 不读取持仓做 sizing

- **WHEN** 事件模式需要给出幅度
- **THEN** 幅度 SHALL 以预期偏差或基本面口径表达
- **AND** SHALL NOT 依据组合净值、可用资金或现有持仓推算

#### Scenario: 方向不携带动作词表

- **WHEN** 事件评审投影通过角色 schema 校验
- **THEN** payload SHALL 只含 `direction` 的 `-1|0|1` 取值与幅度、信心、理由、可证伪条件
- **AND** SHALL NOT 出现 action 词表取值、目标股数、目标金额或组合权重字段

#### Scenario: 主理人不直接映射方向为动作

- **WHEN** 主理人消费到 `direction=1` 的事件评审
- **THEN** 该方向 SHALL 作为研究输入参与综合判断
- **AND** 系统 SHALL NOT 提供把该取值直接转换为买入动作的路径

### Requirement: 基本面不读取行业、宏观观点与其他标的的基本面结论

基本面分析师 SHALL 只消费公司研究包、产业链共享事实与 `InformationBrief`。它 SHALL NOT 读取行业分析师的配置结论、宏观评审的 regime 与情景，SHALL NOT 在其上下文里注入行业或宏观提示块。

跨标的信号链 SHALL 保留，但 SHALL 只以**中性事实**形态进入：上游或同业标的已报的实际值、官方指引区间、产能数字、财报日期与是否已报等可溯源字段，应取自公司 / 一致预期 / 产业链等数据产品，或取自该标的的 `InformationBrief`。基本面 SHALL NOT 消费其他标的的 `FundamentalEventReview` 或 `FundamentalExpectationUpdate` 投影，SHALL NOT 读取其他标的 dossier 的结论摘要、Scorecard 分档或由模型逐维打分派生的评等。

判断界线 SHALL 按**取数入口**而非字段语义执行：经 `ats.data.*` 数据产品或投影中 `information_brief` 角色读取的为中性事实，经其他标的 dossier 或 `fundamental_*` 角色投影读取的为观点。

#### Scenario: 监控与准备阶段不再注入行业与宏观

- **WHEN** 基本面在例行监控或财报准备阶段组装上下文
- **THEN** 上下文 SHALL NOT 包含行业配置结论或宏观 regime 提示
- **AND** 相关信号 SHALL 只以共享事实形式进入

#### Scenario: 图流程不再注入行业与宏观块

- **WHEN** PEAD 图流程组装准备期上下文
- **THEN** SHALL NOT 追加行业或宏观评审的文本块
- **AND** 既有的注入开关 SHALL 被移除而非保留为可开启选项

#### Scenario: 跨标的信号只取中性事实

- **WHEN** 信号链上的上游标的已发布财报
- **THEN** 目标标的 SHALL 只收到该上游的已报实际值、指引区间、财报日期等可溯源事实字段
- **AND** SHALL NOT 收到该上游的评审结论摘要、Scorecard 分档或任何由模型判断派生的字段

#### Scenario: 其他标的的基本面投影被拒绝

- **WHEN** 基本面运行试图读取另一标的的 `FundamentalEventReview` 或 `FundamentalExpectationUpdate` 投影
- **THEN** 该读取 SHALL 被判为跨角色依赖违规并拒绝
- **AND** 跨标的信号 SHALL 改由数据产品或该标的的信息简报提供

### Requirement: 双模式由触发契约选择并分别发布投影

例行与事件模式 SHALL 由显式触发契约选择：例行模式由信息简报或预期数据的有效变化触发，事件模式由财报或明确公司事件触发。两种模式的产出 SHALL 分别写为 `FundamentalExpectationUpdate` 与 `FundamentalEventReview` 投影。

本阶段 SHALL 定义触发契约与显式入口（命令行与显式运行请求），由日程日历自动发现并触发财报事件的部分留待后续阶段。

#### Scenario: 例行模式被触发

- **WHEN** 某实体出现新的有效信息简报或预期数据变化
- **THEN** SHALL 运行例行模式并产出 `FundamentalExpectationUpdate` 投影
- **AND** SHALL NOT 顺带产出事件评审或交易结论

#### Scenario: 事件模式被显式触发

- **WHEN** 以某实体的财报或公司事件发起一次显式运行请求
- **THEN** SHALL 运行事件模式并产出 `FundamentalEventReview` 投影
- **AND** 投影 SHALL 记录冻结基线的引用与 cutoff 时点

#### Scenario: 两种模式不互相覆盖

- **WHEN** 同一实体在同一报告期内既有例行更新又有事件评审
- **THEN** 两类投影 SHALL 各自保留并可分别查询
- **AND** 主理人消费时 SHALL 能看到两类结论及其各自 as-of
