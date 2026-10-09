# Phase F 3.10：独立新入口 capture/run/replay

结论：**3.10 与必要前置 11.2 完成，73/116 完成、43 项待办，第 3 组 10/10。**沿用用户新需求验收裁决，旧入口和旧差异不是验收前置。范围为隔离的 ai_hardware / L4_interconnect / COHR，选定 Routine 与 Event 的六类研究、Chief/Risk/人工批准、Trader A、无网络模拟接收与 Clerk；Technical 研究子范围独立验证。真实 IBKR（含 Paper）、真实数据源、真实模型和生产切流未授权、未测。

## 0.1 影响登记

| 文件 | 改动目的 | 必要补验 |
|---|---|---|
| `workflow/evaluation_clock.py` | 隔离时记录实际评估时刻，生产默认墙上时钟不变 | 时间/报价、隔离恢复与源码漂移 |
| `trader/execute.py`、`decision/repository.py`、`execution/simulation.py`、`data/runtime/broker.py` | Trader 校验、审查审计、模拟提交和 broker 读取使用同一可记录评估时钟，源报价/账户时间不重写 | 授权、两阶段报价、审批及真实业务链、只读 broker 时间 |
| `workflow/business_replay_inputs.py`、`shadow_replay.py` | 外部输入传输、实际人工答复、分阶段时钟冻结；本轮授权依真实新审计链重建 | 未捕获输入禁止 fallback、跨进程无网络重放、旧工具兼容 |
| `workflow/acceptance_runner.py`、`acceptance_reports.py`、`runtime/cli.py` | 独立新入口 capture、实际 Chief/Trader/FakeBroker/Clerk、durable IBKR 拒绝和只读报告检查 | 固定矩阵六面必需断言、独立预期调度、研究缺数据失败、签核与撤销 |

补充实际跨进程发现：Chief 报告曾试图沿配置写入生产 Obsidian；`agents/chief/report.py` 现在对完整隔离绑定强制写入该根的 docs/chief，生产行为不变，须验证外部目录无写入。遗漏的 `sector_inputs.sector_prices` 行情传输也纳入固定 allowlist，防止跨进程导入顺序造成回查。

共享依赖及完整源码闭包影响十角色的最终资格；保留全部历史证据，既有报告不自动沿用。最终生产资格冻结/登记仍依 6.1/6.4。开发期错误及修复重跑保存为不同证据，不改写此前报告。


## 实现与实际验证

`acceptance_runner.capture` 只调用新 Dispatcher，矩阵、所选模式、scope、断言、调度预期和模拟成交协议在运行前固定。capture 前后核对完整实现指纹，configuration/seed 不匹配拒绝。外部数据/模型/人工答复完整保存在输入包；原始报价 source_as_of/queried_at 不改写。研究逻辑时点与实际运行评估时刻分开：后者按业务调用位置/顺序记录，跨进程只取冻结时刻。当前 revision/review/批准和授权由每轮真实业务重新产生，授权不作为 capture 阶段可复用的输出注入。

首次 new run 与另一个进程的 replay 使用同一 input_hash、不同 replay/workflow run ID、不同 ledger。两轮都执行真实六角色、Chief graph、风控/审批写点、Trader A、FakeBroker 与 Clerk。无网络 FakeBroker 接收一笔非空订单，真实重试不增加 receipt；真实 IBKR facade 对同一已批准订单仍拒绝，拒绝记录单独持久化并由报告 checker 回查。Clerk 对实际模拟回报落库，逐笔归因可追溯 snapshot、完整 revision/hash、review、human approval、intent/receipt 和 fill。

所选 Routine 和 Event 各两轮的 **11 个必需断言全部 passed**，每轮 19 个可追溯 refs；独立矩阵中的 6 个研究触发全部覆盖。原调度账本另保存一条内部 research-extraction claim，它不是用新旧运行并集推导的必需集合。纯 Technical 子范围同样独立通过；源行情缺数据的负例保存 failed 报告，签核拒绝；旧入口故意不可用不会阻塞新入口。

当前三个通过报告都保持 **unsigned**，已只读重新解析实际账本并确认可供明确签核。没有替用户签核，没有登记或激活生产资格。失败研究报告也保持原文、不能签核。

- [Routine 完整交易报告](phase_f_independent_runner_20261009/report_routine.json)
- [Event 完整交易报告](phase_f_independent_runner_20261009/report_event.json)
- [Technical 研究报告](phase_f_independent_runner_20261009/report_research_available.json)
- [缺行情的失败报告](phase_f_independent_runner_20261009/report_research_missing.json)
- [真实 run/input/ledger/refs 索引](phase_f_independent_runner_20261009/evidence_index.json)，原始根 `var/isolated/phase_f_independent_runner_20261009_v3` 不清理、不搬迁。
- [当前完整指纹](phase_f_independent_runner_20261009/source_hashes.json)：464 源码/提示/配置依赖，100 个受保护路径；[原始文件摘要](phase_f_independent_runner_20261009/raw_evidence_hashes.json)。后续代码/配置变化会使报告不再适用，须新运行/新报告。

