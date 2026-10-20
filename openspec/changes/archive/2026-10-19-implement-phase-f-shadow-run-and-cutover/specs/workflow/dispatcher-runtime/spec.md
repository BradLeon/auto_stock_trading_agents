# Spec Delta

## Purpose

本文件为 `workflow/dispatcher-runtime` 的增补，只新增旧调度入口接入统一触发身份、以及调度路径切流时的触发所有权与回滚要求。现有 7 条需求规定了注册验证、依赖展开、并发隔离、投影复用、运行持久化、Chief 完整性门禁与单一调度所有者，本文件规定仍可执行的旧入口如何失权、切换瞬间未完成触发如何处置，以及新调度如何独立核验覆盖和安全恢复；不要求旧业务完整运行。

## 2026-10-15 精简裁决

用户裁决（2026-10-15）：切流以 `docs/TARGET_WORKFLOW_DATAFLOW.md`（§1、§12.4、§14.6、§15.5、§16）及用户历次裁决为准；本 change 中超出 TARGET 的派生机制降为可选。G1 以 TARGET 为准；G2 研究读取按消费者整体切换，不再按「标的×时点」；G3 迁移窗口内出问题退回旧路径（TARGET §14.6「保留旧入口作可控回滚」），取代「停止即锁读」作为默认；G4 本 change 以追加裁决方式修订，已完成任务与证据保留为历史。安全底线不变：新路径与隔离运行禁止券商写入；任何时刻只有一个实盘入口；真实交易绑定同一 revision 的风控与 Boss 批准；读/调度/交易分别切换；审批与账本发布成对切换；调度单一负责人；旧实现墓碑退役；回滚已演练；测试全绿。依据：TARGET §16 第 12 条「本次设计和文档实施不使用 OpenSpec」；TARGET 中唯一的 exact-scope 资格表述为 2026-10-09 Phase F 期间补入；按此前机制推进切流出现结构性死循环（`docs/archive/PHASE_F_GUARD_REVIEW.md`、`docs/archive/PHASE_F_8_6_REGISTRATION_DRAFT.md` 第七至九节）。观察期 3 个交易日；自动切回的告警走飞书机器人。

本 spec 各要求处置如下（保留原文；「被取代」者不再作为切流前置，「修改」者以本节为准）：

| 要求 | 处置 | 修订要点 |
|---|---|---|
| Chief 完整性门禁、统一逻辑触发身份、先冻结新认领再清点、不重复消费已执行触发、独立预期触发集合、不误作数据源重启 | 保留 | 调度单一负责人与幂等是 TARGET §8.3、§15.4 要求 |
| 实际 Workflow 与调度必须绑定持久许可和独立运行时点 | 修改 | 保留「独立运行时点记录」；「绑定持久许可」随 cutover-control 被取代 |

> 2026-10-19 归档前整理：上表处置「被取代」的要求已从下方 `ADDED Requirements` 中移除（原文保留在本 change 的 git 历史与 `docs/archive/PHASE_F_OPENSPEC_REVISION_DRAFT.md`），处置「修改」的要求已改写为裁决后的写法，主规格因此只承载仍然有效的要求。

## ADDED Requirements

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
