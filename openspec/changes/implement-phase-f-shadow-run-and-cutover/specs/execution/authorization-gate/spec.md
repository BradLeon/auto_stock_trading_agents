# Spec Delta

## Purpose

本文件为 `execution/authorization-gate` 的增补，新增 A 的受限治理行情读取、审批前规范化与审批后价格校验，以及执行授权生命周期、路由代次绑定与未终结状态要求。现有十字段完整性、批准修订一致性、快照新鲜度与失效后重新审批标准继续适用；行情读取能力不构成执行授权。

## ADDED Requirements

### Requirement: Trader 执行参考价格必须通过受限治理行情契约读取

系统 SHALL 为 Trader 版本化升级 MARKET_DATA 治理读取契约，限定审批前当前待审修订标的的订单规范化/风险用途和审批后获批修订标的的执行条件校验用途。Risk 与 Trader SHALL 共用治理 runtime 价格服务；角色 SHALL NOT 直连 Provider 或底层行情读取旁路。结果 SHALL 标识价格类型、实际 source_as_of、查询时点、来源与币种，按预先明确的新鲜度、交易时段及有效性政策校验；历史收盘价 SHALL NOT 冒充实时可执行报价，决策逻辑时间 SHALL NOT 替代实际行情时点。runtime 行情 SHALL NOT 回写共享事实。获取行情能力 SHALL NOT 绕过授权、grant、账户/环境、代次、幂等或 C3 停用。

#### Scenario: 待审订单通过治理路径读取参考价

- **WHEN** Trader 的审批前规范化组件按新契约读取当前待审修订标的的价格
- **THEN** 系统 SHALL 经治理 gateway 和用途/scope 校验返回带真实时点与来源的价格
- **AND** 该读取 SHALL NOT 产生 broker 写能力或生产订单

#### Scenario: Trader 读取无关标的或直连 Provider

- **WHEN** Trader 请求修订范围外的标的、非执行用途，或绕过治理路径直连 Provider
- **THEN** 系统 SHALL 拒绝该路径并记录权限/scope 失败
- **AND** 新增行情契约 SHALL NOT 被解释为全市场研究权限

#### Scenario: 参考价无效或缺少实际行情时点

- **WHEN** 必要参考价缺失、过期、非有限或非正，币种不符，或来源只返回不满足用途的历史收盘价/逻辑决策时间
- **THEN** 系统 SHALL 按明确的价格政策拒绝该价格用于相应用途并展示原因
- **AND** 股数或风险无法确定的金额型订单 SHALL 阻断规范化，SHALL NOT 以市价降级掩盖未知可执行性或静默丢弃

### Requirement: 价格读取不得改变已批准订单意图

订单的股数、限价、类型与方向 SHALL 在审查批准前规范化，固定完整 revision/hash 后完成 Risk review 与 Boss approval。审批依据报价引用与提交前执行校验报价 SHALL 分别留审计，关联同一修订与审批链。审批后取得的新报价 SHALL 仅检查批准约束及明确的价格偏离/新鲜度政策，SHALL NOT 静默修改已批准 symbol、quantity、price、order type、direction 或 hash。需改变订单或超出批准约束时 SHALL 拒绝执行并返回重新审查批准。行情读取、隔离验证或模拟提交成功 SHALL NOT 自动解除 C3 或形成实盘授权。

#### Scenario: 金额型订单在批准前完成换算

- **WHEN** 待审订单以金额表达且取得有效参考价
- **THEN** 系统 SHALL 在生成最终审查/批准前完成股数与必要限价规范化并固定 revision/hash
- **AND** 审批卡 SHALL 显示订单及审批依据报价，审批后 SHALL NOT 再按新价格重新换算股数

#### Scenario: 审批后报价超出执行条件

- **WHEN** 提交前报价超出批准约束或预先明确的偏离阈值，或必要报价失效
- **THEN** 系统 SHALL 拒绝执行并记录报价及拒绝原因，返回重新审查批准
- **AND** SHALL NOT 修改原授权/订单或用原授权提交改价、改量后的订单

#### Scenario: 新报价仍满足批准条件

- **WHEN** 必要执行报价有效且符合批准约束与价格政策
- **THEN** 系统 SHALL 保持已批准订单及 revision/hash 不变并继续独立验证提交门禁
- **AND** 缺 broker grant 或 C3 停用时 SHALL 仍拒绝真实下单

### Requirement: 执行授权必须具有可查询的生命周期

每一份执行授权 SHALL 具备可查询的生命周期状态，至少区分已签发、已提交、已终结与已失效，并 SHALL 可由权威状态判定而非即时构造。授权的未终结状态 SHALL 可被列举，SHALL NOT 仅由调用方在内存中推断。授权 SHALL NOT 在缺少终态记录时被默认视为已终结。

#### Scenario: 已提交但状态未知的授权未纳入检查

- **WHEN** 一份授权对应的订单已提交而终态未知
- **THEN** 该授权 SHALL 出现在未终结授权的列举结果中
- **AND** SHALL NOT 因授权本身已过期而被移出该范围

#### Scenario: 缺少终态记录的授权被视为终结

- **WHEN** 一份授权已提交但没有任何终态记录
- **THEN** 系统 SHALL NOT 将其视为已终结
- **AND** 路由切换 SHALL 因该授权未终结而被阻断

