# 交易路径人工核验（T0）记录——初稿

> 本文件由 tasks 1.2 起草，3.1 补全 T0.1–T0.9 的核验方式与证据，3.4 由你逐项填写结论。
> 目前只有第 1 节（实现改动前的基线）。

## 1. 切换前基线（tasks 1.2）

**目的：** 在动任何生产代码之前，量出“交易路径”现有保证在今天的代码上是否全部成立，作为后面每一步改动的对照。

**测量条件**

| 项 | 内容 |
|---|---|
| 提交号 | `173fac02b880bce85466222110ec45eebfdbfcdb`（分支 `feat/rebuild_workflow_dataflow`） |
| 日期 | 2026-10-28 |
| 命令 | `pytest -q --tb=short -p no:randomly <文件>`；独立验收两个文件在干净 worktree 里单独重跑（见下） |
| 证据文件 | `var/test_runs/switch-1.2-baseline.log`、`var/test_runs/switch-1.2-baseline.xml` |

**结果**

| 保证 | 测试文件 | 通过 | 说明 |
|---|---|---|---|
| 22 项订单追溯、独立验收、证据登记 | `test_phase_f_integration_evidence.py`、`test_phase_f_independent_runner.py` | **28 / 28** | 在 173fac0 的干净检出里测得（17 分 19 秒） |
| 决策状态机、快照、多轮风控、审批 | `test_phase_d_foundation`（24）、`test_chief_snapshot`（12）、`test_chief_loop`（10）、`test_chief_graph`（8）、`test_decision_risk_review`（17）、`test_approval_narrowing`（13）、`test_chief`（9）、`test_phase_d_e2e`（3） | **96 / 96** | 在主工作区测得，当时工作区带有 1.1、2.8、2.9 的未提交改动 |

**第一次测量为什么不算数。** 同一批命令第一次在主工作区跑时，`test_phase_f_integration_evidence` 的 21 项报错、`test_phase_f_independent_runner` 的 1 项失败，原因都是 “fixed implementation fingerprint drifted”。这两个文件在捕获过程中会对 `src/ats` 全部源码取指纹，我在同一时间修改了 `src/`，指纹因此变化。不是代码回归。所以这两个文件改在干净的 173fac0 检出里重跑，28 项全过。

**限制与后续**
- 96 项那一组不是在干净检出里测的。它们不取源码指纹，当时工作区的改动与它们无关，但严格说不是同一个条件。第 2 组全部完成后会在最终提交上再测一次全量，写进 `docs/TEST_BASELINE.md`。
- 生产环境没有被触碰：这一节只跑了隔离环境里的测试。

## 2. T0 核验清单（tasks 3.1）

> 形式与研究侧 G0 相同：我准备“方式、通过标准、自动证据”，你填“结论、核验人、日期”。
> **结论**只填：通过 / 未通过 / 不适用 / 接受缺口。任一项未通过，都不做生产切换（第 4 组）。
> 带 `〔自动证据，非结论〕` 的是我跑出来的东西，不是结论。改结论时不删旧内容，在备注里补一句。时间用台北时间。
> 隔离环境 = 无网络的 FakeBroker、隔离账本，不写生产；生产只读 = 读真实研究快照与真实账户，不写决策、不下单。
> 证据文件都在 `var/test_runs/`（不在 `tmp/`）。**这些自动证据是在带未提交改动的工作区测得的**，最终提交落定后要在同一命令上复测一次（见第 4 节），数字以复测为准。

### 2.1 T0.1 主理人决策输入（生产只读）

| 项 | 内容 |
|---|---|
| 方式 | 用当天真实研究快照渲染一次主理人上下文（不建周期、不调用模型），与六类投影逐段对照；`ats retirement zero` 看 `chief.legacy_table_direct_reads` |
| 通过标准 | 每段都能指到投影 ID 与 hash；无旧表读取（zero）；人为去掉一类投影时阻断并出缺口报告，不建周期；**你读一份渲染结果，确认内容是你认可的研究** |
| 自动证据 | 渲染材料与 `retirement zero` 输出由 3.2 生成，写入第 3 节；“去掉一类投影即阻断”由 `test_chief_snapshot.py`、`test_decision_snapshot.py` 覆盖；风控状态块与量化/复合排名分歧由 `test_chief_context.py`、`test_chain_to_factor.py` 覆盖；旧决策表读取守卫由 `test_decision_read_audit.py` 覆盖 |
| 状态 | 渲染材料与逐段对照见 3.9（2026-10-29）；待你读一份渲染结果 |

### 2.2 T0.2 主理人—风控多轮（隔离）

| 项 | 内容 |
|---|---|
| 方式 | 状态机测试：首轮通过 / 驳回一次后通过 / 多次驳回后通过 / 超过 3 轮转人工复核 / 不行动 |
| 通过标准 | 全绿；驳回是结构化的；裁剪建议没有静默改单；**你读一份驳回—修订样例** |
| 自动证据 | `test_chief_loop.py`、`test_chief_graph.py`、`test_decision_risk_review.py`、`test_approval_narrowing.py`、`test_chief.py`（第 4 节） |
| 状态 | 驳回—修订样例见 3.9（2026-10-29）；待你读 |

### 2.3 T0.3 命令行审批（隔离）

| 项 | 内容 |
|---|---|
| 方式 | `ats approval pending / show / approve / revise / reject` 全流程，加重复批准、旧修订 hash、已终结周期、前一日待审批周期 |
| 通过标准 | 显示修订 hash、风控结论、组合快照时间、交易摘要和“批准后不会下单”；错 hash、旧周期、已终结一律拒绝；重复批准返回首次结果；**你亲手跑一遍**（命令见 `docs/OPERATIONS.md` §7.1） |
| 自动证据 | `test_approval_cli.py`（18 例）。已知偏差：`approve` 本身就是确认动作（打印与审批时的差异后立即执行），没有“先看再另行确认”的两步，原因是快照新鲜度预算只有 60 秒；批准流程测试在隔离环境里的终态可能是 `executed`，实盘锁定下的 `approved_not_executed` 由 T0.4 的测试覆盖 |
| 状态 | 你在隔离沙箱亲手跑完（2026-10-30），行为全部符合；见 3.15 |

### 2.4 T0.4 执行前检查与锁（隔离 + 生产只读）

| 项 | 内容 |
|---|---|
| 方式 | 隔离：授权十字段、60 秒新鲜度、报价 1% 规则、client_order_id 稳定、重试不重复。生产：锁定下批准一次演练周期 |
| 通过标准 | 隔离下模拟成交正确；生产下到券商交接点被拒绝，进入“已批准未执行”，并记录原因；券商侧零提交（TWS 订单列表对照） |
| 自动证据 | 隔离：`test_approved_not_executed.py`、`test_execution_authorization.py`、`test_authorization_lifecycle.py`、`test_phase_f_trader_a.py`、`test_live_authorisation_provenance.py`（第 4 节） |
| 状态 | **生产一半需要真实批准一个周期，本 change 里它属于 S5 之后的观察窗口，不在 3.2 的“只读”范围内**，见第 5 节待你决定的事项 |

### 2.5 T0.5 Clerk 对账（隔离 + 生产只读）

| 项 | 内容 |
|---|---|
| 方式 | 隔离：部分成交、迟到成交、撤单、拒单、人工订单、无法归因、重启、重放。生产：在生产库副本上 `ats clerk run --dry-run`，覆盖最近 30 天 |
| 通过标准 | 隔离全绿；重放不产生新事实。生产演练各步骤跑通；只有带正向证据（`ats:` orderRef、permId，或订单号加日期对上本系统订单）的成交记为系统，其余记为人工或无法归因，一律不进系统绩效与归因。与 TWS 的持仓和资金差异只记录、不作为通过条件（2026-10-28 你确认：账本是历史成交的累积，且有相当一部分交易是你直接在券商手工下的单）；无法归因的数量与原因由你确认可接受（7 月实盘留下的旧式订单号预计落在这里） |
| 自动证据 | 隔离：`test_clerk_*`、`test_reconcile.py`、`test_ledger_rebuild.py`、`test_journal_ledger.py`、`test_journal_idempotency.py`（第 4 节）；生产演练材料待 3.2 |
| 状态 | 生产演练见 3.3、3.5；人工归类与补录见 3.6—3.8 |

### 2.6 T0.6 订单追溯（隔离）

| 项 | 内容 |
|---|---|
| 方式 | 复跑 22 项订单追溯（`test_phase_f_integration_evidence.py`、`test_phase_f_independent_runner.py`） |
| 通过标准 | 全部通过；每笔模拟订单联接唯一的快照、修订、风控与批准 |
| 自动证据 | 这两个文件会对 `src/ats` 全部源码取指纹，**只能在干净检出里测**；基线 28 / 28 见第 1 节（173fac0）。最终提交落定后在干净 worktree 复测，写入第 4 节 |
| 状态 | `fdc56a3` 干净 worktree 复测 28/28（见 3.9）；首次复测发现并修复了一处回放不确定性 |

### 2.7 T0.7 单活与回退（隔离）

| 项 | 内容 |
|---|---|
| 方式 | 双活拒绝、旧代授权拒绝；按 D8 回退步骤演练一次（停用工作流 → revert 读取方提交 → 重启） |
| 通过标准 | 校验对否定路径判失败；回退后旧读取方能读到观察期内的决策（依赖镜像写，见 D3） |
| 自动证据 | `test_route_single_active.py`、`test_route_switch.py`、`test_live_route_drill.py`、`test_trade_schedules.py`、`test_schedule_owner_switch.py`（第 4 节）。回退演练（revert 读取方提交）是对提交历史的操作，需要在隔离 worktree 里做一次，尚未做 |
| 状态 | 回退演练见 3.9（2026-10-29）；有一处请你判断（镜像写入每一轮修订） |

### 2.8 T0.8 测试基线（固定依赖）

| 项 | 内容 |
|---|---|
| 方式 | 重写 4 个决策路径夹具（`phase_d_e2e` 3、`test_chief` 1），跑全量 |
| 通过标准 | 交易路径相关测试全绿；其余与 `docs/TEST_BASELINE.md` 逐项对照，无新增未归因失败 |
| 自动证据 | 4 个夹具已在第 2 组改写（见 TEST_BASELINE 的“交易路径切换第 2 组”一行）。全量待最终提交后，先重测证据指纹基线再跑 |
| 状态 | 全量与逐项对照见 3.9（2026-10-29，`fdc56a3`，相关修复 `aa174ac`） |

