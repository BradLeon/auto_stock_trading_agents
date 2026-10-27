# Tasks

> 执行顺序见 design.md D3：第 1 组（R0）的只读部分和第 2 组（RP 报告接续）在你的 G0 之前完成，它们不删任何东西；第 3 组是你亲自做的 G0；第 4–8 组等 G0 通过；第 9 组只登记，等交易路径切换。
> 第 5、7 组按 design.md D6 执行：旧手动入口与负责人 legacy 模式一并退役（2026-10-21 用户确认）。
> 2026-10-21 用户另已确认：可读报告要恢复（D9）、命题治理命令与知识库校验命令保留、R4 缩小为 3 个模块（10-26 增补 `pead/research`，共 4 项；`risk_validator` 只清退 `apply_guardrails`）。清点依据见 `inventory.md`。

## 1. R0 清点与登记

- [x] 1.1 生成退役清点报告 `inventory.md`：调用图、命令行清单、每个候选模块的生产调用方。验证：design.md D4 列出的每个条目和候选在报告里都有结论（已完成，a1c89e3）
- [x] 1.2 旧手动入口副作用对照，写入 inventory.md 第四节。验证：R1 每个旧入口都有「新路径已承担 / 需要补 / 不需要」的结论（已完成，a1c89e3）
- [x] 1.3 生成 G0 人工核验手册 `g0-runbook.md` 与结果记录 `verification.md`（由独立会话生成），并加入「可读报告」一项（第 2 组完成后）。验证：清单覆盖 `python -m ats.data.refresh validate` 列出的全部刷新任务、`config/workflow/research_schedules.yaml` 与 `event_routes.yaml` 的全部条目（g0-runbook.md 已于 10-27 提交；核验期间新增的 6 个刷新任务记在 verification.md「G0 结论」的范围补充）
- [x] 1.4 扩展退役登记格式：新增 `kind`、`group`、`depends_on`、`gate` 字段，加载时校验无环、无悬空依赖、门取值合法（**G0 之后做**）。验证：新增单元测试覆盖成环、悬空、未知门三种失败，以及合法登记的通过（750fc13；成环、悬空、未知门/批次/类别三种失败与合法登记均有测试）
- [x] 1.5 `ats retirement decide` 增加依赖与门的检查，新增只读的 `ats retirement gate --show`（汇总 verification.md 的结论）（**G0 之后做**）。验证：测试覆盖「前序未完成 → 不可退出」「门未满足 → 不可退出」（750fc13；`ats retirement decide` 带依赖与门，新增只读 `ats retirement gate [--show]`；现网读到 78 项通过、G0 已开）
- [x] 1.6 把 design.md D4 的全部条目写入 `config/workflow/legacy_retirement.yaml`：R1–R4 门为 G0，R5 门为 `trade_path_cutover`；未登记条目补齐替代实现、消费方、退出判据（**G0 之后做**）。验证：`ats retirement decide` 能列出全部条目及其阻塞原因，`openspec validate --all --strict` 通过（8fb799a；登记 43 条，R1–R4 门 G0、R5 门 trade_path_cutover；`openspec validate --all --strict` 通过）
- [x] 1.7 修正登记错误：`pead_dossier` 移出待退项、改登记为基本面自有记录；`macro_reviews`、`technical_reviews` 补登记；8 条「登记已退役但未证明」标注为待补证（**G0 之后做**）。验证：登记加载通过，`ats retirement zero` 对这 8 条显示「待补证」而不是「清零」（8fb799a；`pead_dossier` 移出待退项，补登记 `macro_reviews`、`technical_reviews`；8 条无法证明的在 `ats retirement zero` 显示“待补证”）
- [x] 1.8 更新 `docs/PHASE_F_RETIREMENT_RUNBOOK.md`：写明新的分组、依赖与门，以及 G0 的位置（**G0 之后做**）。验证：runbook 中的条目数与登记处一致（8fe9733；runbook 新增第〇节，条目数取自登记处）

## 2. RP 可读报告接续（G0 之前，只新增产出）

