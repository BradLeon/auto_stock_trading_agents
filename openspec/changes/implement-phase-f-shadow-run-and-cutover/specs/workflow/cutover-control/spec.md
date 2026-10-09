# Spec Delta

## Purpose

为 Phase F 的逐边界切流提供控制平面：把读路径、调度路径与交易路径的切换动作收敛为可审计、可互斥、可回退的显式操作，让每一次路由判定都绑定该数据域与消费者已签发的读取资格、受保护文件的取证基线未被动摇，并且缺项只阻断受影响范围而不是把系统推向已知故障路径。

## ADDED Requirements

### Requirement: 每条切流边界必须有独立功能开关

系统 SHALL 为 projection read、analyst output、Dispatcher schedule、approval lifecycle、Clerk publication 和 live Trader 六条边界分别维护独立功能开关。开关状态 SHALL 可查询、可追溯到操作者与时间；单边界操作 SHALL NOT 隐式改变其他边界。每条边界 SHALL 声明作用范围、实际调用点、各模式语义与权威写入方，并提供真实业务入口的执行及否定路径证明。声明登记 SHALL 与已生效接线分列；只有字符串声明、静态扫描或 bootstrap 写入 SHALL NOT 被视为已接线。兼容矩阵 SHALL 按具体 workflow 与业务 scope 声明；确需共同迁移的范围 SHALL 使用显式联合批次，SHALL NOT 把不兼容的独立请求自动扩成联合动作。

#### Scenario: 切换读路径不影响调度路径

- **WHEN** 某数据域执行兼容的独立读路径批次，从旧路由切到新路由
- **THEN** 调度路径、审批链与账本发布的开关状态 SHALL 保持不变
- **AND** 该次切换 SHALL 记录变更前后的状态、操作者与时间

#### Scenario: 开关状态可被审计读回

- **WHEN** 审计方查询任一边界的当前开关状态
- **THEN** 系统 SHALL 返回该边界的状态、生效时间与最近一次变更的操作者
- **AND** SHALL NOT 返回未经登记的隐式默认值作为实际生效状态

#### Scenario: 边界未声明接线即被判失败

- **WHEN** 某边界缺少作用范围、调用点或权威写入方声明
- **THEN** 该边界 SHALL 判为未接线并拒绝据其执行切流
- **AND** SHALL 报告缺失的声明项

#### Scenario: 只有接线声明没有实际执行

- **WHEN** 某边界登记 wired，但实际业务写入点不执行该边界校验
- **THEN** 系统 SHALL 将该范围视为未接线并拒绝据其切流
- **AND** SHALL NOT 以声明表非空或辅助函数测试通过替代业务证明

### Requirement: 角色发布、审批写入与账本发布必须受各自边界控制

analyst output、approval lifecycle 与 Clerk publication 三条边界 SHALL 分别在实际写入点受其开关控制，而非仅存在可查询的开关状态。跨边界不兼容的组合 SHALL 被拒绝。新投影渲染与仍保留的旧入口 SHALL 有明确的 owner 边界，SHALL NOT 处于无人负责的中间状态。

#### Scenario: 审批写入未受开关控制

- **WHEN** approval lifecycle 开关为关闭而某路径仍写入审批记录
- **THEN** 系统 SHALL 判该写入越界并拒绝
- **AND** 该路径 SHALL 被指出为绕过边界的写入点

#### Scenario: 账本发布绕过 Clerk 边界

- **WHEN** Clerk publication 开关为关闭而某路径直接发布账本记录
- **THEN** 系统 SHALL 判该发布越界并拒绝
- **AND** SHALL NOT 以最终数据一致为由容忍越界写入

#### Scenario: 跨边界不兼容组合

- **WHEN** 某组开关组合违反既定的跨边界约束
- **THEN** 系统 SHALL 拒绝该组合并指出冲突的边界
- **AND** SHALL NOT 选取其中一个边界继续运行

### Requirement: 冲突的切流配置必须在启动时 fail closed

当同一条边界上出现互斥的路由配置，或边界状态组合违反既定约束时，系统 SHALL 在启动阶段判定失败并指出冲突的边界与配置项。系统 SHALL NOT 在冲突状态下继续运行、默认选取其中一条路由，或降级为无门禁运行。权威状态不可读时 SHALL fail closed。

#### Scenario: 同一边界声明两个活跃路由

