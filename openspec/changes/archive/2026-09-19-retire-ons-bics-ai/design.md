## Context

现状与本设计直接相关的约束（动机见 `proposal.md`）：

- **配置真源与库镜像分离**：`config/data/structured.yaml` 是签入真源；DB 的 `structured_sources` / `structured_datasets` 由 `bootstrap_catalog()` 单向同步。删 config **不会**删 DB 行，会留孤儿——这正是「退役」必须显式设计、不能只删配置的原因。
- **生命周期入口已存在**：`ats data {validate-source, ingest, release-check, publish, rollback}`，其中 `validate-source` / `publish` 已经是「默认只读预检、显式参数才改状态」的先例；`validate_source_registration()` 同时被 CLI 与 `ReleaseManager` 的发布门共用。运行适配器经 `adapters/structured/registry.py::_RUNTIMES` 惰性解析。
- **库结构已具备 purge 所需的度量**：`structured_observations` 经 `series_id` 关联 `structured_series`；`structured_artifact_blobs.content_hash` 有 UNIQUE 约束（内容级去重），`structured_artifacts` 经 `blob_id` 引用 blob；`SQLiteStructuredRepository.artifact_usage(source_id=...)` 已能直接给出 artifacts / unique_blobs / referenced_bytes / physical_bytes。ONS 现状：5,081 obs、5,081 series、3 artifacts、7,629,839 字节 blob、2 source checks、1 source/dataset 注册行。
- **文档镜像表被机器校验**：`tests/test_structured_docs_consistency.py` 的 `test_operations_source_matrix_matches_machine_catalog` / `..._dataset_matrix_matches_machine_catalog` 会遍历 `structured.yaml` 的 `sources` / `datasets` 并逐行断言 `STRUCTURED_DATA_OPERATIONS.md` §10.1/§10.2 存在对应行——增删来源或数据集必须与两张表同批改，否则立刻红。
- **OpenSpec 场景名不可重命名**：`openspec` 1.13.0 的 `findMissingCurrentScenarios()` 对场景名做**精确字符串匹配**；MODIFIED 块替换整个 requirement，缺少原场景名会被判为丢弃、`openspec validate` 报 ERROR 且 archive 拒收。
- **工作区脏**：`codex/new_data_source` 分支上有与本次退役高度重叠的未提交改动（含 `structured.yaml`、`schedules.yaml`、4 份 docs、1 份 src、3 份 tests）。

## Goals / Non-Goals

**Goals:**

- 把「退役来源」做成一个可复用的平台原语，而不是一次性的删代码：机器可读墓碑 + 注册校验 fail-closed + 显式 purge。
- 退役后任何路径都无法再发现、采集、发布或查询该来源，且该来源不会重新出现在目录、覆盖统计、Observer 输入或报告中。
- purge 可预演、可审计、不可隐式触发，且与既有 `rollback` 语义严格区分。
- ONS 的配置、代码、规范、文档、测试干净退场，同时**不产生任何 L1 输出差异**。

**Non-Goals:**

- 不引入替代来源（Eurostat `isoc_eb_ai` 等英国/欧洲采用来源需另行立 change）。
- 不做批量多来源退役编排；本次一次只退役一个来源。
- 不导出 ONS 历史数据（用户已决策：purge 不导出）。
- 不改变 L1 三轴命题、报告、图表与 manifest；不触碰 Chain / PEAD / Chief / Trader / Portfolio / Risk。
- 不重构 `ReleaseManager` 既有的 publish / rollback 语义。

## Decisions

### D1 墓碑放在 `config/data/structured.yaml` 的顶层 `retired_sources`，而不是 `sources` 内的状态字段

把退役写进 `sources.<id>.catalog_status = retired` 会让它继续与活跃来源共用同一条目：`validate_source_registration()` 以 `row is not None` 判定 `source_configured`；`bootstrap_catalog()` 会把它同步进 `structured_sources`；`sources()` / `datasets()` 目录会继续列出它；`source_health()` 以 `structured_sources` 为驱动表，退役来源仍会出现在健康表里。要做到「不可被静默复活」，它就必须物理上不在 `sources` 中，同时另存一份机器可读记录。独立顶层键 `retired_sources` 同时满足这两点。

字段（以实施后的 config 为准）：`retired_at`、`reason`、`prior_catalog_status`、`disposition`（`purged` / `retained_orphan`）、`disposition_at`、`successor`（替代来源建议，可空）、`spec_removed`（该 capability 的规范是否随之退役）。

**Alternatives considered**：

