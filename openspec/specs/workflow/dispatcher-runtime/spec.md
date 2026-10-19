# Dispatcher Runtime Specification

## Purpose

让手动、定时和事件触发的分析使用同一个依赖感知执行入口，明确记录每个任务的输入、结果和缺口，并在研究输入不完整时阻断组合决策。

## Requirements

### Requirement: Dispatcher 必须验证任务注册和运行范围

Dispatcher SHALL 只接受已登记任务，启动前 SHALL 拒绝未知任务、缺失依赖、循环依赖和未声明的跨分析师观点依赖。任务注册 SHALL 声明角色、scope、输出 schema、超时、重试、资源组和支持的触发模式。

#### Scenario: 注册表存在循环

- **WHEN** 两个任务互相声明为依赖
- **THEN** Dispatcher SHALL 在任何 Agent 启动前拒绝该注册表
- **AND** 错误 SHALL 列出形成循环的任务 ID

#### Scenario: 声明未授权依赖

- **WHEN** Sector 任务声明读取 Macro 投影
- **THEN** Dispatcher SHALL 拒绝该任务注册或运行
- **AND** 合法的跨分析师依赖 SHALL 只包括 Layer → Sector、Information → Fundamental

### Requirement: Dispatcher 必须按请求范围展开依赖

Dispatcher SHALL 支持单任务、依赖子流程和完整分析。单任务或子流程只展开其声明的上游；分析型请求 SHALL 默认在投影发布后终结。要求进入决策周期的请求 SHALL 在启动前固定按 scope 确定的必需分析类别；未明确要求完整决策输入的局部请求 SHALL NOT 进入 Chief。

#### Scenario: 手动请求 Sector

- **WHEN** 用户请求 AI 硬件 Sector 分析，且不存在可复用的 Layer 投影
- **THEN** Dispatcher SHALL 运行对应 Layer 后再运行 Sector
- **AND** SHALL NOT 因此运行 Macro、Information 或 Technical

#### Scenario: 局部请求附带决策标志

- **WHEN** 用户只请求 Macro，却设置进入决策周期
- **THEN** Dispatcher SHALL 拒绝该请求并列明缺少的必需分析类别
- **AND** SHALL NOT 用已有旧投影静默补成一个交易周期

### Requirement: 无依赖任务必须有界并发且失败隔离

Dispatcher SHALL 在资源组限制内并发运行已就绪任务，只在上游成功或可复用时启动其下游。一个任务失败或超时 SHALL 阻断其下游，SHALL NOT 取消无关分支。

#### Scenario: Information 运行失败

- **WHEN** 完整分析中的 Information 失败，而 Layer、Macro 和 Technical 已启动
- **THEN** Fundamental SHALL 标为被上游阻断
- **AND** 其余无依赖分支 SHALL 继续到终态

#### Scenario: 同一数据库写资源受限

- **WHEN** 多个已就绪任务属于同一受限写资源组
- **THEN** Dispatcher SHALL 遵守该组并发上限
- **AND** 等待资源的任务 SHALL 保留可观察状态，不得被误报为成功

### Requirement: 投影复用必须记录可验证的依据

复用判断 SHALL 同时检查角色、scope、payload schema、`as_of`/`valid_until`、必需输入投影、关键数据 vintage 和终态。每次复用或拒绝复用 SHALL 持久记录候选投影、判断时点和原因；没有可验证 vintage 的输入 SHALL 不得被假定为未变化。

#### Scenario: 依赖投影未过期但数据已修订

- **WHEN** Layer 投影的有效期未到，但其关键数据 vintage 发生变化
- **THEN** Dispatcher SHALL 重新运行 Layer 或将 Sector 标为输入不完整
- **AND** SHALL 记录失效原因为数据版本不匹配

#### Scenario: 依赖投影可以复用

- **WHEN** Information 投影的全部兼容性条件成立
- **THEN** Fundamental SHALL 接收该投影的精确 ID 和 hash
- **AND** 运行记录 SHALL 标记该依赖为复用，而非伪装为本轮 Agent 新产出

### Requirement: 运行和任务结果必须持久且可恢复

每次调度 SHALL 记录运行请求、固定任务计划、各任务 attempt、输入引用、开始和结束时间、投影引用、结构化失败原因及终态。重启后 SHALL 从已持久化状态恢复或安全重试未完成任务，SHALL NOT 重跑已成功且结果仍有效的任务。

