# Proposal

## Why

Phase A–E 与目标数据流专项（`complete-target-dataflow`，44 项任务）均已归档，十个目标角色的契约、门禁与数据面已就位，但系统仍整体运行在旧入口上：`config/workflow/workflow_owners.yaml` 的 7 个 workflow 全部 `mode: legacy`，`config/workflow/phase_e_schedules.yaml` 的 5 个 schedule 全部 `enabled: false`，`config/data/structured.yaml` 的消费者虽已签发 platform 模式，但按 `docs/DATA_ARCHITECTURE.md:67` 的记录十个 consumer 均未取得生产读取资格。同时存在三个结构性缺口：读/调度/交易三条边界没有统一的切流开关与互斥校验（交易路径完全缺失，`src/ats/broker/ibkr.py:282` 无 paper/live 断言，「任何时刻只有一条可下真单的入口」目前无任何实现）；影子运行只有零散的双读比对（`data/cutover.py` 的非追加式 comparison、`agents/chief/assemble.py:650` 只比 presence 计数），没有覆盖调度遗漏、风控 verdict、审批链与交易归因的差异报告；旧实现退役登记中 `chief.legacy_table_direct_reads`、`sector_reviews`、`trades.client_order_id.legacy_derivation` 三条 pending 明确写着「切流属 Phase F」。

现在必须执行切流，因为继续叠加兼容层会同时稀释审计真相与写入边界，且 `docs/TARGET_WORKFLOW_DATAFLOW.md` §15.5 的上线门禁中第 3、4、5 条（影子运行无未审批下单、影子订单全链可追溯、回滚已演练且单交易入口）当前 0/3 达成。

本提案经外部审阅后修订。审阅指出六项 P1：进程内能力签发无法定义跨进程单活、授权缺少可查询生命周期与原子切换协议、计划中的 catalog 配置切换会使资格指纹自失效、影子证据缺少完整输入固定与通过判据、调度切换缺少旧入口接入统一触发身份的任务、资格失效时缺少「无安全旧路由」分支；另两项 P2 指出三条边界接线不具体与下单证明观测对象不足。经逐条核对代码后全部采纳，并额外补入审阅未覆盖的两点：`manifest_hash` 使契约清单本身的改动也会使全部证据漂移（范围比 `structured.yaml` 更广），以及由此推出的实施顺序约束（取证整体后置到代码冻结之后）。

## What Changes

- **新增切流控制平面**：为 projection read、analyst output、Dispatcher schedule、approval lifecycle、Clerk publication、live Trader 六条边界建立独立功能开关；每条边界须声明作用范围、实际调用点、模式语义与权威写入方，未声明接线者不得据其切流；不兼容的跨边界组合被拒绝。读路径与调度路径的路由判定逐条依据 `ats.data.assurance.qualification()` 的 exact-scope 资格，禁止用单一总开关覆盖未通过路径。
- **交易单活改为跨进程持久化代次模型**：活跃交易路由由持久化权威状态定义，含路由标识与**单调递增代次**；下单能力与执行授权绑定 `(route_id, generation, environment, account)`，每次提交重新校验权威状态，权威状态不可读即 fail closed。切换遵循**冻结 → 排空 → 换代次 → 开放**的原子协议；未终结订单按 submitted / partial / unknown / 迟到成交显式处置。只读对账承接不恢复旧路由的新增提交能力。
- **新增统一影子运行**：以**可重放输入包**固定已发布数据引用、运行时行情与账户与历史状态、逻辑评估时间、规则版本与模型提示配置；按**逐批适用矩阵**分级要求（纯研究读取批次只固定持久化引用，含决策与交易批次要求全量固定）；比较输入快照、分析输出、调度遗漏、风控 verdict/counterproposal、审批链与交易归因六类差异，未接受差异须显式处置，切流引用报告时校验签核、覆盖、差异接受与适用性四项。影子期从进程能力层禁止新路径 broker write，影子与真实订单账本隔离。
- **旧调度先接入统一逻辑触发身份**：把旧 job 与 event 映射到 workflow + scope + 计划时点或 event + version 的共同身份，执行前读取共享 owner 代次并登记认领，切换按「冻结新认领 → 清点 → 移交」执行。仅改配置不足以证明旧进程停止触发。
- **安全回退分支**：资格未通过时先判定回退是否安全（回退证明有效、目标未退役、目标实际可用），任一不满足则停止受影响范围并记录 blocked / unavailable，阻断依赖该输入的交易，而非退回墓碑已退役标识或已知故障路径。
- **取证基线保护与顺序约束**：运行时切流只使用发布覆盖层与独立控制状态，不修改受指纹约束的代码、配置与消费者契约清单；确需修改时在最终状态上重新取证并重算全部受影响消费者资格。**取证整体后置到代码冻结之后**。
- **下单禁止的验收以提交审计为主证据**：新路径的券商提交调用与拒绝审计、能力校验结果、隔离演练的模拟接收记录为主证据；真实成交账本未污染为独立验收，二者不互相替代。
- **前置接收与实际接入核验（F.0）**：建立版本化前置接收矩阵；按 exact-scope 校验并追加式登记已有资格证据；经**隔离接入验收入口**核验十个角色的实际接入（该入口在无生产资格时可用，但运行于进程禁写与隔离账本之下）；对实际缺口形成逐消费者处置与逐批切流 dry-run 清单。
- **退役清零与回滚演练**：按 11.8 清零旧实现消费者、数据对账、回滚窗口与墓碑登记检查；按 11.9 独立演练 read/schedule/trade 三条边界的回滚。
- **不重建已验收实现**：复用 `ats.data.assurance` 追加式证据 API、`config/data/target_dataflow_coverage.yaml` 消费者契约、`rollout_modes` 四级解析、`workflow/ownership.py` owner 模式、`execution/authorization.py` 授权与 `legacy_retirement` 三段式退出机制；十角色未取得资格前不得为「让切换可执行」而降低证据标准。
- **新增 pytest 回归保护**：`ats.data.assurance` 当前仅有脚本验证、`tests/` 零覆盖，本 change 补齐其回归测试，并把 Phase F 的门禁不变量纳入架构守卫。
- **收尾状态分列**：分别列示「门禁实现验收完成」「生产读/调度切流完成」「实盘切流完成」，未获授权或未就绪的实际切换任务保持未完成并向总 change 回写。

