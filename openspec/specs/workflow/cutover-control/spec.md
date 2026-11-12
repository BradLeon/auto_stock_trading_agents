# workflow/cutover-control Specification

## Purpose
为 Phase F 的逐边界切流提供控制平面：把读路径、调度路径与交易路径的切换动作收敛为可审计、可互斥、可回退的显式操作，让每一次路由判定都绑定该数据域与消费者已签发的读取资格、受保护文件的取证基线未被动摇，并且缺项只阻断受影响范围而不是把系统推向已知故障路径。

## Requirements

### Requirement: 每条切流边界必须有独立功能开关

系统 SHALL 为 projection read、analyst output、Dispatcher schedule、approval lifecycle、Clerk publication 和 live Trader 六条边界分别维护独立功能开关。开关状态 SHALL 可查询、可追溯到操作者与时间；单边界操作 SHALL NOT 隐式改变其他边界。每条边界 SHALL 声明作用范围、实际调用点、各模式语义与权威写入方，并提供真实业务入口的执行及否定路径证明。声明登记 SHALL 与已生效接线分列；只有字符串声明、静态扫描或 bootstrap 写入 SHALL NOT 被视为已接线。兼容矩阵 SHALL 按具体 workflow 与业务 scope 声明；确需共同迁移的范围 SHALL 使用显式联合批次，SHALL NOT 把不兼容的独立请求自动扩成联合动作。

入口证明 SHALL 按独立于证据提交者的入口契约及当前配置核对 kind/id/entities。Dispatcher 的单标的任务只处理该标的；组合级 Information CLI 一次处理配置中的完整组合，SHALL NOT 接受单标的或组合子集冒充组合请求。所有登记调用点 SHALL 保留在证据包中：契约兼容入口须有成功与拒绝两类实际执行证明；契约不兼容入口须证明业务执行前拒绝且无业务副作用，SHALL NOT 要求或伪造越界成功。调用者 SHALL NOT 自行声明豁免，配置/代码/测试及证明漂移 SHALL 使相应证据失效。

#### Scenario: 单标的批次核验组合级入口

- **WHEN** 单标的 MRVL 请求经 Dispatcher 执行，同时验收器检查登记的组合级 Information CLI
- **THEN** Dispatcher 仍须证明成功及拒绝路径；该 CLI 须证明拒绝单标的请求且未进入业务执行，不要求它越界成功，也不得从登记集合删除

#### Scenario: 完整组合入口及越界请求

- **WHEN** Information CLI 收到与当前配置完全匹配的组合请求，或收到单标的、错误组合标识或组合子集请求
- **THEN** 完整组合须验证成功及拒绝路径；越界请求 SHALL 在采集、模型或业务发布前拒绝，验收器 SHALL 核对配置与实际测试的指纹

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

### Requirement: 联合边界切换必须显式且不可暴露半迁移状态

联合边界协调 SHALL 仅适用于必须成对切换的审批与账本发布；研究读取与调度的单独切换 SHALL NOT 经联合协调器。

生产联合授权 SHALL 绑定完整规范请求（包括 workflow、业务身份、边界、目标及预期代次），持久化且保留不可修改历史；授权过期或撤销 SHALL 阻止提交或恢复开放。隔离 fixture 门禁 SHALL 仅在物理隔离和进程 broker 禁写能力均经核验时使用。恢复 SHALL 核对原权威状态路径与本批次冻结来源，SHALL NOT 接管其他操作的冻结 token。

确需联合的批次 SHALL 明确列出全部边界、workflow/consumer scope、资格、报告、授权范围及回退协议。单边界动作不兼容时 SHALL 拒绝，SHALL NOT 隐式扩大授权。联合动作 SHALL 以事务或冻结后可恢复协议协调全部权威状态，在提交时重验前置条件；故障后 SHALL NOT 暴露可执行的不兼容半迁移状态。控制表、发布覆盖层与 owner/schedule 生效状态的权威及恢复方式 SHALL 明确。回退 SHALL 遵守同一范围与协调协议。

#### Scenario: 第一边界写入后崩溃

