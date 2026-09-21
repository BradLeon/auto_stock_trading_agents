## Purpose

定义英国 ONS Business Insights and Conditions Survey 中不定期出现的 AI 问题如何被发现、按问卷版本保存并作为美国之外的企业采用与组织嵌入补充证据。

## ADDED Requirements

### Requirement: ONS discovery 区分 BICS wave 与 AI 模块发布
系统 SHALL 定期发现新的 BICS dataset edition、workbook、questionnaire 和相关 AI article/chart files，并在每个 wave 中确认是否实际存在受支持的 AI 问题。BICS 发布新 wave 但没有 AI 问题 SHALL 返回 `question_not_fielded`，不得继承上一 wave 数值或假定 AI 指标按月两次更新。

#### Scenario: 新 wave 未包含 AI 问题
- **WHEN** ONS 发布新的 BICS workbook 但 questionnaire 没有任何受支持 AI question
- **THEN** 来源检查 SHALL 记录 wave、questionnaire version 和 `question_not_fielded`
- **AND** latest AI observation SHALL 保持为最近真实发布的 AI wave

### Requirement: ONS 以问题身份和 universe 建立 methodology regime
每条 observation SHALL 保存 wave、question identifier、完整问题文本哈希、答案、reference window、universe/route、统计单位、企业规模、SIC industry、estimate、standard error/confidence information 和 release identity。问题文字、答案 buckets、routing、企业规模范围或技术列表变化 SHALL 形成新 methodology regime，SHALL NOT 被静默拼接。

#### Scenario: extensive use 问题修改答案选项
- **WHEN** 后续 wave 改变 extensive/limited/pilot 的定义或 routing
- **THEN** 系统 SHALL 创建新的 question regime 并保留旧序列
- **AND** 跨 regime 趋势 SHALL 返回 `methodology_break`

### Requirement: ONS 保存采用广度和组织嵌入深度但不合成单值
首版 SHALL 在可用 wave 中保存至少一种 AI 技术采用率、平均使用技术数量、extensive/limited/pilot 分布、每日使用 AI 的员工占比 buckets、改善运营用途、采用方式、培训/技能整合和工作岗位影响等白名单指标。采用广度与嵌入深度 SHALL 保持独立，且所有条件问题 SHALL 显式标明以全部企业或 AI 使用企业为分母。

#### Scenario: extensive use 只问 AI 使用企业
- **WHEN** ONS questionnaire 将使用程度问题 route 给报告使用至少一种 AI 技术的企业
- **THEN** observation SHALL 标明 conditional-on-AI-user universe
- **AND** SHALL NOT 将 extensive 百分比称为全部英国企业的生产化率

### Requirement: ONS 只作为地域补充且披露覆盖限制
系统 SHALL 将 ONS 结果标记为 UK supplement，披露 BICS 的自愿调查、official-statistics-in-development 状态、response rate、排除行业和各图表采用的企业规模范围。ONS 值 SHALL NOT 与 BTOS 美国值默认排名、相减或合并；跨地域并列只可在显式可比性矩阵中展示。

#### Scenario: 美国和英国 headline 同时可用
- **WHEN** Observer 同时获得 BTOS 与 ONS 企业采用率
- **THEN** 输出 MAY 并列展示两者及各自分母、技术定义、规模和行业范围
- **AND** SHALL NOT 生成 US-minus-UK gap 或统一企业采用率

### Requirement: ONS 质量门保留低响应、抑制和口径差异
百分比 SHALL 位于 `[0,100]`，平均技术数量 SHALL 非负；缺失、抑制、低样本、高 standard error、问题未出现和 workbook schema drift SHALL 使用不同状态。图表文章与 workbook 对同一 cell 不一致时 SHALL 形成 reconciliation warning 并保留两个来源 artifact，未经解释不得发布趋势。

#### Scenario: 图表 CSV 与 wave workbook 不一致
- **WHEN** 相同 wave、question、universe 和 stratum 的公开图表值与 workbook 值超出声明容差
- **THEN** 系统 SHALL 标记 conflict 并阻止该 cell 进入默认趋势
- **AND** SHALL 提供两个 artifact 的 lineage 供审计

### Requirement: ONS 查询返回可审阅的深度快照
DataProducts SHALL 按 wave、as-of 和 question regime 返回采用广度、嵌入深度、行业/规模切片、coverage、质量、freshness、问卷引用和 lineage。没有连续同 regime observations 时 SHALL 返回 snapshot 而非趋势。

#### Scenario: 只有一个 extensive-use wave
- **WHEN** 请求期间内只有一个可比 wave 提供 extensive/limited/pilot
- **THEN** 系统 SHALL 返回 `research_snapshot` 与 `insufficient_history`
- **AND** SHALL NOT 以 BICS 主调查的其他 waves 补齐该序列
