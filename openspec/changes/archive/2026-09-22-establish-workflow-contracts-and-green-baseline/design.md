## Context

动机与范围见 `proposal.md`；行为契约见 `specs/`。本节只记录塑造实现方式的现状与约束。

**现状（2026-09-22 实测）**

| 事实 | 证据 |
|---|---|
| Workflow memory 是单一 SQLite 库，`TradingMemory` 持有一个共享 `sqlite3.Connection`（`check_same_thread=False`, `timeout=30`） | `src/ats/memory/store.py:372` |
| `TradingMemory.__init__` 先 `executescript(_SCHEMA)`，再 `_migrate()`，最后 `_retire_data_tables()` 主动 `DROP` 38 张数据层表与 2 个视图 | `src/ats/memory/store.py:374-401` |
| 环境由 `uv` 托管（`uv = 0.9.25`，CPython 3.12.12）。可选 extras 必须经 `uv sync --all-extras` 装入；未同步时调度、市场日历、checkpoint、券商、通道相关模块整体缺失 | `.venv/pyvenv.cfg`；`pyproject.toml:24-49` |
| **数据层搬迁只完成读侧**：数据层已有逐列等价的 `data_evidence_observations` / `data_evidence_facts` / `data_evidence_projections` / `data_evidence_failures` / `data_task_projections`，读入口为只读仓库；但 `src/` 中没有这些表的写入侧 schema | `src/ats/data/stores/unstructured/platform.py:12-18`（自述「写入方仍留在旧路径，直到各自的源 cutover 发布」）；`tests/test_platform_unstructured_repository.py:24-41` 是当前唯一建表处 |
| 证据写入收口于 `TradingMemory.save_observation()`：它读写 `evidence_observations` 与 `evidence_fact_projections`，而这两张表（连同 `evidence_facts` / `evidence_failures`）都在 `_retire_data_tables()` 的 DROP 名单内 | `src/ats/memory/store.py:1167/1175/1212/1221`；DROP 名单见 `store.py:393-394` |
| 该收口点的四条调用路径：调度观察、`chain/sources`、`chain/articles`、命令行入口 | `runtime/scheduler.py:340-397`、`chain/sources.py:287`、`chain/articles.py:281`、`runtime/cli.py:1363/1379` |
| `task_projections` 表已存在且**未**被 DROP（属 Workflow memory 表），但形态与目标 envelope 不同：`projection_id, profile, profile_version, input_kind, input_ref, target_type, target_id, payload, created_at, expires_at` | `src/ats/memory/store.py:337-343`；`data/stores/ownership.py` |
| action 词表存在三处不一致声明 | `schemas/decision.py:11`（5 值）、`schemas/pead.py:203`（4 值，缺 `add`）、`schemas/journal.py:121`（裸 `str`） |
| 到期桶为**累积**语义（`active = days_to_expiry <= horizon`），边界闭区间 | `src/ats/risk/assess.py:227-240` |
| `tests/conftest.py` 有一个 **autouse** fixture `_isolate_db(tmp_path, ...)`，因此每个测试都依赖 pytest 临时目录——这是整库级故障的放大器，不是成因 | `tests/conftest.py:16-30` |
| 执行环境对文件删除设有配额（每轮 50 次）；pytest 运行期清理临时编号目录会触及该配额，守卫以 `SystemExit` 打断 fixture setup | 守卫自述输出 `SAFE_DELETE_BULK_CONFIRM_REQUIRED {"count":50,"threshold":50,"scope":"turn"}`；换全新 `--basetemp` 仍复现 |
| 已有可复用的退役机制先例：结构化来源的墓碑登记 + fail-closed 读取门 + 两段式清除 | `config/data/structured.yaml` 的 `retired_sources`；`catalog/structured.py:41-54/120-131`；`adapters/structured/registry.py:219-223`；`stores/structured/repository.py:952` |

**约束**