- **WHEN** 联合动作在第一边界写入后、其余边界完成前崩溃
- **THEN** 系统 SHALL 回滚事务或保持受影响范围冻结并按协议恢复
- **AND** 重启后 SHALL NOT 运行读 target/调度 legacy 等不兼容组合

#### Scenario: 部分消费者未通过

- **WHEN** 联合批次中 layer 合格而 sector 不合格
- **THEN** 仅依赖闭合的 layer 范围 SHALL 可执行，其余明确未切换
- **AND** SHALL NOT 修改全局路由使 sector 实际使用新路径

### Requirement: 切流门禁不变量必须由机器校验

系统 SHALL 提供可重复执行的校验，确认安全底线：边界开关互斥、必须成对切换的边界组合兼容、禁止券商写、交易入口单活、调度单一负责人，以及回退方式已验证。校验 SHALL 覆盖否定路径（冲突配置、双活交易入口、无回退批次均必须判失败）。入口证据包、兼容性证明文件与绑定请求哈希的精确授权 SHALL NOT 作为切流前置。校验失败 SHALL 阻断切流操作。

#### Scenario: 校验捕获双活交易入口

- **WHEN** 校验发现旧交易入口与新交易入口同时处于可提交真实订单的状态
- **THEN** 校验 SHALL 失败并指出两个入口标识
- **AND** 该失败 SHALL 阻断该边界的切流操作

#### Scenario: 校验纳入架构守卫

- **WHEN** 新增绕过切流控制直接改变路由、直接提交真实订单或绕过边界开关写入的代码路径
- **THEN** 架构守卫 SHALL 判失败并指出该路径
- **AND** 该路径 SHALL NOT 通过人工豁免长期保留，豁免必须声明收敛阶段

### Requirement: 迁移完成后研究读路由必须固定为新路径并在发布时与读取前检查数据产品

系统 SHALL 在实际 CLI、Workflow 与 Dispatcher 读取入口按消费者（`domain_id + consumer_id + contract_version`）整体路由研究读取。研究消费者完成切换、迁移窗口关闭后，其读路由 SHALL 固定为新路径，系统 SHALL NOT 提供把研究消费者切回旧路径的操作入口，也 SHALL NOT 因健康检查失败自动改写路由。消费者读取的数据产品 SHALL 在两个时点检查：数据刷新任务完成后，以及 Dispatcher 实际执行该消费者的任务之前（可复用既有投影时不必检查）。

检查不合格时 SHALL 告警并阻断受影响任务，SHALL NOT 以单一总开关或「同一数据域其他消费者已通过」为由放行。精确 scope 路由行 SHALL NOT 成为日常运行前提。读取入口 SHALL 使用明确一致的业务 scope，SHALL NOT 将实体或时间范围替换为消费者产品列表；读取失败 SHALL 显式报错，SHALL NOT 吞为空结果。同一消费者状态未变化时 SHALL NOT 重复告警。

#### Scenario: 数据刷新后产品不合格

- **WHEN** 一次数据刷新完成后，某研究消费者读取的数据产品检查不合格
- **THEN** 系统 SHALL 告警并记录检查结果
- **AND** 该消费者的读路由 SHALL 保持不变

#### Scenario: 任务执行前产品不合格

- **WHEN** Dispatcher 要执行某消费者的任务，而该消费者读取的数据产品检查不合格
- **THEN** 该任务 SHALL 不执行并记为数据产品不合格的缺口
- **AND** 整次运行 SHALL 记为不完整，与它无关的任务 SHALL 继续执行

#### Scenario: 状态未变化时不重复告警

- **WHEN** 同一消费者连续多次检查结论相同
- **THEN** 系统 SHALL 只在状态由合格转不合格、或由不合格转合格时告警

#### Scenario: 同一数据域其他消费者已通过

- **WHEN** 某数据域的其他消费者均已通过，而本消费者检查未通过
- **THEN** 本消费者的任务 SHALL 被阻断
- **AND** SHALL NOT 以数据域整体通过为由放行

#### Scenario: 读取资格不得开启交易权限

