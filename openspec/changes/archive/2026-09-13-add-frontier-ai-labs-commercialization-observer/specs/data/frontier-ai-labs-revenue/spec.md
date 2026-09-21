## Purpose

为尚未通过定期财报披露完整经营数据的 Frontier AI Labs 建立可追溯、可区分计量口径、可保留历史修订的收入观察数据，使 L1 商业化能力分析能够使用真实来源而不是无来源的连续估算曲线。

## ADDED Requirements

### Requirement: 收入证据必须区分公司、产品、指标口径和观察身份
数据集 SHALL 首版覆盖 OpenAI 与 Anthropic，并为每条观察保存公司、可选产品、数值、币种、计量口径、观察身份、参考期间、披露日、known-at、来源、原文引用、质量状态和 lineage。计量口径 SHALL 至少区分 `reported_arr`、`annualized_run_rate`、`trailing_revenue` 与 `forward_projection`；观察身份 SHALL 至少区分 `company_reported`、`media_reported`、`third_party_estimate` 与 `projection`。系统 SHALL NOT 将不同口径或身份静默合并为同一事实序列。

#### Scenario: 收录 Sacra 的年化收入观察
- **WHEN** Sacra 页面将某值标记为 annualized revenue 并给出对应日期
- **THEN** observation SHALL 标记为 `annualized_run_rate` 和 Sacra 对应的观察身份
- **AND** SHALL NOT 改写为经审计收入或标准合同 ARR

#### Scenario: 公司与产品指标并存
- **WHEN** 来源同时出现 Anthropic 总体收入与 Claude Code 产品收入
- **THEN** 两者 SHALL 使用不同 entity/product scope 和 series identity
- **AND** SHALL NOT 相加、相减或用产品值替代公司值

### Requirement: Sacra 公开页面作为最新收入主信源
系统 SHALL 定期发现 Sacra 的 OpenAI 与 Anthropic 公开公司页面是否出现新的收入观察、引用、参考期间或历史修订，并保存可验证的来源快照与内容指纹。采集 SHALL 只访问无需登录的公开内容；页面未变化时 SHALL 返回 `no_change`，不得重复新增 observation。

#### Scenario: Sacra 出现更晚收入点
- **WHEN** 页面出现晚于库内 latest period 且通过质量门的收入观察
- **THEN** 系统 SHALL 保存新 artifact 与 observation，并使 latest 查询返回新期间
- **AND** 原有 observation SHALL 保持可按 as-of 重放

#### Scenario: 页面不可达或结构改变
- **WHEN** 页面不可达、收入段落无法识别或引用链缺失
- **THEN** 本次发现 SHALL 返回明确的 source/parse warning 并保留上次正式数据
- **AND** SHALL NOT 写入零值、猜测值或删除历史

### Requirement: TickerTrends 只补充明确披露的 2026 年上半年历史点
系统 SHALL 将指定公开文章 `anthropic-vs-openai-arr-tracking` 中参考期间位于 2026-01-01 至 2026-06-30、且正文明确披露的 OpenAI/Anthropic 数值作为 `third_party_estimate` 历史补充。系统 SHALL 保存文章发布时间、原文片段、原始数值和来源 URL，不得从图形目测取值、不得插值缺失月份、不得将 7 月及以后数值纳入该历史补充范围。

#### Scenario: 文章只披露部分月份
- **WHEN** 2026 年 1–6 月中只有部分月份存在明确文字数值
- **THEN** 系统 SHALL 仅保存这些离散月份并标记 period gaps
- **AND** SHALL NOT 生成未披露月份的估算 observation

### Requirement: 跨来源冲突必须可见且不得被来源优先级掩盖
同公司、同参考期间出现 Sacra 与 TickerTrends 不同值时，系统 SHALL 保留两个 source-specific observations，并按指标口径、观察身份和 known-at 判断是否可比。用于 headline 的来源选择 SHALL 遵循配置化优先级并披露被选择值及冲突；不得覆盖或删除另一来源。

#### Scenario: 同月估算值不一致
- **WHEN** Sacra 与 TickerTrends 对同一公司同月给出不同 third-party estimate
- **THEN** 查询和报告 SHALL 返回冲突状态、两个值和各自来源
- **AND** headline SHALL NOT 把来源优先级解释为数值已经得到确认

### Requirement: 收入数据必须支持幂等增量、修订 vintage 与离线重放
系统 SHALL 以来源、公司/产品、指标口径、观察身份、参考期间和 payload identity 建立稳定 observation identity。完全相同的重复内容 SHALL 幂等；同一来源同一期间数值或引用发生改变 SHALL 新增 revision vintage，并保留旧版本的 as-of 可见性。正式报告重放 SHALL 只读取受治理存储，不得重新访问外部网页。

#### Scenario: Sacra 修改同一期间数值
- **WHEN** 新页面快照对已有期间给出不同值且通过质量门
- **THEN** 系统 SHALL 保存新的 revision vintage 并保留旧版本
- **AND** 当前查询与历史 as-of 查询 SHALL 分别返回正确版本

### Requirement: 质量门必须阻止无日期、无口径或不可追溯数值进入正式数据
正式 observation SHALL 具有正数金额、币种、公司、参考期间或明确可解释的 period、指标口径、观察身份、来源 URL 和可审计引用。来源文本只说“收入增长”但没有数值，或只有数值而无法判断其为 ARR、run rate、实际收入还是预测时，SHALL 作为文档证据保存但不得发布为数值 observation。

#### Scenario: 只有模糊收入标题
- **WHEN** 页面标题包含 ARR 数值但正文无法确认日期或计量口径
- **THEN** 该候选 SHALL 被隔离并返回具体质量原因
- **AND** 正式收入曲线 SHALL 不包含该数值