- **WHEN** 配置同时把某条边界的旧入口与新入口声明为可写活跃
- **THEN** 启动 SHALL 失败并指出该边界与两个冲突路由标识
- **AND** SHALL NOT 选取其中任一路由继续提供服务

#### Scenario: 新路由激活要求未取得的读取资格

- **WHEN** 请求激活交易新路由，而任一必需分析类别在该决策 scope 内未取得读取资格
- **THEN** 激活 SHALL 被拒绝并指出缺失资格的消费者与数据域
- **AND** SHALL NOT 以「其他路径已就绪」为由放行

#### Scenario: 该约束不追溯否决现存旧路由

- **WHEN** 某必需消费者未取得新读取资格，而当前交易的旧路由为现存活跃路由
- **THEN** 该情形 SHALL NOT 被判为启动失败
- **AND** 系统 SHALL 仅在激活新路由时要求该资格

#### Scenario: 交易开关关闭时研究服务可启动

- **WHEN** 交易开关为关闭而研究读取开关为开启，且无其他冲突
- **THEN** 研究服务 SHALL 允许启动
- **AND** 研究读取与交易激活 SHALL 作为两件独立事处理

#### Scenario: 启动时未知 scope 不被假定为已验证

- **WHEN** 启动时存在尚未作出资格判定的未知 scope
- **THEN** 系统 SHALL NOT 假定该 scope 已全面验证
- **AND** 后续提交前 SHALL 仍按本次决策的 scope 重新验证资格

### Requirement: 读路径路由必须逐消费者依据读取资格判定

系统 SHALL 在实际 CLI、Workflow 与 Dispatcher 读取入口按 `domain_id + consumer_id + contract_version + scope` 逐条判定。资格查询、影子报告、回退证明、持久化运行路由和记录 SHALL 使用同一业务 scope；SHALL NOT 将实体或时间范围替换为消费者产品列表。运行路由 SHALL 能独立表达不同消费者/范围的迁移状态，重启后保持一致。任一必需证据缺失、过期、被撤销或回滚证明未通过时，SHALL 进入安全回退判定而非直接切流。SHALL NOT 以单一总开关或「同一数据域其他消费者已通过」为由放行未通过的消费者。

#### Scenario: 同一消费者另一实体未取得资格

- **WHEN** 该消费者仅在实体 A 的 scope 合格，而实际请求读取实体 B
- **THEN** 系统 SHALL 查询 B 的 exact-scope 资格并按其结果处置
- **AND** SHALL NOT 用 A 或消费者全产品列表的资格放行 B

#### Scenario: 数据域通过但单个消费者无安全恢复证明

- **WHEN** 某数据域的写入、准入、读取与血缘证据齐备，而其中一个消费者没有经验证的安全停止或版本恢复证明
- **THEN** 该消费者 SHALL 不具备切换资格
- **AND** 系统 SHALL 进入安全回退判定并报告缺少安全恢复证明

#### Scenario: 资格在切换前被撤销

- **WHEN** 某消费者的读取资格在切换执行前被撤销或因来源版本变化而失效
- **THEN** 该次切换 SHALL 被拒绝并报告失效原因
- **AND** 系统 SHALL 保持其原有稳定路由；原路由无法确认安全时保持停止

#### Scenario: 读取资格不得开启交易权限

- **WHEN** 某消费者的全部读取资格均为通过状态
- **THEN** 该资格 SHALL 仅允许其读路径切流
- **AND** SHALL NOT 被解释为任何交易、审批或下单权限

#### Scenario: 原生产品查询不能替代业务 scope

- **WHEN** 原生消费者 API 收到产品查询参数，却没有显式业务 scope 或绑定的当次入口上下文
- **THEN** 系统 SHALL 拒绝读取，而不是使用产品列表构造资格 scope
- **AND** 绑定后更换实体、消费者或历史 cutoff SHALL 被拒绝，资格门禁失败 SHALL NOT 被吞为成功的空 payload

### Requirement: 资格失效时必须执行已登记的安全停止或恢复策略

系统 SHALL 按 workflow/scope 在运行前登记安全停止、恢复已验收版本或可选旧目标策略。停止 SHALL 禁止该范围继续读取/发布与依赖交易，保留历史和审计；恢复 SHALL 核验目标 scope、版本、有效证明、资格、未退役及实际可读性，并重查撤销/代次。缺目标或核验失败 SHALL 保持停止。系统 SHALL NOT 要求修复旧业务或强退故障旧路；下游已组装快照 SHALL 重验失效依赖。