- `sources` 内加 `retired: true` —— 需要给注册校验、catalog bootstrap、`release_check` 来源枚举、目录查询、`source_health` 全部加过滤分支；且 DB 行仍在，每新增一个消费者就要记得过滤一次，漏一个就复活。否决。
- 单独文件 `config/data/retired_sources.yaml` —— 与 `catalog.yaml` 的 domains 注册机制冲突（需新增 domain 与 loader 分支），而退役记录与来源注册同属一个域、同批演进，拆开只增加不一致面。否决。
- 完全不登记 —— 库内孤儿无解释、id 可被静默复用。否决。

### D2 注册校验 fail-closed 的判定点收敛到两处

- `validate_source_registration(source_id)`：命中墓碑时在 `checks` 中加入 `source_retired: False`，返回 `reason_codes: ["source_retired"]`，并附 `retired_at` / `reason` / `disposition`。这样 CLI `validate-source` 与 `ReleaseManager` 发布门同时 fail-closed，无需两处改。
- `StructuredCatalog.load()`：同一 `source_id` 同时出现在 `sources` 与 `retired_sources` 时直接抛配置错误——这是「显式改写墓碑」之外唯一可能出现的复活路径。

`release_check` 的来源枚举以 `catalog.sources` 为集合，天然不含退役来源，**不需要额外分支**；但要新增一条回归测试锁定「默认全来源检查不含退役来源」，防止未来有人把枚举改成读 DB。

墓碑读取收敛为 `StructuredCatalog` 的访问器（`retired_source(source_id)` / `retired_sources()`），其余模块只读该访问器，避免逻辑散落。

### D3 purge 作为独立 CLI action，默认干跑，与 `rollback` 明确隔离

复用既有 `ats data <action>` 形态，不新造入口。

- 新增 action `purge-source`，复用 `--db` / `--artifact-root` 隔离路径与 `--source` / `VALUE` 定位，新增确认参数（如 `--confirm`）。
- **无确认参数**：只调 `artifact_usage(source_id=...)` 与 count 查询，返回 `mode: "dry_run"` 与逐表待删行数、artifact 数、字节数。
- **有确认参数**：先断言来源已登记墓碑，否则拒绝执行；再在**单个事务**内按依赖序删除 `structured_observations` → `structured_series` → `structured_artifacts` → `structured_source_checks`，同时算出仅被该来源引用的 `blob_id` 集合。
- **blob 文件在事务提交之后删除**：DB 事务无法回滚文件系统。反序（先删文件后提交事务）一旦失败会留下指向不存在文件的 DB 引用，读取时报错；正序失败只留下无引用的残留文件，属安全侧的温和失败，可由 `artifact_usage` / `data health` 事后核对。
- **共享 blob 的删除条件是「不被任何其他 `structured_artifacts` 行引用」**，而不是「不被其他来源引用」——后者在来源内部内容去重时会误留/误删。`blob_id` 与 `content_hash` 一一对应，判定按 `blob_id` 做。
- 与 `rollback` 的语义边界写进 requirement：`rollback` 只改运行模式并**保留** artifact、观测 vintage 与失败记录（既有 requirement 已如此规定）；`purge-source` 才物理删除且不可恢复。两者不得互相隐式触发。

### D4 `disposition`（配置侧台账）与清除记录（库侧事实）分离

配置是签入的，数据库是运行期状态，二者必须能独立演进。若把「已清除」写成 config 中的期望值，配置就变成对运行期状态的断言，在本机 / 隔离验收库 / 未来 CI 多个环境里必然失真。

做法：`disposition` 由实施阶段手工写入一次，描述意图与已发生的处置；`purge-source --confirm` 的清除记录写进**数据库**并可查询，不回写 config。审计时交叉核对两侧。

### D5 三处主 spec 用 MODIFIED 删除 ONS 例外句；`question_not_fielded` 的 ONS 场景名**有意保留**

这是本次最重要的技术约束。`openspec` 1.13.0 的场景缺失检查按精确字符串匹配，因此「把 `data/structured-ingestion` 中 `#### Scenario: ONS 新 wave 没有 AI 模块` 改写成与来源无关的表述」在当前工具链下无法通过校验，archive 会拒收。

结论：该 requirement（「主动发现保存可审计的检查状态」）**原样不动**。理由是它的正文本身与来源无关，`question_not_fielded` 是仍在役的通用状态，而该场景是这一状态在规范层**唯一的示例**与历史回归样例。

**已知残留**：该场景名与其 WHEN 仍含 ONS 字样。已在 `proposal.md` 与本文件显式记录为有意保留，避免后人误判为遗漏。若要消除，唯一合规路径是直接在 `openspec/specs/data/structured-ingestion/spec.md` 中编辑该场景标题——OpenSpec 的 `RENAMED` 只覆盖 requirement 层级，不覆盖 scenario。