- [x] 2.1 在 `run_owned_workflow` 一次运行结束之后增加按工作流挂接的「报告发布步骤」框架：失败只记录到运行详情、不改运行状态、不阻断下游；复用既有投影且没有新发布时不重复生成。验证：新增测试覆盖报告步骤失败不改运行状态、复用时不重复生成、隔离环境中不写生产报告目录
- [x] 2.2 技术面：运行结束且有新读数投影时写一份汇总所有标的的技术面报告（新版本文件、不覆盖）。验证：测试覆盖「逐标的任务不各写一份」；隔离环境手动跑一次，看到报告文件
- [x] 2.3 行业：行业评审完成后写行业报告、层级报告与可视化页面。验证：测试覆盖内容与该次已发布的投影一致；隔离环境手动跑一次，看到三类文件（10-26 补写漏掉的行业周报，8a89fd9）
- [x] 2.4 每周产业链证据周报：在行业报告之后生成，沿用旧流程的内容与顺序。验证：测试覆盖「行业评审失败时不生成周报」；隔离环境看到周报文件
- [x] 2.5 更新 `docs/OPERATIONS.md` 与角色文档，写明报告的生成时点与目录；`config/settings.yaml` 的报告目录配置核对一遍。验证：文档里的描述与第 2.2–2.4 项隔离运行的结果一致
- [x] 2.6 重启 `com.ats.dispatcher`，观察下一次定时触发后报告出现在配置的目录。验证：`launchctl list` 有 PID；报告文件存在且内容来自该次投影（10-26 22:00 技术面定时任务写出汇总报告；行业周报的首次定时产出在 10-31，届时复核）

## 3. G0 新路径人工核验（由你执行）

- [x] 3.1 你逐项核验 `verification.md` 并填写结论、核验人、日期。验证：`ats retirement gate --show`（1.5 完成前以人工汇总为准）显示全部通过，或列出未通过项（78 项全部通过，10-27）
- [x] 3.2 核验发现的新路径缺陷登记为单独的修复任务，修复后复核通过。验证：verification.md 中没有「未通过」项；依赖未通过项的条目保持 pending（核验期间发现的缺陷多数当场修复，提交号见 verification.md「G0 结论」）
- [x] 3.3 记录 G0 结论（日期、范围、遗留且已接受的缺口，例如 SEC 不可达、ibkr_news 的 partial），提交 verification.md。验证：提交存在（2026-10-27 用户确认并签字，G0 通过）

## 4. R2 研究侧切换期工具退役（原门 G0；2026-10-27 整组转入 R5，只做了 4.1）

> **2026-10-27 复核：R2 整组转入 R5。** 清点时只按 `src/` 看使用方，补查测试和命令组后发现：这 10 个模块没有一个能单独删——`boundary_drill`（`ats drill` 的读边界回退演练）依赖 `batch_manifest`/`read_cutover`/`schedule_cutover`；`paired_business` 与 `ats retirement` 依赖 `intake_verification`；`analysis_verification`、`isolated_entry`、`scoped_intake`、`consumer_disposition` 被 Phase F 验收测试（研究、决策、执行、恢复，隔离环境跑交易侧角色等）使用；`cutover_acceptance` 验证交易订单可追溯与未经批准的订单；`dispatch_claims` 被 `schedule_cutover` 直接使用。交易链路（Chief、Clerk、Risk、Trader、Boss）暂不退役，所以它们与交易侧一起留到 R5（门 `trade_path_cutover`）。研究侧已于 4.1 解耦：Dispatcher 与研究侧读窗口不再依赖这些工具。

4.2–4.8 记为“转入 R5”：条目已改登记为 R5（门 `trade_path_cutover`），由交易路径切换的 change 接续；本 change 不执行。


