## Purpose

为分析建议、Chief 决策、风险检查和交易日志提供唯一的 action 语义，消除大小写、缺失枚举值和各模块自行解释带来的漂移。

## ADDED Requirements

### Requirement: 内部 action 必须使用单一规范词表

所有分析建议、决策、风险检查和账本记录 SHALL 使用单一声明的小写值：`buy`、`add`、`hold`、`trim`、`sell`。模块 SHALL NOT 各自声明不同子集或接受任意字符串。

#### Scenario: PEAD 产出 add 建议

- **WHEN** Fundamental 需要表达在既有持仓上增持
- **THEN** 它 SHALL 使用规范值 `add`
- **AND** 风险、Chief 和账本 SHALL 无需二次翻译即能识别该值

#### Scenario: 内部输入使用大写 BUY

- **WHEN** 内部 schema 接收 `BUY` 而非规范值 `buy`
- **THEN** 默认严格校验 SHALL 拒绝该输入
- **AND** 仅明确的旧数据迁移适配器可以记录转换后输出 `buy`

### Requirement: 券商买卖方向必须通过显式映射

券商侧 `BUY` / `SELL` 等表示 SHALL 保持为外部协议语义，由唯一映射层从内部 action 和订单意图生成。账本 SHALL 同时保留内部 action 和实际券商 side。

#### Scenario: trim 生成券商卖单

- **WHEN** 已审批的决策 action 为 `trim`
- **THEN** 映射层 SHALL 根据持仓和已审批数量生成券商 `SELL`
- **AND** 订单记录 SHALL 保留原始 `trim` 决策语义