### 2.9 T0.9 运维说明

| 项 | 内容 |
|---|---|
| 方式 | 按 `docs/OPERATIONS.md` §7.1 的草稿操作一遍 |
| 通过标准 | 照文档能完成查看待审批、审批、停用与回退 |
| 自动证据 | 审批与停用/恢复的每条命令都有测试；整套文档流程没有端到端演练 |
| 状态 | 你操作一遍待做 |

### 2.10 结论表（你填）

| 编号 | 对象 | 结论 | 证据 | 核验人 | 日期 | 备注 |
|---|---|---|---|---|---|---|
| T0.1 | 主理人决策输入 | 首轮未通过（你 2026-10-29 审阅，四处缺陷＋事件模式），已修待复核 | 3.9（首轮渲染）；3.10（修复与重渲染）；3.11（按标的取模式，例行与事件两份渲染 `var/switch_t0d/`） | | | |
| T0.2 | 主理人—风控多轮 | 通过（2026-10-30 复核）：5 条路径样例通过；真实周期逻辑正确、决策可接受 | 3.13（首轮意见与修复、5 条路径样例 `var/switch_t0g/t0_2_paths_reading.md`）；3.14（真实周期 `var/switch_t0h/t0_2_real_cycle_reading.md`） | 用户 | 2026-10-30 | 用户保留意见：Trader 不一定能把主理人提案解析成正确的订单；3.14 两项遗留（审查中报价 30 秒过期被当成风控驳回、模型期权建议的事实错误）未修 |
| T0.3 | 命令行审批 | 通过（2026-10-30 复核）：隔离沙箱里三个周期亲手跑完，清单 12 项行为全部符合预期 | 3.15（三个沙箱周期 a/b/c 的逐项对照）；第 4 节 t03（18 例通过） | 用户 | 2026-10-30 | 沙箱的主理人是替身（revise 的修订内容不代表真实模型）；体验问题 5 项待定，见 3.15；60 秒快照预算在 `approve` 里被超过（115.6 秒），真实周期的执行前检查由 T0.4 验 |
| T0.4 | 执行前检查与锁 | | 第 4 节 t04；3.4 LIVE-X 作废；生产一半见第 5 节 | | | |
| T0.5 | Clerk 对账 | | 3.3、3.5（30 天演练）；3.6—3.8（人工归类、补录、期权）；第 4 节 t05 | | | |
| T0.6 | 订单追溯 | | 3.9（干净 worktree 28/28，含一处回放不确定性的修复） | | | |
| T0.7 | 单活与回退 | | 3.9 回退演练；第 4 节 t07 | | | |
| T0.8 | 测试基线 | | 3.9 全量与逐项对照；TEST_BASELINE 2026-10-29 一行 | | | |
| T0.9 | 运维说明 | | OPERATIONS §7.1；待你操作一遍 | | | |

## 3. 生产只读证据（tasks 3.2、3.3）

### 3.1 做法与“生产未被写入”的证明方式（3.2）

- **没有在生产库上运行任何会写的东西。** 用只读连接（`mode=ro`）把 `ats.sqlite`、`phase_f_cutover.sqlite`、`phase_f_dispatch.sqlite`、`phase_f_routes.sqlite` 备份成 `var/switch_3_2/copy/`（2026-10-28 19:49，清单 `copy_manifest.json`），渲染与 Clerk 演练都只指向副本。
- **“对比写前写后行数”改在副本上做，不在生产上做**：生产库有 Dispatcher、数据刷新等进程在并发写，前后行数一定不同，对比不出东西。副本上的行数变化如下，说明这两件事各自会写什么；生产库只做了事后抽查（`mode=ro`）：`clerk_runs` 0 行、`decision_cycles` 0 行、`trade_episodes` 29 行，与副本演练前一致，副本演练写入的那条 `run_5f49…` 不在其中。
- 副本上的写入：**渲染**只写 `production_consumer_receipts` +5（读取回执，在 `phase_f_cutover.sqlite`），因此在生产上直接渲染并不是严格“零写入”；**Clerk `--dry-run` 并不只读**，写了 1 条 `clerk_runs`、4 条 `trade_episodes`。
- 证据文件（都在 `var/switch_3_2/`）：`chief_context_render.md`（渲染出的主理人上下文）、`chief_snapshot.json`、`counts_before_render.json` / `counts_after_render.json` / `counts_after_clerk.json`、`clerk_vs_tws.json`。脚本在会话草稿目录，不在仓库里。

### 3.2 T0.1：渲染主理人上下文——**未能按生产的治理路径渲染**（血缘问题已修，复测见 3.5）

按生产现状直接渲染，**两道门都拒绝**，我为了让你能看到内容，在副本进程里分别绕过它们，结果如下：

| 门 | 拒绝原因 | 含义 |
|---|---|---|
| 读路由 | `new decision dependency requires qualified target business scope` | Chief 目前没有已认证的读路由，这是 S1 才会补的，**符合预期** |
| 冻结血缘校验（`decision_requirements.validate`） | `fixed input/vintage lineage differs: dispatcher:layer-review:layer:L1_app:…` | **不符合预期，见下** |

**血缘校验为什么拒绝（已查到原因，未改任何代码）：** 独立运行的 Chief 周期（`dr.standalone`）会建一个覆盖 sector / fundamental / macro / technical 的计划，要求**每一份**上游投影的 `input_refs` 都记着这个计划的**全部**配置哈希（`macro.yaml`、`pead.yaml`、`pead/*.yaml` 共 11 个标的、`technical.yaml`、`risk.yaml`、`sectors/ai_hardware.yaml`，一共 16 个）。生产里的投影是研究侧各工作流各自跑出来的，只记自己用到的配置（例如层级分析只有 `risk.yaml` 与 `sectors/ai_hardware.yaml`），所以我逐份核对了快照里的 127 份投影，其中 **126 份的配置引用都不齐**（层级分析 8/8、行业配置 1/1、信息简报 11/11、技术面 11/11、基本面 95/95 都不齐）；宏观 1 份我的核对脚本没能对上计划里的任务，未判定。**也就是说：只要还走独立运行的路径，今天生产里的任何 Chief 周期都过不了这道校验。** 我没有改 `decision_requirements.py`，这个行为不是本 change 引入的；4 个决策路径夹具按“冻结血缘”重写（任务 1.1）时，夹具把整套配置闭包都写进了投影，所以测试是通的，没有暴露这个差距。

**绕过两道门后渲染出的内容**（`chief_context_render.md`，供你阅读；账户号在文件里，不要外传）：
- 共 **352,853 个字符**，其中研究快照 351,485 个，基本面 95 份投影占了绝大部分；这么长的上下文能否被模型一次吃下、费用多少，**没有验证**（没有调用模型）。
- **组合账本状态：`partial`，来源时间 2026-08-31，最近一次对账 2026-08-31**，`broken_links` 9 条；上下文里明确写了“账本未完整对账，以下持仓、成交与绩效不得当作完整事实”。
- **风控状态：** 指令 `REPAIR_ONLY`、不得增加风险、`L1-L4_chip_design` 23% 超 15% 上限；评审日期 2026-08-31，上下文里标了“已 57 天前，姿态可能已变化”。
- 研究快照六类投影均可用（层级分析 8/8、信息简报 11/11、行业配置 1/1、技术面 11/11 等），最近的是 10-26～10-27。
- 战绩反馈一段 474 个字符。

**还没有做的：** “六类投影逐段对照投影 ID 与 hash”“人为去掉一类投影时阻断”（后者有测试，生产上没演练）；`ats retirement zero` 的 `chief.legacy_table_direct_reads` 输出。

### 3.3 T0.5：Clerk 对账 30 天演练——**与 TWS 不一致，且有一步在生产数据上崩溃**（崩溃已修，复测见 3.5；TWS 差异按新口径只记录）

命令：`ats clerk run --dry-run --window-start 2026-09-28 --window-end 2026-10-28`，指向副本，读 TWS 实盘（只读，`ATS_BROKER_WRITE_PROHIBITION=1`）。

| 步骤 | 结果 |
|---|---|
| reconcile | `fills_seen 0`：**TWS 当前返回 0 笔成交**（它只返回当前会话的执行），所以窗口内没有任何成交可对账 |
| episodes | 10 个标的、23 个回合，新“播种”22 个 |
| marks、performance | **失败**：`TradeEpisode.invalidation_source` 校验错误，库里 33 条回合中 11 条该列为 `NULL`，模型要求字符串（`schemas/journal.py:218` 默认值是 `""`，库里是后来 `ALTER TABLE` 加列留下的 `NULL`）。整次运行记为 `failed` |
| 结论 | `clerk_runs` 状态 `failed`，说明 **S4 启用 `clerk-daily` 后，在现有生产数据上第一次运行就会失败** |

**持仓对比（`clerk_vs_tws.json`）：** 账本里一共只有 **13 笔成交（2026-07-17 至 2026-08-31）**，而 TWS 有 19 个净持仓标的。账本与 TWS 数量不一致的有 19 个标的（含账本里有、TWS 没有的 EUR、SPCX）（例如 DRAM TWS 500 股、账本 0；NVDA 140 对 0；TSM 80 对 0；SHV 300 对 0；GOOG 80 对 -13；AXTI -5 对 +100）。**不是“差异逐条可解释”，而是账本自 8 月底之后没有再对过账，且 TWS 取不回历史成交，Clerk 日跑补不上这个缺口。** TWS 净值 257,607，账本侧上下文里的净值 248,823（8-31 的快照），相差 8,784。资金差异没有再拆分。

**无法归因：** 账本异常 9 条，全是 `broken_link`（成交没连上决策），0 条未归因成交；因为窗口内 0 笔成交，旧式订单号那批没有被这次演练碰到。

### 3.5 阻断修复后复测（2026-10-28 晚，副本 `var/switch_3_2b/copy/`）

你 2026-10-28 的裁决：阻断 1 用方案 A；阻断 2 写库统一用 NULL、读库对 NULL 与空串都做防御；阻断 3 账本与 TWS 有差异是正常的，不能默认成交都归因到系统决策。修改与复测如下（都未提交）。

**修了什么：**