- **WHEN** 某消费者的读路径已切换
- **THEN** 该切换 SHALL 仅影响其读路径
- **AND** SHALL NOT 被解释为任何交易、审批或下单权限

#### Scenario: 原生产品查询不能替代业务 scope

- **WHEN** 原生消费者 API 收到产品查询参数，却没有显式业务 scope 或绑定的当次入口上下文
- **THEN** 系统 SHALL 拒绝读取，而不是使用产品列表构造 scope
- **AND** 绑定后更换实体、消费者或历史 cutoff SHALL 被拒绝，读取失败 SHALL NOT 被吞为成功的空 payload

### Requirement: 数据产品检查失败时必须安全停止受影响范围而不得退回已关闭的旧路径

迁移窗口已关闭的研究消费者，数据产品检查失败或读取资格被撤销时，系统 SHALL 按 workflow/scope 登记的安全停止或恢复已验收版本策略处置，SHALL NOT 退回旧路径。停止 SHALL 禁止该范围继续读取/发布与依赖交易，保留历史和审计；恢复 SHALL 核验目标版本、有效证明、未退役及实际可读性。缺目标或核验失败 SHALL 保持停止。尚未切换的边界在其迁移窗口内 SHALL 仍以旧路径为回退。系统 SHALL NOT 要求每个 scope 一份带有效期的停止证明，也 SHALL NOT 要求修复旧业务；下游已组装快照 SHALL 重验失效依赖。

#### Scenario: 迁移窗口关闭后的检查失败
- **WHEN** 迁移窗口已关闭的研究消费者在任务执行前检查失败
- **THEN** 系统 SHALL 阻断该范围的任务并告警，保留历史与审计
- **AND** SHALL NOT 把该消费者切回旧路径

#### Scenario: 旧入口退役后的安全停止
- **WHEN** 旧入口已退役，某 scope 资格失效且已登记安全停止
- **THEN** 系统 SHALL 停止该范围并阻断其依赖交易、保留历史与审计
- **AND** 已实测上述行为 SHALL 满足该范围的恢复验收，不要求旧入口可用

#### Scenario: 恢复已验收的新版本
- **WHEN** 操作者选择已验收的新版本作为恢复目标
- **THEN** 系统 SHALL 核验该版本的 scope、有效证明与实际读取
- **AND** SHALL NOT 自动沿用当前版本的资格或恢复旧授权

#### Scenario: 已组装快照继续执行
- **WHEN** 已组装快照的必需依赖失效
- **THEN** 下游审批/提交 SHALL 重验并停止无效范围
- **AND** SHALL NOT 仅记日志继续，也不扩大为无关研究范围的全局阻断

#### Scenario: 尚未切换的边界
- **WHEN** 交易路径等尚未切换的边界在其迁移窗口内检查失败
- **THEN** 系统 SHALL 仍以旧路径为回退并告警

### Requirement: 被意外切回旧路径的研究消费者必须能经批准恢复到新路径

研究消费者的读路由 SHALL 只能朝新路径移动：系统 SHALL 提供一个运维入口，把停在旧路径上的研究消费者恢复到新路径，该入口 SHALL 要求用户批准原文、原因与通过的数据产品检查，SHALL 拒绝已在新路径的消费者与非研究消费者，并 SHALL 把操作写入只增不改的切换日志。系统 SHALL NOT 提供把研究消费者切回旧路径的入口。

#### Scenario: 恢复被切回旧路径的消费者

- **WHEN** 某研究消费者停在旧路径，操作者给出批准原文与原因，且其数据产品检查通过
- **THEN** 该消费者的读路由 SHALL 恢复为新路径，代次加一
- **AND** 日志 SHALL 记录操作者、批准原文、原因与检查结果

#### Scenario: 缺少批准或检查未通过

- **WHEN** 恢复请求缺少批准原文、原因或操作者，或数据产品检查未通过
- **THEN** 系统 SHALL 拒绝并保持原路由

#### Scenario: 已在新路径或非研究消费者

- **WHEN** 对已在新路径的消费者，或对 Chief、风控、交易员、书记员发起恢复
- **THEN** 系统 SHALL 拒绝
