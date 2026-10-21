## ADDED Requirements

### Requirement: 研究工作流必须只经 Dispatcher 执行

每个已迁移的分析 workflow SHALL 由 Dispatcher 独占执行所有权。迁移完成后，研究分析的旧手动入口与旧调度入口 SHALL 退役，手动运行 SHALL 经 Dispatcher 的手动触发入口，与定时、事件触发使用同一运行契约、认领与执行记录。影子运行 SHALL 禁止真实券商写入。工作流被停用时 SHALL NOT 由任何其他入口执行该工作流。

#### Scenario: 同一日度分析被手动与定时同时触发

- **WHEN** 一个研究 workflow 的同一逻辑窗口同时收到手动触发与定时触发
- **THEN** 二者 SHALL 经同一 Dispatcher 认领
- **AND** 同一逻辑窗口 SHALL 只执行一次

#### Scenario: 调用已退役的旧分析入口

- **WHEN** 操作者调用已退役的旧研究分析命令
- **THEN** 系统 SHALL 报告该入口已退役并给出 Dispatcher 手动运行方式
- **AND** SHALL NOT 直接调用对应 Agent

#### Scenario: 已停用的工作流

- **WHEN** 某研究工作流处于停用状态
- **THEN** 定时、事件与手动触发 SHALL 都不执行它，并记录被跳过的原因

### Requirement: 工作流停用与恢复必须先冻结新认领再清点

停用或恢复一个研究工作流前，系统 SHALL 先冻结新认领，再清点未完成触发，并逐个声明处置方式：恢复后承接、执行完毕，或作废并记录原因。存在处置方式未声明的未完成触发时 SHALL 拒绝停用或恢复。冻结 SHALL 先于清点，SHALL NOT 在清点后仍允许产生新认领。停用与恢复 SHALL NOT 使同一逻辑触发被执行两次。

#### Scenario: 清点后又产生新触发

- **WHEN** 清点已完成但冻结未生效，其间出现新的认领
- **THEN** 停用或恢复 SHALL 判定无效并重新冻结与清点
- **AND** SHALL NOT 继续执行

#### Scenario: 存在处置方式未声明的未完成触发

- **WHEN** 某未完成触发未声明其承接、执行完毕或作废的处置方式
- **THEN** 停用或恢复 SHALL 被拒绝并列出该触发标识
- **AND** SHALL NOT 以默认方式处置该触发

#### Scenario: 有正在运行的认领

- **WHEN** 停用时该工作流存在 running 状态的认领
- **THEN** 系统 SHALL 拒绝停用并报告该认领
- **AND** SHALL NOT 强行作废它

#### Scenario: 停用时作废未完成触发

- **WHEN** 某未完成触发被声明为作废
- **THEN** 系统 SHALL 记录作废原因与操作者
- **AND** 该触发 SHALL NOT 在恢复后被自动执行

### Requirement: 调度覆盖必须依据独立预期触发集合

系统 SHALL 从已固定的需求、schedule/event 配置及已发布事件版本独立导出预期触发集合，对照实际认领、执行与发布记录核验覆盖、去重与遗漏。缺少基准或来源不一致 SHALL 明确未验收，不静默采用执行记录并集。

#### Scenario: 调度遗漏必需触发
- **WHEN** 实际执行记录缺少独立预期集合中的必需触发
- **THEN** 覆盖核验 SHALL 失败并列出遗漏
- **AND** SHALL NOT 以执行记录本身作为预期

#### Scenario: 停用期间的触发
- **WHEN** 某工作流在停用期间有预期触发
- **THEN** 核验 SHALL 把这些触发记为因停用而跳过，SHALL NOT 记为遗漏

#### Scenario: 预期来源不一致
- **WHEN** 预期集合的需求/配置/事件版本与本次范围不一致
- **THEN** 系统 SHALL 拒绝据此判通过并报告来源缺口

### Requirement: 研究工作流的可读报告必须由 Dispatcher 路径生成

旧手动入口退役之前，Dispatcher 路径 SHALL 承担这些入口原先生成的可读报告：技术面报告、行业报告（含层级报告与可视化页面）、每周产业链证据周报；宏观周报沿用既有行为。报告 SHALL 是已发布投影与存档的派生物，生成失败 SHALL NOT 改变分析运行的状态，也 SHALL NOT 阻断下游任务，失败 SHALL 记录在运行详情中。报告文件 SHALL 以新版本写入、SHALL NOT 覆盖既有文件；复用既有投影而没有新产出的运行 SHALL NOT 重复生成同一份报告。

#### Scenario: 技术面定时运行后

- **WHEN** 技术面工作流一次运行结束且产生了新的读数投影
- **THEN** Dispatcher 路径 SHALL 生成一份汇总所有标的的技术面报告
- **AND** 不会因逐标的任务而每个标的各写一份

#### Scenario: 周度行业评审后

- **WHEN** 行业评审工作流完成并发布了新的配置结论
- **THEN** Dispatcher 路径 SHALL 生成行业报告、层级报告与可视化页面，并在其后生成本周产业链证据周报
- **AND** 报告内容 SHALL 与该次已发布的投影一致

#### Scenario: 报告生成失败

- **WHEN** 报告目录未配置、不可写或渲染失败
- **THEN** 分析运行 SHALL 仍按其任务结果记为完成或不完整
- **AND** 运行详情 SHALL 记录报告失败的原因

#### Scenario: 复用既有投影

- **WHEN** 一次运行复用了既有投影而没有新的发布
- **THEN** SHALL NOT 重复生成同一份报告

## REMOVED Requirements

### Requirement: 旧分析入口迁移必须保持单一调度所有者

**Reason**: 本条约束迁移期新旧入口并存时的所有权。旧调度器已删除，研究分析的旧手动入口随本 change 退役。

**Migration**: 由「研究工作流必须只经 Dispatcher 执行」取代。

### Requirement: 调度路径切换必须先冻结新认领再清点

**Reason**: 本条约束新旧两条调度路径之间的移交。旧路径已不存在，负责人切换收窄为工作流的停用与恢复。

**Migration**: 由「工作流停用与恢复必须先冻结新认领再清点」取代，冻结先于清点、未完成触发逐个处置、不得重复执行的约束原样保留。

### Requirement: 新调度覆盖必须依据独立预期触发集合

**Reason**: 本条的场景以旧侧执行为对照（新旧均遗漏、仅旧侧未执行），旧侧已不存在。

**Migration**: 由「调度覆盖必须依据独立预期触发集合」取代，预期集合独立导出、不以执行记录并集为准的约束原样保留。

### Requirement: 旧调度入口必须接入统一逻辑触发身份

**Reason**: 旧的串行调度器及其 launchd 服务已于 2026-10-20 删除（退役登记 `scheduler.hardcoded_serial_run` 已 retired），研究分析的旧手动入口随本 change 退役；不再存在需要与 Dispatcher 共享触发身份、去重或失权的旧调度入口。为此建立的新旧调度去重工具（旧任务映射、旧 worker 失权与移交证明）一并退役。

**Migration**: 所有研究触发（定时、事件、手动）只经 Dispatcher 与 Trigger Ledger，逻辑触发身份由「运行和任务结果必须持久且可恢复」「实际 Workflow 与调度必须持久化独立运行时点」两条要求约束。交易路径的定时任务（主理人每日决策、Clerk 每日对账）在交易路径切换时直接由 Dispatcher 排程，不经旧入口。