| 阻断 | 修改 | 文件 |
|---|---|---|
| 1 配置血缘 | 每个任务只核对它自己（连同声明的上游）读的配置，新函数 `task_config_hashes`；`Dispatcher._task_inputs` 写入与核对都用它。产出尝试比对改为命名空间、任务、范围，不再比对实例键末尾的整计划配置版本 | `workflow/planning.py`、`workflow/dispatcher.py`、`workflow/decision_requirements.py` |
| 2 回合字段 | `invalidation_source` 改为可空，未判定一律写 NULL；读库时空串也当未判定，其他有默认值的字段读到 NULL 取默认值，没有默认值的事实字段读到 NULL 照旧报错；不改写生产库已有的行 | `schemas/journal.py` |
| 3 账本口径 | 不改代码。T0.5 通过标准与停止条件 5 改为“归因正确、差异只记录”（design.md、本文件 2.5） | — |

**测试：** 夹具改成生产形态（每个研究任务族单独跑出来的血缘，`tests/phase_d_frozen_harness.py::producer_inputs`），新增 5 个血缘测试（`test_chief_snapshot.py`：单独工作流的投影能过闸门；任务自己的配置变了仍被拒；别的任务配置变了不连带技术面；Routine 尝试换了配置版本仍认；换了范围仍拒）和 4 个回合读写测试（`test_llm_boundary.py`）。把两处源码改动临时撤回后，新血缘测试有 4 个变红，确认测试能抓到这个问题。受影响的 40 个测试文件：526 通过、7 失败（`var/test_runs/switch-blockers-1-2.log`）。其中 1 个由本次改动引起（`test_routine_baseline_and_reuse` 用了没有 `tasks` 的桩计划），已修：没有配置哈希时直接返回空集，复跑该文件与 `test_chief_snapshot`、`test_workflow_planning` 49 通过；2 个是已登记的 `test_phase_f_research_gate` 真实网络连接；4 个 `test_phase_f_stage_one` 报“未登记的实现依赖”，列出的是工作区里尚未纳入版本的文件（`pending_channel.py`、`trade_schedules.yaml`，以及其他会话的 `issuer_ir.yaml` 等），与本次改动无关，干净检出后复测。

**复测（同样只读复制生产库，`copy_manifest.json`，2026-10-28 14:29 UTC）：**
- **渲染：** 只绕过读路由门（S1 才补），**血缘校验不再绕过，通过**。上下文 303,844 个字符（研究快照 302,476），`gap_report` 为空。仍未调用模型。
- **Clerk `--dry-run`（同一窗口，读 TWS 只读）：** 运行状态 `completed`，五步都跑通：reconcile 看到今天会话的 3 笔成交、0 笔连上系统订单、记为无法归因 3 笔（dry-run 不登记异常）；marks 6；episodes 10 个标的 23 个回合（播种 22）；predictions 0；performance 已记录。日志 `var/switch_3_2b/clerk_run.log`。
- **新发现：** `--dry-run` 下 performance 步骤仍会把 TWS 返回的成交写进 `fills`（`trader/performance.py:42`），副本 `fills` 由 13 行变成 16 行。和此前记下的 `clerk_runs`、`trade_episodes` 一样，说明 `--dry-run` 不是只读。

**仍待你决定（阻断 3 的延伸）：** 见第 5 节。（2026-10-29 已定，见 3.6。）

### 3.6 人工归类截止日与对账单补录（2026-10-29，副本 `var/switch_stmt/copy/`）

**你的决定（2026-10-29）：** 同意“截止日后无系统标签即人工”；历史成交用你上传的交易确认报告（2026-09-01—10-28，原件存 `var/broker_statements/`，不进 git）补录。

| 改动 | 位置 | 做法 |
|---|---|---|
| 截止日 | `config/settings.yaml` `journal.manual_classification_since: "2026-07-27"` | 取 `ats:` orderRef 上线日（提交 `3cbae47`）。本系统 52 笔订单最后一笔在 2026-07-23，之后没有发过单 |
| 归因 | `trader/reconcile.py` 新 `attribute()` | 截止日后，既无 `ats:` 标签、又按 permId/订单号+日期对不上系统单的成交记为 `manual`（`link_confidence=untagged_after_cutoff`），不登记异常。带 `ats:` 却对不上的仍记无法归因；截止日前不变 |
| 补录 | 新 `trader/statement_import.py`，命令 `ats clerk import-statement --file <pdf> [--dry-run]` | 只取“股票”段的执行明细（期权、外汇跳过：`fills` 只按标的记，不区分合约）；美东时间按夏令时换 UTC；`stmt:` 内容哈希作执行编号，重复导入无效果；与 TWS 已抓成交（同标的/方向/股数/价格、2 秒内）重复的跳过；归因走同一个 `attribute()`；报告没有已实现盈亏，补录腿留空 |
| 校验 | 同上 `verify()` | 每个“标的 × 方向”的股数必须与报告“总数”行完全一致，金额和佣金差额不超过逐行四舍五入（每行 0.005）；任一不符或有解析不了的交易行，整批不写 |
| 回合重建 | `journal/episodes.py` `_drop_superseded` | **计划外，补录暴露的既有缺陷：** 回合编号取自开仓腿，新种子或补进更早的成交会换编号，但重建只 upsert 不删旧行，旧行成了“幽灵持仓”（副本上 53 行里 19 行是旧的；有标的同时挂着 3 个未平仓回合）。现在重建某标的时删掉该标的本次没产出的回合；有持仓快照时，已不持有、也没有成交的独立种子一并删除。回合是可从成交重建的派生读模型 |

**副本上的结果（`var/switch_stmt/import.log`、`episodes_after_fix.log`）：**
- 演练：执行明细 93 条，32 组与总数行一致；跳过期权 38 条、外汇 10 条；与 TWS 已抓的 10-28 三笔重复 3 条；归因 manual 90；写入 0。
- 实写：写入 90 条，全部 `manual`，无新异常；再导一次写入 0。
- 回合重建（读 TWS 持仓，只读）：53 → 34 行（删掉 19 个旧行），再跑一次再删 1 个（CRDO 已不持有的种子），第三次 0，稳定。每个标的最多一个未平仓回合；`pre_tracking` 种子从 18 个降到 2 个（BRK B、QQQ，都是没有任何成交记录的持仓）。

**测试：** 新增 `tests/test_statement_import.py`（9 个，合成报告，不含真实账户）；`test_reconcile.py` 新增 4 个；`test_episodes.py` 新增 2 个。原 3 个依赖“今天的无标签成交=无法归因”的用例，把成交日期挪到截止日前，继续覆盖旧规则。变异检查：去掉截止日分支，4 个用例变红；去掉回合删除，对应用例变红。回归：27 个相关测试文件 360 通过、4 失败（`var/test_runs/switch-statement-import.{log,xml}`）；4 个都是 `test_phase_f_stage_one` 的“未登记实现依赖”，列出的是其他会话未提交的文件（issuer_ir、pending_channel、trade_schedules 等），不含本次的 `statement_import.py`（已登记到 `freeze_surface.yaml` `src_tools`），与 3.5 记下的同一批，须在干净检出复测。

**发现的既有问题（未处理）：** TWS 的 `reqExecutions` 和持仓快照把期权、外汇按标的名记（例：08-31 的 `AAPL BOT 1 @37.5` 实为 AAPL 期权，`EUR` 外汇也成了一个回合；AXTI/COHR 当前的负持仓是卖出的看跌期权），与股票回合混在一起。补录已避开，但日常对账本身也有这个问题，需要另开 change。

**写生产（2026-10-29 你同意后执行，日志 `var/switch_stmt/prod/`）：**
- 先备份：`var/backups/ats.sqlite.before-statement-import.20261029T084537`（SQLite 在线备份，integrity_check ok；当时 fills 13、trade_episodes 29、ledger_exceptions 9）。
- 先确认 TWS 当前 `reqExecutions` 返回 0 条（只给当日会话），补录的 ≤10-28 成交不会与之后抓到的重复。
- 演练：93 条，32 组一致，重复 0（生产上没有 10-28 那三笔，副本上那三笔是此前 dry-run 写进去的），归因 manual 93。
- 实写：写入 93 条，全部 `manual`/`untagged_after_cutoff`，无新异常；再导写入 0。
- 回合重建（`ats journal episodes`，只读 TWS 持仓）：33 个回合，每个标的最多一个未平仓回合。
- 回退办法：用上面的备份文件替换 `var/ats.sqlite`（替换前须停 dispatcher）。

**你的进一步决定（2026-10-29）：** 外汇不记（只是跨市场换汇，不是投资标的）；期权要记录和对账（有方向、敞口、盈亏）。期权方案另行提交你审。

### 3.7 期权进账本、外汇不记（2026-10-29，方案经你批准；副本 `var/switch_opt/copy/`）

| 改动 | 位置 | 做法 |
|---|---|---|
| 合约身份 | `journal/episodes.py` “Instrument identity” 段 | 股票按 symbol（回合编号不变），期权按 OCC 代码（与对账单“总数”行同格式）。放进已登记的 episodes.py，不新增运行时文件：新增文件须登记进清单，会改清单摘要、作废已记录的证据行 |
| 成交表/回合表 | `memory/store.py` | `fills` 加合约类型、合约键、乘数、看涨/看跌、行权价、到期日、币种、合约编号、合约代码；`trade_episodes` 加合约键、合约类型、乘数。旧行为 NULL，按股票读 |
| 外汇 | `store.upsert_fills` | 所有写成交的路径都在这里丢掉 `CASH` |
| TWS 抓取 | `broker/ibkr.py` `get_fills` | 每笔成交带合约字段 |
| 旧成交分类 | 新表 `fill_classifications`，命令 `ats clerk classify-fill` | 记录谁按什么依据定的合约类型；成交行原有事实不改 |
| 对账单 | `trader/statement_import.py` | 期权段一并补录：按“合约 × 方向”核对总数，乘数取自金额/（张数×价格）；佣金按带符号记（对账单上正数是返佣）；股票行执行编号与第一次补录完全一致（已核对 93/93）；第一次补录的行补上合约字段（只填空值） |
| 回合 | `journal/episodes.py` | 按合约重建；持仓按合约匹配；期权券商均价按每张记，种子价除以乘数；到期日已过、已不持有、没有平仓成交的期权，在到期日收盘补一条价格 0 的合成平仓腿（`exit_reason=expired`，不落库、不带已实现盈亏） |
| 估值 | `journal/marks.py`、`execution/clerk.py` | 现价按合约取；期权不用标的日线算 MAE/MFE；浮动 R 乘上乘数 |