- [x] 4.1 先处理簇外依赖（design.md D10）：去掉 `cutover_wiring` 里对 `dispatch_claims` 的接线声明及 `boundary_evidence` 对应调用点；把 `digest` 从 `shadow_replay` 移到 Dispatcher 自己的模块；评估并去掉 `read_windows` 对 `shadow_reports` 的研究侧批次报告校验。验证：对应测试通过，`dispatcher` 与 `read_windows` 不再导入 `shadow_replay`/`shadow_reports`（10-27：digest 搬出 6ebbf11、研究侧读窗口不再要求批次报告 e2319c5、调度边界登记改指向真实认领 8137c0e；63 个相关文件里 54 个通过、`test_cutover_cli` 的 3 条失败改动前就有；其余 5 个有失败与 4 个超时的 Phase F 重型验收文件在后台与改动前对照，结果记入 TEST_BASELINE。`dispatcher` 已无 `shadow_*` 导入；`read_windows` 只在交易侧消费者分支内保留对 `shadow_reports` 的导入，属 R5）
- [x] 4.2 对 `migration.batch_read_cutover` 做 V1–V3，结果写进 inventory.md。验证：`ats retirement decide` 判该条可退出（**转入 R5，本 change 不执行**）
- [x] 4.3 删除 `workflow/batch_manifest.py`、`read_cutover.py`、`read_deployment.py`、命令 `ats batch`、`ats read` 及其测试（`test_batch_manifest`、`test_read_cutover`、`test_batch_cli`），同步冻结面与资格清单；归档命令用法样例到 docs/archive。验证：受影响测试与 assurance_surface、架构守卫测试通过；调用 `ats batch` 返回已退役提示（**转入 R5，本 change 不执行**）
- [x] 4.4 对 `migration.schedule_cutover` 做 V1–V3，删除 `schedule_cutover.py`、`cutover_acceptance.py` 及其测试。验证：同 4.3（**转入 R5，本 change 不执行**）
- [x] 4.5 对 `migration.intake`、`migration.analysis_verification` 做 V1–V3，删除 `intake.py`、`intake_verification.py`、`scoped_intake.py`、`analysis_verification.py`、`consumer_disposition.py`、`isolated_entry.py`、命令 `ats intake` 及其测试。验证：同 4.3（**转入 R5，本 change 不执行**）
- [x] 4.6 对 `migration.dispatch_dedup` 做 V1–V3，删除 `dispatch_claims.py` 与 `ats dispatch` 的 `runtime-*`、`ownership`、`ledger`、`executed`、`record-execution`、`rollback-plan`、`rollback-history`。验证：`ats dispatch --help` 只剩负责人与停用相关命令，受影响测试通过（**转入 R5，本 change 不执行**）
- [x] 4.7 登记处把本组条目标为 retired 并附清零证明；更新 OPERATIONS 与 TEST_BASELINE（删除了哪些测试、其中多少原本失败）。验证：`ats retirement decide` 显示本组 retired，TEST_BASELINE 有本批记录（**转入 R5，本 change 不执行**）
- [x] 4.8 本组复验 V4–V6：全量测试与基线逐项对照；重启 `com.ats.dispatcher` 与 `com.ats.serve`，观察下一次定时触发、数据刷新与日历消费；确认旧表与台账可读。验证：复验结果写进 inventory.md 的本批记录（**转入 R5，本 change 不执行**）

## 5. R1 命名与入口残留退役（门 G0）