## Capabilities

### New Capabilities
- `workflow/cutover-control`: 切流控制平面——六条边界的独立功能开关与接线声明、跨边界兼容矩阵、启动 fail-closed 校验、逐路径 exact-scope 资格门控路由、安全回退判定、取证基线保护、逐批切流 runbook 的执行与回退状态机。
- `workflow/shadow-reconciliation`: 统一影子运行与差异归因——可重放输入包与逐批适用矩阵、六类差异报告与未接受差异处置、报告适用性校验、broker write 进程能力禁令与提交审计、影子账本隔离。
- `execution/live-route-switch`: 交易路径切换——跨进程单活与单调代次、账户环境匹配、冻结到开放的原子切换协议、未终结订单显式处置、外部实盘授权门、切换与回滚演练。

### Modified Capabilities
- `data/target-dataflow-assurance`: 增加 Phase F 前置接收要求——版本化接收矩阵、已有资格证据的受控追加登记、十个角色实际接入核验、逐消费者处置、逐批 dry-run 报告，以及取证基线不得被切流动摇与隔离接入验收入口（现有 7 条需求只规定资格如何签发，未规定谁接收、如何复核后使用）。
- `workflow/dispatcher-runtime`: 增加旧调度入口接入统一逻辑触发身份、冻结新认领后清点、承接时的旧执行方停止发布证明、调度遗漏比较须含独立预期触发集合。
- `workflow/legacy-retirement`: 增加切流窗口的清零判据与回退目标可用性——消费者清零证明、数据对账、回滚窗口、墓碑一致性，以及已退役标识不得被指定为回退目标。
- `execution/authorization-gate`: 增加授权生命周期与路由代次绑定——可查询的未终结状态、切换前先冻结再核验、代次变更后旧授权失效、影子与纸面授权不可用于真实提交。

## Impact

- **新增代码**：`workflow/` 下的切流控制平面（含边界开关与接线声明、资格门控路由、安全回退判定）、影子输入包与比较矩阵模块（旧调度适配层），`execution/` 下的 route 代次仲裁、下单能力校验与授权生命周期。
- **改动代码**：`config/workflow/workflow_owners.yaml` 与 `phase_e_schedules.yaml`（owner 模式与 schedule 启用状态随逐批切换变更）、`config/workflow/legacy_retirement.yaml`（三条 Phase F 相关 pending 的状态与判据）、`src/ats/runtime/scheduler.py`（旧入口接入统一触发身份与认领）、`src/ats/execution/authorization.py`（授权生命周期与代次绑定）、`src/ats/broker/ibkr.py`（提交前能力与账户校验）、`src/ats/graph/chief.py` 与 `src/ats/trader/execute.py`（交易入口接仲裁）、`src/ats/runtime/cli.py`（切流与差异报告入口）。
- **新增/改动配置**：新增切流开关与 runbook 配置文件；受指纹约束的 `config/data/structured.yaml`、`config/data/unstructured.yaml`、`config/data/target_dataflow_coverage.yaml` 与 `config/risk.yaml` **不在运行时切流的改动面内**。
- **数据与账本**：影子订单与真实订单账本隔离会影响 `trades` 表写入路径；资格证据继续写入 `dataflow_assurance_events`（追加式，不修改既有行）；新增持久化活跃 route 状态。
- **测试**：`ats.data.assurance` 补 pytest 回归；新增切流门禁、影子输入包与比较矩阵、路由代次与授权生命周期、旧调度适配、边界接线、安全回退、回滚演练的测试；Phase F 相关架构守卫并入 `workflow/architecture-guards`。
- **实施顺序约束**：进程级 broker write 禁令与隔离环境必须先于接入核验；取证必须后置到代码冻结之后；切流实现不得落在受指纹约束路径内（必要触及项须在取证前完成）。
- **外部依赖与授权**：真实部署路由变更需明确部署授权，live Trader 真实切换需明确实盘授权；两者均不由本 change 的完成状态自动获得。
- **不在范围内**：不重建数据平台、不重新采集、不改动 Phase A–E 已验收的契约与门禁标准、不在本 change 内执行真实 live route 切换。