**副本结果（`var/switch_opt/import_classify.log`、`episodes.log`）：**
- 补录：131 条执行明细（股票 93、期权 38），64 组核对一致，跳过外汇 10 条；新写入期权 38 条，给已有 93 条股票行补了合约字段；再导一次写入 0 条。
- 旧成交分类（按方案）：AAPL 08-31 一笔、LITE 两笔记为“期权（合约不明）”，EUR 一笔记为外汇。
- 回合：50 个（股票 29、期权 21），第二次重建不变。**未平仓回合与 TWS 当前持仓逐个合约一一对上**（两个方向都没有多余），每个合约最多一个未平仓回合。AXTI、COHR 的看跌期权成了独立的空头回合，股票回合不再被拖成“空头”。
- 到期推断：本次 0 个。10-16 到期的 QQQ/SMH 看跌期权在 09-22 已有平仓成交。

**发现的两点：**
1. **AAPL 08-31 那笔应归到 AAPL 2027-03-19 300C。** 当前持有 1 张这个合约，均价每股 37.46；对账单里只有 09-08 买 1、10-26 卖 1。只有算上 08-31 这笔买入（37.5），持仓与均价才对得上。按“合约不明”处理时，这笔被种子补成一个虚构的空头回合，300C 的回合则要靠种子补一张。需要你确认是否改登记。
2. **8 月有成交缺口。** AXTI、COHR 股票各有一个“开在 08-03 / 08-12、当天就平”的种子回合。8 月 4 日到 31 日系统没抓成交，对账单又从 9 月 1 日开始，现有成交解释不了当前持仓，种子只能把差额放在最早一笔成交之前。导出一份 8 月的交易确认报告可以补上。

**测试：** 新增 `tests/test_instrument_ledger.py`（12 个，含外汇丢弃、同标的股票与看跌期权分开、期权种子按每股成本、到期推断与不推断、旧成交分类、期权不算 MAE、按合约取价）；`test_statement_import.py` 改 3 个、增 3 个（期权合约字段、返佣符号、股票执行编号与第一次补录一致、补合约字段）。变异检查：持仓按 symbol 匹配或关掉到期推断，5 个用例变红。回归：36 个相关文件 475 通过、4 失败（`var/test_runs/switch-options-ledger.{log,xml}`），4 个仍是 `test_phase_f_stage_one` 的“未登记实现依赖”，所列文件不含本次改动。

**写生产：** 未做，后续见 3.8。

### 3.8 8 月交易记录补录与旧成交认定（2026-10-29，副本 `var/switch_txn/copy/`）

**你提供：** 当前期权持仓截图（AAPL 2027-03-19 300C 1 张、QQQ 2027-01-15 680/630 熊市看跌价差、COHR 2026-11-20 210P −2、AXTI 2026-11-20 50P −5），以及 8 月 3—31 日的 Transaction History CSV（原件存 `var/broker_statements/`）。

| 改动 | 位置 | 做法 |
|---|---|---|
| CSV 补录 | `trader/statement_import.py` `parse_transactions`，同一命令 `ats clerk import-statement --file <csv>` | 只取“买/卖”行；外汇、股息、利息、费用跳过。核对：全部 75 行净额合计必须等于表头“变更”（−5,292.13，一致）；美元行“总额”必须等于数量 × 价格 × 乘数。总额和佣金是美元，非美元行用自身比值折回交易币种（7709 佣金折回 18 港元、HY9H 3 欧元，与 PDF 口径一致）。期权由代码列的 OCC 码直接识别 |
| 只有日期 | 同上 | 文件按新到旧排列；同一天的成交按旧到新放在美东 12:00 起每行加 1 秒，只保证当天先后顺序。执行编号前缀 `stmt:txn:` |
| 旧成交认定 | 同上 | TWS 早先抓到、没带合约字段的成交，若与 CSV 某行同日、同方向、同数量、同价格，就不重复导入；那一行写明是期权或外汇时，记为该成交的分类（`decided_by=broker_record`），可覆盖此前“合约不明”的判断 |

**副本结果（`var/switch_txn/import.log`、`episodes.log`）：**
- 9—10 月 PDF：期权 38 条写入，股票 93 条补合约字段。
- 8 月 CSV：57 条成交（股票 40、期权 17），与 TWS 已抓的重复 7 条（AXTI×3、AVGO、COHR、LITE 720P、AAPL 300C），新写入 50 条；再导写入 0、不再重复认定。
- 按券商记录认定 3 笔旧成交：AAPL 08-31 → **AAPL 2027-03-19 300C**；LITE 08-12 → **LITE 2026-09-18 720P**；EUR 08-12 → 外汇（不记）。只剩 LITE 07-23 那笔（7 月，无券商记录）按方案记“期权（合约不明）”。
- 回合：59 个（股票 31、期权 28），重建两次结果相同。**未平仓回合与 TWS 持仓逐个合约对上，与你的截图一致**；按观测成交建的回合，入场价与券商均价只差佣金（例：AAPL 300C 37.45 对 37.46，AXTI 50P 4.80 对 4.803）。
- 到期推断 3 个，都是 08-21 到期：LRCX 340P、LITE 700P、COHR 240P。
- 原先的 AXTI、COHR 股票“当天开平的空头”种子回合消失了。还剩 15 个种子回合，都是 8 月 3 日以前就有的持仓（GOOG、7709、ASML、SPCX 等，以及 HY9H、MRVL、NVDA、TSM、MSFT 的期初部分）。

**存疑一处：** LRCX 2026-08-21 340P 于 08-18 以 20.01 买入，当时 LRCX 约 311，到期时多半是价内，应被行权而不是作废。CSV 里没有行权或指派的行，现在按“到期作废”推断（价格 0，标 `expired`），可能不对。如果券商有 8 月 21 日前后的行权记录，可以据此更正。

**测试：** `test_statement_import.py` 新增 4 个 CSV 用例（解析与现金核对、币种折算、当天顺序、核对不符整批不写、按券商记录认定旧成交并覆盖“合约不明”、重导无副作用）。回归：36 个相关文件 479 通过、4 失败（`var/test_runs/switch-txn-import.{log,xml}`），4 个仍是 `test_phase_f_stage_one` 的“未登记实现依赖”。

**写生产（2026-10-29 你同意后执行，日志 `var/switch_txn/prod/`）：**
- 备份：`var/backups/ats.sqlite.before-options-txn.20261029T102847`（integrity_check ok；当时 fills 106、trade_episodes 33、ledger_exceptions 9）。写前确认 TWS 当日成交 0 条。
- 9—10 月 PDF：期权 38 条写入，股票 93 条补合约字段。
- 8 月 CSV：新写入 50 条，跳过与 TWS 重复的 7 条；按券商记录认定 AAPL 300C、LITE 720P、EUR 外汇 3 笔。
- `classify-fill`：LITE 07-23 记为“期权（合约不明）”。
- 两份都再导了一次，写入 0、认定 0。没有新增异常（仍是原有的 9 条 `broken_link`）。
- 成交：股票 128、期权 53，另 13 条早先 TWS 抓的旧行没有合约字段（按股票读，其中 4 条由分类表认定）。
- 回合重建并估值：59 个（股票 31、期权 28，到期推断 3 个），再重建一次不变。**未平仓回合与 TWS 持仓逐个合约一一对上**，每个合约最多一个未平仓回合。结果与副本一致。
- 回退：停 dispatcher 后，用上面的备份文件替换 `var/ats.sqlite`。
- 仍存疑：LRCX 340P 按“到期作废”推断，可能实际被行权（见上）。

### 3.4 LIVE-X（任务 3.3）

**调查结论（2026-10-21 只读调查，design.md D9-1）：** `LIVE-X` 很可能是开发时的冒烟测试误写进了生产库，不是你签发的授权。依据：
- 字段全是占位值：授权人 `o`、签发人 `i`、账户 `U1`（IBKR 真实账户是 U 加 7 位数字）、有效期到 2099 年、备注与记录人为空。
- 写入时间 2026-10-07 11:24（北京时间），比 `live_authorisation.py` 首次提交（同日 19:29，`02c81f2`）早 8 小时，正处在这个模块的开发过程中。库文件 `var/phase_f_live_authorizations.sqlite` 的修改时间也停在 10-07 11:24。
- 写入函数的缺省路径就是这个生产库，同一时刻没有其他库被写。
- 10-07 当天有记录的 Codex 会话从 13:18 UTC 才开始，只发现过它、没有写过它；当天没有 Claude 会话。

你的确认（2026-10-21）：“这不是我签发的实盘授权，同意作废。”

**作废记录（2026-10-29，日志 `var/switch_3_3/livex_void.log`）：**
- 先备份授权库：`var/backups/phase_f_live_authorizations.sqlite.before-livex-void.20261029T110021`（integrity_check ok）。
- 作废前，`ats live gate --reference LIVE-X` 拒绝，理由是“授权来源未核实”（2.9 起所有既有记录一律视为未核实）。
- 执行 `ats live void --reference LIVE-X --confirmed-by boss --confirmation "这不是我签发的实盘授权，同意作废。" --confirmation-source "Claude Code 会话，2026-10-21 LIVE-X 只读调查后用户回复；见 design.md D9-1" --confirmed-at 2026-10-21`。
- 作废后，`ats live gate --reference LIVE-X` 返回 `refused / live_authorisation_voided`：“已作废：LIVE-X 已于 2026-10-29T03:00:22Z 作废……原记录保留可读，但不再采信”。
- `ats live show-auth --reference LIVE-X` 原记录各字段不变，`verification_history` 多了 1 条作废记录。
- 作废只追加记录，不删原行。实盘锁本身不受影响，仍然锁定。

### 3.9 T0 补充证据（2026-10-29，提交 `09a36d0` 之后）