**Alternatives considered**：

- 保留场景名、只改写正文 —— 名称与内容语义冲突（名说 ONS、正文泛指），比保留原样更差。否决。
- 直接改主 spec 绕过 delta —— 主 spec 只应在 archive 时由 delta 更新，绕过会让 change 与 spec 失去对应关系。否决。
- 无视校验器继续写 —— archive 失败，change 无法收口。否决。

### D6 实施顺序：先解除引用，再删实体，最后 purge 数据

1. 配置：同时新增墓碑与删除活条目（**同一批**，否则中间态下校验返回 `source_not_configured` 而非 `source_retired`，掩盖真实原因）。
2. 代码：删 `sources/ons_bics_ai.py`、`products/ons_bics_ai.py`、`products/base.py::ons_bics_ai_snapshot`、`registry.py::_ons_bics_ai` 与 `_RUNTIMES["ons_bics_ai"]`。
3. spec / docs / tests 同步（`STRUCTURED_DATA_OPERATIONS.md` 两张镜像表必须与第 1 步同批）。
4. purge——唯一不可逆步骤，放在最后单独执行。

### D7 借用 ONS 当「任意来源」载体的通用测试改绑在役来源，而不是直接删除

`tests/test_ai_adoption_source_adapters.py` 中有若干测试只是**借** `ons_bics_ai` 当载体（发现状态机持久化、`source_checks` 无观测等），删掉会损失通用覆盖。做法：这类测试的 `source_id` 改绑 `rps_genai_adoption` / `us_census_btos`；只删除真正测 ONS 工作簿解析与 ONS DataProduct 的用例。

`tests/test_ai_adoption_bundle.py` 中「`ons_bics_ai_snapshot` 必须不被调用」的守卫断言随方法移除而删除——其守护意图（bundle 不读该来源）已由新 spec「只消费已注册且未退役来源」的要求承接，并由新增测试覆盖。

## Risks / Trade-offs

- [墓碑是一个新的配置键，未来所有读取 `sources` 的路径都要意识到它存在] → 墓碑只承担「拒绝注册 + 解释孤儿 + 审计」三件事，不被任何采集/发布/目录路径读取；访问器收敛到 `StructuredCatalog`，并把该边界写成 `data/structured-ingestion` 的契约与回归测试。
- [purge 是本次唯一不可逆动作，误操作会删掉在役来源数据] → 三重护栏：默认干跑、必须显式确认、必须已登记墓碑；加上单事务删除、blob 文件在提交后删。
- [wave 159 的深度题孤本永久丢失，L1 从此没有非美国企业采用切片] → 用户已知并接受。已在 proposal 的 Impact 与退役 spec 的 Migration 中显式写明「不得用文字回填这一空缺」，防止报告层事后用叙述掩盖来源缺失。
- [`.workbuddy/` 记忆文件与 `docs/L1_OBSERVER_DATA_SOURCES.md` 中仍有 ONS 叙述] → 记忆文件不属交付物，随实施一并更新；该文档的 `tickertrends_public_research` 行存在既有漂移（仍写 `frozen_seed`），改到该文件时一并修正。
- [purge 后 artifact 目录可能残留无引用文件] → 不构成正确性问题；`artifact_usage` / `data health` 可事后核对，必要时按 `retained_orphan` 登记。

## Migration Plan

1. 先处理工作区未提交改动（提交或另行暂存），确保退役 diff 干净——这是先决条件。
2. 配置（墓碑 + 删活条目）与 `STRUCTURED_DATA_OPERATIONS.md` 两张镜像表同批改，跑 `test_structured_docs_consistency`。
3. 代码删除 + 墓碑访问器 + 注册校验 fail-closed，跑通用测试。
4. `openspec validate retire-ons-bics-ai` 通过（当前已通过）。
5. 执行 purge：先 `purge-source --source ons_bics_ai` 干跑并核对 5,081 / 5,081 / 3 / 7,629,839 字节 / 2；确认无误后加确认参数执行。
6. 验收：`ats data sources` 与 `ats data datasets` 中无 ONS；L1 三个 Observer 重跑，命题、状态判定、manifest 与 rows hash 与退役前一致。
7. **回滚策略**：第 1–4 步全部可 git 回退；第 5 步**不可回滚**，只能依赖备份或上游重采——这正是把 purge 单独隔离、置于最后的原因。

## Open Questions

- 是否存在需要一并处理的运行期残留（`var/` 下 ONS 相关的隔离验收库、报告 assets 中残留的 UK 图表）？实施时检索；若存在，按 `retained_orphan` 登记并说明，不在本次强制删除。
