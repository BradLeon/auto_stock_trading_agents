## Purpose

将决策背景、审批、订单、成交、持仓、资金、绩效和归因组织为可重放的确定性交易账本；Clerk 是串联、对账、补偿和发布读模型的确定性服务，不是在事后猜测事实的 Agent。

## Requirements

### Requirement: Clerk 必须是确定性的编排服务

Clerk SHALL 只读取决策与审批的领域记录、券商回报和确定性计算的结果来串联账本，SHALL NOT 由 LLM 生成、猜测或覆盖 order、fill、position、cash、PnL 或 attribution 事实。

各业务服务 SHALL 在事件发生时事务性写入自己的领域记录；Clerk SHALL 负责串联、对账、补偿和发布读模型，SHALL NOT 重写其他服务已经落地的领域事实。

Clerk 的一次运行 SHALL 由「运行种类 + 对账窗口 + 数据截止时点」派生的幂等键标识，重复执行同一窗口 SHALL 产生相同的账本状态，SHALL NOT 产生新的事实记录。

#### Scenario: 同一对账窗口被重复执行

- **WHEN** Clerk 在同一窗口被调度两次（定时重叠或人工重跑）
- **THEN** 第二次运行 SHALL 复用第一次的幂等键结果
- **AND** SHALL NOT 新增成交、重复计入绩效或改写已有账本事实

#### Scenario: 领域服务已写入的订单事实被 Clerk 处理

- **WHEN** Clerk 处理一条由 Trader 写入的系统订单记录
- **THEN** Clerk SHALL 只补充对账、归属、补偿和派生读模型所需的字段
- **AND** SHALL NOT 改写该订单的提交意图、数量、标的或决策链标识

### Requirement: 系统订单与成交必须绑定完整决策链

每个系统 order 与 fill SHALL 可追溯到 cycle、decision revision、decision hash、risk review 和 Boss approval。新写入的系统订单与成交 SHALL 携带完整的决策链标识，缺失 SHALL 被拒绝写入或登记为审计异常，SHALL NOT 静默写入半条链。

该强制 SHALL 由写路径校验实现，SHALL NOT 用数据库非空约束表达：除主键外，任何字段 SHALL 允许为 NULL，以容纳历史行、人工订单和无法归因订单——它们没有可填的链接，非空约束只会逼出伪造值或阻断迁移。

#### Scenario: 新系统订单缺少修订标识

- **WHEN** 写路径尝试写入一条缺少 revision 或 decision hash 的系统订单
- **THEN** 系统 SHALL 拒绝该次写入并登记审计异常
- **AND** SHALL NOT 以空值或推测值补齐决策链

#### Scenario: 券商成交匹配到系统订单标识

- **WHEN** 一笔 fill 的订单引用匹配到系统 client order ID
- **THEN** Clerk SHALL 将该 fill 链接到对应 order 及其完整决策审批链
- **AND** SHALL 保留券商订单号、成交号、时间、数量、价格和费用

#### Scenario: 历史订单存在无法还原的断链

- **WHEN** 迁移前落地的历史订单缺少决策链字段
- **THEN** 系统 SHALL 将其登记为待清偿的历史缺口并保留可空字段
- **AND** SHALL NOT 伪造 cycle、revision 或 approval 链接

#### Scenario: 人工或无法归因的成交缺少决策链

- **WHEN** 一笔人工订单或无法归因的成交写入账本时没有决策链可填
- **THEN** 系统 SHALL 允许其以空链接字段落地并登记归属与审计异常
- **AND** SHALL NOT 因数据库非空约束拒绝写入，SHALL NOT 用占位值或推测值填充链接

### Requirement: 成交归属必须区分系统订单、人工订单和无法归因订单

Clerk SHALL 依据可验证的订单引用把券商成交分为系统订单、人工订单或无法归因订单，并保留判定依据与置信度。

无法归因的成交 SHALL 一律归属为 unattributed 并产生显式的审计异常项，SHALL NOT 由实现者选择归入 manual，SHALL NOT 被删除、被静默归类为人工订单或被强行归类为系统订单。

#### Scenario: 对账发现无法匹配任何系统订单的成交

- **WHEN** 一笔 fill 既不匹配订单引用、也不匹配券商持久标识、也不满足同标的同会话推断
- **THEN** Clerk SHALL 将其归属记录为无法归因并生成显式异常项
- **AND** SHALL NOT 伪造 cycle 或 approval 链接，SHALL NOT 将其计入系统交易绩效

#### Scenario: 券商成交属于人工下单

- **WHEN** 一笔 fill 被判定为人工订单
- **THEN** Clerk SHALL 保留其券商身份与判定依据并纳入账本
- **AND** SHALL 在内部状态中将其与系统交易分开呈现

### Requirement: 对账与补偿必须可幂等重放

