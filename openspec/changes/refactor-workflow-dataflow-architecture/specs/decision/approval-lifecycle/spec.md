## Purpose

建立从 Chief 研究快照、不可变决策 revision、Risk 审查、Boss 人工批准到 Trader 幂等执行的完整可审计生命周期。

## ADDED Requirements

### Requirement: 决策周期必须绑定完整研究快照

Chief SHALL 只能在被要求的分析师投影全部有效时创建 decision cycle。Cycle SHALL 引用不可变 research snapshot，并保留触发来源、不行动选项和当前状态。

#### Scenario: 完整输入下 Chief 决定不交易

- **WHEN** Chief 基于完整 research snapshot 选择 No Action
- **THEN** cycle SHALL 记录 No Action 理由并进入终态
- **AND** SHALL NOT 调用 Risk、Boss 或 Trader

### Requirement: 每次 Chief 修订必须生成不可变 revision

每个交易提案 SHALL 以 cycle 内单调递增的 revision 存储，包含 parent revision、完整指令、Chief 理由、输入引用、模型/prompt 版本和规范化 decision hash。旧 revision SHALL NOT 被覆盖。

#### Scenario: Chief 接受 Risk counterproposal

- **WHEN** Chief 接受 Risk 提出的最大可增持金额边界
- **THEN** Chief SHALL 创建引用前一 revision 的新 revision
- **AND** 新 revision SHALL 计算自己的 decision hash 并重新进入 Risk

### Requirement: Risk pass/fail 必须由确定性规则决定

Risk review SHALL 绑定 decision hash、ruleset version、portfolio snapshot ID 和 market as-of。确定性风险引擎 SHALL 决定硬规则 pass/fail 和交易前后指标；Risk Agent SHALL 只解释违规、允许边界和冲突，SHALL NOT 覆盖硬规则结果。

#### Scenario: LLM 解释建议通过但硬规则失败

- **WHEN** 确定性风控检测到硬限额违规而 Risk Agent 文本误判为可接受
- **THEN** Risk review SHALL 为 reject
- **AND** SHALL 保留 Agent 文本作为可观测异常而不影响审批结果

### Requirement: Risk 不得静默修改交易提案

Risk SHALL 对被审 revision 返回 approve 或 reject。任何数量裁剪、标的替代或行为调整 SHALL 作为结构化 counterproposal 返回 Chief，SHALL NOT 就地改写被审 revision。

#### Scenario: 单票权重超限

- **WHEN** 提案的交易后单票权重超过硬上限
- **THEN** Risk SHALL reject 原 revision 并返回 rule ID、limit、post-trade value 和允许边界
- **AND** 原 revision 的订单数量 SHALL 保持不变

### Requirement: Chief—Risk 自动 Loop 必须有界

同一 cycle 默认 SHALL 最多执行 3 轮自动 Risk 驳回和 Chief 修订。通过前可持续产生新 revision；达到上限或无法生成合规方案时 SHALL 进入 `manual_review`。

#### Scenario: 第三轮仍被驳回

- **WHEN** cycle 的第三次风控审查仍为 reject
- **THEN** cycle SHALL 进入 `manual_review` 终态
- **AND** SHALL NOT 自动创建第四个修订轮次或进入 Boss

### Requirement: Boss 只能批准或拒绝同一被风控批准的 revision

Boss approval SHALL 绑定已通过 Risk 的 exact decision hash。Boss SHALL 只能 approve 或 reject；任何修改意见 SHALL 作为 reject note 保存，并由 Chief 创建新 revision 后重新风控。

#### Scenario: Boss 尝试改变数量

- **WHEN** Boss 对已批准 revision 提交了不同数量
- **THEN** 该动作 SHALL 被记录为拒绝及修改意见
- **AND** Trader SHALL NOT 收到修改后指令

#### Scenario: 重复或旧 revision 回调

- **WHEN** Boss 回调重复到达或指向非当前 revision
- **THEN** 系统 SHALL 幂等返回已有结果或拒绝旧 revision
- **AND** SHALL NOT 对已终结 cycle 产生新执行授权

### Requirement: Trader 只能消费新鲜且完整的执行授权

ExecutionAuthorization SHALL 绑定 cycle、revision、decision hash、risk review、Boss approval、ruleset version、portfolio snapshot 和 market as-of。市场或账户快照默认超过 60 秒时 SHALL 失效，必须用新快照重新 Risk 并重新获得 Boss 批准。

#### Scenario: Boss 批准后快照过期

- **WHEN** Trader 准备下单时 portfolio 或 market snapshot 已超过配置新鲜度
- **THEN** Trader SHALL 拒绝当前 authorization 并将 cycle 返回 Risk pending
- **AND** SHALL NOT 使用原 Boss approval 执行新快照下的交易

### Requirement: Trader 提交必须幂等

每个订单的 client order ID SHALL 由 cycle、revision 和订单序号稳定派生。相同 authorization 和订单序号的重试 SHALL 查询或返回既有提交结果，SHALL NOT 重复下单。

#### Scenario: 提交超时后重试

- **WHEN** 首次券商提交超时且调用方以同一订单序号重试
- **THEN** Trader SHALL 先用稳定 client order ID 查询已有订单
- **AND** 只在确认未提交时才可继续，不得生成新逻辑订单

### Requirement: 检查点不得作为唯一审计真相

运行检查点 SHALL 可用于恢复中断流程，但 revision、risk review、Boss approval、cycle event 和执行记录 SHALL 以领域存储为权威来源。

#### Scenario: 检查点丢失但领域记录存在

- **WHEN** 运行检查点不可用而领域审计记录完整
- **THEN** 系统 SHALL 以领域记录判定已发生的决策、审批和执行事件
- **AND** SHALL NOT 因检查点丢失而重复审批或下单