**T0.1 主理人决策输入**（生产只读副本 `var/switch_t0/copy/`，取于 11:02 台北时间；只绕过 S1 之前不存在的读路由闸门，血缘校验不绕过；没有建周期、没有调用模型）
- 渲染：`var/switch_t0/chief_context_render.md`（303,786 字符），快照 `var/switch_t0/chief_snapshot.json`，缺口报告为空。
- 逐段对照：研究快照 110 段（层级分析 8、信息简报 11、行业配置 1、基本面 78、宏观 1、技术面 11），**110 段在渲染文本里都带投影 ID 与 sha256**，全部是可复用的已发布投影。各类最新时间：基本面、技术面、信息简报 10-28；层级分析 10-25/26；行业配置 10-26；宏观 10-24。
- `ats retirement zero`（`var/switch_t0/retirement_zero.txt`）：`chief.legacy_table_direct_reads` 清零；主理人对 `sector_reviews`、`macro_reviews`、`technical_reviews` 的读取清零；`store.direct_trade_reads` 对主理人、`risk.assess`、`channel.context` 清零。仍未清零的是 `decision_roles.unwired_to_governed_read_api`（风控、Clerk），属 6.4（第 6 组）；其余“待补证”都是命令行与旧决策写路径，属第 6—8 组退役范围，不是 T0.1 的对象。
- 待你做：读一份渲染结果，确认内容是你认可的研究（文件含真实账户号，勿外传）。

**T0.2 驳回—修订样例**（隔离环境：FakeBroker、确定性风控状态，单笔上限临时设为 25,000，种子提案 NVDA 买 50,000；`var/switch_t0/t0_2_reject_revise_sample.json`）
- 第 1 轮（`394a93cc2c47`）：风控 `rejected`。违规是结构化的：`rule_id=max_single_order_usd`、`severity=hard`、`actual=49948.5`、`limit=25000`，说明“提案 $49,948 超出可接受上限 $25,000（反向建议，未裁剪）”；可接受边界 `per_symbol_max_notional={NVDA: 25000}`。
- 第 2 轮（`8b4de071afcd`，`parent_revision_no=1`）：主理人采纳边界，理由写明“[revise@r1] 采纳边界 $25,000”，金额 24,924；风控 `approved`。
- 批准绑定第 2 轮的 hash；只下了 1 笔单，金额 24,924（隔离环境的模拟成交）。第 1 轮记录未被改写：风控没有静默改单，是主理人新出一轮。

**T0.7 回退演练**（隔离；日志 `var/switch_t0/rollback/`）
1. 停用：在调度状态副本上对 `chief-daily`、`clerk-daily` 执行 `ats dispatch pause`，**都被拒绝：`workflow has no declared schedule owner`**。原因是交易路径工作流要到第一次恢复（S4/S5）才有调度归属记录，在此之前它们本来就不被注册（10:52 重启后 dispatcher 只注册 5 个研究工作流）。恢复后的停用由 `test_trade_schedules.py::test_resume_needs_the_approval_text_and_the_consumers_target_route` 覆盖。已在 OPERATIONS 第 7 节回退步骤补了一句说明。
2. revert：在独立 worktree 里对 `09a36d0`、`3ce04d6` 执行 `git revert --no-commit`，改动 90 个文件，无冲突。
3. 旧读取方读观察期决策：先用当前代码在隔离环境跑完一个周期 `chief-20261029-t07`（同 T0.2 场景，镜像写入旧 `cycles`/`decisions` 表），再用 revert 后的旧代码读这份库。`last_chief_run()`、`recent_decisions()`、`trades` 都能读到这个周期，不需要恢复数据。
   - **请你判断：** 镜像把**每一轮修订**都写进了旧 `decisions` 表。旧读取方会在一个“approved”的周期下列出两条 NVDA 买单（49,948.5 和 24,924），其中第一条是被风控驳回的那轮。第二条的理由带 `[revise@r1]`，可以区分。按 D2，镜像不带审批语义，授权从不读它。如果你希望回退后旧界面只看到最终一轮，需要改镜像写法。
4. 路由冻结核验（生产只读，`freeze_check_prod.log`）：没有安装路由、未冻结、提交回执 0 条、决策周期 0 个，没有未终结授权。`trades` 里有 10 条 7 月旧订单状态为 `expired`（7-04 两条、7-15 两条、7-17 六条，都是推断出来的过期）。路由切换的排空按周期检查，`expired` 按 2.7 归为“待只读对账确认”，这 10 条不属于任何新周期，不阻断。

**T0.6 订单追溯（干净 worktree）**
- 第一次在 `09a36d0` 的干净检出复跑：5 通过、2 失败、21 报错（基线 `173fac0` 为 28/28）。原因是第 2 组新加的“组合账本状态”“战绩反馈”两段用 `str(dict)` 渲染。实时读取时字典按插入顺序，从回放磁带读回时按键排序，同一份数据渲成不同文本，主理人模型请求的回放键因此不同。回放判“未录制的业务输入”，拒绝调用模型，主理人不出决策，后续风控为空而报错。定位办法：把回放失配的请求与录制候选逐项比对，只有 `prompt_hash` 不同；再导出两次主理人提示词全文对比，差异只在这两行的键顺序。
- 修复：提交 `fdc56a3`，`assemble.py` 新 `_canonical()`，字典与列表按排序键 JSON 渲染。新用例 `test_ledger_and_performance_render_the_same_whatever_the_key_order`，改回 `str()` 时变红。
- 复测：`fdc56a3` 干净 worktree，`test_phase_f_integration_evidence.py`、`test_phase_f_independent_runner.py` **28 / 28 通过**（11 分 41 秒；`var/test_runs/switch-t06-clean.{log,xml}`）。
- 测量条件：worktree 位于临时目录，`git status` 干净。`uv sync` 下载依赖卡住（onnxruntime 不在本地缓存），所以 worktree 的 `.venv` 链接到主仓库环境，并以 `PYTHONPATH=<worktree>/src` 运行；子进程用 `uv run --offline --no-sync`，不会改动主环境（事后核对：主环境的可编辑安装仍指向主仓库 `src`，site-packages 修改时间仍是 10-27）。导入路径已核对为 worktree。

**T0.8 全量测试（干净 worktree）**
- 条件：`fdc56a3` 干净 worktree，`pytest -q --tb=no -rA -p no:cacheprovider -p no:randomly -v`（与 `scripts/run_tests.sh` 的参数相同；依赖环境同 T0.6 的说明），2026-10-29 11:50—13:05，用时 1 小时 15 分。证据：`var/test_runs/switch-t08-full.{log,xml}`，逐项对照 `var/test_runs/switch-t08-vs-retire-final.txt`。
- 结果：**94 failed / 4586 passed / 9 skipped / 10 errors**。权威基线（退役变更 10.1，`f74d21a`）为 73 failed / 4507 passed / 10 errors。
- 逐项对照：
  - **新增变红 25 个：**
    - 20 个是 FactSet 本地测试（`test_factset_index_semantic_local` 18、`test_factset_core_guidance_count_local` 1、`test_factset_core_surprise_local` 1），报 `var/data_artifacts/...bin` 不存在。这些数据文件不进 git，干净检出里没有；同样 3 个文件在主仓库复跑 21/21 通过。属环境，与本变更无关。
    - 5 个属于本变更，已在提交 `aa174ac` 修复：`statement_import.py` 的 `"BUY"` 字面量违反动作词表约定；新表 `fill_classifications` 未登记归属；`test_order_tagging` 仍按旧规则断言；`test_intake_verification` 两个用例仍认为主理人未接受治理读取面（第 2 组的预期结果）。修复后这 5 个文件加 `test_chief_context` 在干净 worktree（`aa174ac`）126 通过。
  - **不再红 4 个：** `test_chief::test_run_chief_cli_executes_when_decisions` 与 `test_phase_d_e2e` 3 个，即 1.1 重写的 4 个决策路径夹具。
  - **只在基线里有的 21 个：** 第 2 组按 TEST_BASELINE 登记删除的 18 个测试函数（参数化展开后 21 例）。
  - **新增 130 个测试全部通过。** 9 个跳过都是“有真实数据文件才跑”的测试，干净检出没有这些文件。
  - 其余失败与报错和基线逐项相同，没有新增未归因失败。
- 因此 T0.8 的通过标准“交易路径相关测试全绿；其余与基线逐项对照，无新增未归因失败”在 `aa174ac` 上满足（全量在 `fdc56a3` 上测，之后的提交只改上述 5 处并已定向复测）。

### 3.10 T0.1 未通过项的修复与重渲染（2026-10-29，tasks 3.6，design.md D3-2）

**你的审阅结论（原文）：** 组合账本状态对不上，应该直接用 TWS API 取账户状态；风控状态不能接受 8-31 的版本，先基于准确的组合单独更新风控；研究快照应当精简，保留各分析师的输出，不应包含信息简报原文；绩效统计的内容和数值不知道怎么来的，无从判断。修复方案你回复“按推荐执行”。

**风控单独刷新（生产写，16:17）：** 先备份 `var/backups/ats.sqlite.before-risk-refresh.20261029T161725`，再 `ats risk report`（确定性，不调用模型）。新评审读 TWS 实时持仓：净值 $252,613、维持保证金 $77,553、剩余流动性 $171,513，与你的 TWS 截图一致。仍是 REPAIR_ONLY，破限只剩压测（-28.02% vs ≥-25%）；8-31 的“芯片设计层 23%”破限已不存在（现 17.8%）。

**代码修复：**
- 账户：`PORTFOLIO_DATA` 改为决策时读 TWS（`state_api.live_account_state`），上下文列出账户值、保证金和全部持仓（期权带到期、行权价、乘数、delta，成本按每股）。读不到就阻断周期，不用旧快照。
- 风控：决策图在同一份实时快照上先跑确定性风控评审并落库（`risk_officer.review.record_posture`），再渲染上下文。
- 研究：不再渲染信息简报（仍在冻结快照里），去掉簿记字段。渲染从 303,786 字缩到 100,441 字，研究部分 97,685 字。
- 绩效：回合的已实现盈亏在券商未给时按平均成本（含手续费）计算。生产重建前先备份 `var/backups/ats.sqlite.before-realized-rebuild.20261029T164824`，重建后 38 个已平仓回合里只剩 1 个持平：之前 35 个为 0 或空，已实现合计 -66.85。非美元回合（7709 港币、HY9H 欧元）按当地货币单列、不换算。绩效每项写明来源、窗口、未计入什么，净值快照过期时标明。

**重渲染（`var/switch_t0b/chief_context_render.md`，副本 `var/switch_t0b/copy/` 取于 16:51，账户读 TWS 实时，没建周期、没调用模型）：**
- 账户：净值 $252,564，现金 -$813，维持保证金 $77,297，剩余流动性 $171,712，持仓 21 项（期权 6 项）。
- 绩效：已平仓 38（盈 17 / 亏 20 / 持平 1），胜率 46%，美元回合已实现 -$3,561、盈亏比 0.67；港币回合 +17,744 港币。
- **这次渲染跳过了一道校验，原因与本次修复无关：** 12:09 GOOG 财报事件（FY2026Q3 发布第 3 版）触发了一份信息简报，其输入血缘与主理人例行计划要求的不一致，`decision_requirements.validate` 报 `fixed input/vintage lineage differs`。为了让你读到内容，只在这个渲染进程里跳过了这一道；真实周期今天会被它阻断。登记在第 5 节。

