# Phase F 控制、读、调度与交易操作手册

> **2026-10-19 说明：** 本手册写于 2026-10-15 精简裁决之前。其中按「标的×时点」逐 scope 取得资格、入口证据包、绑定请求哈希的精确授权等做法已被裁决取代：研究读取按消费者整体切换，前置是消费者层面的数据产品健康检查，迁移期退回旧路径。当前生产状态、切换与回退命令以 `docs/archive/PHASE_F_FINAL_ACCEPTANCE_20261018.md` 和 `docs/PHASE_F_RETIREMENT_RUNBOOK.md` 为准；以下内容保留作历史与实现参考。

本手册对应 `implement-phase-f-shadow-run-and-cutover`。所有命令使用项目 uv 环境。生产部署需要当前 exact-scope 资格、当前新入口验收签核和明确部署授权；隔离 fixture 不满足这些条件。C3 仍关闭真实自动券商写入，隔离验收可明确选择无网络 FakeBroker。

架构及验收依据为 [ARCHITECTURE.md](../ARCHITECTURE.md)，逐项对应见 [验收要求映射](PHASE_F_ACCEPTANCE_REQUIREMENTS.md)。本手册不扩展其角色依赖、单次运行范围或上线条件。

2026-10-10 用户明确运行语义：上线批准一次，在批准范围内持续运行直到主动停用/撤销，无需逐 tick/逐日部署续批。部署许可寿命与 as_of、查询窗口及资格/输入 TTL 分离；每次仍自动核验当前许可、资格、报告、输入与停止/代次。历史有限授权不自动升级；现有命令的有限 valid-until 不应被解读为已经实现持久运行许可。此改造由 5.16 承接，任务仍待完成，生产操作须等其实现与验证。

## 控制平面与实际接线（5.13）

```sh
uv run --offline --no-sync ats cutover state
uv run --offline --no-sync ats cutover wiring
uv run --offline --no-sync ats cutover history
uv run --offline --no-sync ats dispatch runtime-state
```

首次安装只允许显式 `ats cutover state --bootstrap`。CLI、scheduler.start 和 approval webhook 启动只读检查六边界；普通启动不会修复或创建缺失权威。wiring 是声明，不能替代 `boundary_evidence` 中该 identity、该 mode 的实际正反业务入口证据。全局 target 不让未知 scope 合格；资格失败与停止按业务 scope 生效，其他研究不受牵连。

读 identity 固定 domain/consumer/contract、kind/id/entities。历史有限授权保留 time_range；新协议允许显式 `time_policy: per_invocation`，不得同时带固定 time_range。只有不可变部署请求明确包含 `deployment_policy: until_stopped` 且已经提交，运行时才区分部署动作凭据的期限与持续许可寿命；撤销、停止和代次检查仍执行。此协议已有定向验证，5.16 的完整角色/事件绑定尚未闭合，不能据此开始生产部署。Event 的现有精确授权仍固定 event_id/event_version，不隐式扩展到新事件。调度 SQL identity 固定 workflow + kind/id + 计划时刻或 event/version；手动重放须携带原逻辑 identity，不能生成新时间伪装未执行触发。快照记录全部研究依赖的路由、代次与恢复策略代次；风险审查、人工审批回调和每笔 Trader 交接前再验，恢复换代不恢复旧快照的审批权限。

## 读与调度部署（9.1、10.1）

清单必须包含 owner、精确资格范围、观察窗口、成功判据、停止条件、预登记恢复证明、报告引用及显式依赖。`scope.read_request` 包含 batch_id/workflow/members/schedules，members 每项明确 boundary/identity/route/expected_generation；另提供 projection_refs、compatibility_proofs 和 deployment_authorisation。Routine/Event 必须按本次所选模式满足其必需任务，其他分析类别仍各自必需。