#### Scenario: Agent 发布投影后进程崩溃

- **WHEN** Agent 已发布成功投影，但 Dispatcher 尚未记录任务终态时进程崩溃
- **THEN** 恢复过程 SHALL 按稳定任务身份找到既有投影并补齐运行结果
- **AND** SHALL NOT 生成第二个逻辑产出

### Requirement: Chief 门禁必须以固定必需类别复查完整性

请求进入决策周期时，Dispatcher SHALL 在 Chief 启动前对固定的必需类别重新核验成功状态、scope、schema、输入血缘、新鲜度和投影 hash。AI 硬件完整流程 SHALL 包含 Layer、Information、Sector、Fundamental、Macro、Technical 六类；Fundamental 例行或事件模式 SHALL 按请求语义选择其一，不得要求两者同时存在。任何缺口 SHALL 形成 `incomplete` 结果和可读缺口报告，SHALL NOT 创建 decision cycle。

#### Scenario: 等待过程中 Technical 过期

- **WHEN** 全部任务曾成功，但 Technical 投影在 Chief 门禁时已过期
- **THEN** Dispatcher SHALL 标记该运行 `incomplete` 并列明 Technical 失效
- **AND** SHALL NOT 调用 Chief、Risk 或 Trader

#### Scenario: 非 AI 硬件范围

- **WHEN** 完整流程的范围不支持 Layer Analyst
- **THEN** 必需类别 SHALL 来自该范围明确发布的决策需求清单
- **AND** 系统 SHALL NOT 伪造 Layer 输出或因任务未登记而静默缩减必需类别

### Requirement: 旧分析入口迁移必须保持单一调度所有者

每个已迁移的分析 workflow SHALL 由 Dispatcher 独占执行所有权；旧 CLI 和 scheduler 入口 SHALL 转发到相同运行契约，或在兼容窗口内明确保持旧所有者。影子运行 SHALL 禁止真实券商写入，并 SHALL 能对比新旧任务身份、输入与结果。

#### Scenario: 同一日度分析被新旧 scheduler 同时唤醒

- **WHEN** 一个 workflow ID 已切给 Dispatcher，但旧日度作业也被唤醒
- **THEN** 旧入口 SHALL 不再直接调用该 Agent
- **AND** 同一逻辑窗口 SHALL 只有一个执行所有者

### Requirement: Chief 完整性门禁必须同时满足必需类别与所选任务模式

完整决策请求 SHALL 在启动前固定必需分析类别、各类别所选任务及依赖、scope、Fundamental 模式与有效性条件。实际 Dispatcher、研究快照、run contract 和 Chief 开周期入口 SHALL 使用同一固定需求判定，要求必需类别齐全且所选任务依赖闭包成功或合法复用。选择 Event SHALL 满足 Event，选择 Routine SHALL 满足 Routine；另一模式 SHALL NOT 替代所选模式的缺失或失败，未选模式 SHALL NOT 被强制同时运行。任一 Fundamental 模式 SHALL NOT 替代其他必需类别；单任务/局部研究完成 SHALL NOT 被当作完整决策资格。

固定需求 SHALL 包含版本、profile/config 摘要、每个所选 task/scope/schema、依赖与实际投影 ID/hash 和运行结果，并随冻结研究快照持久化。跨进程重新解析、模型调用与实际 create_cycle 前 SHALL 校验固定需求完整性、源配置漂移、全部 scope 的有效性、input/vintage lineage 与已终结成功的 task attempt；不允许只读取类别代表产物而省略其他必需 scope。独立 Chief 入口 SHALL 明确选择 Fundamental 模式，未配置 Chief runner 的研究运行 SHALL NOT 宣称进入决策周期。

#### Scenario: 所选模式缺失且另一模式有效

- **WHEN** 请求选择 Event 而仅 Routine 有效，或选择 Routine 而仅 Event 有效
- **THEN** 系统 SHALL 报告所选模式缺口并阻断创建 decision cycle
- **AND** SHALL NOT 用另一模式的有效投影放行

#### Scenario: 所选模式满足但其他必需类别缺失

- **WHEN** 所选 Fundamental 任务与依赖成功或合法复用，但其他必需类别缺失、过期或无效
- **THEN** 系统 SHALL 阻断创建 decision cycle
- **AND** SHALL 报告具体类别及其有效性失败

#### Scenario: 未选模式没有运行