1. 本阶段是六个阶段中的第一个，其产出（契约）是 B–E 的输入，因此字段一旦冻结，后续只能 additive 演进。
2. 迁移期的兼容原则保护的是旧**读**入口，由读模型或适配层过渡。已被边界归类判定为数据层所有的表不在此列：它们的失效本身就是边界决策的结果，正确处置是补齐另一侧的归属，而不是恢复旧表（见 D3）。任何旧实现的最终退出都须经退役登记（见 D8）。
3. OpenSpec 场景名不可重命名为工具链限制，因此对 `data/data-layer-architecture` 采用 ADDED 追加需求，不动既有需求正文，避免丢失既有场景名。

## Goals / Non-Goals

**Goals**

- 使基线可经**既有的** `uv` 入口重建，把环境性失败与真实回归分开，并把测量条件随数字一并记录，使后续阶段的每次迁移都有可比较的判据。
- 冻结 `TaskProjection`、`WorkflowRun*`、`TriggerContext` 三类契约的结构与幂等语义，足以让 B–E 各自实现而不互相发明字段。
- 建立旧实现退出的统一机制与首批待退项登记，使兼容迁移不因缺少登记机制而无期限堆积。
- 把三条架构约束变成可执行检查，使 Phase D 的角色重构有机械判据。
- 消除三处已确认的缺陷（含补齐数据层写侧 cutover），且不改变分析结论口径、风险规则数值或交易路径。

**Non-Goals**

- 不实现 Dispatcher 运行时、依赖调度执行与并发执行（Phase E）。
- 不实现决策审批链、revision/risk review/boss approval 存储与状态机（Phase B）。
- 不实现 Clerk、账本与绩效归因（Phase C）。
- 不拆分或重构任何分析师角色（Phase D）。
- 不实现自动事件日历（Phase E）。
- 本阶段的三类契约只定义结构与校验，不接入实际调度。
- **不执行任何旧实现的物理清除**：退役机制在本阶段只建立并登记，删除动作留待各阶段在其退出条件满足后按两段式流程发起。

## Decisions

### D1 · 能力归域：三个新域 `agent/`、`workflow/`、`execution/`

`workflow/` 承载调度与运行契约（Dispatcher 契约、触发、测试基线、架构守卫、旧实现退役登记）；`agent/` 承载角色与投影契约（`TaskProjection` envelope、action 词表）；`execution/` 承载执行与账本侧行为（本阶段只有期权到期生存模型）。`sector/` 保留为产业链**领域知识**域，不再扩张为「所有角色」的容器；`sector/layer-analyst` 属角色契约，留待 Phase D 迁入 `agent/`。

**备选与取舍**：单域 `workflow/`（新增域最少，但「分析角色契约」与「确定性服务行为」并置，边界靠命名约束）；两域 `agent/` + `workflow/`（用「人 vs 机制」切分，会让审批链与 Dispatcher 混在一起）。选择三域是因为本设计后续阶段要按「有状态审批」与「无状态编排」分开处置，目录结构应提前反映这一区分。

### D2 · 投影 envelope 使用新表 `task_projection_envelopes`，不原地改造 `task_projections`

新建 **`task_projection_envelopes`** 表承载目标 `TaskProjection` 结构；既有 `task_projections` 与其读取方（`store.projection_lineage` 同时解析两个投影族、`tests/test_fact_projections.py`）保留为兼容读模型。两者在迁移期并存，新流程以 `task_projection_envelopes` 为通用引用层。注意 `evidence_fact_projections` 不在本项范围内——它随 D3 一并迁往数据层。

**理由**：既有表的键语义（`profile` / `target_type` / `target_id` / `input_kind` / `input_ref`）服务于证据事实投影，与目标 envelope 的 `agent_role` / `scope` / `data_vintage_refs` / `content_hash` 并非同一抽象；原地改列会让既有查询与测试同时失效。

**备选**：直接 `ALTER TABLE` 扩列（省一张表，但两套语义挤在一张表里，`NOT NULL` 与索引都会被拉扯）；直接在旧表上重建（破坏兼容读模型）。

