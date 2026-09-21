# data/rps-genai-adoption Specification

## Purpose
定义 Real-Time Population Survey 经 FRED 发布的工作用途 GenAI 序列如何被持续采集、版本化和解释，为 L1 提供具有就业人口分母的员工采用、重复使用与工时强度证据。

## Requirements

### Requirement: RPS 只使用工作用途且定义稳定的核心序列
系统 SHALL 采集工作采用、上周至少一次工作使用、上周每个工作日使用、总工时中 GenAI 辅助比例和自报节省工时比例。系统 SHALL NOT 使用混合个人、教育和工作用途的 all-purpose daily series 代替工作序列；每条 series SHALL 保存 FRED series ID、原始 notes、单位、频率、季节调整状态和来源引用。

#### Scenario: 工作和全部用途序列同时可用
- **WHEN** FRED 同时提供 `Daily Use for Work` 与 `Daily Use for All Purposes`
- **THEN** L1 核心数据产品 SHALL 只采用工作用途序列
- **AND** all-purpose series SHALL 被标为 out-of-scope 而不是回退源

### Requirement: RPS 按季度主动发现新 observation 和修订
系统 SHALL 定期检查核心 series 的 latest observation、updated time 和内容身份，并只在出现新季度或历史修订时拉取和准入新 artifact。FRED 未公布 next release date SHALL NOT 导致固定发布日期假设；没有变化 SHALL 返回 `no_change`，来源不可达 SHALL 保留最近成功数据。

#### Scenario: 周检期间季度数据没有更新
- **WHEN** 各核心 series 的最新 period、updated time 和内容哈希均未改变
- **THEN** discovery SHALL 更新 `last_checked_at` 并返回 `no_change`
- **AND** SHALL NOT 重复创建 observations 或将季度序列标记为 stale

### Requirement: RPS 明确就业人口分母和自报属性
全国 headline observation 的统计单位 SHALL 为美国 18–64 岁 employed adult，值 SHALL 为未季调百分比。行业或职业细分只有在 series metadata 明确给出分类与分母时才可作为诊断切片；结果 SHALL 标明自报、可能包含个人账户或未经企业批准的使用，不能确认正式企业部署。

#### Scenario: Agent 解读工作采用率
- **WHEN** Agent 获取 `worker_genai_work_adoption_pct`
- **THEN** 结果 SHALL 说明分母是目标就业人口而非企业、Claude 用户或工作任务
- **AND** SHALL NOT 将该值陈述为企业批准率、席位率或持续生产工作流比例

### Requirement: RPS 提供透明的持续使用与强度派生
系统 SHALL 提供 `weekly_persistence_proxy`、`daily_persistence_proxy`、连续季度百分点变化和可用时的同比变化。前两者 SHALL 分别按上周使用率/工作采用率、每日工作使用率/工作采用率计算，并明确是总体比例之比而非 cohort retention；全部派生 SHALL 返回公式、版本和输入 observation IDs。

#### Scenario: 计算每日持续使用代理
- **WHEN** 同一季度的工作采用率和每日工作使用率均有效且分母兼容
- **THEN** 系统 SHALL 返回二者之比及 `persistence_proxy` 标签
- **AND** SHALL NOT 将其命名为用户留存率或账号留存率

### Requirement: RPS 质量门检查序列边界与逻辑关系
核心百分比 SHALL 位于 `[0,100]`；同一季度正常情况下每日工作使用率 SHALL 不高于上周工作使用率，后者 SHALL 不高于工作采用率。违反该关系、单位或频率变化、重复冲突和 schema/notes drift SHALL 阻止自动趋势发布或产生明确 warning；时间节省 SHALL 继续标为受访者反事实估计。

#### Scenario: 上周使用率高于工作采用率
- **WHEN** 同一季度已准入候选出现 `last_week > adoption`
- **THEN** 对应季度 SHALL 进入 validation warning 或 failure 并停止派生 persistence
- **AND** 质量报告 SHALL 展示所有输入、来源更新时间和异常关系

### Requirement: RPS 查询为 Agent 提供紧凑且有血缘的员工扩散上下文
DataProducts SHALL 返回最新季度、可比较历史、核心水平值、持续性派生、行业/职业诊断的样本风险、来源 notes 摘要、freshness、质量和 lineage。Observer 与 Agent SHALL NOT 直接访问 FRED 网页或 CSV。

#### Scenario: L1 获取员工持续使用证据
- **WHEN** L1 Observer 请求 RPS evidence slice
- **THEN** 结果 SHALL 在固定字段中返回 adoption、last-week、daily、assisted-hours、time-saved 和可比性状态
- **AND** SHALL 能由 snapshot manifest 在不访问 FRED 的情况下重放
