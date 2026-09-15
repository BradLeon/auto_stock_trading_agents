# data/openrouter-rankings Specification

## Purpose

为 OpenRouter 公共路由流量建立可追溯、可版本化且不会被误读为全市场收入的数据契约，使 L1 商业化能力分析能够观察真实 token 用量扩张、模型采用和厂商竞争格局。

## Requirements

### Requirement: 数据必须来自 OpenRouter 官方 Rankings 出口
系统 SHALL 使用 OpenRouter 官方 Rankings Data API 获取公共排行榜数据，首版端点为 `GET /api/v1/datasets/rankings-daily`。每个 artifact SHALL 保存请求日期窗、period 参数、响应 `meta.as_of`、数据版本、获取时间、内容 SHA-256、来源 URL、解析器版本和 lineage。API key SHALL 仅由安全运行时配置注入，不得写入仓库、artifact、日志或报告。

#### Scenario: 官方 API 返回有效数据
- **WHEN** 调度器使用有效凭据请求一个受支持日期窗
- **THEN** 系统 SHALL 保存原始 JSON artifact 并生成可追溯 observations
- **AND** 报告来源 SHALL 按 CC BY 4.0 要求标注“Source: OpenRouter (openrouter.ai/rankings), as of {as_of}”

#### Scenario: API 不可用或未授权
- **WHEN** 接口返回未授权、限流、网络失败或不合法 payload
- **THEN** 本次运行 SHALL 返回明确的 source 状态并保留最近 accepted vintage
- **AND** SHALL NOT 以截图、OCR、搜索摘要或未文档化隐藏接口替代

### Requirement: 数据模型必须保留模型、作者、Top 50 和 Other 语义
每条日度 observation SHALL 至少保存 reference date、model permalink/slug、model display name、model author、token count、rank、`is_other`、可识别的 `is_free_route`、source period、quality 和 artifact identity。官方每天只披露 Top 50 模型时，长尾 SHALL 以原生 `Other` 保存；系统 SHALL NOT 将 `Other` 逆向分配给厂商，也不得把已归属厂商份额描述成完整厂商份额。

#### Scenario: 每日响应包含 50 个模型和 Other
- **WHEN** 某日 payload 具有 Top 50 模型行及 `Other` 汇总行
- **THEN** 总 token SHALL 等于所有模型行与 `Other` 的和
- **AND** 厂商聚合 SHALL 保留独立 `unattributed_other` 份额，使可见部分与 Other 合计为 100%

#### Scenario: 模型作者映射未知
- **WHEN** 新模型 slug 无法映射到受治理作者名录
- **THEN** observation SHALL 保留原始 model identity 并进入 `unknown_author` 分组或隔离队列
- **AND** SHALL NOT 猜测厂商或丢弃其 token

### Requirement: Token、请求、用户、支出和收入必须严格区分
系统 SHALL 将 `total_tokens` 定义为 OpenRouter 公共请求中 prompt 与 completion token 的合计，并明确其不是请求数、会话数、用户数、API 支出、OpenRouter 收入或全行业收入。上游 provider tokenizer 不同造成的跨模型 token 不完全可比 SHALL 作为固定 methodology warning。未经独立数据支持，系统 SHALL NOT 用当前价格乘 token 估算历史收入。

首版 SHALL NOT 观测或使用 `session-cost` 数据产品；median session cost 缺少与 model×date token 行一致的 session/token 分母，不能与 `total_tokens` 相乘得到渠道收入。

#### Scenario: 消费者查询 Token 经济规模
- **WHEN** 消费者请求 OpenRouter 总体趋势
- **THEN** DataProduct SHALL 返回 `openrouter_public_routed_tokens` 和渠道覆盖说明
- **AND** 人类报告 SHALL 使用“OpenRouter 公共路由 token 用量”而不是“全市场 Token 经济收入”

#### Scenario: 官网图表显示 request share
- **WHEN** 官方 Rankings Data API 只提供 token 且没有稳定公开 request-count 出口
- **THEN** 系统 SHALL 仅发布可复算的 token share
- **AND** SHALL NOT 将 token share 标注为 text request share

#### Scenario: 稳定官方 request-share 出口后续可用
- **WHEN** OpenRouter 提供具有文档、版本、分母和历史日期的全局 request-count/share 官方出口
- **THEN** 系统 MAY 以独立 metric identity、artifact 和 lineage 接入
- **AND** SHALL NOT 与 token share 合并、替代或使用同一标签

### Requirement: 采集必须支持历史回填、重叠增量和修订 vintage
系统 SHALL 从 2025-01-01 起分窗回填官方可用历史，并按日探测最近更新；增量请求 SHALL 至少覆盖最近 14 个完整 UTC 日以识别迟到修订。相同请求身份和 payload SHA-256 SHALL 幂等；同一日期内容改变 SHALL 追加 revision vintage，旧版本保持可按 `as_of` 重放。报告运行 SHALL 只读取结构化库，不得触发外部采集。