研究读批次至少明确 projection_read；独立 schedule 批次至少明确 dispatcher_schedule 和实际 SQL owner 动作，并逐个声明非空 consumers，不能以空资格集合通过。兼容证明须列出实际依赖边，固定源码/config/prompt 指纹、scope、目标 routes、报告、有效期。可独立切换的边界保持未请求边界不变；不兼容边由同一个 dependency-closed request 联合提交，禁止隐式扩 scope 或改变采集 owner。

```sh
uv run --offline --no-sync ats batch show --batch-id BATCH
uv run --offline --no-sync ats read show-auth --authorisation AUTH
uv run --offline --no-sync ats read plan --batch-id BATCH --authorisation AUTH
uv run --offline --no-sync ats read switch --batch-id BATCH --authorisation AUTH --apply --actor OPERATOR
uv run --offline --no-sync ats read history --batch-id BATCH --json
uv run --offline --no-sync ats dispatch runtime-state
```

`batch show/dry-run`、`read plan` 使用正式 executor 的同一组 gates，阻塞返回非零。计划只追加审计，不能改 route/owner/业务输出。`read switch` 也支持明确的 schedule 类批次，复用同一 coordinator，但 governing boundary 为 dispatcher_schedule。单独读批次不移交调度；单独调度不切读。生产授权的 `read authorize` 是人工裁决的登记操作，只能在已批准材料上调用；reference 绑定不可变请求，重复相同登记幂等，不能扩大范围或替换有效期。

切换前记录实际 route/generation、owner/generation、未完成认领、投影 id/hash 与完整 revision/review/approval 链。执行过程 prepared→frozen→routes_committed→owners_committed→committed；进程死亡或重验失败保留 journal fence。恢复只能重放相同 batch/request，且重新通过当前所有 gates；禁止手工清 journal、改 SQL 或切换控制库来解除冻结。

## 停止、恢复与观察

使用另一个明确获授权批次，将指定 boundary 置 disabled；调度停止 request 同时列出精确 SQL scope，认领和发布入口都必须拒绝。恢复指向当前实际可读、已验收的新版本，使用新的边界/owner 代次。无可用目标、验收撤销、资格过期、执行记录缺失或在途处置不足时保持停止。旧 worker 即使存活，也不能借新代次发布。完成触发读取历史执行记录，不重跑；旧结果不满足当前血缘/时点时显式拒绝复用，保留旧输出；新逻辑触发可在恢复后运行。

```sh
uv run --offline --no-sync ats dispatch runtime-inventory --token TOKEN
uv run --offline --no-sync ats dispatch runtime-expected --from-time START --to-time END
uv run --offline --no-sync ats dispatch runtime-compare --from-time START --to-time END
```

观察集合从配置及已发布 Event/version 独立生成，禁止用成功执行的并集代替预期集合。验收逐 scope 覆盖、唯一认领、成功或有效复用、迟到发布拒绝、控制库重开及采集 owner 不变。停止条件包括缺必需投影/行情、资格或签核失效、半迁移、未知成交、重复认领、执行记录/历史引用缺失。窗口必须登记起止与负责人，未到期不记通过；触发停止条件后重新裁决与补验，不能复用旧绿色窗口。

生产 `runtime-freeze/handover` 必须由已授权 coordinator 控制；独立直接 CLI 操作只用于物理隔离演练。更改 YAML 只影响唤醒配置，不重置 SQL owner/generation。reload 读取真实 SQL，并记录配置 hash；不迁移已运行采集队列、不改 source lifecycle。

## 交易与状态恢复（11.4）

Trader A 从受治理行情接口读取参考价，核对授权绑定、price source/as_of/session/age/size/tick/currency 和价格政策；行情或快照过期必须重审，不补造参考价。B 只是价格随授权传递的历史方案，不作为 A 的通过证据。人工审批绑定 cycle/revision/hash/review/approval/account；研究读权限不开放交易权限。

依据用户裁决，真实 TWS 读写核验直接在本机、沙盒外启动（工具需要时申请权限），避免 API 权限造成误判；仍分别核对只读查询与写入授权。

