# Spec Delta

## ADDED Requirements

### Requirement: 趋势序列按来源与口径分开计算
收入趋势 SHALL 在同一公司、同一来源、同一指标口径与同一观察身份内计算。不同来源或不同口径的点 SHALL NOT 拼接成一条趋势线；来源切换处 SHALL 断开并标注。公司在注册文件或定期报告中自报的数值 SHALL 作为独立的公司自报序列，SHALL NOT 与第三方估算混合。

#### Scenario: 来源切换造成的假下滑
- **WHEN** 某公司 6 月的点来自 TickerTrends 的 ARR，7 月的点来自 Sacra 的年化收入
- **THEN** 两个点 SHALL 分属两条序列，系统 SHALL NOT 报告 6 月到 7 月的环比下降

#### Scenario: 公司自报数值
- **WHEN** ANT 的 424B4 披露某期间收入
- **THEN** 该值 SHALL 进入公司自报序列，并与 Sacra 等第三方序列并列呈现
