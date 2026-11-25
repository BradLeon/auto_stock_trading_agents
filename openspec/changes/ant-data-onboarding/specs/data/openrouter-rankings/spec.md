# Spec Delta

## MODIFIED Requirements

### Requirement: Token、请求、用户、支出和收入必须严格区分
系统 SHALL 将 `total_tokens` 定义为 OpenRouter 公共请求中 prompt 与 completion token 的合计，并明确其不是请求数、会话数、用户数、API 支出、OpenRouter 收入或全行业收入。上游 provider tokenizer 不同造成的跨模型 token 不完全可比 SHALL 作为固定 methodology warning。系统 SHALL NOT 把 token 与价格的乘积称为收入或实际支出；只允许按"估算花费份额"要求发布带估算标签的份额，且 SHALL NOT 用当前价格回填价格采集开始之前的历史期间。

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

#### Scenario: 价格采集开始前的历史期间
- **WHEN** 消费者请求价格采集开始之前某周的估算花费份额
- **THEN** 系统 SHALL 返回"无价格数据"，SHALL NOT 用当前价格回算

## ADDED Requirements

### Requirement: 模型价格按公开模型列表定期采集
系统 SHALL 从 OpenRouter 公开模型列表采集每个模型的输入与输出单价，保存模型标识、作者、单价、币种与单位、采集时间，并按修订 vintage 保存价格变化。价格 SHALL 只作为价格事实发布，SHALL NOT 改写 token 观测。

#### Scenario: 模型降价
- **WHEN** 某旗舰模型的输出单价在两次采集之间下降
- **THEN** 系统 SHALL 保存新旧两个价格及各自的有效时间
- **AND** 旗舰模型价格的月度变化 SHALL 可从已保存价格重算

### Requirement: 估算花费份额必须带估算标签与假设
系统 SHALL 按"周 token × 当周有效单价"计算各作者的估算花费份额。由于排行数据不区分输入与输出 token，计算 SHALL 使用一个已声明的输入输出比例假设，并在结果中披露该假设。结果 SHALL 标注为"估算花费份额"，SHALL NOT 称为收入、实际支出或市场份额。缺少价格的模型 SHALL 计入"无价格"桶并披露其 token 占比。

#### Scenario: 发布 Anthropic 估算花费份额
- **WHEN** 消费者查询最近一周 Anthropic 的估算花费份额
- **THEN** 结果 SHALL 包含份额、输入输出比例假设、无价格桶的 token 占比
- **AND** 标签 SHALL 为"估算花费份额"

#### Scenario: 无价格模型占比过高
- **WHEN** 当周无价格模型的 token 占比超过 20%
- **THEN** 结果 SHALL 带质量警告，提示估算份额的可靠性不足
