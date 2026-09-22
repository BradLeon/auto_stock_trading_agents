## Why

`docs/TARGET_WORKFLOW_DATAFLOW.md` 把 Workflow 与 Dataflow 迁移拆成 Phase A–F 六个阶段：从「硬编码串行 scheduler + 单次通过的 Chief 决策 + 分析师互相读取观点」迁移到「Dispatcher 依赖编排 + 独立分析角色 + 不可变决策 revision + 确定性账本」。Phase A 是其余五个阶段的共同前提，原因有两条。

第一，**没有可重复的绿色基线就无法判断结构性迁移是否引入回归**。当前基线不绿，且失败集中在两类可解释的来源，都不是回归，但都会淹没真实缺陷。

- **依赖未同步**。项目已由 `uv` 管理环境（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`，CPython 3.12.12），但 `pyproject.toml` 声明的可选 extras 从未同步，导致调度、市场日历、LangGraph SQLite checkpoint、券商接口、交互通道等模块整体缺失。执行 `uv sync --all-extras` 后，2026-09-22 实测为 **121 failed / 1149 passed / 263 errors**；对比未同步时的 205 failed，通过数增加 92。
- **测量环境的删除配额**。pytest 在运行期清理临时编号目录（`cleanup_numbered_dir` → `rm_rf`），累计删除量触及沙箱守卫 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（`count: 50, threshold: 50, scope: turn`）后，守卫以 `SystemExit(1)` 打断 fixture setup；而 `tests/conftest.py:16` 的 **autouse** fixture `_isolate_db(tmp_path, ...)` 使每个测试都依赖临时目录，故障因此被放大为整库失败（实测 `22 passed / 1511 errors`，13 秒）。该现象**与 `--basetemp` 是否复用无关**——换全新目录同样复现。

因此本阶段的首要工作不是「修失败」，而是把测量条件固定下来：唯一环境入口、依赖范围、以及执行环境对结果的影响必须被记录，否则后续五个阶段的结构性迁移没有可比较的判据。

第二，**没有共享契约，B–E 阶段会各自定义接口并二次返工**。Phase B 的决策审批链、Phase E 的 Dispatcher 与事件日历、Phase D 的六个分析角色，都要读写同一套运行与投影结构。若此时不固定 `TaskProjection`、`WorkflowRun` 和 `TriggerContext`，三个阶段将独立发明互不兼容的字段与幂等语义。

第三，**兼容迁移本身就是一项必须被立规的约束**。设计文档 §12.4 规定所有 schema 变更只做 additive、不删旧表旧列、在新写路径稳定前保留旧读入口。这条原则若无登记机制，后续阶段将无法回答「哪套旧实现已可退出、退出条件是什么、旧写路径何时下线」。本阶段把「旧实现退役登记」立为契约（见 `workflow/legacy-retirement`），使每个后续阶段在开工时就必须声明退出面，而不是把兼容层无限期堆积。

本阶段只建立基线与契约，不改动任何分析结论、风险规则数值或交易路径。已知与设计文档的一处冲突：文档 §16 第 12 条声明「本次设计和文档实施不使用 OpenSpec」，本次经用户裁决以指令为准，改用 OpenSpec 承载实施规划；建议随本 change 一并修订该条。

## What Changes

- **把既有的 `uv` 环境入口固化为唯一取数/测试入口**。`pyproject.toml` 已声明 `schedule`（APScheduler + pandas_market_calendars）、`memory_persist`（langgraph-checkpoint-sqlite）、`broker`（ib_async）、`channel`（FastAPI）四组 extras，`.venv` 也确由 `uv` 托管；问题不在缺少入口，而在入口未被使用。本 change 把 `uv sync --all-extras` + `uv run pytest` 固定为唯一入口（写入文档与脚本），并要求记录测量条件（命令、依赖范围、执行环境对结果的影响），使基线可重建、可复核。
- **完成数据层写侧 cutover**（缺陷 ①，也是本阶段唯一具有系统性的修复项）。中性证据事实的写入仍指向 Workflow memory 的旧表 `evidence_observations` / `evidence_facts` / `evidence_fact_projections` / `evidence_failures`，而这些表已被 `TradingMemory._retire_data_tables()` 在初始化时主动 `DROP`，运行期抛 `sqlite3.OperationalError: no such table`。数据层已具备**逐列等价**的孪生表（`data_evidence_*`）与只读仓库，但缺少写入侧 schema 与写入接口——即数据层搬迁只完成了读侧。权威基线归因显示该项影响 **116 项失败（135 项中的 86%）**，波及 `chain`、`scheduler`、`data-products`、`document-assets` 等多条链路。本 change 补齐数据层写入侧并把写入路径改指过去；**不恢复旧表、不撤销边界决定**。
- **修复另外两处已确认漂移**（详见 Impact 的证据）：
  - PEAD action 词表与决策层词表不一致，且大小写漂移（规范值全小写，PEAD 报告路径使用大写 `BUY`）；
  - 期权到期资金桶边界为闭区间，导致不同到期日被并入同一桶，`≤30天` 桶把 0 天与 30 天到期合并计算。
- **建立 Agent 依赖与数据边界的机器可验守卫**。把「分析师互不读取彼此观点（两条明确依赖除外）」「Agent 不得直接调用 Provider」「Agent 观点不得回写共享事实」三条约束写成架构测试，使 Phase D 的角色重构有可执行的判据，而不是靠审阅发现。
- **定义三类契约**：`TaskProjection`（Agent 产出 envelope）、`WorkflowRunRequest` / `WorkflowRunResult` / `WorkflowTaskSpec`（Dispatcher 运行契约）、`TriggerContext` 与稳定幂等键（手动、定时、事件三种触发的统一表示）。本阶段只定义结构与校验，不实现 Dispatcher 本身。
- **立「旧实现退役登记」契约**：任何阶段宣告某套旧实现退出时，须经「墓碑登记 → fail-closed 读取门 → 两段式清除」三个环节，并登记退出条件、依赖消费方清零判据与验证手段。沿用结构化数据层已有的 `retired_sources` 墓碑模式，不另造机制。本阶段同时登记首批待退项与各自的目标阶段。
- **不包含**：Dispatcher 运行时、审批链状态机、Clerk、事件日历、六个分析师角色的职责重构。这些属 Phase B–E，各自独立成 change。

## Capabilities

### New Capabilities

- `workflow/run-contracts`：Dispatcher 面向的运行契约——`WorkflowTaskSpec`（`dependencies`、`trigger_modes`、`freshness_policy`、`retry_policy`、`resource_group`）、`WorkflowRunRequest`、`WorkflowRunResult`（含 `missing_requirements` 与 `terminal_status`），以及手动/定时/事件三类触发统一为 `TriggerContext` 并派生稳定幂等键的规则。
- `workflow/architecture-guards`：Agent 依赖与数据边界的可执行约束——分析师可读取的输入集合与两条唯一跨分析师依赖、禁止直接导入 Provider 适配器、禁止把 Agent 观点回写为共享事实；以架构测试而非人工审阅作为判据。
- `workflow/test-baseline`：环境的单一入口（`uv sync --all-extras` + `uv run pytest`）、测量条件的记录要求，以及「环境性失败与真实回归可区分」的判据——使完整依赖下的基线可重建、可重复、可解释。
- `workflow/legacy-retirement`：旧实现退出的统一机制与登记表——墓碑登记（同一标识不得同时存在于「在用」与「已退役」两处）、fail-closed 读取门（命中墓碑即返回显式退役原因，不得静默回退到旧路径）、两段式清除（默认只读干跑，显式确认方执行物理删除，并保留导出与审计备注）；以及各阶段待退旧实现的登记与退出条件。
- `agent/task-projection`：所有 Agent 共用的 `TaskProjection` envelope（`projection_id`、`agent_role`、`scope`、`as_of` / `valid_until`、`schema_version`、`input_refs`、`data_vintage_refs`、`model_version`、`prompt_version`、`payload`、`content_hash`、`status`），以及「payload 必须经角色专用 schema 校验、不得只保存无法机器解析的报告文本」的规则。
- `agent/action-vocabulary`：分析建议与交易决策共用的单一 action 词表——一处声明、统一大小写、`buy` / `add` / `hold` / `trim` / `sell` 五个规范值，且风险检查、券商下单映射与账本记录必须消费同一声明。
- `execution/option-survival`：确定性期权到期资金生存模型的桶语义——到期桶划分必须互斥且完整，每个到期日只落入一个桶；桶边界为半开区间，概率来源与名目金额口径需显式标注。

### Modified Capabilities

- `data/data-layer-architecture`：新增需求，明确**中性证据事实的全部写入路径**（观察名单 observe tier、`chain/sources`、`chain/articles` 与 `cli` 四条调用路径，收口于 `TradingMemory.save_observation()`）归数据层所有——必须写入数据层证据存储并保留原始文档版本与来源血缘，不得写入 Workflow memory 中已被退役的表。同时要求数据层补齐写入侧 schema 与接口，使读侧与写侧归属一致。Workflow memory 只保留含观点的分析结论与其对数据层血缘的引用。

## Impact

**受影响代码**

| 区域 | 文件 | 变化 |
|---|---|---|
| 环境与测试入口 | `README.md` 或等价文档、测试脚本 | 固化 `uv sync --all-extras` + `uv run pytest` 为唯一入口；记录测量条件 |
| 证据写入（收口点） | `src/ats/memory/store.py`（`save_observation`、`save_observation_failure`） | 写入目标由 Workflow memory 旧表改指数据层证据存储 |
| 证据写入（同族读取） | `src/ats/memory/store.py`（`observations`、`facts`、`fact_projections`、`projection_lineage`、`discovery_evidence` 回写等） | 同步改指数据层；旧表不再被读写 |
| 数据层写入侧 | `src/ats/data/stores/unstructured/platform.py`（当前仅 `data_document*` 有 writer schema） | 补齐 `data_evidence_*` 与 `data_task_projections` 的写入侧 schema 与写入接口 |
| 证据写入（调用方） | `src/ats/runtime/scheduler.py`（`_observe_window`）、`src/ats/agents/evidence/observer.py`、`src/ats/chain/sources.py`、`src/ats/chain/articles.py`、`src/ats/runtime/cli.py` | 调用契约保持，随收口点一并生效；核对四条路径无旁路写入 |
| Workflow 存储 | `src/ats/memory/store.py`（`_retire_data_tables`、`_SCHEMA`） | 边界归类复核；`task_projections` 未被 DROP，其旧列（`profile` / `profile_version` / `input_kind` / `input_ref` / `target_type` / `target_id`）的退出条件在本阶段**只登记不予删除** |
| action 词表 | `src/ats/schemas/decision.py`、`src/ats/schemas/pead.py`、`src/ats/schemas/journal.py`、`src/ats/agents/chief/outputs.py` | 收敛为单一词表声明 |
| action 消费方 | `src/ats/risk/checks.py`、`src/ats/risk/marginal.py`、`src/ats/risk_validator.py`（`agents/`）、`src/ats/broker/ibkr.py`、`src/ats/journal/calibration.py` | 改为引用统一词表；`broker` 的 `BUY`/`SELL` 是券商侧表示，保持独立但需显式映射 |
| 到期资金桶 | `src/ats/risk/assess.py`（`option_survival` / `ExpiryFundingBucket`） | 桶边界改为半开区间 |
| 退役登记 | 新增登记处与只读门；供后续阶段登记 | 本 change 新建机制并登记首批待退项 |
| 新契约 | 新增模块（`workflow` 契约与 `agent` 投影 envelope）、`tests/` 架构守卫 | 本 change 新建 |

**已确认的缺陷证据**

1. **证据写入路径与数据层归属不一致（系统性）**——`tests/test_chain_evidence.py:61` 断言 `store.save_observation(a) is True`，在 `src/ats/memory/store.py:1175` 抛 `sqlite3.OperationalError: no such table: evidence_observations`（同函数在 1221 行 INSERT 同一张表，并在 `evidence_fact_projections` / `evidence_facts` 上读写；这四张表均在 `_retire_data_tables()` 的 DROP 名单内，另有 `evidence_failures`）。同一根因的四个调用路径：`runtime/scheduler.py::_observe_window` → `observer.observe_document()`（记 `WARNING observe[amc] MU failed: no such table`，断言以 `KeyError: 'sym'` 失败）、`chain/sources.py:287`、`chain/articles.py:281`、`runtime/cli.py:1363/1379`。实测真实失败面：`test_chain_evidence` 15 failed、`test_chain_articles` 12 failed、`test_chain_report` 9 failed、`test_chain_kb_review` 8 failed、`test_chain_sources` 5 failed、`test_chain_induction` 5 failed。数据层已存在逐列等价的 `data_evidence_observations` / `data_evidence_facts` / `data_evidence_projections` / `data_evidence_failures`，读路径由只读的 `PlatformUnstructuredRepository` 提供（该模块 docstring 自述「写入方仍留在旧路径，直到各自的源 cutover 发布」），但 `src/` 中不存在这些表的写入侧 schema——即搬迁只完成读侧。
2. PEAD action 词表——`src/ats/schemas/decision.py:11` 为 `Literal["buy","add","hold","trim","sell"]`（5 值），`src/ats/schemas/pead.py:203` 为 `Literal["buy","trim","sell","hold"]`（缺 `add`），`src/ats/schemas/journal.py:121` 退化为裸 `str`；`tests/test_pead_graph.py:160` 以大写 `action="BUY"` 构造 `PeadRecommendation`，与全小写规范值冲突。
3. 期权到期桶——`tests/test_risk.py::test_sell_put_survival_separates_expiry_dates` 失败：`ExpiryFundingBucket(label='≤30天', through_days=30, expiries=['20260831','20260930'], full_notional=20000.0)`，期望 `10000`。闭区间把 0 天与 30 天两个到期日并入同一桶。

**基线实测（须连测量条件一起读）**

| 测量 | 命令与条件 | 结果 |
|---|---|---|
| 依赖同步 | `uv sync --all-extras`（57 秒） | 全部可选分组可导入：`apscheduler`、`pandas_market_calendars`、`langgraph.checkpoint.sqlite`、`ib_async`、`fastapi`、`chromadb`、`duckdb`、`pytest_asyncio` |
| 全量基线 | `uv run pytest`，`--basetemp` 指向当次新建目录，411 秒 | **121 failed / 1149 passed / 263 errors** |
| 对照：同步前的 venv | 同命令，extras 全缺 | 1057 passed / 205 failed / 271 errors |
| 受限执行环境复现 | 同一全量命令，换**全新** `--basetemp` | 22 passed / 1511 errors（13 秒）——删除配额被打满，`SystemExit` 打断 setup |

**结论与处置**：263 errors 不属于业务回归，其成因是执行环境的删除配额与 pytest 临时目录清理的交互（证据为守卫自身输出的 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`，且换全新目录仍复现）；121 failed 中包含缺陷 ① 的系统性失败面，以及待逐项归因的其余失败。**权威基线数字须在不受删除配额约束的环境中重测**，并按本 change 的 `workflow/test-baseline` 要求把命令、依赖范围与执行环境条件一并记录。设计文档 §15.1 记录的「111 passed / 11 failed / 8 errors」与全量实测不一致，属针对性子集运行；该节按本节结论回填。

**依赖与风险**

- 不新增运行时依赖，只把既有 extras 通过既定入口一次性装入。
- 数据层写侧 cutover 为**表名与库的重新指向**：两侧列结构逐列等价，不改列语义、不做破坏性变更，既有数据层只读消费方不受影响。
- 对已部署 SQLite 库的写入路径：改后写入落在数据层库，Workflow memory 侧不再持有证据事实，与该库既有的 `_retire_data_tables()` 边界一致。
- `data/data-layer-architecture` 采用 ADDED 而非 MODIFIED 需求，避免改动既有场景名（OpenSpec 1.13.0 场景名不可重命名）。
- 与后续阶段的接口：`workflow/run-contracts`、`agent/task-projection` 与 `workflow/legacy-retirement` 是 Phase B–E 的共用输入，Phase A 冻结后，后续阶段的字段增补应走 additive 迁移，旧实现退出须经退役登记。
