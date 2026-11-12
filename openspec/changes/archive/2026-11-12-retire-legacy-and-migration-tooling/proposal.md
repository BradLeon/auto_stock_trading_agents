# Proposal

## Why

A–F 迁移已经完成，但旧实现和迁移期留下的东西没有一起退出：

- 退役登记处的 27 条里只有 4 条有证据证明已经退出，8 条登记为已退役却无法证明没人在用，15 条仍是 pending。
- 迁移期为切换而写的工具和机制从来没登记过：约 21 个只被命令行和测试使用的模块（一万多行代码）、每次读取都要经过的新旧路由机制、只为"退回旧路径"保留的旧手动入口，以及 `consumer-cutover` 这类按迁移阶段起的名字。

结果是：
- 文档和命令里处处是已经不成立的"新路径 / 旧路径"概念；
- 全量测试里 41 个红测试属于一套已被取代的切换机制；
- 每改一处都要绕开一层不再需要的兼容。

现在要做这件事，是因为研究侧已经在新路径上稳定运行，旧调度器也已删除（faeac82），继续拖下去只会让两套概念长期并存。

退役本身不可逆，所以本 change 先把**要退什么、凭什么能退、先退哪个**定清楚：
- **前置门：** 由你对新路径的全部 Dataflow 和 Workflow 做人工核验，核验通过才动手；
- **依赖：** 交易路径相关的条目要等交易路径切换之后。

## What Changes

- **建立退役前置门（G0）：** 新路径全部 Dataflow（44 个数据刷新任务及其数据产品）与 Workflow（7 个研究工作流、日历事件路径、数据产品健康检查与告警）的人工核验清单。核验结果逐项落档，通过之前不执行任何删除。
- **完整清点并登记待退项：** 把登记过的 27 条和未登记的迁移期工具、读路由机制、旧手动入口、命名残留，全部纳入退役登记。每条写明替代实现、消费方、退出判据、依赖的前序条目与所属门（G0 或"交易路径切换"）。同时修正登记里的错误：
  - 8 条"登记已退役但无法证明"的条目，补角色映射后重扫；
  - `pead_dossier` 其实是基本面自有记录，从待退项中移出；
  - `macro_reviews`、`technical_reviews` 在用但没有退役条目，补登记。
- **分批退役研究侧条目（G0 之后执行）：**
  - R1 命名与入口残留：`consumer-cutover` → 数据健康命令；研究分析师的旧手动入口；"负责人退回 legacy"模式改为停用 / 恢复工作流。
  - R2 研究侧切换期工具：逐批、逐 scope 切流，接入核验，新旧调度去重等。（2026-10-27 复核后整组转入 R5，见 design.md D4）
  - R3 研究侧已登记条目。
  - R4 只被旧入口用到的旧分析代码。
  - 每批退役前做消费方清零验证，退役后复验运行与测试基线。
- **登记但不在本 change 执行：** 交易路径相关的 11 条已登记条目，以及与 Chief 共用的读路由 / 资格机制和交易侧切换工具（R5）。它们依赖交易路径切换，在本 change 里只登记退出判据与依赖。
- **规格同步：**
  - cutover-control 的读路由要求改为"迁移完成后固定新路径，数据产品在发布时与读取前检查"。这一行为已在 29c6b4d 先行实现，规格在本 change 补齐。
  - 删除随切换期工具退役而失效的要求：批次记录、前置接收矩阵、逐批 eligible 报告、旧调度入口接入统一触发身份。
  - dispatcher-runtime 去掉新旧双轨表述。
  - legacy-retirement 增加 G0 前置门、依赖顺序、批后复验与"登记已退役须可证明"。
- **BREAKING（运维命令）：** `ats consumer-cutover`、`ats batch`、`ats read`、`ats intake`，以及研究分析师的旧手动命令将被删除或改名，具体以 design 的清单为准。

## Capabilities

### New Capabilities

无。退役前置门、依赖顺序与批后复验属于既有的 `workflow/legacy-retirement` 能力。

### Modified Capabilities

- `workflow/legacy-retirement`：
  - 登记范围扩展到迁移期工具与机制，条目须声明依赖与所属门；
  - 新增退役前置门（新路径人工核验）、按依赖顺序执行、批后复验，以及"登记为已退役须能证明清零"；
  - "已演练切回旧路径"的要求按迁移完成后的现实改写。
- `workflow/cutover-control`：
  - 研究读路由在迁移完成后固定为新路径；数据产品在发布时与读取前检查，不合格只告警并阻断受影响任务，不再退回旧路径；
  - 删除"切流执行必须留下可回退的批次记录"。
- `workflow/dispatcher-runtime`：
  - 研究工作流只经 Dispatcher 执行，旧手动入口退役；
  - 删除"旧调度入口必须接入统一逻辑触发身份"；
  - 负责人切换改为工作流停用 / 恢复，保留"先冻结再清点"；
  - 预期触发覆盖去掉旧侧对照。
- `data/target-dataflow-assurance`：删除"Phase F 前置接收必须以版本化矩阵进行"与"前置门禁必须交付逐批 eligible 与 ineligible 报告"。这两条服务于已被 10-15 裁决取代的逐批切流。

## Impact

- **代码：**
  - `src/ats/workflow/` 下的切换期模块（`batch_manifest`、`read_cutover`、`read_deployment`、`schedule_cutover`、`cutover_acceptance`、`intake*`、`scoped_intake`、`dispatch_claims` 等）；
  - `src/ats/runtime/cli.py` 的 `consumer-cutover` / `batch` / `read` / `intake` 命令组与旧分析入口；
  - `workflow/ownership.py` 的 legacy 负责人模式；
  - R4 范围内只被旧入口引用的旧分析模块（以 R0 清点为准）。
- **配置：** `config/workflow/legacy_retirement.yaml`（新增条目、依赖与门字段）、`freeze_surface.yaml` 与 `target_dataflow_coverage.yaml` 的路径清单、`workflow_owners.yaml`。
- **测试：** 删除只测被退役机制的测试，其中含 10-19 登记的 41 个旧批次 / 读取切换失败测试；同步 `TEST_BASELINE.md`。
- **文档：** `OPERATIONS.md`、`ARCHITECTURE.md`、`PHASE_F_RETIREMENT_RUNBOOK.md`、`README.md`。
- **数据：** 不删除任何历史表、账本与审计记录。旧表只停止读写入口，物理清除走两段式且默认只读干跑。
- **不受影响：** 交易路径（Chief、风控、Boss 审批、Trader、Clerk）与实盘锁定；与 Chief 共用的读路由 / 资格机制在 R5 之前保持原样。