**裁决记录**：设计文档 §12.2 原将 `task_projections` 列为统一 envelope 的承载表，与本决策互斥。已裁决保留本决策（新建独立表），§12.2 已随本 change 同步修订为 `task_projection_envelopes`，分歧关闭。

### D3 · 完成数据层写侧 cutover：证据写入改指数据层，不在 Workflow memory 重建表

把 `TradingMemory.save_observation()` / `save_observation_failure()` 及同族读取（`observations`、`facts`、`fact_projections`、`projection_lineage`、`discovery_evidence` 回写）改指数据层的 `data_evidence_*` 表，并补齐数据层写入侧 schema 与写入接口。收口点改一处，四条调用路径（调度观察、`chain/sources`、`chain/articles`、命令行入口）一并生效。不恢复旧表、不撤销边界决定。

**理由**：`_retire_data_tables()` 的存在本身就是一条已固化的边界决策——Workflow memory **不是**数据存储。在 `_SCHEMA` 中恢复该表会让「边界」与「实现」互相矛盾，并与目标设计 §3「中性证据事实归共享事实层」直接冲突。而数据层搬迁本就只完成了读侧：孪生表与只读仓库已就位，写侧从未发布——缺的正是这一半。这也解释了为什么失败面横跨调度与 chain 两条不相干的链路：它们共用同一个收口点。

**成本与风险**：两侧表**逐列等价**（`data_evidence_observations` 与 `evidence_observations` 列集合一致；`data_evidence_facts` / `data_evidence_projections` / `data_evidence_failures` / `data_task_projections` 同理），因此本决策本质是**表名与库的重新指向**，不改列语义、不需重新设计 schema。需注意 `ObservationInput`（`data/core/structured_models.py:224`）属另一条路径——它是结构化序列（注册表驱动）的写入模型，与本决策的抽取型观察事实无关，不要误用。

**备选**：在 `_SCHEMA` 中恢复该表（改动最小、立刻让测试变绿，但把边界妥协固化，并造成数据层与 Workflow memory 双份真相）。不采用。

### D4 · action 词表以 `schemas/decision.py` 的 `Action` 为唯一真源

其余模块 import 该声明：`schemas/pead.py` 的 `action` 字段改为引用同一 `Literal`，`schemas/journal.py` 的裸 `str` 收紧为同一类型。大小写归一只发生在**进入领域对象之前**（上游抽取/coerce 层），领域对象本身只接受规范小写。券商侧 `BUY`/`SELL` 保持为独立表示，由显式映射函数从规范动作派生。

**理由**：`broker/ibkr.py` 的 `BUY`/`SELL` 是外部接口协议，不是内部词表，把两者合并会让外部协议变更冲击内部语义。归一只做一次（入口），避免「多层各自归一」造成的不可预测行为。

**备选**：在 `Literal` 上开 `case_insensitive` 接受大小写（会把非法输入静默接受，与「未声明取值必须拒绝」的要求冲突）。

### D5 · 到期桶改为半开区间，并与标签同步

边界条件由 `days_to_expiry <= horizon` 改为 `days_to_expiry < horizon`，标签由 `≤{horizon}天` 改为 `<{horizon}天`，使标签与实际包含关系一致。累积语义保留（桶仍以上界表达「到该上界为止的合计」），因为资金占用是按horizon聚合的业务含义，不是互斥分箱。

**理由**：缺陷的本质是「标签声明闭区间、实现按闭区间收边，但业务期望边界上的到期日归入更宽的桶」。只改比较符而不改标签会留下第二个不一致。

**备选**：改成互斥分箱（`prev < days <= horizon`）——会让每个桶只含一个区间，但破坏「到 N 天为止的累计资金占用」这一被 `p99_funding_gap` 等汇总消费的语义。

### D6 · 测试基线：固化既有 uv 入口，并把测量条件随数字一起记录