**测试：**
- 新增：实时账本渲染、读不到时阻断、口径标注、币种分列、三种已实现盈亏（均价、券商优先、卖出看跌后买回）、决策图阻断与同一快照。
- 交易路径各组回归：412 通过，证据 `var/test_runs/switch-t01-fix.log`。
- 读路径相关 32 个文件（`switch-t01-fix-wide.log`）：903 通过、9 失败。其中 6 个与 T0.8 基线完全相同；另 3 个是直接调用 `assemble.build()` 的旧断言（期望净值来自旧 performance 表、期望信息简报出现在上下文里），已按新规则改写，复跑通过。
- 其余会走到决策图的 14 个文件（`switch-t01-fix-graph.log`）：210 通过、3 失败。其中 2 个与基线相同。`test_phase_f_opinion_execution.py::test_actual_opinion_edges_and_published_lineage` 在基线 `fdc56a3` 是绿的，但在不含本次改动的 HEAD `d3f8c68` 干净 worktree 上同样失败：行业分析走到了 `ats.data.collection`，被判 `provider_or_raw_repository_bypass`。因此它与本次修复无关，是 `fdc56a3` 之后其他提交引入的，登记在第 5 节。

- T0.6 订单追溯在干净 worktree `7920970` 复跑：**28/28 通过**（`var/test_runs/switch-t06-clean-7920970.{log,xml}`，挂连接拦截插件，命中 0 次）。
- 提交 `7920970` 后已重启 dispatcher（PID 4871）与 serve（PID 4874），`/health` 正常。

**事件模式渲染（2026-10-29 22:00 副本 `var/switch_t0e/copy/`，账户读 TWS 实时，没建周期、没调用模型）：**
- **严格校验：被阻断。** 11 个标的里 5 个没有可用的事件评审（AVGO、COHR、LITE、MRVL、NVDA），缺口报告见 `var/switch_t0e/chief_snapshot_strict.json`。上下文为空。因此事件触发的周期（`pead-chief`）只有在全部标的都有新近事件评审时才能运行，财报季里实际跑不起来。
- **仅供阅读版**（`var/switch_t0e/chief_context_render.md`，103,525 字）：只在渲染进程里强制放开完整性门禁和血缘校验。事件评审 6 个标的 fresh（ASML、GOOG、LRCX、MSFT、SKHY、TSM），5 个标为 missing；这 5 个标的的例行预期更新在事件模式下完全不出现。
- 风控现算：新增 `L1-止损 LYTE -26% vs ≥-25% → 强制 trim`，压测 -27.6%。

**测试事故（如实记录）：** 改动后第一次跑回归时，新的实时读账户代码路径在端到端测试里会构造真实的 IBKRBroker：原测试夹具只替换了 `tport.snapshot`。那次运行在进入端到端用例后被我停掉，期间很可能以只读方式连过一次本机 TWS（7496），读了账户和持仓。没有任何下单路径（下单走 FakeBroker），风控结果写进的是测试临时库，不是生产库。之后用连接拦截插件复现确认会尝试连接，并做了两处修复：
- 夹具补了假账户；
- `tests/conftest.py` 加了全局护栏 `_never_reach_tws`，任何测试连 7496/7497/4001/4002 都直接失败。

修复后用拦截插件复跑，命中 0 次。

### 3.11 基本面按标的取模式、事件周期由触发标的发起（2026-10-29，tasks 3.7，design.md D3-3）

**起因：** 你审阅事件模式渲染时指出：事件触发的主理人评审不要求所有标的都有评审，有什么标的有事件就可以评审。实现上的偏差在于，规格写的是“例行或事件二者之一满足”，实现却是整个周期二选一。

**实施中两处修正（与 D3-3 原方案不同，请复核）：**
1. “取较新的一份”改为“事件评审在有效期内优先”。例行更新一天跑多次（当天最后一次 13:28），总比事件评审新；按原规则，GOOG、MSFT、SKHY 都会被判成例行。
2. 事件评审还要求它所依据的信息简报仍在 72 小时内。快照选取的是该份简报，而不是最新的简报。不这样做的话，今天 GOOG 会在来源一致性校验处失败（事件评审依据 10:24 的简报，13:22 又来了一份新的）；TSM 也会失败（10-26 的事件评审所依据的简报已超过 72 小时）。

**重渲染（副本 `var/switch_t0d/copy/` 取于 22:20；账户读 TWS 实时；只绕过 S1 之前尚不存在的读路由，血缘校验与完整性门禁均不绕过；没建周期，没调模型）：**
- 例行模式 `var/switch_t0d/chief_context_render_routine.md`（99,494 字）：
  - 通过，缺口报告为空；
  - 基本面 11/11 个标的可用：GOOG、MSFT、SKHY 用事件评审，其余 8 个用例行更新；
  - TSM、ASML、LRCX 10-26 的事件评审所依据的简报已过 72 小时，回到例行更新。
- 事件模式 `var/switch_t0d/chief_context_render_event.md`（99,533 字）：
  - 通过，开头列出“本轮事件标的：GOOG、MSFT、SKHY”；
  - 其余标的照常用例行更新，本次没有“无新近基本面”的标的。
- 之前登记的“事件触发的信息简报让主理人周期被阻断”（§5 第 10 项）由此修复，这两份渲染没有跳过血缘校验。

**测试：**
- 新增 `tests/test_fundamental_per_target.py` 14 例：
  - 计划按标的分派、覆盖不全时拒绝、单标的配置血缘；
  - 例行周期读刚出财报标的的事件评审；
  - 事件周期只要求触发标的、其余缺失时标明；
  - 触发标的缺评审时阻断、无事件时阻断、例行周期过期照旧阻断；
  - 事件评审优先于之后的例行摘录、事件过期回到例行；
  - 选取下游依据的简报而非最新简报、简报过期则回到例行。
- 回归（33 个文件，`var/test_runs/switch-d33-regress.log`）：628 通过、19 失败，19 个与 T0.8 基线逐项相同（`test_read_cutover` 16 个、`test_phase_f_schedule_runtime` 1 个、`test_phase_f_research_gate` 2 个）。挂连接拦截插件，命中 0 次。
- T0.6 订单追溯在干净 worktree `cc0d197` 上复跑 **28/28 通过**（`var/test_runs/switch-t06-clean-cc0d197.{log,xml}`），拦截插件命中 0 次。
- 提交后已于 23:14 重启 dispatcher（PID 36980）与 serve（PID 36982），`/health` 正常。

### 3.12 信息简报有效期改为 120 小时（2026-10-30 用户裁决）

- 用户原文：“我认可。不过有效期 72 小时太短了，怕信息简报内容不够多，改成 120 小时吧。”
- 改动：`planning.FRESHNESS_SECONDS["information-brief"]` 从 72 小时改为 120 小时；例行更新仍为 72 小时，事件评审仍为 4 天。
- 影响：
  - 决策侧：事件评审所依据的简报在 120 小时内都可用，事件评审的窗口改由它自身的 4 天决定。
  - 调度侧：信息简报在数据版本不变时最多复用 120 小时，会少发模型请求，不会多发。
- 验证：只跑了直接受影响的 5 个文件，78 通过。新增两例：简报 100 小时仍可用、130 小时回到例行。按用户要求没有再做更广的回归和重渲染。

### 3.13 T0.2 审阅意见（2026-10-30 用户）

对 3.9 驳回—修订样例的结论：

| 问题 | 用户结论 |
|---|---|
| 驳回信息够不够主理人改单、要不要附规则出处 | 够，不需要附规则出处 |
| "风控只给边界、由主理人重新出一轮"的分工 | 认可 |
| 超过 3 轮转人工复核的上限 | 合适 |
| 各轮时间戳相同 | **须修复**。原话："每一轮，以及一轮内不同角色的交互，时间戳应该能体现串行和 loop 的关系。" |

用户另外指出两处欠缺：

1. 首轮通过、多次驳回后通过、超过 3 轮转人工复核、不行动这 4 条路径**也要有样例**，不能只靠自动测试。
2. 样例里的主理人是测试桩，看不出真实提案的样子、判断依据和推理过程。

审阅后核查到的情况（只读，未改代码）：

- **时间戳是生产代码的问题，不是测试造成的。** `src/ats/graph/chief.py` 写入周期、修订、风控审查、状态转换、不行动、人工复核时，`created_at` 一律传 `state.as_of`（本周期的决策基准时间），所以同一周期所有行都是同一个时间。另外，主理人提案和风控审查都在 `persist_decision` 节点里一起落库，即使改用写入时刻，两者也只差几微秒，体现不出"主理人提案 → 风控审查"的先后。
- **生产里的修订轮不是模型重新判断。** `chief_revise` 是确定性代码：被驳回的加仓单直接把金额改成风控给的上限，理由用固定模板"[revise@rN] 采纳边界…"；没有上限可采纳的单直接放弃。主理人模型只出第一轮。用户认可的"由主理人重新出一轮"，现在的实现是机械地采纳边界，不是模型复议。
- **模型版本没有留痕。** `persist_decision` 写修订时 `model_version` 固定传空字符串，真实运行也不会记录。
- **新路径在生产上还没有跑过真实周期。** 生产 `decision_revisions` 共 33 行，全部是从旧表迁入的（`revision_source=legacy_revision`，2026-07-15 至 09-02）。旧路径的真实提案有逐标的理由，例如 chief-20260717-035655：6 笔减仓，每笔都写明触发的风控规则、PEAD 分数、行业评审结论和 20 日动量。

修复与补样例的方案用户回复"按方案执行"（design.md D3-4，tasks 3.8）。

**修复：**
- 时间戳按各角色完成时刻落库；
- 有主理人模型的运行中，驳回后由模型重新决策；
- 修订记录模型配置和提示词 hash。