#### Scenario: 安全停止作为完整恢复策略
- **WHEN** 某 scope 资格失效且已登记安全停止
- **THEN** 系统 SHALL 停止该范围并阻断其依赖交易、保留历史与审计
- **AND** 已实测上述行为 SHALL 满足该范围的恢复验收，不要求旧入口可用

#### Scenario: 恢复已验收的新版本
- **WHEN** 操作者选择已验收的新版本作为恢复目标
- **THEN** 系统 SHALL 核验该版本的资格、scope、有效证明与实际读取并重查撤销/代次
- **AND** SHALL NOT 自动沿用当前版本的资格或恢复旧授权

#### Scenario: 明确选择旧目标
- **WHEN** 批次明确登记可选旧目标恢复
- **THEN** 系统 SHALL 核验未退役、证明/指纹有效与当次真实读取引用
- **AND** 任一失败 SHALL 保持停止，SHALL NOT 为通过验收修复旧业务

#### Scenario: 恢复证明被撤销
- **WHEN** 某 scope 的恢复证明被撤销
- **THEN** 系统 SHALL 追加 scope/引用/操作者/原因并停止后续使用该证明
- **AND** SHALL NOT 隐式撤销其他范围

#### Scenario: 已组装快照继续执行
- **WHEN** 已组装快照的必需依赖失效
- **THEN** 下游审批/提交 SHALL 重验并停止无效范围
- **AND** SHALL NOT 仅记日志继续，也不扩大为无关研究范围的全局阻断

### Requirement: 切流不得动摇受取证约束的文件与基线

运行时切流 SHALL 只使用既有发布覆盖层与本 change 独立的控制状态，SHALL NOT 修改已被取证的消费者契约基线与受指纹约束的代码或配置文件。若确需修改，系统 SHALL 在最终代码与配置上重新验证、追加证据并重算受影响消费者资格，SHALL NOT 修改旧证据或放宽指纹标准。系统 SHALL NOT 因一次切流动作而使已登记证据整体失效。

#### Scenario: 切流动作改动了受指纹约束的配置

- **WHEN** 某次切流改动了被资格政策纳入指纹的文件
- **THEN** 系统 SHALL 判该动作不安全并拒绝
- **AND** SHALL 报告该文件的指纹变化将使相关消费者资格失效

#### Scenario: 共同依赖文件的改动影响多个消费者

- **WHEN** 为某个消费者调整路由而需要改动被多个消费者共同纳入指纹的文件
- **THEN** 系统 SHALL 要求重算全部受影响消费者的资格
- **AND** SHALL NOT 只为当前消费者重新取证

#### Scenario: 必须修改时在最终状态重新取证

- **WHEN** 确需修改受约束文件以完成某项工作
- **THEN** 系统 SHALL 在修改完成后的最终代码与配置上重新验证并追加证据
- **AND** 旧证据 SHALL 保持原样且不被改写

### Requirement: 切流执行必须留下可回退的批次记录

每次切流 SHALL 以批次记录标识、涉及边界、逐消费者范围、实际 owner、前后路由、明确观察窗口/成功与停止条件、所用新入口验收报告和已验证安全停止/版本恢复策略。正式 dry-run 与执行器 SHALL 强制核验报告适用性及投影实际可用性；缺报告、缺校验能力、缺接线证明或不可确认投影 SHALL 拒绝，SHALL NOT 默认通过。需求面不适用 SHALL 依据新需求矩阵显式声明，SHALL NOT 以空报告 ID 表示通过。回退演练 SHALL 在首次生产切换前于隔离环境完成，不以已经生产切换为前置；切后实际回退 SHALL 单独留证。安全停止经真实入口验证 SHALL 可作为恢复策略；既无可用恢复目标也无经过验证的停止策略时 SHALL 保持未切换。

#### Scenario: 无可用回退的批次

- **WHEN** 某批次既未登记可用版本恢复，也未登记经实际验证的安全停止
- **THEN** 该批次 SHALL 不进入切流执行
- **AND** SHALL 被登记为缺口且相关消费者保持未切换

#### Scenario: 批次执行后观察窗口内出现停止条件

- **WHEN** 批次切换后在观察窗口内出现登记的停止条件
- **THEN** 系统 SHALL 执行该批次登记的回退方式并记录回退结果
- **AND** SHALL 报告回退后各边界的实际路由状态

