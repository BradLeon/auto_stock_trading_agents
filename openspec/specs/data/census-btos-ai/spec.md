# data/census-btos-ai Specification

## Purpose
定义美国 Census Business Trends and Outlook Survey 核心 AI 问题的新口径数据如何被持续发现、受治理保存并用于衡量企业采用广度，同时防止把企业计数比例误读为员工或任务渗透率。

## Requirements

### Requirement: BTOS 只准入 2025 年 11 月后的新问题口径
系统 SHALL 只准入采集开始日期不早于 2025-11-17、问题文本为过去两周在任一业务职能使用 AI 及未来六个月预计在任一业务职能使用 AI 的 Core observations。旧“producing goods or services”口径 SHALL NOT 被回填、拼接或用于本数据产品趋势；口径变更 SHALL 以独立 methodology regime 表达。

#### Scenario: API 同时提供旧口径和新口径
- **WHEN** release metadata 或历史下载同时包含两种 AI Core 问题
- **THEN** 系统 SHALL 只发布新口径且符合日期边界的 observations
- **AND** SHALL 将旧口径记录为 out-of-scope 而非缺失、零值或趋势起点

### Requirement: BTOS discovery 区分计划期间与已经发布的数据
系统 SHALL 通过官方 periods、questions、answers、data metadata 与历史下载发现候选期间，并仅将已经具有可读取数据和完整问题字典的期间视为 available。未来预排 period、进行中 collection 或尚无数据的 period SHALL NOT 被当作新发布；每次采集 SHALL 保存查询、响应内容哈希、获取时间和官方文件或 API 指针。

#### Scenario: periods API 已列出未来期间
- **WHEN** periods metadata 包含未来 collection dates 但 data endpoint 尚无已发布 observation
- **THEN** discovery SHALL 返回 `not_yet_published` 或忽略该未来候选
- **AND** latest available period SHALL 保持为最近具有完整数据的期间

#### Scenario: 已发布期间后来被修订
- **WHEN** 相同 period 的官方响应内容哈希变化
- **THEN** 系统 SHALL 追加新的 artifact 和 observation vintage
- **AND** 旧 `as_of` 查询 SHALL 继续返回修订前值

### Requirement: BTOS 保存企业总体、行业和规模口径
系统 SHALL 保存全国、NAICS sector/subsector、employment-size 和 sector-by-size observations，并将统计单位标为 employer business。首版 L1 数据产品 SHALL NOT 使用州、MSA 或其他地理差异。每条 observation SHALL 明确 stratum、估计类型、问题、答案、reference window、release time、estimate、standard error 和质量状态。

#### Scenario: 查询全国当前采用率
- **WHEN** 消费者请求某个已发布 period 的美国企业 AI 当前采用率
- **THEN** 系统 SHALL 返回回答 Yes 的企业计数加权百分比及 standard error
- **AND** SHALL 明确该值不是就业加权、员工使用率、席位渗透率或任务自动化率

### Requirement: BTOS 原始指标和派生趋势保持可复算
系统 SHALL 将当前采用、未来六个月预期采用及其 standard error 保存为 Provider 原始指标。四期移动平均、当前与预期的百分点差、连续 period 变化 SHALL 作为版本化派生结果返回全部输入 observation IDs；不得跨 methodology regime、结构性缺口或不连续 period 计算。

#### Scenario: 计算四期移动平均
- **WHEN** 同一 stratum 存在四个连续、同口径且通过质量门的已发布 periods
- **THEN** 系统 SHALL 返回四期简单移动平均、公式、derivation version 和四个输入 IDs
- **AND** 原始双周估计和 standard errors SHALL 保持可查询

### Requirement: BTOS 质量门保留调查误差和抑制语义
百分比 SHALL 位于 `[0,100]`，standard error SHALL 非负；同一问题和 stratum 的完整回答集合 SHALL 在官方允许误差内合计为 100。官方抑制、缺答、未来未发布、API 不可达和 schema drift SHALL 使用不同状态；被抑制值 SHALL NOT 转换为零或进入移动平均。

#### Scenario: 行业 cell 被官方抑制
- **WHEN** 某行业答案被标为 suppressed 或非数值质量代码
- **THEN** 系统 SHALL 保存来源 cell 和抑制原因但不发布数值 observation
- **AND** Observer SHALL 显示 coverage gap 而不是零采用率

### Requirement: BTOS 只通过受治理数据产品提供给 L1
Agent 和 Evidence Observer SHALL 通过稳定 DataProducts 查询 BTOS，不得直接读取 Census API 或工作簿。结果 SHALL 包含统计主体、分母、行业/规模范围、问题 regime、period、standard error、质量、freshness、artifact 和 lineage。

#### Scenario: Agent 获取企业采用证据
- **WHEN** L1 Agent 请求 BTOS 最新企业采用证据
- **THEN** DataProduct SHALL 返回最新值、可比较历史、方法断点和引用信息
- **AND** Agent SHALL 无需知道 Census 表结构或 API 参数