- **WHEN** 所选模式及其依赖、其他必需类别和固定有效性条件均满足，而未选 Fundamental 模式没有运行
- **THEN** 完整性门禁 SHALL NOT 因未选模式缺失而拒绝
- **AND** SHALL 继续独立执行风控、审批和交易门禁

### Requirement: 旧调度入口必须接入统一逻辑触发身份

仍可能触发生产执行的旧调度作业与旧事件入口 SHALL 映射到共同的逻辑触发身份，由 workflow 标识、scope 与计划时点，或事件标识与版本共同确定。旧入口在执行前 SHALL 读取共享的 owner 状态与其代次，并登记对该逻辑触发的认领。仅修改 owner 模式的配置 SHALL NOT 被视为旧入口已停止触发。配置变更的重载或受控重启方式 SHALL 明确登记。

实际新 cron/event、手动入口和 Dispatcher SHALL 使用同一权威认领与执行记录；仍可执行的旧入口 SHALL 受相同失权/发布控制，或提供可核验关停证明。结果发布点 SHALL 重验 owner/代次。控制表与 YAML 启用状态的生效关系 SHALL 明确，SHALL NOT 仅改变未被 scheduler 读取的控制状态便宣告调度切流。接线验收 SHALL 调用实际入口，保留独立预期触发集合及实际记录；空账本 SHALL 判无从核验，SHALL NOT 视为无双活。研究调度批次 SHALL 明确其实际消费者与依赖读 scope，SHALL NOT 以 consumers 为空绕过资格。

#### Scenario: 旧 worker 在移交后发布结果

- **WHEN** 已在运行的旧 worker 在 owner/代次改变后尝试发布同一逻辑触发结果
- **THEN** 实际发布点 SHALL 拒绝该发布并记录旧代次
- **AND** SHALL NOT 仅依赖认领表更新或人工确认旧进程退出

#### Scenario: 旧业务不可运行但退出安全可核验

- **WHEN** 旧业务失败或不完整，但其入口已关停/失权，迟到发布与提交被拒，新调度满足独立需求
- **THEN** 系统 SHALL 允许新调度验收通过
- **AND** SHALL NOT 要求旧分析工作成功

#### Scenario: 旧日度级联与新 schedule 对应同一工作

- **WHEN** 旧日度级联中的某项工作与新调度路径的某项 schedule 对应同一逻辑工作
- **THEN** 二者 SHALL 解析为同一逻辑触发身份
- **AND** 该身份在同一时刻 SHALL 至多有一个执行所有者

#### Scenario: 旧常驻进程未退出即开始新路径运行

- **WHEN** 旧常驻调度进程仍在运行而新路径已开始执行
- **THEN** 旧进程 SHALL 因代次或认领不符而不能发布同一结果
- **AND** 系统 SHALL 报告该冲突双方

#### Scenario: 手动调度与自动触发同一逻辑工作

- **WHEN** 同一逻辑工作经手动调度与自动触发同时到达
- **THEN** 二者 SHALL 归并为同一逻辑触发身份
- **AND** SHALL 至多执行一次

### Requirement: 调度路径切换必须先冻结新认领再清点

切换调度路径前，系统 SHALL 先冻结新认领，再清点未完成触发，并逐个声明处置方式：迁由新路径承接、由旧路径执行完毕，或作废并记录原因。存在处置方式未声明的未完成触发时 SHALL 拒绝切换。冻结 SHALL 先于清点，SHALL NOT 在清点后仍允许产生新认领。切换 SHALL NOT 使同一逻辑触发同时被两条路径认领。

#### Scenario: 清点后又产生新触发

- **WHEN** 清点已完成但冻结未生效，其间出现新的认领
- **THEN** 切换 SHALL 判定无效并重新冻结与清点
- **AND** SHALL NOT 继续执行移交

#### Scenario: 存在处置方式未声明的未完成触发

- **WHEN** 某未完成触发未声明其承接、执行完毕或作废的处置方式
- **THEN** 切换 SHALL 被拒绝并列出该触发标识
- **AND** SHALL NOT 以默认方式处置该触发

#### Scenario: 同一触发被两条路径同时认领

- **WHEN** 切换过程中同一逻辑触发同时被旧路径与新路径认领
- **THEN** 系统 SHALL 判定为所有权冲突并拒绝该认领
- **AND** SHALL 报告两个认领方与认领时间

#### Scenario: 切换时作废未完成触发