#### Scenario: 未通过路径不得随整体批次切换

- **WHEN** 一个批次包含多个消费者范围，而其中一部分未取得资格
- **THEN** 该批次 SHALL 仅对已通过的消费者范围执行切换
- **AND** 未通过范围 SHALL 明确报告为未切换，SHALL NOT 静默沿用新路由

#### Scenario: 批次缺少可用新入口验收报告

- **WHEN** 某批次没有新入口验收报告、没有报告校验能力，或引用了不适用/撤销的报告
- **THEN** 系统 SHALL 拒绝执行该批次并报告报告问题
- **AND** SHALL NOT 以其他批次的报告替代

#### Scenario: 没有投影可用性检查

- **WHEN** 正式入口没有提供投影实际可用性检查，或无法确认投影可读取
- **THEN** 该批次 SHALL 拒绝执行并报告缺失能力或投影缺口
- **AND** SHALL NOT 将 available=true 且 checked=false 作为通过证据

### Requirement: 联合边界切换必须显式且不可暴露半迁移状态

生产联合授权 SHALL 绑定完整规范请求（包括 workflow、业务身份、边界、目标及预期代次），持久化且保留不可修改历史；授权过期或撤销 SHALL 阻止提交或恢复开放。隔离 fixture 门禁 SHALL 仅在物理隔离和进程 broker 禁写能力均经核验时使用。恢复 SHALL 核对原权威状态路径与本批次冻结来源，SHALL NOT 接管其他操作的冻结 token。

确需联合的批次 SHALL 明确列出全部边界、workflow/consumer scope、资格、报告、授权范围及回退协议。单边界动作不兼容时 SHALL 拒绝，SHALL NOT 隐式扩大授权。联合动作 SHALL 以事务或冻结后可恢复协议协调全部权威状态，在提交时重验前置条件；故障后 SHALL NOT 暴露可执行的不兼容半迁移状态。控制表、发布覆盖层与 owner/schedule 生效状态的权威及恢复方式 SHALL 明确。回退 SHALL 遵守同一范围与协调协议。

#### Scenario: 独立读请求需要联合调度

- **WHEN** 具体 workflow/scope 的独立读请求违反兼容矩阵，且未声明联合调度动作
- **THEN** 系统 SHALL 拒绝并报告需要的联合批次范围
- **AND** SHALL NOT 自动改变调度边界

#### Scenario: 第一边界写入后崩溃

- **WHEN** 联合动作在第一边界写入后、其余边界完成前崩溃
- **THEN** 系统 SHALL 回滚事务或保持受影响范围冻结并按协议恢复
- **AND** 重启后 SHALL NOT 运行读 target/调度 legacy 等不兼容组合

#### Scenario: 部分消费者未通过

- **WHEN** 联合批次中 layer 合格而 sector 不合格
- **THEN** 仅依赖闭合的 layer 范围 SHALL 可执行，其余明确未切换
- **AND** SHALL NOT 修改全局路由使 sector 实际使用新路径

### Requirement: 切流门禁不变量必须由机器校验

系统 SHALL 提供可重复执行的校验，确认边界开关互斥、跨边界组合兼容、读路径资格绑定、受取证文件未被动摇、交易入口单活与回退方式已验证。校验 SHALL 覆盖否定路径（冲突配置、缺资格、双活交易入口、无回退批次、指纹漂移均必须判失败）。校验失败 SHALL 阻断切流操作。

#### Scenario: 校验捕获双活交易入口

- **WHEN** 校验发现旧交易入口与新交易入口同时处于可提交真实订单的状态
- **THEN** 校验 SHALL 失败并指出两个入口标识
- **AND** 该失败 SHALL 阻断该边界的切流操作

#### Scenario: 校验捕获指纹漂移

- **WHEN** 校验发现受指纹约束的文件已相对取证时点发生变更
- **THEN** 校验 SHALL 失败并列出漂移文件
- **AND** 该失败 SHALL 阻断据旧证据执行的切流

#### Scenario: 校验纳入架构守卫

- **WHEN** 新增绕过切流控制直接改变路由、直接提交真实订单或绕过边界开关写入的代码路径
- **THEN** 架构守卫 SHALL 判失败并指出该路径
- **AND** 该路径 SHALL NOT 通过人工豁免长期保留，豁免必须声明收敛阶段