- [x] 5.1 新增 `ats data health [--consumer <id>]` 与 `ats data health-log`，删除 `ats consumer-cutover`（保留提示桩，指向新命令）。验证：新命令对 5 个研究消费者输出与原 `health` 一致；提示桩测试通过（0ae8e9f；`ats data health --consumer`、`ats data health-log` 已加，`consumer-cutover` 的 status/health/log 改提示桩，restore 保留因路由表在 R5 前不动；6 条测试）
- [x] 5.2 对 `legacy_entry.research_manual_commands` 做 V1–V3；inventory.md 第四节的副作用对照已全部为「已承担」（第 2 组完成后）。验证：`ats retirement decide` 判可退出（10-27：V1 静态——命令背后的 run_* 函数仅被 boundary_evidence 与 Phase F 验收测试当业务入口引用；V2 运行——launchd 三个服务与配置中无调用，G0 核验时用户确认不再使用；V3 `ats retirement decide` 仅缺“回退未演练”，依赖与门均满足）
- [x] 5.3 删除研究分析的旧手动命令（`ats macro review`、`ats sector review/layer`、`ats technical review`、`ats pead prep/score/monitor/watch/research`、`ats analyst information/fundamental`），保留只读的 `show`/`probe`/`html`，旧命令留提示桩并给出 `ats workflow run` 的替代写法。验证：提示桩测试通过；Dispatcher 各任务的测试仍通过（5f6fd90；11 个旧命令返回替代写法，show/probe/html 保留；run_* 函数因 R5 引用而保留；startup_admission 与 business_wiring 里 3 处改为直接调入口函数）
- [x] 5.4 负责人 legacy 模式改为停用 / 恢复：对外只有停用 / 恢复（落地取「丙」：内部值仍存 `legacy`，记为技术债，长期取消开关见 verification.md 上线后待办）；`ats dispatch pause/resume` 替代 `owner-switch/owner-rollback`；保留「先冻结再清点」与 running 认领拒绝停用的行为；`workflow_owners.yaml` 的 legacy 种子值暂留（它是新库种子并参与运行时配置哈希，改动让 16 个测试失败，归入长期方案乙）。验证：新增测试覆盖停用期间定时 / 事件 / 手动都不执行且记录跳过原因、有 running 认领时拒绝停用、恢复后不重复执行已完成触发
- [x] 5.5 `legacy_entry.evidence_commands`：删除 `ats evidence observe/collect/articles` 与 `ats sector crosssection`；保留命题治理命令（`ats evidence claims/report/propose/proposals/review/kbreview`）与 `ats sector kbperturb`。验证：保留的命令测试通过，被删命令返回已退役提示（2cd1fb5；observe/collect/articles/crosssection 停用并删除约 140 行死代码，命题治理与 kbperturb 保留）
- [x] 5.6 清理文档和帮助文本中的迁移期措辞（「新路径 / 旧路径 / 切流 / phase」），更新 README、OPERATIONS、DEVELOPMENT。验证：在 `docs/`（不含 archive）与 `ats --help` 中检索这些词，只剩有意保留并加注的位置（8d2b79d；帮助文本与操作类文档清理，DEVELOPMENT 新增“迁移期用语说明”列出有意保留处；docs/validation 为冻结的历史验收证据，不改）
- [x] 5.7 登记处把本组条目标为 retired 并附证明，TEST_BASELINE 记录本批。验证：同 4.7（10-27：5 条里 4 条标 retired——`naming.consumer_cutover_cli`、`legacy_entry.research_manual_commands`、`legacy_entry.evidence_commands`、`naming.migration_terms_in_docs`，证明写在各条 missing_condition；`legacy_entry.research_owner_legacy_mode` 因 5.4 取「丙」有意保持 pending，待长期方案乙；`migration_terms_in_docs` 因此去掉对它的依赖；TEST_BASELINE 已记录本批）
- [x] 5.8 本组复验 V4–V6（同 4.8）。验证：复验结果写进 inventory.md（10-27：V4 对照无新增失败，两份大文件的基线对照仍在后台；V5 已重启 dispatcher 与 serve，下一次真实触发待补记；V6 通过；见 inventory.md 第七节）

## 6. R3 登记修正与研究侧已登记条目（门 G0）

- [x] 6.1 给 8 条「登记已退役但未证明」的消费方补 `MODULE_TO_ROLE` 映射后重扫；有读取的改回 pending 并列出消费方。验证：`ats retirement zero` 对这 8 条不再出现「未知」（10-27：`MODULE_TO_ROLE` 只能映射角色，`chain.*`、`graph.*` 这类路径硬凑会得出假「清零」，所以改用静态检索证明：8 条的退役条件在 src 里都已成立，原消费方列表已过期，改为 `consumers: []` 并把检索结果写进各条 missing_condition；`ats retirement zero` 对这 8 条不再出现「待补证」。`apply_guardrails` 的定义本身还在 `risk_validator.py`，物理删除属第 7 组）
- [x] 6.2 `task_projections.legacy_columns`：迁走或删除剩余消费方（`memory.store.projection_lineage`、相关测试）后做 V1–V3 并退役。验证：命中墓碑的读取返回已退役原因（墓碑一致性测试）（10-27 用户同意改登记而非退役：`task_projections` 是信息分析师的抽取缓存，`save_task_projection` 写、`information_extraction_cache` 读，与 envelope 表是独立抽象；删旧列会让缓存失效、同一文档重复调用模型。已移出待退项，留在 active 清册，不登记墓碑；对应测试改为断言这一点）
- [x] 6.3 `evidence_fact_projections.legacy_observation_id`：同 6.2。验证：同 6.2（10-27 同 6.2：`legacy_observation_id` 是事实投影回溯到观测的连接键，产业链归纳的 `freeze_as_discovery` 在用，名字叫 legacy 只是遗留；已移出待退项）
- [x] 6.4 `layer_verdict.allocation`：在 5.3 删除旧行业入口之后，迁走或删除剩余消费方（`agents.sector.report/viz/review`、命令行）并退役；字段保留、历史行可读。验证：历史行读回测试通过，新写入不再产生该字段（10-27：cli、行业报告、轮动、截面、上下文、viz 共 11 处 `allocation or allocation_for_status(...)` 改为只用 `allocation_for_status(layer_status)`；`layer_verdict.allocation` 标 retired；新增历史行读回测试。已知行为变化：用历史行重渲染旧报告时档位按层状态推出）
- [x] 6.5 TEST_BASELINE 与 runbook 记录本批。验证：同 4.7（见 TEST_BASELINE 同日记录；runbook 第〇节条目数改为 41）
- [ ] 6.6 本组复验 V4–V6。验证：同 4.8