- **WHEN** 某未完成触发被声明为作废
- **THEN** 系统 SHALL 记录作废原因与操作者
- **AND** 该触发 SHALL NOT 在切换后被任一路径自动执行

#### Scenario: 承接任务需证明旧执行方已停止发布

- **WHEN** 某未完成触发被移交由新路径承接
- **THEN** 系统 SHALL 能证明旧执行方不能继续发布该触发的同一结果
- **AND** 无法证明时 SHALL 拒绝该承接

### Requirement: 调度安全恢复不得重复消费已执行触发

安全停止或恢复已验收版本 SHALL 保留权威触发记录、owner/generation 与结果引用；任何恢复目标 SHALL 按真实执行记录跳过已完成触发，不按时间窗口猜测。记录缺失或目标不可用 SHALL 保持停止。旧入口仅在明确选择且核验可用时作为目标，SHALL NOT 要求其业务运行成功作为新入口验收前置。

#### Scenario: 已验收版本再次遇到完成触发
- **WHEN** 新路径已完成某触发，恢复目标再次收到该触发
- **THEN** 目标 SHALL 跳过并记录原因
- **AND** SHALL NOT 第二次执行或发布

#### Scenario: 停止后执行记录不可读
- **WHEN** 恢复时无法读取真实执行记录
- **THEN** 系统 SHALL 保持停止并列明缺口
- **AND** SHALL NOT 为恢复服务重跑未知范围

### Requirement: 新调度覆盖必须依据独立预期触发集合

系统 SHALL 从已固定的新需求、schedule/event 配置及已发布事件版本独立导出预期触发集合，对照新路径实际认领、执行与发布记录核验覆盖、去重与遗漏。旧侧是否触发 SHALL NOT 定义预期集合或通过判据；缺少基准或来源不一致 SHALL 明确未验收，不静默采用执行记录并集。

#### Scenario: 新旧均遗漏
- **WHEN** 新调度缺少独立预期集合中的必需触发，且旧侧也没有执行
- **THEN** 新调度覆盖 SHALL 失败并列出遗漏
- **AND** SHALL NOT 因双方一致而通过

#### Scenario: 仅旧侧未执行
- **WHEN** 新路径满足独立预期集合，而旧侧无记录或失败
- **THEN** 系统 SHALL 依据新路径覆盖判定
- **AND** SHALL NOT 因旧侧缺失阻塞

#### Scenario: 预期来源不一致
- **WHEN** 预期集合的需求/配置/事件版本与本次范围不一致
- **THEN** 系统 SHALL 拒绝据此判通过并报告来源缺口

### Requirement: 调度切流不得被误作数据源重启

调度路径切换 SHALL 只改变触发与执行的归属，SHALL NOT 重启已注册的数据源采集。已在生产运行的采集路径 SHALL 保持其既有所有者与运行状态；系统 SHALL 拒绝把调度切换解释为对全部数据源的重启或重新登记。

#### Scenario: 调度切换触发数据源重新登记

- **WHEN** 调度路径切换的操作触发了某已注册数据源的重新登记或重启
- **THEN** 系统 SHALL 拒绝该操作并指出调度切换不拥有数据采集生命周期
- **AND** 该数据源 SHALL 保持其原所有者与运行状态

### Requirement: 实际 Workflow 与调度必须持久化独立运行时点

新 Workflow、cron/event 和手动调度 SHALL 为每次执行持久化独立 plan.as_of、查询窗口、实际 Event/version 与运行结果。每次执行/复用/发布 SHALL 核验当次输入、停止状态与 owner 代次；范围逃逸 SHALL 拒绝。查询窗口推进及符合已批准 Event 规则的版本更新 SHALL NOT 要求人工续批。logical trigger 身份 SHALL 继续由 workflow/scope/计划时点或实际 Event/version 确定，SHALL NOT 因 owner 代次变化重新执行已完成工作。

#### Scenario: 常驻调度跨日继续运行
- **WHEN** 调度负责人未变、两个独立 tick 位于不同日期且当次输入检查通过
- **THEN** 调度 SHALL 保留各自 plan.as_of/输入窗口，无需日常人工续批

#### Scenario: 同一逻辑触发重复到达
- **WHEN** 已完成触发再次到达，即使 owner 代次已变化
- **THEN** 系统 SHALL 依据原逻辑触发记录跳过，SHALL NOT 重复执行或发布