新增测试覆盖时间严格递增（`test_audit_rows_carry_each_roles_finish_time`）和模型修订路径（`test_chief_revise_with_a_chief_redecides_from_the_review`）。T0.2 相关 5 个测试文件 54 个测试通过；另外用到被改夹具 `FakeChannel` 的 3 个文件（`test_e2e_approval_chain`、`test_execution_authorization`、`test_phase_d_e2e`）27 个通过。按用户 10-30 的要求只跑直接相关的测试：一次交易路径大范围回归跑到约 70% 时停掉，当时有 3 个失败未定位，没有据此下结论。

**5 条路径样例**（隔离环境、测试桩主理人；`var/switch_t0g/p1…p5_*.json`，阅读版 `var/switch_t0g/t0_2_paths_reading.md`）：首轮通过、驳回一次后通过、多次驳回后通过（3 轮）、超过 3 轮转人工复核、不行动。每条路径的时间都按"提案 → 风控 → 状态 → 下一轮提案 … → 批准 → 执行"严格递增。

**真实主理人样例**（`var/switch_t0g/real_chief_sample.json`，阅读版 `var/switch_t0g/t0_2_real_chief_reading.md`）：
- 运行条件：
  - 生产库副本 10-30 09:36，在 `isolated_run` 中执行，券商写入已禁止；
  - 真实模型，只调用一次，答案已缓存复用；
  - 账户实时只读 TWS；单笔上限临时调为 8,000；美股休市，报价用合成的最近收盘价；审批代为拒绝，dry_run。
- 结果：主理人提出清仓 GOOG、COHR、AVGO、LYTE，四笔都用 `target_weight=0` 表达。订单规范化报 `quantity_or_budget_missing`，周期直接转人工复核。风控规则审查和修订轮都没有发生。
- 由此发现的问题，待用户决定修法：
  1. 订单规范化把 `target_weight` 当成"权重 × 净值"的预算，而不是与当前持仓的差额；0 被当成空值。用目标权重表达的减仓、清仓一律走不通。
  2. 主理人提出卖 COHR 正股，但账户只有 COHR 卖出看跌期权。
  3. 规范化失败不退回主理人修订，直接转人工复核。
  4. 人工复核原因写成"1 轮修订后仍被驳回"，与实际不符。
  5. 理由没有填 `references`。
- 运行环境中的两处说明：
  - 只设 `ATS_BROKER_WRITE_PROHIBITION=1` 不会安装券商写入禁令，必须走 `isolated_run`。10-29 的渲染脚本只设了环境变量，但它们不建周期、不下单。
  - 脚本退出时进程以 138 退出，发生在打印周期号之后，推测是 IB 连接关闭时的问题，未影响落库。

### 3.14 真实周期：完整修订与风控审查（2026-10-30，tasks 3.9，design.md D3-5）

**修复范围：** 见 design.md D3-5。只跑了直接相关的测试：
- 主理人图、循环、上下文、快照、按标的基本面、审批命令行、风控审查、审批链、规范化：全部通过。
- 用到主理人输出结构的两个阶段 F 文件：17 通过，2 失败。失败的是 `test_actual_opinion_edges_and_published_lineage`（第 5 节已登记的既有红）和 `test_complete_business_execution_and_recovery[50000-False]`，两者都是行业分析触发 `provider_or_raw_repository_bypass`，与本次改动无关。

**真实周期** `chief-t02real-20261030-035009`：

| 项 | 内容 |
|---|---|
| 数据 | 生产库副本 10-30 11:17，在 `isolated_run` 中运行 |
| 真实部分 | 持仓（实时只读 TWS）、例行研究快照、主理人模型、生产风控规则与单笔上限 $25,000 |
| 人为部分 | 美股休市，报价用持仓最新市价并标为合成；审批代为拒绝；dry_run |
| 导出 | `var/switch_t0h/real_cycle_full.json`；阅读版 `var/switch_t0h/t0_2_real_cycle_reading.md` |

经过：
- **r1：** 主理人提出清仓 LYTE（止损破限）、卖 FORM 37 股（压测超标）、卖 GOOG 28 股（技术面 0/7）。复查了 17 个持仓，权衡了 8 个来源，列出 5 个考虑过但没做的交易，并建议平仓 COHR、AXTI 卖出看跌。
- **风控 r1：** 驳回 GOOG，放行 LYTE、FORM。
- **r2：** 模型重新决策，放弃 GOOG，保留 LYTE、FORM。
- **风控 r2：** 通过，压测利用率从 1.014 降到 0.990。
- 之后进入待审批，被代为拒绝。各行时间严格递增。

**过程中的模型调用（共 4 次）：**
1. 输出被截断，未得到答复。随后调整了 chief 的输出上限。
2. 网络中断，没有收到响应。
3. 得到答复，但持有天数为 0 导致整轮报错，答复丢失。是我的脚本把缓存放在了错误的层级；随后修了计划字段容错，脚本改为缓存原始答复。
4. 成功，第 1 轮的答复来自这一次，后续复用。

修订轮又调用了一次。

**这次运行暴露、尚未修复的问题（待用户决定）：**
1. **风控 r1 驳回 GOOG 的理由是"报价过期"，不是风控规则。** 规范化取的报价在逐单审查时已超过 30 秒有效期，前面几笔审查占用了时间。这是执行环境的问题，却以风控驳回的形式交给主理人，主理人因此放弃了 GOOG。同一份提案在 11:40 那次运行里，GOOG 是被风控规则驳回的（"修复态必须改善至少一项已破限指标"）。
2. **模型在期权建议里有事实错误：**
   - 称 COHR "股价 $234.42 远低于行权价 $210"，实际高于行权价，这张卖出看跌是虚值；
   - 把 AAPL 看涨期权的期权价格当成股价；
   - 到期日写成 `2026-11-20` 而不是 `YYYYMMDD`。
3. **引用质量：** r1 有 1 个快照里查不到的 ID（像是抄错了一位）；r2 的两笔订单没写引用。已记入 `reference_audit`。

**用户复核结论（2026-10-30）：** 原话："对于 T0.1 的其他四种样例，我认为可以通过。对于基于真实情况的 chief 的输出，我看了也还算逻辑正确，决策可接受（虽然 trader 不一定能解析成正确的订单）。"其中"T0.1 的其他四种样例"按上下文指 T0.2 的另外 4 条路径样例（3.13）。T0.2 记为通过；上面两项遗留和"Trader 能否正确解析订单"作为后续事项保留，不阻断 T0.2。

### 3.15 T0.3 命令行审批：隔离沙箱亲手跑（2026-10-30，tasks 3.4）

**场景。** 生产库的只读副本（`var/switch_t0i/copy`，2026-10-30 12:44 取）加隔离运行，券商写入禁止、实盘三道锁照旧。三个等待审批的周期由真实决策图产生（待审批通道、演练模式）。沙箱的几处替身，读结果时要记住：

- **主理人是替身：** 第一轮是 T0.2 缓存的真实回答；风控驳回后替身撤下被驳回的标的；`revise` 时把你的意见原话引进摘要和第一笔订单的理由，使修订 hash 变化。所以 `revise` 只验证流程，不验证"模型按意见改单"。
- **报价是替身：** 休市，用 TWS 持仓的最近收盘价；并且仅在沙箱里放宽了"合成报价超 30 秒算过期"（TWS 读取超时使风控阶段超过 30 秒，这是 3.14 的遗留缺陷 1）。
- **提醒不外发，** 终端只打印"沙箱：提醒不外发"。
- 脚本在 `var/switch_t0i/`（`t03_ats.py` 包装命令，`t03_env.py` 替身，`t03_make_cycles.py` 造周期）。

**逐项对照（你亲手执行、粘贴结果；我对照审计表复核）：**

| # | 操作 | 周期 | 结果 |
|---|---|---|---|
| 1 | `pending` | — | 符合：列出周期、hash、风控结论、快照、已等多久 |
| 2 | `show` | a | 符合：修订 hash、订单（卖 LYTE、FORM、GOOG）、主理人摘要、期权建议、风控结论、组合快照时间、"批准后不会下单：演练模式；实盘锁定" |
| 3 | 8 位 hash 批准 | a | 符合：`修订 hash 前缀至少 12 位（收到 8 位）` |
| 4 | 旧 hash 批准 | b | 符合：`hash 不匹配：当前是修订 3，hash 前缀 c68b541bc497` |
| 5 | 正确 hash 批准 | a | 符合：终态 `approved_not_executed:dry_run`，无订单 |
| 6 | 重复批准 | a | 符合：`已批准过，首次结果：approved_not_executed` |
| 7 | 对已终结周期再答复 | c | 符合：`已终结（approval_rejected），不能再审批` |
| 8 | `revise` 不带意见 | b | 符合：命令行报缺 `--note` |
| 9 | `revise` 带意见 | b | 符合：新修订重新过风控并回到待审批 |
| 10 | 第 3 次 `revise` | b | 符合：`已用完 2 次驳回修改，只能 approve 或 reject` |
| 11 | `reject` | c | 符合：`approval_rejected（dry_run）`，备注保留；重复拒绝返回第一次结果和备注 |
| 12 | 新周期取代旧周期 | b | 符合：b 变 `superseded`，再批准报`已被新周期取代（失效）`。`chief run` 命令最后崩溃是沙箱替身缺陷（静音通道顶替了审批通道，已修），不是产品缺陷 |

**修订号为什么跳着走（审计表核实）：** b 的 `revise` 之后看到修订 3、再之后修订 5。每次 `revise` 实际走两步：你的意见产生修订 2（4），风控驳回它（GOOG：`marginal_policy`，修复态必须改善至少一项已破限指标），主理人按驳回结论再出修订 3（5），通过。这是设计的行为。

**`approve` 耗时 115.6 秒的构成（a 周期，审计时间加分段计时 `var/switch_t0i/approve_profile.txt`）：**

| 阶段 | 约耗时 | 说明 |
|---|---|---|
| 取新组合快照 | 12 秒 | 连接 TWS；每次连接都做一次"已完成订单"同步，TWS 常不应答而超时 |
| 同一修订的确定性重审 | 52–82 秒 | 主要是联网取数：逐持仓取 beta、基本面轻量数据、期权信息、历史收盘价（yfinance），SSL 连接被断后重试退避；睡眠退避累计占大头 |
| 记录审批、恢复图、写报告 | 约 20 秒 | 含研究快照读取 |

重审落库时间距新快照 82 秒，超过了设计里 60 秒的快照新鲜度预算。演练周期不走 Trader 的执行前检查，所以没有被拦；真实周期会不会被拦，由 T0.4 验。

**用户提出的体验问题（待定，不阻断 T0.3）：**