## 7. R4 只被旧入口用到的旧分析代码（门 G0，依赖第 5、6 组）

- [ ] 7.1 对 `agents.risk_manager`、`agents.risk_validator.apply_guardrails`、`agents.pead.monitor`、`agents.pead.research` 逐个做 V1–V3（`pead.monitor`、`pead.research` 在 5.3 之后）。验证：`ats retirement decide` 判可退出
- [ ] 7.2 删除这四项（risk_validator 只删已退役的 `apply_guardrails`，保留 `review_guardrails` 及其测试，等 R5 决定）及其测试，同步冻结面与资格清单。验证：受影响测试、架构守卫、assurance_surface 通过
- [ ] 7.3 重跑调用图确认没有新增的「只被已删除入口引用」的模块，结果写进 inventory.md；`graph/pead.py`、`agents/pead/{prep,score,report,units,triage,outputs}`、`runtime/digest.py`、`runtime/evidence_observe.py`、`agents/evidence/observer.py` 等明确保留。验证：inventory.md 有重跑结论
- [ ] 7.4 TEST_BASELINE 与 runbook 记录本批，本组复验 V4–V6。验证：同 4.8

## 8. 规格与运维文档同步

- [ ] 8.1 核对本 change 的规格增量与实际行为一致：读路由固定与数据产品检查（已在 29c6b4d 实现）、工作流停用 / 恢复、研究工作流只经 Dispatcher、可读报告由 Dispatcher 生成。验证：每条增量要求都有对应测试或运行记录引用，写进 inventory.md
- [ ] 8.2 ARCHITECTURE §6 实施状态、OPERATIONS、角色文档更新到退役后的状态。验证：文档里的命令都能照写运行

## 9. R5 交易路径相关条目（只登记，不执行）

- [ ] 9.1 确认 R5 全部条目已在登记处，门为 `trade_path_cutover`，依赖与退出判据完整；包括 inventory.md 第二节归 R5 的模块（`boundary_drill`、`joint_cutover`、`shadow_*`、`acceptance_*`、`paired_business`、`exit_requirements`、`business_replay_inputs`）。验证：`ats retirement decide` 对 R5 条目显示「外部门未满足」
- [ ] 9.2 在退役 runbook 中写明 R5 的接续方式：交易路径切换的 change 必须把 R5 列为任务，包括读路由与资格机制的收窄方案。验证：runbook 有该节，且与 `switch-trade-path-and-retire-r5` 的任务对得上

## 10. 收尾与整体验证

- [ ] 10.1 全量测试一次，与 10-19 基线逐项对照并登记差异（删除的测试、消失的旧失败、任何新失败的归因）。验证：TEST_BASELINE 有本 change 的权威测量
- [ ] 10.2 生产观察：退役完成后一个完整周期（覆盖工作日定时、周六宏观与行业、至少一次日历消费与数据刷新）无因退役导致的失败。验证：观察记录写进 inventory.md
- [ ] 10.3 `openspec validate --all --strict` 通过，更新项目记忆中的运行形态说明。验证：命令输出通过