#### Scenario: 授权状态由权威状态判定

- **WHEN** 调用方声称某授权已终结而权威状态无对应记录
- **THEN** 系统 SHALL 以权威状态为准
- **AND** SHALL 报告该声称与权威状态的差异

### Requirement: 执行授权必须绑定签发时的路由代次

每一份执行授权 SHALL 记录其签发时的交易路由标识与路由代次。授权仅对该代次有效；权威代次变更后，既有授权 SHALL 失效，须按新代次重新完成审查与人工批准。系统 SHALL 在提交前校验授权绑定的代次与当前代次一致。

#### Scenario: 代次变更后提交旧授权

- **WHEN** 交易路由代次已提升，而调用方提交由旧代次签发且仍在有效期内的授权
- **THEN** 执行环节 SHALL 拒绝该提交并指明授权所属代次已非当前代次
- **AND** SHALL NOT 通过重新解释授权字段使其继续可用

#### Scenario: 授权缺少代次绑定

- **WHEN** 一份执行授权未记录其签发时的路由代次
- **THEN** 执行环节 SHALL 拒绝该授权并报告缺少代次绑定
- **AND** SHALL NOT 依据当前代次推断其归属

#### Scenario: 回滚后旧代次授权仍被拒绝

- **WHEN** 路由经历 A→B→A 后提交 A 初始代次签发的授权
- **THEN** 执行环节 SHALL 拒绝该授权
- **AND** 仅比较路由标识的实现 SHALL 被判为不满足本要求

### Requirement: 交易路由切换必须先冻结再核验在途授权

切换交易路由前，系统 SHALL 先对当前路由冻结新授权签发与新提交，再核验无未终结授权。存在未终结授权时 SHALL 拒绝切换并列出这些授权标识与状态。系统 SHALL NOT 以强制失效、缩短有效期或静默作废的方式绕过该检查，SHALL NOT 在冻结生效前开始核验。

#### Scenario: 存在未终结授权时请求切换

- **WHEN** 当前存在已提交但未终结的授权，而操作者请求切换交易路由
- **THEN** 系统 SHALL 拒绝该切换并列出该授权标识与状态
- **AND** SHALL NOT 自动作废或改写该授权

#### Scenario: 核验期间出现新签发

- **WHEN** 核验已完成但冻结未生效，其间出现一份新的有效授权
- **THEN** 系统 SHALL 判定该切换无效并重新执行冻结与核验
- **AND** SHALL NOT 继续提升代次

#### Scenario: 在途授权已终结

- **WHEN** 全部在途授权均已终结或已由只读对账承接
- **THEN** 切换 SHALL 可继续进行
- **AND** 系统 SHALL 记录在途授权已清空的核验结果

### Requirement: 影子与纸面运行不得签发可用于真实提交的授权

在影子或纸面运行中产生的审查与批准 SHALL 明确标记其运行模式，且签发的授权 SHALL NOT 被接受为真实提交的授权。真实提交 SHALL 只接受在活跃真实路由与当前代次上、按真实审批链取得的授权。

#### Scenario: 影子运行的授权被用于真实提交

- **WHEN** 调用方提交一份在影子或纸面运行中签发的执行授权用于真实下单
- **THEN** 执行环节 SHALL 拒绝该提交并报告授权来自非真实运行模式
- **AND** SHALL NOT 因该授权字段完整而放行

#### Scenario: 真实路由上的授权在影子运行中被引用

- **WHEN** 影子运行引用一份在真实路由与当前代次上签发的有效授权作为对照
- **THEN** 该引用 SHALL 仅用于比对，SHALL NOT 产生任何真实提交
- **AND** 该引用 SHALL 被记录为对照而非授权来源

### Requirement: 隔离模拟执行必须与生产下单禁令分离

系统 SHALL 仅在完整隔离、真实 broker 进程禁写和明确无网络 FakeBroker 同时成立时允许完整业务链模拟提交。模拟执行 SHALL 保留审批、修订/授权、行情、grant、账户/代次与幂等检查，产物 SHALL 只写隔离账本。生产 C3 SHALL 保留；IBKR Paper 的网络写入 SHALL 需另行明确开放。

#### Scenario: 完整业务链模拟提交

- **WHEN** 隔离环境实际 Trader 经有效审查、批准与执行检查向 FakeBroker 提交
- **THEN** 系统 SHALL 记录非空模拟接收/成交并交由实际 Clerk 对账和重建绩效
- **AND** SHALL NOT 以预写 submitted/filled 订单作为全链验收证据

#### Scenario: 执行价格变化后重新审批被拒绝

- **WHEN** 批准后价格不满足约束，真实执行链返回重新审查并提出新的订单 revision，而人工拒绝新的审批
- **THEN** 系统 SHALL 终止该次执行，SHALL NOT 继承旧 stale outcome 继续反复审查和请求审批
- **AND** 原批准 revision/hash SHALL 保持不变，新的 revision SHALL 单独保留审查与拒绝记录，FakeBroker SHALL 无模拟接收

#### Scenario: 模拟权限逃逸到真实 broker

- **WHEN** 模拟授权或测试 grant 被用于 IBKR、生产账本或退出隔离后的提交
- **THEN** 系统 SHALL 拒绝并保留可定位原因
- **AND** 生产 C3 和真实 broker 写禁令 SHALL 保持有效