1. 耗时过长（上表）。
2. 日志噪声：`consumer_read` 的 JSON 日志、TWS 的 `Error 300`（`Can't find EId with tickerId`）、行情源 SSL 告警。其中 `Error 300` 是取消已不存在的行情订阅，无害；"已完成订单超时"和行情源 SSL 失败是真信号，且正是上面慢的原因。
3. `show` 的"研究快照引用"约 80 行，订单理由被截断在 100 字符的半句处。
4. 短 hash 的报错不说出当前 hash。
5. 日常使用要求手敲 12 位 hash，交互不方便。

**修复（design.md D3-6，tasks 3.10，用户 2026-10-30 回复"按建议做"）：**

- **耗时：** 风控用的 beta 跨进程缓存一天；`approve` 在取新快照前先预热。沙箱里同一修订的重审：缓存冷时 60.6 秒，热时 22.7 秒（`var/switch_t0i/approve_profile_after{1,2}.txt`）。剩下的约 22 秒主要是历史收盘价下载（约 10 秒）和期权标的现价，这两项没有处理。取快照仍约 12 秒（连 TWS）。60 秒阈值没有放宽；批准全程耗时需要在真实周期上再量一次。
- **输出：** 审批命令丢弃读取追踪的 INFO 副本和 TWS 的 300 号错误，折叠"已完成订单超时"和重复的行情源失败并在末尾计数。
- **show：** 研究快照引用默认折叠为一行汇总，`--full` 看全部；订单理由超过 120 字符以省略号结尾；短 hash 的报错带当前修订的 hash 前缀。
- **免输 hash：** 终端里 `approve / revise / reject` 可不带周期和 `--revision`，看审批卡后输入完整动词确认，答复绑定卡上的 hash；没有终端仍须写明。
- **测试：** `test_beta_cache.py`（6 例）、`test_approval_cli.py` 新增 9 例并改 1 例；与改动有关的 13 个用例（含重审和 show）、`test_risk.py` 的 beta 用例都通过。没有重跑 `test_approval_cli.py` 里与改动无关的其余 13 例，也没有跑阶段 F 端到端文件（改动不在其路径上）。**这些修复还没有在沙箱里让你亲手再跑一遍。**主理人的期权建议里有事实错误（AAPL 现价写成 $62.10 等），是 3.14 遗留缺陷 2，T0.3 无新增。

## 4. 自动证据：隔离环境测试（tasks 3.1）

**测量条件：** 提交号 `173fac0` 加工作区未提交改动；日期 2026-10-28；命令 `uv run pytest -q --tb=short -p no:randomly <文件> --junitxml=var/test_runs/switch-3.1-<组>.xml`；日志在同目录同名 `.log`。**未提交的工作区，最终提交后要复测。**

| 组 | 对应 | 文件 | 结果 |
|---|---|---|---|
| t02 | T0.2 | `test_chief_loop`、`test_chief_graph`、`test_decision_risk_review`、`test_approval_narrowing`、`test_chief` | **52 通过** |
| t03 | T0.3 | `test_approval_cli` | **18 通过** |
| t04 | T0.4 | `test_approved_not_executed`、`test_execution_authorization`、`test_authorization_lifecycle`、`test_phase_f_trader_a`、`test_live_authorisation_provenance` | **128 通过** |
| t05 | T0.5 | `test_clerk_compensation`、`test_clerk_e2e`、`test_clerk_ledger_store`、`test_clerk_linkage`、`test_clerk_orchestration`、`test_reconcile`、`test_ledger_rebuild`、`test_journal_ledger`、`test_journal_idempotency` | **77 通过** |
| t07 | T0.7 | `test_route_single_active`、`test_route_switch`、`test_live_route_drill`、`test_trade_schedules`、`test_schedule_owner_switch` | **105 通过** |

**最终提交复测（2026-10-29，干净 worktree `aa174ac`；证据 `var/test_runs/switch-final-<组>.{log,xml}`、`switch-t06-clean-aa174ac.{log,xml}`）：** t02 **52 通过**；t03 **18 通过**；t04 **128 通过**；t05 加入 `test_statement_import`、`test_instrument_ledger`、`test_episodes` 后 **132 通过、1 跳过**（跳过的是“有生产库副本才跑”的用例）；t07 **105 通过**；T0.6 两个文件 **28 / 28 通过**。上表数字以此为准。

**没有覆盖的部分（不要把上表当成该项已通过）：** T0.1 的渲染材料、T0.2 的驳回—修订样例、T0.5 的生产对账演练、T0.6 的 22 项订单追溯（须干净检出）、T0.7 的回退演练、T0.8 的全量、T0.9 的文档走查。T0.3 已由你在沙箱亲手跑完（3.15），T0.9 需要你亲手跑。

## 5. 待你决定的事项

**2026-10-29 你离开期间整理的待办（回来一并处理）：**

1. **T0.1、T0.2 需要你读：** 渲染结果 `var/switch_t0/chief_context_render.md`（含真实账户号，勿外传），驳回—修订样例 `var/switch_t0/t0_2_reject_revise_sample.json`（3.9 有摘要）。
2. **T0.9 需要你亲手跑一遍（T0.3 已在沙箱跑完，见 3.15）。** 只读的 `ats approval pending/show` 可以随时在生产上跑（现在没有待审批周期，会显示空）。要完整走 `approve/revise/reject`，需要一个处于 `pending_approval` 的周期。可选两种：
   - 我在隔离沙箱里造几个待审批周期给你练手。注意沙箱里的 `approve` 会读真实 TWS 持仓做重新风控，三道实盘锁照旧挡住提交。
   - 放到 S3 之后、S5 之前，用一次真实的 `ats chief run --channel pending --live` 来走。这一步会调用模型、在生产库写周期，与下面第 3 条相关。
3. **T0.4 的生产一半**（见下一条原文）：放进观察窗口第一次真实批准，还是 S3 后单独做一次演练周期。我倾向前者。
4. **T0.7 回退时旧界面的显示：** 镜像把每一轮修订都写进旧 `decisions` 表，回退后旧界面会在一个“已批准”的周期下列出被驳回的那一轮。可接受，还是要改成只镜像最终一轮？
5. **LRCX 2026-08-21 340P：** 按“到期作废”推断，但买入时价内约 29 美元，可能实际被行权。有券商的行权/指派记录可以更正（3.8）。
6. **TWS 自动重启时间：** design 的 Open Question 要求 `clerk-daily`（16:30 纽约）避开 TWS 的自动重启时点。请告诉我你在 TWS 里设的自动重启/登出时间；它影响 S4，不影响 S1。
7. **一处未查明的运维现象（不需要决定，知会）：** 2026-10-29 11:48:55 dispatcher 日志多出一次启动，之前没有停止记录（launchd 的 KeepAlive 会在进程退出后自动拉起），我没找到触发原因。主环境没被改动，dispatcher 现在正常。
8. **3.4 结论表：** 证据列我已填好指向，结论、核验人、日期留给你填。全部通过后我做 3.5（记录结论与提交号并提交 verification.md），之后才能进入 S1。
9. **（新）信息简报要不要移出“决策必需类别”：** 现在主理人不读它，但它过期仍会阻断决策周期（ARCHITECTURE §10.1 定义的六类之一，改动面约 24 个测试）。要移出吗？
10. ~~**（新，缺陷）事件触发的信息简报让主理人周期被阻断：**~~ 已修（3.11）。原文： 见 3.10。只要某个标的最新的简报来自财报事件，例行决策的血缘校验就不通过。S5 启用每日决策前必须修；修法涉及“事件产物能否被例行决策引用”的设计取舍，我先出方案给你审。
11. **（新）逐日净值与出入金：** 要算扣出入金的时间加权收益和真实回撤，需要逐日净值和出入金记录。建议你从 IBKR PortfolioAnalyst 或对账单导出（Flex 也可以），我做导入；之后由 Clerk 每日记录。风控的“回撤 -12.45%”也来自同一组旧快照（7-17 到 8-31 的最大回撤），在补齐之前不代表当前回撤。
12. **（新）已实现盈亏与 TWS 的对照：** 重建后美元回合合计 -$3,561、港币回合 +17,744 港币，只覆盖 7-17 以后的成交；TWS 的年初至今已实现 42,770 包含 7 月以前，两者对不平是预期的。要逐笔对平，需要年初以来的成交或 IBKR 的已实现盈亏报表。
13. **（新，知会）一个与本次无关的新红测试：** `test_actual_opinion_edges_and_published_lineage` 在 `fdc56a3` 之后变红（行业分析读到 `ats.data.collection`），见 3.10。它属于研究侧数据代码，不在本变更范围；要我另开会话查吗？


- **T0.4 的生产一半**（“锁定下批准一次演练周期”）要求真实批准一个周期。批准在实盘锁定下只会走到“已批准未执行”，不会有券商提交，但它仍会在生产库里写一条决策周期、一条批准记录。设计里 D1 说只读核验“不写决策、不下单”，两者有出入。要么把这一半挪到观察窗口的第一次真实批准里验证，要么你明确同意在 S3 之后、S5 之前做一次演练周期。我倾向前者。
- ~~**手工单的归类**~~ 已定（2026-10-29，见 3.6）。原文：**手工单的归类（2026-10-28 复测发现）：** 你在 TWS 手工下的单如果没有自填 orderRef，现在被记为“无法归因”，非 dry-run 时每笔登记一条 `unattributed_fill` 异常，计入账本完整度，主理人上下文会一直标“账本未完整对账”。它不进系统绩效（符合你的要求），也不阻断交易，但这个警示会被手工单长期占满。可选：本系统发出的单已经全部带 `ats:` orderRef 与 permId，可以把“某日期之后、既没有 `ats:` 标记也对不上本系统订单”的成交直接记为人工、不登记异常；之前的旧单仍记无法归因。这会改动先前“无证据即未知”的规则，需要你同意。
- ~~**拉取历史成交**~~ 已改用你上传的交易确认报告补录（2026-10-29，见 3.6）；Flex 暂不做。原文：**拉取历史成交：** TWS API 的 `reqExecutions` 只返回当前会话（TWS 里调设置后最多 7 天）的执行，补不了 9 月至今的缺口；要拉全量历史只能用 IBKR Flex Web Service（需要你在券商后台建查询、生成令牌，我不能代填）。拉回来的成交按上面的规则归类，不会默认归到系统决策。建议另开 change。