11.2 的 7 个新增案例经真实签发/Trader/submit facade 覆盖：允许明确模拟接收，缺审批、缺 grant、错误账户/代次、误选真实 broker、退出测试后复用均拒绝。既有实际 ShadowBroker/影子账本及退出验证也通过。测试 grant 不解除生产 C3，真实 IBKR（包括 Paper）的写点在网络会话创建前拒绝。

## 测试与保留失败

所有命令经 uv 运行，未激活或直接使用 venv。

| 证据 | 原始结果 | 当前结论 |
|---|---|---|
| [runner_authority_v3.xml](phase_f_independent_runner_20261009/runner_authority_v3.xml) | 40 项：37 passed / 3 failed，142.924s | 29 项核心新入口/隔离授权/影子业务全部通过；3 条启动旧夹具只有 YAML owner，未设置当前 SQL owner，尚未到达待测入口 |
| [startup_aligned.xml](phase_f_independent_runner_20261009/startup_aligned.xml) | 11 项：8 passed / 3 failed | 首次试图用生产 freeze API 修正夹具，授权守卫正确拒绝；未放宽守卫 |
| [startup_final.xml](phase_f_independent_runner_20261009/startup_final.xml) | **11 passed / 2.953s** | 改用真实 initialize 在独立测试数据库初始安装 shadow SQL owner，实际 owner/Dispatcher 到 broker 拒绝及缺禁令停止全部通过 |
| [regression.xml](phase_f_independent_runner_20261009/regression.xml) | **274 passed / 388.822s** | 新报告/矩阵/六面断言、A 报价与授权、真实业务/部分迟到成交/Clerk、旧可选工具、审计/快照、隔离、十角色指纹与 optional 政策全部通过 |

有效范围是 **29 个核心 + 11 个启动 = 40 项通过，另 274 项相关回归通过**。并未重跑全仓；此前 12 条旧回退夹具失败仍按原报告保留，不宣称全仓全绿。

文档同步后的 [records_final.xml](phase_f_independent_runner_20261009/records_final.xml) 为 **20 passed / 3.19s**，覆盖记录守卫与进度文档。首次记录检查的格式失败保留在 records.xml；修正进度数字格式后复验通过。[任务依赖核对](phase_f_independent_runner_20261009/verification.json) 确认 73/116 完成、43 项剩余，无未知依赖或环；11.2 与 3.10 的全部前置任务均已完成。两个 change 的 OpenSpec strict 校验通过。

开发期错误和修复重跑见同目录 development 日志。首次将测试根放在生产 docs 子树被隔离守卫拒绝（runner_authority.xml：7 passed / 20 failed / 13 errors）；随后独立 var 父目录未创建导致 runner_authority_final.xml 的 40 setup errors。均保留，正确根下的 v3 核心账本和后续启动复验才构成本次有效证明。

## 新命令与后续工作

`uv run ats shadow acceptance-capture --isolation-root <root> --input-store <inputs.sqlite> --matrix-file <pre-run.json> --run-id <capture-id> [--request-file <execution.json>]`：必须已有选定 scope 的真实隔离种子和预先固定矩阵。完整交易 recipe 显式声明 cycle_id、account、fill_price；CLI 人工批准提示输出到 stderr，JSON 结果保留 stdout。捕获从不调用旧侧。

`uv run ats shadow acceptance-run --isolation-root <root> --input-store <inputs.sqlite> --packet-hash <hash> --matrix-file <new-or-replay.json> --run-id <fresh-id>`：每次要求新目录、独立 workflow/replay ID；scope/requirements 不变，代码/配置/阶段/输入缺失拒绝，禁止网络 fallback。

报告继续使用 acceptance-record / acceptance-report / acceptance-signoff / acceptance-check；proof 指向两轮实际 run 与 input store。签核是后续显式动作，失败或未测不能豁免。

**后续注意/待修复项**：7.8 动态索引、13.4/13.5 集成追溯与无真实提交、11.3 交易仲裁演练、5.15 联合恢复及最终 6.1/6.4 冻结/资格仍待依赖推进；研究生产范围、真实 TWS 只读证明、真实模型/网关、生产部署/观察和实盘未测。父 change 的全范围完成状态不由这两个交易 fixture 自动勾选。

**为何现在不修**：本次任务为选定隔离范围的新入口独立 capture/run/replay；测试数据、模型/账户/审批为明确 fixture。扩大生产范围或写入 IBKR 需要对应证据和授权，不能从本次通过推定。原材料及原失败保留，后续计划仍按任务依赖推进。