真实 IBKR 写入须独立 route/grant/environment/account 四方匹配、未冻结、当前 generation，以及真实会话账户核对。无 grant 必须在连接会话前拒绝写入；合法只读连接不由读切流授予写权限。当前 change 不执行 live 开放。FakeBroker 验收须完整物理隔离且显式模拟 transport，不能以 paper 环境名代替隔离。

交易停止后保存授权与批准链、intent/receipt、全部在途及部分/迟到成交。Clerk 只读对账可以继续；未知 intent 或未终结订单阻止安全移交。恢复使用已验收目标证明和新的 grant/route generation，旧 token/旧授权不复活。重试不能再次发送结果不明的同一逻辑订单。

## 验证与证据

```sh
./scripts/run_tests.sh tests/test_phase_f_startup_admission.py tests/test_phase_f_snapshot_dependencies.py
./scripts/run_tests.sh tests/test_phase_f_batch_executor.py tests/test_phase_f_read_executor.py
./scripts/run_tests.sh tests/test_phase_f_schedule_stop.py tests/test_phase_f_schedule_runtime.py tests/test_phase_f_joint_cutover.py
```

验证运行使用独立 basetemp 并保存原始日志/JUnit，失败也保留。共享实现改动使既有指纹报告成为历史证据，最终冻结后补验当前依赖闭包和十角色，不沿用历史签核。`docs/validation/`、`docs/assets/` 按仓库规则只留本地、不提交 Git。生产动作和隔离演练、工具实现和当前资格、切前恢复和生产观察必须分开记录。

## 退出判据与责任（12.7）

```sh
uv run --offline --no-sync python scripts/inspect_phase_f_exit.py --out docs/validation/exit-inventory-UNIQUE.json
uv run --offline --no-sync python scripts/inspect_phase_f_exit.py --requirements REQUIREMENTS.json --out docs/validation/exit-coverage-UNIQUE.json
```

requirements 在验收前逐项声明 use/consumer/精确 scope、report_id/report_db、batch_class 和必需 dimensions。`exit_requirements.verify_new_coverage` 打开原始新运行/重放库，重新计算固定断言和当前签核，解析完整 output_refs 与实际治理读。缺新必需用途、scope 不符、血缘/完整性失败、执行库或历史引用不可解析均 pending。废弃旧用途不需要恢复，旧算法/旧结果不同不能阻塞新需求；旧 reconcile/静态扫描只保留历史诊断价值。

生产清零还需覆盖退役登记的全部生产消费方和真实新 CLI/Workflow 入口，以及整个观察窗口内的真实 route/owner、读写调用与未完成认领。新契约的隔离通过不能清零生产；当前 inventory 从只读控制库/调度库和已登记实际调用点读取状态，缺生产观察时明确 pending。已有旧生产 route/owner/读写时，继续 pending 并记录位置和负责模块；停用的离线诊断、历史原文、旧引用保留不算仍在生产消费。

`verify_recovery_window` 分别检查实际安全停止/已验收版本恢复演练引用、旧 worker 被隔离、历史保留、观察 receipts、窗口到期与停止条件。未演练、缺证据、未到期、窗口失败和历史不完整均不能退出。演练的原始库/JUnit、签核和生产观察须可审阅，禁止只填写 passed 标记充当证据。

部署负责人负责 scope、停止阈值和窗口，验收权限方负责报告签核，采集/source owner 保持既有归属，历史清理负责人处理外部保留义务。只有条件全满足的具体条目才能申请受控退役；保留原 target_phase，未满足条目逐项列出缺项和负责人。历史判据工具实现完成与当前退出结果分列。墓碑、读取拒绝和历史引用保留之后才可另行审批物理清除；本 change 不删除历史数据。

阶段一的独立归因、用途盘点、预检命令与未批准的生产观察方案见 [阶段一报告](PHASE_F_STAGE_ONE.md)。正式冻结和生产动作仍按 tasks 依赖推进。