把 `uv sync --all-extras` + `uv run pytest` 固定为唯一的环境与测试入口，写入文档与脚本；每一次被引用的基线测量必须记录命令、依赖范围与执行环境条件。

**理由**：项目已由 `uv` 托管（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`），`pyproject.toml:24-49` 也早已声明 `schedule` / `memory_persist` / `broker` / `channel` / `dev` 五组 extras——缺的不是入口，而是入口未被使用。实测 `uv sync --all-extras` 后全部模块可导入，全量运行得到 `121 failed / 1149 passed / 263 errors`，对比未同步时的通过数增加 92。

**同时更正一条曾被误判的结论**：曾把整库级 setup 失败归因于「pytest 临时目录不可用」与「`--basetemp` 必须当次唯一」。实测证明该归因错误——换**全新** `--basetemp` 同样复现（`22 passed / 1511 errors`，13 秒），真因是执行环境对文件删除设有配额，pytest 运行期清理临时编号目录时触及配额，守卫以 `SystemExit` 打断 fixture setup。因此对策不是「换目录名」，而是：①按文件分批运行以限制单次运行的删除量；②或在不受该配额约束的环境中测量；③并在记录中标注该限制及其影响范围。

**备选**：在 conftest 中改写 `tmp_path_factory` 的根（可行，但会同时影响所有测试的隔离语义，风险高于收益）；依赖默认 OS 临时目录（已证明该路径在受限环境中同样不可用）。

### D8 · 旧实现退出走「墓碑登记 → fail-closed 读取门 → 两段式清除」

不新造机制，移植结构化数据层已验证的退役模式：标识登记处存放墓碑、同一标识不得同时出现在「在用」与「已退役」（两处并存即使配置加载失败）、读取命中墓碑即返回显式退役原因码而非静默回退、物理清除默认只读干跑且须显式确认并留审计备注。本阶段只建立该机制并登记首批待退项，不执行任何清除。

**理由**：设计文档 §12.4 规定 additive-only、不删旧表旧列、保留旧读入口——这条原则若无登记与判据，后续阶段无法回答「哪套旧实现已可退出、条件是什么、何时下线」，兼容层会无限期堆积。项目已经在结构化来源上把这三个环节跑通并验证过（互斥校验、`reason_codes: ["source_retired"]`、`purge-source` 的只读干跑），复用同一模式可使新旧两处的退役语义一致，也避免为 Workflow 侧另立标准。

**取舍**：墓碑是**声明式**的，它强制登记与显式门禁，但不自动判定某旧实现是否「已无人依赖」。因此本能力同时要求每个阶段登记「依赖消费方清零的判据」，把判定责任放在提出退出的那一方，而不是靠工具猜测。

### D9 · `task_projections` 旧列本阶段只登记、不删除

`task_projections` 未被 `_retire_data_tables()` 删除（它被归类为 Workflow memory 表），当前功能正常。按 D2，目标 envelope 由**新建的独立表** `task_projection_envelopes` 承载，因此 `task_projections` 属**待替换**而非待演进：其旧列（`profile` / `profile_version` / `input_kind` / `input_ref` / `target_type` / `target_id`）的退出条件是「其读取方全部切换到 `task_projection_envelopes`」。本阶段只把它登记为「待退、条件未满足」，具体删除条件留待 Phase D/E 依据实际消费方清零情况确定。

**理由**：Phase A 的边界是「只定契约」，此时尚不存在新 envelope 的写入实现与消费方迁移数据，任何具体的删除时点都会是猜测。把条件后置、但把**必须登记**这一点前置，既守住 Phase A 的范围，又不给后续阶段留下「无人负责回答退出问题」的缺口。

**取舍**：代价是旧列在本阶段之后继续存在一段时间，`task_projections` 与新 envelope 表短期并存。缓解方式是新 envelope 表独立成型，旧表在新写路径中不再被填充，从而可被可靠判定为「零写入」并具备下线条件。

### D7 · 架构守卫用静态分析而非运行时探针

守卫以 AST 扫描实现：扫描 Agent 模块的 import 目标与跨角色投影读取点，判定是否越界；并在测试中断言为准（失败即失败，不降级为告警）。

**理由**：运行时探针需要真实执行所有路径，且无法覆盖「某角色在某些条件下才读取」的分支；静态扫描在无外部依赖的 CI 中可确定性地执行。

**取舍**：静态分析会误报动态导入与间接读取。缓解方式是允许**显式、带理由**的例外清单，并限制其为具体模块而非通配。

### 首批待退项登记（本阶段只登记；除标注「本阶段退出」者外，不执行任何删除）

| 待退项 | 位置 | 替代实现 | 退出条件 | 状态 |
|---|---|---|---|---|
| `evidence_observations` / `evidence_facts` / `evidence_fact_projections` / `evidence_failures`（Workflow memory 侧） | `src/ats/memory/store.py:187-334` | 数据层 `data_evidence_*` | 写侧 cutover（D3）完成，四条调用路径全部改指，且数据层读入口能按同一标识与血缘读回 | **本阶段退出**：cutover 完成后不再被引用（表本身已由边界归类删除） |
| `task_projections` 旧列（`profile` / `profile_version` / `input_kind` / `input_ref` / `target_type` / `target_id`） | `src/ats/memory/store.py:337-343` | 新 envelope 表 `task_projection_envelopes`（D2） | 读取方全部切换到 `task_projection_envelopes`，旧表零写入 | 待退，条件未满足 |
| `evidence_fact_projections.legacy_observation_id` | 同上 | 数据层投影表 | 历史观测标识不再被任何读取方引用 | 待退，条件未满足 |
| 硬编码串行的运行入口 | `src/ats/runtime/scheduler.py` | Dispatcher（Phase E） | 任务注册表与依赖解析上线并通过验收 | 待退，Phase E |
| `pead_dossier` / `sector_reviews` / `macro_reviews` 等旧读模型与旧报表入口 | Workflow memory | `task_projection_envelopes` + 角色专用 payload | 新写路径稳定，且旧报表与 CLI 消费方迁移完成 | 待退，Phase D/E |

登记要求（由 `workflow/legacy-retirement` 约束）：每一项都必须写明替代实现、退出条件与消费方清零判据；条件未满足的项必须显式标注「待退」并说明所缺条件，不得省略。

## Risks / Trade-offs

- [契约冻结后 B–E 阶段需要新字段] → envelope 携带 `schema_version`；字段增补走 additive 迁移，不改既有字段语义。
- [证据写入改道牵动 evidence 侧既有测试与四条调用路径] → 实施时先跑 `tests/test_chain_*.py`、`tests/test_evidence_*.py` 与 `tests/test_scheduler_jobs.py` 建立改前基线，改道后逐项核对；收口点单一，便于定位遗漏。
- [改道后数据层读入口读不回新写入的事实] → 两侧列逐列等价，改道后必须核对数据层读入口能按同一标识与血缘读回；坚持单写单读，不保留双写。
- [action 词表归一可能暴露既有大写输入，静默降级会掩盖问题] → 明确「拒绝而非降级」；实施时先扫描现有大写产出点（已知 `tests/test_pead_graph.py:160`）再定归一位置。
- [架构守卫首次运行会报出大量既有违规，导致本 change 无法完成] → 分两步：先把现状登记为显式例外清单使守卫可上线，再按角色逐项清除；例外清单必须可审阅且有上限，不得表现为通配排除。
- [权威基线数字受执行环境限制] → **已消解**：权威测量（不受删除配额约束）已取得 135 failed / 1398 passed / error = 0，证实 263 errors 为纯环境性。记录仍须同时给出命令、依赖范围与执行环境条件三者（见 `workflow/test-baseline`）。
- [135 项失败中 17 项尚未归因，可能被误当作 cutover 的同一根因处理] → 归因结论强制区分「同源（116 项）」与「独立（19 项）」；本 change 只承诺同源项转绿与 ②③ 修复，其余 17 项须逐项归因并登记，不得以「其余失败」省略。
- [同源的 12 项接口签名漂移可能是「测试滞后于实现」而非生产缺陷，误修会掩盖真实行为变更] → 每项须先判定归类（生产侧参数被移除 / 测试替身未同步）再动代码，判定依据需随修复记录。
- [退役登记流于形式，只登记不推进] → 要求登记同时给出替代实现、退出条件、依赖消费方清零判据与未完成时的处理方式；每个阶段的规划产物都须包含该登记，缺失即判定为规划不完整。
- [到期桶语义改动会改变风险汇总的触发阈值判定] → 边界收紧只会把边界上的到期日移入更宽的桶，`peak_expiry_full_notional` 与 `total_full_assignment_notional` 不变；实施后需复核 L2 四类 breach 的判定未被无意改变。

## Migration Plan

**部署顺序**

1. 环境与测试入口（D6）：先把 `uv sync --all-extras` + `uv run pytest` 固化并跑出可复核基线，后续每一步才有判据。
2. 数据层写侧 cutover（D3）：先补齐数据层写入侧 schema 与接口，再改收口点 `save_observation` / `save_observation_failure` 及同族读取，最后核对四条调用路径与数据层读入口。此步是让基线转绿的关键，须独立完成并单独验证。
3. 另两处缺陷（D4 / D5）：与 cutover 互不依赖，可并行。
4. 契约定义（D2）：只新增结构，不接线。
5. 退役登记机制（D8）与首批登记（D9）：机制先落地，再把 `task_projections` 旧列等首批待退项登入，全部标注「条件未满足」。
6. 架构守卫（D7）：最后上线，先把现状登入例外清单，使守卫通过后再逐步清除。

**数据库影响**

- Workflow memory 侧：证据事实不再写入该库（其表已被 `_retire_data_tables()` 删除）；新增 `task_projection_envelopes` 为 additive。
- 数据层侧：新增 `data_evidence_*` 与 `data_task_projections` 的写入侧 schema（列结构已由读侧契约固定），无列语义变更。
- 不删表、不删列、不改既有列语义；`task_projections` 的旧列本阶段保留不删。
- 本阶段不执行任何物理清除动作，退役登记只产生声明与门禁。

**回滚**

- 代码层：环境入口、cutover、另两处缺陷、契约、登记机制、守卫六项互相独立，可按项 revert。
- cutover 的回滚方式是**把收口点改指回原表**；由于 `_retire_data_tables()` 仍在删除原表，回滚必须与边界归类一并处理，不能只回滚代码——这是本项区别于其它项的额外风险。
- 数据层：新增 schema 可保留（无写入时无副作用）；因未做破坏性变更，无需数据回滚。

## Open Questions

- `task_projections` 旧列的具体删除条件（双写周期数、消费方清零判据）留待 Phase D/E 依据实际迁移数据确定；本阶段只登记「条件未满足」。
- Dispatcher 的调度后端（进程内 asyncio、线程池或外部队列）留待 Phase E；本阶段只需要 `WorkflowTaskSpec` 能表达 `resource_group`，不约束后端实现。

## 实施中已关闭的 Open Questions

- ~~退役登记处的物理形态（并入既有注册文件，还是独立登记文件）~~ → **已确定：独立登记文件**
  `config/workflow/legacy_retirement.yaml`。理由：登记处同时承载「在用清册」与「墓碑」两份互斥清单，
  并入既有注册文件会让「加载时互斥校验」跨越两个配置源，冲突检测无法在单次加载中完成。
- ~~架构守卫例外清单的首版粒度（按模块还是按符号）~~ → **已确定：按「模块 + 导入目标」对**
  而非按模块整包放行。理由：整包放行等于对该模块撤销守卫；按对登记可保留该模块其它路径的检查，
  且实测首版 27 条即可覆盖全部现状违规（逐条附理由与预计撤销相位）。