#### Scenario: 首次历史回填
- **WHEN** 数据库没有 OpenRouter observations
- **THEN** 系统 SHALL 按受限窗口获取 2025-01-01 至最近完整 UTC 日的数据
- **AND** SHALL 遵守官方每 key 每分钟 30 次、每账户每天 500 次的限制

#### Scenario: 日常数据没有变化
- **WHEN** 重叠窗口返回的每日值、版本和 payload identity 与 accepted vintage 相同
- **THEN** 运行 SHALL 返回 `no_change`
- **AND** SHALL 不新增重复 artifact、observation 或 manifest 行

#### Scenario: 历史日被修订
- **WHEN** 重叠窗口对已有日期返回不同 token 或元数据
- **THEN** 系统 SHALL 追加新 vintage 并保留旧值
- **AND** 当前查询与历史 `as_of` 查询 SHALL 分别返回正确版本

### Requirement: 质量门必须检查完整性、守恒和方法漂移
正式 observation SHALL 通过日期、非负整数 token、每日唯一 model identity、Top-N 排名、`Other` 唯一性、总量守恒、作者映射状态和 schema/version 检查。日期缺口、Top-N 数量异常、负值、重复、总量不守恒、响应字段或定义改变 SHALL 产生可定位的质量结果；schema 或方法漂移 SHALL 阻止受影响 vintage 自动发布到 platform，但不得删除最近有效数据。

#### Scenario: 每日总量不守恒
- **WHEN** 声明总量与模型行加 `Other` 的合计不一致
- **THEN** 该日期 SHALL 被隔离并返回 `token_conservation_failed`
- **AND** SHALL 不进入趋势、份额或正式图表

#### Scenario: 数据定义改变
- **WHEN** API version、字段、private/public 范围或 token 定义改变
- **THEN** 系统 SHALL 创建 methodology regime boundary 并标记 `methodology_drift`
- **AND** SHALL 在获得显式兼容规则前停止跨 regime 趋势派生

### Requirement: 聚合和趋势必须只使用完整可比期间
系统 SHALL 从 accepted 日度 observations 派生完整 UTC 周的总 token、模型 token、作者已归属 token、`Other` token、作者 token share、Top-3/Top-5 share 和 HHI。周趋势 SHALL 默认排除未完成周，并披露数据点数、起止期间、最新 4 个完整周相对前 4 个完整周的变化和 4 周移动平均。份额与浓度只能在相同 methodology regime 内计算。

#### Scenario: 当前周尚未结束
- **WHEN** 最新日属于不完整 UTC 周
- **THEN** 正式周趋势 SHALL 截止到上一完整周
- **AND** 最新日数据 MAY 在审计表中出现但不得与完整周直接比较

#### Scenario: 厂商绝对量增长但份额下降
- **WHEN** 某作者 token 绝对量增加而全渠道增长更快导致其 share 下降
- **THEN** DataProduct 和报告 SHALL 同时返回绝对量与份额变化
- **AND** SHALL NOT 仅凭份额下降断言该厂商需求收缩

### Requirement: DataProducts 必须提供可审计的规模、份额和榜单结果
系统 SHALL 提供语义等价于 `openrouter_token_volume_series`、`openrouter_author_share_series`、`openrouter_model_leaderboard`、`openrouter_model_ranking_series` 和 `openrouter_concentration_series` 的受治理查询。每个结果 SHALL 包含 period、value/unit、model/author、coverage、`Other`、quality、freshness、methodology regime、artifact/observation IDs、derivation version 和 source citation。默认查询 SHALL 只返回 accepted/warning，隔离记录仅由审计入口读取。

#### Scenario: 查询模型排名与 token 量时序
- **WHEN** 消费者请求模型排名时序
- **THEN** 结果 SHALL 默认从 2026-01-01 起按完整 UTC 周返回稳定模型集合的 token 量、份额和相对全部模型的 rank
- **AND** 稳定模型集合 SHALL 按该窗口的累计 token 选择，发布日期后缀的同一模型版本 SHALL 合并为一个展示分组，`Other / unselected` SHALL 保持为残差桶

#### Scenario: 查询最新完整周榜单
- **WHEN** 消费者请求最新完整周 Top 10 模型
- **THEN** 结果 SHALL 返回按 token 排序的十个模型、作者、token、占比和 free-route 标记
- **AND** SHALL 同时返回该周总 token 与 `Other` 覆盖说明

#### Scenario: 查询厂商份额趋势
- **WHEN** 消费者请求作者周度 share
- **THEN** 结果 SHALL 返回已归属作者、`unattributed_other`、绝对 token 和份额
- **AND** 所有份额在容差内 SHALL 合计为 100%