Clerk SHALL 使用券商稳定身份和本地幂等键重放订单、成交、撤单和拒单对账。部分成交、迟到成交、进程重启或某日漏跑 SHALL NOT 造成重复成交、丢失原记录或重复计入绩效。

重放 SHALL 以 order attempt 序列为最小单位：同一提交意图的多次尝试（含重试）SHALL 按 attempt 序号被识别为同一意图的不同尝试，SHALL NOT 产生重复成交或重复计入绩效。

#### Scenario: 相同成交被重复返回

- **WHEN** 后续对账再次收到已记录的券商成交号
- **THEN** Clerk SHALL 幂等更新对账时点或返回已有记录
- **AND** SHALL NOT 新增第二笔成交或重复计入绩效

#### Scenario: 一笔订单多次部分成交

- **WHEN** 同一订单先后回报多笔部分成交
- **THEN** Clerk SHALL 累计成交数量并按成交量加权累计成交均价
- **AND** 在订单未收口前 SHALL 保持部分成交状态，SHALL NOT 直接改写为完全成交

#### Scenario: 成交迟到于下单日之后回报

- **WHEN** 一笔成交在下单日的后续对账中才出现
- **THEN** Clerk SHALL 将其回填到原订单且不新增第二笔订单
- **AND** SHALL 记录该成交为迟到补偿并保留回填时点

#### Scenario: 同一提交意图被重试提交

- **WHEN** 同一订单意图因券商掉线或不确定结局被再次提交，产生新的 order attempt
- **THEN** Clerk SHALL 将其识别为同一意图的新一次尝试并累加 attempt 序号
- **AND** SHALL NOT 新增第二笔订单事实，SHALL NOT 重复计入成交量或绩效

#### Scenario: 进程在对账过程中重启

- **WHEN** Clerk 运行在对账中途被中断并在之后重新执行同一窗口
- **THEN** 重放 SHALL 从已落地的事实继续，不得重复计入或丢失已处理记录
- **AND** SHALL NOT 把中断前已完成的补偿重做一遍

#### Scenario: 某个交易日整体漏跑

- **WHEN** 某个交易日的对账从未运行且券商接口已无法返回该日的成交
- **THEN** Clerk SHALL 将该窗口登记为显式对账缺口并给出影响范围
- **AND** SHALL NOT 以空结果伪造「该日已对账」，SHALL NOT 用其他日期的数据填充

### Requirement: 订单终态必须可判定且推定必须留痕

每笔订单 SHALL 收敛到可判定的终态（成交、部分成交后收口、撤单、拒单、过期或错误），且终态 SHALL 携带依据来源。

缺少券商证据的推定终态 SHALL 显式标记为推定并记录推定依据，SHALL NOT 与券商明确回报的状态混淆。

#### Scenario: 券商明确回报撤单或拒单

- **WHEN** 券商回报订单已撤单或被拒绝
- **THEN** Clerk SHALL 将该订单终态置为撤单或拒单并记录券商依据
- **AND** SHALL NOT 继续等待成交，SHALL NOT 计入成交绩效

#### Scenario: 在途订单缺少券商证据而被推定过期

- **WHEN** 一笔当日有效的在途订单超过有效期限且券商无明确回报
- **THEN** Clerk SHALL 将其终态置为过期并标记该判定为推定
- **AND** SHALL 保留推定依据与判定时点，供后续补偿修正

### Requirement: Clerk 必须对账持仓与资金

Clerk SHALL 读取券商持仓与资金并与本地账本比对，差异 SHALL 被登记为显式对账差异项，SHALL NOT 被静默忽略或用于静默改写本地账本。

#### Scenario: 券商持仓与本地账本不一致

- **WHEN** 对账时券商持仓数量与本地账本推导的持仓不一致
- **THEN** Clerk SHALL 登记差异项并保留两侧数值、时点和标的
- **AND** SHALL NOT 以券商数值静默覆盖本地账本，也不得以本地数值掩盖差异

### Requirement: LLM 输出不得成为账本事实

LLM 的输出 SHALL 只作为附加分析或标注类字段落地，SHALL NOT 成为 order、fill、position、cash、PnL 或 attribution 的事实来源。

由 LLM 产生的标注 SHALL 携带来源标记与生成时点，SHALL NOT 覆盖或回写确定性计算结果。

#### Scenario: 可选 critic 对交易生成复盘文字

- **WHEN** LLM critic 为已完成交易生成复盘叙事
- **THEN** 叙事 SHALL 作为附加分析存储并链接到不可变账本事实
- **AND** SHALL NOT 修改任何账本金额、交易身份或归因计算

#### Scenario: LLM 判定作为标注字段写入

- **WHEN** LLM 对某笔交易产生失效判定之类的布尔判定
- **THEN** 该判定 SHALL 只写入标注类字段并标记来源为 LLM
- **AND** SHALL NOT 写入或修改金额、数量、成交身份、持仓或归因数值
