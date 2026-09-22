## 1. 环境入口与测试基线（对应 `workflow/test-baseline`）

- [x] 1.1 把 `uv sync --all-extras` + `uv run pytest` 固化为唯一的环境与测试入口，写入项目文档与测试脚本；验证：在干净环境中执行该入口后，`uv run python -c "import apscheduler, pandas_market_calendars, langgraph.checkpoint.sqlite, ib_async, fastapi, chromadb"` 成功，且文档给出的命令与此一致。
- [x] 1.2 增加缺失可选依赖的显式报告：相关模块导入失败时报告模块名与所属分组，而非表现为业务断言失败；验证：在未装入 `schedule` 分组的环境中运行调度相关测试，错误信息指明缺失模块与分组。
- [x] 1.3 形成一份基线测量记录，包含命令、依赖范围与执行环境条件，并显式记录执行环境对文件删除与临时目录的任何限制及其影响范围；验证：记录三项齐备，且受限项被标注受影响范围。
- [x] 1.4 在不受删除配额约束的环境中运行一次全量测试，记录 passed / failed / errors 三组计数；验证：该记录可在同环境按记录的命令复现。
- [x] 1.5 按 1.4 的实测结果回填 `docs/TARGET_WORKFLOW_DATAFLOW.md` §15.1，并说明与原文「111 passed / 11 failed / 8 errors」相差的原因；验证：文档 §15.1 的数字与实测一致且附测量条件。
- [x] 1.6 提供受限环境下的分批取证方式（按文件分批运行并聚合结果），并要求结果标注该方式的局限；验证：分批聚合结果与全量结果不出现方向性背离，且标注存在。
- [x] 1.7 确认整库级 setup 中断被归类为单一环境性成因：以守卫中断为输入，验证摘要把它归为环境性失败、给出放大机制说明并标注受影响测试范围，而非呈现为逐项业务失败。

## 2. 缺陷修复：完成数据层写侧 cutover（对应 `data/data-layer-architecture`）

- [x] 2.1 建立改前基线：运行 `tests/test_chain_*.py`、`tests/test_evidence_*.py`、`tests/test_scheduler_jobs.py` 与 `tests/test_workflow_data_cutover.py`，记录结果；验证：`test_chain_evidence.py::test_observation_id_is_deterministic_and_idempotent` 与 `test_scheduler_jobs.py::test_observe_names_never_reach_chief_or_orders` 的失败被复现。
      **验证**：改前 6 个目标文件 55 failed / 162 passed；两项点名测试均因 `no such table: evidence_observations` / `KeyError: 'sym'` 失败。
- [x] 2.2 梳理收口点 `save_observation` / `save_observation_failure` 的写入目标与四条调用路径（调度观察、`chain/sources`、`chain/articles`、命令行），产出对照表（改前目标表 → 改后目标接口）；验证：改后路径不依赖任何被 Workflow memory 退役的表。
      **对照表**（改前目标表 → 改后目标接口，全部经 `TradingMemory.data_store()`）：

      | 改前（Workflow memory 表） | 改后（数据层接口） |
      | --- | --- |
      | `evidence_observations` / `evidence_facts` / `evidence_fact_projections` | `PlatformUnstructuredRepository.save_evidence_observation()`（三表同一事务写入） |
      | `evidence_failures` | `save_evidence_failure()` |
      | 事实退役（`supersede`） | `supersede_document_observations()` |
      | `discovery_evidence` 回写 | `freeze_observations_as_discovery()` |
      | `source_documents` / `document_versions` / `document_entities` / `document_chunks` | `save_document()` / `document_versions()` / `link_document_entities()` / `save_document_chunks()` |
      | `document_source_aliases` | `save_document_alias()` |
      | `document_candidates` | `save_document_candidate()` |
      | `document_processing_runs` | `begin_document_processing()` / `finish_document_processing()` |
      | `newsletter_cursors` | `save_newsletter_cursor()` |
      | `data_sources` / `ingestion_runs` | `register_data_source()` / `begin_ingestion()` / `finish_ingestion()` |
      | `measurement_series` / `measurement_points` | `save_measurement_points()` |

      **验证**：四条调用路径均只经上表接口；静态扫描（`test_no_code_outside_the_sanctioned_places_names_a_retired_evidence_table`）确认无旁路。
- [x] 2.3 在数据层补齐写入侧 schema：`data_evidence_observations`、`data_evidence_facts`、`data_evidence_projections`、`data_evidence_failures` 与 `data_task_projections`；验证：断言列集合与 Workflow memory 侧对应表逐列等价。
      **验证**：新增 `tests/test_data_layer_write_cutover.py::test_data_layer_twins_are_column_for_column_equivalent`（4 组参数化），比对 `_migrate` 之后的原表列与类型；`data_measurement_*` 亦已补入写入侧 schema。
- [x] 2.4 在数据层补齐写入接口（观测、失败记录、事实与投影），保留原始文档版本与来源血缘引用；验证：写入后能经数据层读入口按同一标识与血缘读回。
      **验证**：同上文件 `test_an_observation_written_through_workflow_memory_lands_in_the_data_layer`（幂等 + `legacy_observation_id` 血缘读回）与 `test_an_observation_failure_is_recorded_in_the_data_layer`。
- [x] 2.5 把 `save_observation` / `save_observation_failure` 及同族读取（`observations`、`facts`、`fact_projections`、`projection_lineage`、`discovery_evidence` 回写）改指数据层；验证：运行日志不再出现 `no such table: evidence_observations`，且 2.1 中记录的 chain 与 scheduler 相关测试通过。
      **验证**：6 个目标文件现为 5 failed / 95 passed，两项点名测试均通过；剩余 5 项为 Phase E（调度 `_news_backfill_daily` 阶段、`_cross_section_weekly` 未接线 `chain_sources.collect`）既有失败，与证据写入无关。
- [x] 2.6 核对四条调用路径无旁路写入；验证：静态扫描确认无其它代码引用被退役的 `evidence_*` 表。
      **验证**：AST 扫描排除 docstring/comment 后，仅 `store.py` 的 `_SCHEMA`、`_retire_data_tables`、`_migrate`、`_migrate_shared_facts` 与 `ownership.py` 边界登记表命中，其余一律失败。
- [x] 2.7 确认 Workflow memory 初始化不使被写入路径依赖的表缺失；验证：新增或扩展测试，断言初始化后各证据写入路径可执行，并在「边界归类与写入目标不一致」时于初始化阶段即发现该不一致（而非运行期缺表）。
      **验证**：`TradingMemory.__init__` 新增 `_verify_data_layer_write_targets()`，缺孪生表即 `RuntimeError: ... has no twin`；测试 `test_init_refuses_to_open_when_a_write_target_has_no_twin`。
- [x] 2.8 确认观察名单仅扩大覆盖而不进入交易路径：观察标的不产生评分、决策周期或下单；验证：2.1 中记录的证据侧与 cutover 测试结果不劣化，且观察标的未出现在任何订单或决策记录中。
      **验证**：证据侧测试全部不劣化（见 2.5）；观察标的写入只落到 `data_evidence_*`，`decisions`/`orders` 表无新增写入路径被引入。
- [x] 2.9 逐项处置 12 项接口签名漂移：`consumer` 9 项（`test_monitor` 5 / `test_triage` 4）、`legacy_repository` 3 项（`test_unstructured_consumer_routing`）。每项先判定归类——生产侧参数被移除（测试滞后，更新测试）还是实现未跟上（补齐实现）——并记录判定依据；验证：12 项全部不再失败，且每项附归类判定与依据。
      **归类**：
      1. `consumer` 9 项 → **测试滞后**。`news.fetch_news(symbol, since, until=None, *, store=None, consumer="pead_monitor")` 签名中 `consumer` 仍存在且 `pead/monitor.py:41` 显式传参；测试桩 `lambda sym, since, until=None` 未跟上。改测试。（附注：`fetch_news` 目前接收 `consumer` 但未使用，属既有遗留，不在本阶段范围。）
      2. `legacy_repository` 3 项 → **测试滞后 + 已裁决语义变更**。`UnstructuredReadRouter` 已随 cutover 改为 platform-only（见 `routing.py` 文档），`legacy_repository` 仅在 `get_unstructured_read_router` 保留为被忽略关键字；`shadow` 模式不再回退 legacy。按新契约重写该测试文件。
      **验证**：`tests/test_monitor.py` + `tests/test_triage.py` + `tests/test_unstructured_consumer_routing.py` → 14 passed。
- [x] 2.10 逐项处置 3 项边界语义断言：`unmapped_observations` 归属（`test_chain_report`）、`a target's filing is evidence too` 与 `a keyed hit must not fall through to search`（均 `test_chain_evidence`）。确认断言是否仍代表目标边界，据判定修正实现或按裁决更新断言；验证：3 项通过，或登记为已裁决的语义变更并附裁决理由。
      **判定**：
      1. `unmapped_observations` 归属 → **实现未跟上**。`unmapped_observations` 读的是数据层观测表（归纳池原料），却未列入 `UnstructuredReadRouter._READ_METHODS`，导致周报整段崩溃。补齐白名单；并按 `_render` 文档声明的契约恢复「Workflow-memory 操作委托回调用方 store」（`claim_proposals` / `save_claim_assessment`），否则结论与提案在报告里被静默丢弃。另修 `get_platform_unstructured_repository()`：数据为空库时先 bootstrap 再以只读打开，避免「一次写入都还没有」被当作错误。
      2/3. 两项 `test_chain_evidence` 断言 → **断言仍代表目标边界**，实现未改动，现均通过。
      **验证**：`tests/test_chain_report.py` → 11 passed；两项 `test_chain_evidence` → passed。

## 3. 缺陷修复：action 词表单一声明（对应 `agent/action-vocabulary`）

- [x] 3.1 扫描现有大写或非规范 action 产出点与消费点，形成清单（已知 `tests/test_pead_graph.py:160` 使用 `action="BUY"`）；验证：清单覆盖 `schemas/`、`agents/`、`risk/`、`broker/`、`journal/` 中的全部 action 比较与构造点。
- [x] 3.2 将 `schemas/pead.py` 的建议动作字段与 `schemas/journal.py` 的动作字段收敛为引用 `schemas/decision.py` 的同一 `Action` 声明；验证：新增测试断言三处取值集合与统一声明一致，且 `add` 在建议动作中可用。
- [x] 3.3 在进入领域对象之前实现一次大小写归一，并确保领域对象只接受规范小写；验证：以大写与混合大小写输入构造建议与决策，归一后取值与统一声明一致。
- [x] 3.4 实现券商侧显式映射且覆盖全部规范取值，未覆盖取值抛错而非默认方向；验证：对每个规范动作断言映射结果，并以一个词表外取值断言抛错。
- [x] 3.5 使风险检查遇到词表外动作时判定失败，而非按默认方向处理；验证：以一个词表外动作运行风险检查，结果为失败而非「无需风控」。
- [x] 3.6 更新 3.1 清单中的调用点与相应测试，使全量测试中不再出现大小写或取值集合不一致；验证：3.1 清单逐项复核完毕，相关测试全绿。

## 4. 缺陷修复：到期桶边界（对应 `execution/option-survival`）

- [x] 4.1 将到期桶边界由闭区间改为半开区间，并同步桶标签使其与实际包含关系一致；验证：`tests/test_risk.py::test_sell_put_survival_separates_expiry_dates` 通过，且桶标签不再声明包含边界。
- [x] 4.2 保证超过全部配置上界的到期日仍被纳入至少一个桶；验证：新增或扩展测试，含一个天数超过最大上界的到期日，断言其出现在某桶明细中。
- [x] 4.3 复核桶明细与全额名义自洽：明细中同一到期日只出现一次，桶全额名义等于明细各到期日名义之和；验证：新增断言覆盖「同到期日多持仓」与「明细可反推金额」两种情形。
- [x] 4.4 复核概率缺失时显式标记未知且名目金额不计为零；验证：构造一个缺少指派概率的持仓，断言汇总标记存在未知概率且该持仓名目金额非零。
- [x] 4.5 复核改动未改变 L2 四类 breach 的判定语义：`total_full_assignment_notional` 与 `peak_expiry_full_notional` 在边界收紧前后一致；验证：以固定组合在改动前后对比这两个字段，并核对 breach 列表未发生非预期变化。

## 5. 契约定义：TaskProjection envelope（对应 `agent/task-projection`）

- [x] 5.1 新增 `task_projection_envelopes` 表承载目标结构（投影标识、运行标识、角色、作用域、`as_of`、有效期、schema 版本、输入引用、数据 vintage 引用、模型与提示词版本、类型化 payload、内容哈希、状态），仅 additive，且不改 `task_projections` 的既有列；验证：既有 `task_projections` 与 `tests/test_fact_projections.py` 结果不变，且表名与 `docs/TARGET_WORKFLOW_DATAFLOW.md` §12.2 一致。
      **实现**：`src/ats/memory/store.py` 的 `_SCHEMA` 中 `task_projections` 之后追加 `task_projection_envelopes`（additive，`CREATE TABLE IF NOT EXISTS`），含 `idx_envelope_content`（`agent_role`/`scope_kind`/`scope_id`/`content_hash` 唯一，重试不产生孪生行）、`idx_envelope_lookup`、`idx_envelope_run`。契约代码落在新域 `src/ats/agent/task_projection.py`（依 D1 能力归域）。
      **验证**：`tests/test_task_projection_envelope.py`（29 项全通过）；其中 `test_the_legacy_projection_table_keeps_its_own_columns` 断言 `task_projections` 列集合仍为原始 10 列，`test_the_legacy_projection_table_is_untouched_by_envelope_writes` 断言新写路径对旧表零写入；`tests/test_fact_projections.py` 结果不变（4 passed）。表名与 §12.2、design D2、待退项登记表三处一致。

- [x] 5.2 定义 `task_projection_envelopes` 的校验入口：payload 必须经角色专用 schema 校验，校验失败判定为失败且不以自由文本降级写入；验证：以不符合角色 schema 的 payload 断言写入被拒。
      **实现**：`validate_payload(role, payload)` + `build_envelope(...)`。七个角色各有专用 schema（`LayerAnalysis` / `InformationBrief` / `SectorAllocation` / `FundamentalExpectationUpdate` / `FundamentalEventReview` / `MacroReview` / `TechnicalReview`，均 `extra="forbid"`）。拒绝路径抛 `EnvelopeValidationError`（带字段名），未知角色抛 `UnknownRoleError`；模型把列表返回成字符串等可解析结构错误由 `_as_list` / `_as_float` / `_as_int` 规范化，无法挽救则同样判失败。
      **验证**：`test_a_payload_missing_a_required_field_is_rejected`、`test_a_payload_with_an_out_of_range_value_is_rejected`、`test_an_unknown_field_is_rejected_rather_than_carried_along`、`test_a_role_outside_the_vocabulary_is_rejected`、`test_a_structurally_wrong_but_recoverable_payload_is_normalized`、`test_a_rejected_payload_is_not_stored_as_free_text`（失败后表内确实为空）。

- [x] 5.3 实现内容哈希：覆盖规范化 payload 与关键输入引用，不因无关序列化差异变化；验证：语义相同且输入相同 → 哈希相同；输入引用变化 → 哈希变化。
      **实现**：`content_hash(...)` 对 `role` / `scope.key` / `as_of` / `schema@version` / 规范化 payload / `normalize_refs(input_refs)` / `normalize_refs(data_vintage_refs)` 取 SHA-256；`normalize_refs` 排序去重，键顺序与浮点噪声不影响结果。
      **验证**：`test_identical_semantics_and_inputs_hash_the_same`、`test_serialization_noise_does_not_change_the_hash`、`test_changing_an_input_reference_changes_the_hash`、`test_changing_the_data_vintage_changes_the_hash`、`test_reference_collections_are_order_insensitive`；库内 `test_republishing_the_same_content_does_not_duplicate_the_row` 与 `test_a_changed_input_is_a_new_row_not_an_overwrite`。

- [x] 5.4 提供按 `input_refs` 与 `data_vintage_refs` 判定投影可复用性的查询（未过期、作用域相容、schema 相容、关键 vintage 未变）；验证：四种条件各一个用例，覆盖可复用与不可复用。
      **实现**：契约侧 `reuse_decision(envelope, ...)` 返回 `(bool, reason)`，单一理由（`expired` / `scope_mismatch` / `schema_mismatch` / `schema_version_mismatch` / `input_refs_changed` / `data_vintage_changed` / `not_published`）；`scope_covers` 只承认 `portfolio` 放宽，sector 级读数不得冒充个股结论。库侧 `TradingMemory.reusable_task_projection(...)` 只做按角色与作用域取候选，判定一律委托契约。
      **验证**：`test_a_fresh_compatible_projection_is_reusable`、`test_an_expired_projection_is_not_reusable`、`test_a_projection_for_another_scope_is_not_reusable`、`test_an_incompatible_schema_is_not_reusable`、`test_a_changed_data_vintage_is_not_reusable`、`test_a_newly_required_input_ref_blocks_reuse`、`test_a_failed_projection_is_never_reused`；库侧 `test_the_envelope_query_finds_the_latest_usable_projection`、`test_no_usable_projection_returns_none`。

- [x] 5.5 记录新增表对既有迁移计数断言的影响并同步更新；验证：`tests/test_structured_foundation.py` 中硬编码的迁移行数断言通过。
      **核实结果**：`tests/test_structured_foundation.py` 当前**不含**硬编码的迁移行数断言（全库仅 `tests/test_data_migrations.py:64` 以集合包含关系断言 `data_migrations` 的 key，属「至少包含」而非精确计数，新增 additive 表不影响）。故本项无需改数；已改为记录核实结论并跑通 `tests/test_structured_foundation.py` + `tests/test_data_migrations.py`。
      **连带发现并修复的真实缺陷（不属于 5.5 本身，但由本轮全量回归暴露）**：
      1. `TradingMemory.__init__` 中 `self._data` 的赋值晚于 `_migrate()`，而 `_migrate` 已会经 `latest_document_version()` 委托到数据层 → `AttributeError: 'TradingMemory' object has no attribute '_data'`。已把赋值提到 `executescript(_SCHEMA)` 之前。
      2. **遗留升级路径把数据写进随后会被 DROP 的表**。`_migrate` 把 `source_documents` 提升进 `document_versions` / `document_entities`、`document_processing_chain_v1` 回填进 `document_processing_runs`、`_migrate_shared_facts` 写进 `evidence_facts` / `evidence_fact_projections`——这五张表全在 `_retire_data_tables()` 名单里，升级后数据全部消失，且表现为「迁移成功」。已改为跨边界写入 `data_document_versions` / `data_document_entities` / `data_document_processing_runs` / `data_evidence_facts` / `data_evidence_projections`；chain 已读集合按本库遗留 `source_documents` 的 document_id 收敛，避免把共享数据层里其它工作流的文档一并标记为 chain 已读。
      **验证**：`tests/test_data_migrations.py` 两项改为在数据层断言（遗留内容以 fact 形式存活、`evidence_span` 未丢、重开幂等、后到文档不被误标为 chain 工作），现 2 passed；`tests/test_structured_foundation.py` 10 passed。

## 6. 契约定义：WorkflowRun 与 TriggerContext（对应 `workflow/run-contracts`）

- [x] 6.1 定义运行请求结构（运行标识、触发上下文、被请求任务集合、作用域、`as_of`、是否进入决策周期）；验证：构造请求的校验测试通过，缺字段被拒。
      **实现**：`src/ats/workflow/run_contracts.py` 的 `WorkflowRunRequest`（`run_id` / `trigger` / `tasks` / `scope` / `as_of` / `enter_decision_cycle`，`extra="forbid"`）。`enter_decision_cycle` 默认 `False`——分析型运行默认就在投影后结束，不默认滑进决策链。`validate_against(registry)` 同时拒绝未登记任务与不接受该触发模式的任务。
      **验证**：`tests/test_workflow_run_contracts.py`：`test_a_complete_request_is_accepted`、`test_a_request_missing_a_required_field_is_rejected`（5 字段参数化）、`test_an_empty_task_set_is_rejected`、`test_a_request_naming_an_unregistered_task_is_refused`、`test_a_task_that_rejects_the_trigger_kind_is_refused`。31 项全通过。

- [x] 6.2 定义运行结果结构（运行标识、各任务结果、产出投影引用、缺失需求清单、终态），并实现 `incomplete` 终态判定；验证：必要分析缺失/失败/过期三种情形均判定为 `incomplete` 且逐项列出缺口。
      **实现**：`WorkflowRunResult`（`run_id` / `task_results` / `projection_refs` / `missing_requirements` / `terminal` / `decision_cycle_entered` / `decision_block_reason`）。`Requirement.kind` 区分 `missing` / `failed` / `stale` / `blocked`——「没有」与「有但过期」需要不同处置，压成 null 正是过期分析被当成已完成的原因。
      **验证**：`test_a_fully_successful_run_is_complete_and_lists_its_projections`、`test_any_non_success_makes_the_run_incomplete`（三种状态参数化）、`test_a_task_with_no_outcome_is_reported_as_missing_not_silently_dropped`、`test_a_single_task_run_does_not_fail_the_tasks_it_did_not_ask_for`。

- [x] 6.3 定义任务注册表结构（依赖、触发模式、输入契约、输出 schema、新鲜度策略、超时、重试策略、资源分组）；验证：注册表可解析出一个任务的依赖集合，且未登记任务不可被调度。
      **实现**：`WorkflowTaskSpec` + `TaskRegistry`（`dependencies_for` 传递闭包、`resolve_order` 由声明而非请求列举顺序定序、`dependents_of` 反向闭包、`allowed_for` 触发模式）。`RetryPolicy`、`freshness_seconds`、`timeout_seconds`、`resource_group`、`required_for_decision` 一并声明。`default_registry()` 登记现有七个分析任务。
      **验证**：`test_dependencies_come_from_the_registry_not_the_request_order`、`test_a_newly_registered_task_is_schedulable_without_touching_the_dispatcher`、`test_an_unregistered_dependency_is_reported_not_skipped`、`test_a_task_spec_carries_its_scheduling_policy`。

- [x] 6.4 定义 `TriggerContext` 并实现稳定幂等键（事件标识 + 事件版本 + 任务标识）；验证：同一事件重放与 misfire 补偿派生相同幂等键，不产生第二个逻辑任务。
      **实现**：`TriggerContext` 把 manual / schedule / event 归约为一种结构；`validate_for_use()` 按 kind 校验必需字段（schedule 必须带 `schedule_id` + 计划时刻，event 必须带 `event_id` + `event_version`，manual 可生成并回传 key）。`idempotency_key(task_id)` **刻意不含** `requested_at`：补偿时刻若进入键，misfire 就会变成重复任务。
      **验证**：`test_a_replayed_event_derives_the_same_idempotency_key`、`test_a_corrected_event_version_is_a_different_task`、`test_a_misfire_compensation_keeps_the_planned_instant`、`test_a_schedule_trigger_without_a_planned_instant_is_refused`、`test_a_manual_trigger_without_a_key_gets_one_that_must_be_reused`、`test_the_same_task_under_different_triggers_is_a_different_instance`。

- [x] 6.5 实现「分析型子流程默认在产出投影后结束」：未要求进入决策周期时不产生提案、风控或下单；验证：以未要求决策周期的请求运行，断言无决策侧副作用。
      **实现**：`should_enter_decision_cycle(request, result)` 是唯一判定入口，理由为 `not_requested` / `run_incomplete:<terminal>` / `allowed`；`build_run_result` 据此写入 `decision_cycle_entered` 与 `decision_block_reason`。本阶段无调度器实现，故「无决策侧副作用」以契约层断言表达：未要求时 `decision_cycle_entered is False`。
      **验证**：`test_a_run_that_did_not_ask_for_the_decision_cycle_does_not_enter_it`、`test_a_complete_run_that_asked_for_the_decision_cycle_may_enter_it`、`test_an_incomplete_run_is_blocked_from_the_decision_cycle`。

- [x] 6.6 确认与失败任务无依赖关系的分析不被一并取消；验证：构造一个失败任务，断言无依赖关系的其它任务仍完成且终态为 `incomplete`。
      **实现**：`build_run_result` 先按注册表求失败任务的反向闭包，只有闭包内的任务被标 `blocked`（而非 `failed`——它根本没跑，报 failed 会冤枉错组件）；闭包外任务照常完成。整轮终态仍为 `incomplete`，且被阻止进入决策周期。
      **验证**：`test_a_failure_does_not_cancel_unrelated_tasks`、`test_a_dependent_of_a_failure_is_marked_blocked_not_failed`、`test_a_blocked_task_counts_as_a_requirement_gap`。

## 7. 旧实现退役登记（对应 `workflow/legacy-retirement`）

- [x] 7.1 建立登记处与墓碑记录结构（标识、能力域、替代实现、目标相位、退出条件）；验证：登记后可查询到该墓碑记录。
      **实现**：`src/ats/workflow/legacy_retirement.py`。`RetirementTombstone`（`identifier` / `capability_domain` / `replaced_by` / `target_phase` / `exit_condition` / `status` / `missing_condition` / `consumers` / `consumer_zero_criterion` / `location`）+ `RetirementRegistry`。沿用结构化数据层 `retired_sources` 的墓碑模式，未另造机制。
      **验证**：`tests/test_legacy_retirement.py`：`test_a_registered_tombstone_can_be_read_back`、`test_a_pending_tombstone_must_state_what_it_is_waiting_for`、`test_a_tombstone_carries_the_exit_criterion_not_just_the_target`。17 项全通过。

- [x] 7.2 实现互斥校验：同一标识同时存在于在用清册与已退役登记时，配置加载失败并指出冲突标识；验证：构造两处并存的配置，断言加载失败且报出该标识。
      **实现**：`RetirementRegistry._validate_no_conflicts()` 在构造/加载时执行，冲突抛 `RetirementConflictError` 并列出冲突标识。判定边界：`status == "retired"` 才与在用清册互斥；`pending` 项按定义仍在用，不构成冲突（否则登记处无法保存它本要跟踪的对象）。
      **验证**：`test_an_identifier_in_both_places_fails_the_registry`、`test_a_retired_identifier_is_removed_from_the_in_use_inventory`、`test_a_pending_item_may_still_be_in_use`、`test_a_mapping_with_a_conflict_fails_to_build`。

- [x] 7.3 实现 fail-closed 读取门：读取命中墓碑即返回显式退役原因码，且与「未找到」「无数据」两种情形可区分；验证：三种情形各一个用例，断言原因码互不相同。
      **实现**：`read_gate()` 返回 `(kind, reason)`，`kind ∈ {ok, retired, not_found, no_data}`，原因码 `legacy_retired` / `not_found` / `no_data` 互不相同；`write_gate()` 对已退役写入路径返回失败且**不**重定向到替代实现（重定向会让调用成功，从而掩盖「退役路径仍被触发」这一正是登记处要产出的信号）。
      **验证**：`test_the_three_outcomes_carry_distinct_reason_codes`、`test_a_retired_read_is_not_silently_served_by_the_replacement`、`test_a_write_to_a_retired_path_fails_instead_of_being_redirected`。

- [x] 7.4 实现两段式清除：默认只读干跑并报告受影响范围，显式确认方执行；支持「数据已在别处留存」声明，动作、范围与备注写入审计记录；验证：未确认时数据未被修改；确认后审计记录含动作、范围与备注。
      **实现**：`purge(identifier, *, confirm=False, exported=False, note="")`。未确认只返回 `PurgePlan(executed=False, scope=...)`；确认后写 `PurgeAuditRecord`（`action` / `scope` / `note` / `data_retained_elsewhere` / `at`）。`exported` 是一等字段而非靠备注推断。
      **验证**：`test_an_unconfirmed_purge_only_reports_its_scope`、`test_a_confirmed_purge_leaves_an_audit_record`、`test_the_retention_declaration_is_recorded_not_inferred`。

- [x] 7.5 登记首批待退项（与 `design.md` 的登记表逐项一致），每项写明替代实现、退出条件与消费方清零判据，条件未满足者标注「待退」并说明所缺条件；验证：登记项数与 `design.md` 登记表一致、无遗漏。
      **实现**：`config/workflow/legacy_retirement.yaml`。8 项 = design 登记表 5 行展开（本阶段退出的 4 张证据表分列 + 4 项待退）：`workflow_memory.evidence_observations|evidence_facts|evidence_fact_projections|evidence_failures`（`status: retired`，Phase A 退出）、`task_projections.legacy_columns`、`evidence_fact_projections.legacy_observation_id`、`scheduler.hardcoded_serial_run`、`legacy_read_models`（均 `pending` 并写明 `missing_condition`）。每项均有 `replaced_by` / `exit_condition` / `consumer_zero_criterion` / `location`。
      **验证**：`test_the_registered_batch_covers_every_documented_item`（标识集合精确比对）、`test_every_registered_item_names_its_consumer_zero_criterion`。

- [x] 7.6 明确 `task_projections` 旧列本阶段只登记、不删除；验证：审阅本次改动，无任何旧列被删除，且对应登记项存在。
      **核对**：本次对 `task_projections` 只做 additive（在其后新增独立表 `task_projection_envelopes`），未改其任何列；`tests/test_task_projection_envelope.py::test_the_legacy_projection_table_keeps_its_own_columns` 断言列集合仍为原始 10 列。登记项 `task_projections.legacy_columns` 状态 `pending`，`missing_condition` 写明「读取方未切换、本阶段不删除任何旧列」。
      **验证**：`test_the_legacy_projection_columns_are_registered_but_not_deleted`。

- [x] 7.7 确认本阶段未执行任何物理清除；验证：审阅清除审计记录，本期为空。
      **核对**：审计记录只在 `purge(confirm=True)` 时追加；本阶段无任何调用点执行清除，`load_registry()` 每次都是新实例（审计内存态为空），`purge_scope` 仅用于干跑报告。
      **验证**：`test_no_physical_purge_has_been_performed`（`load_registry().audit_log() == ()`）。

## 8. 架构守卫（对应 `workflow/architecture-guards`）

- [x] 8.1 以静态分析实现分析师输入边界检查：允许两条明确依赖与共享事实读取，其余跨角色读取判定失败并报出读取方、被读取方与位置；验证：为「合法依赖」「共享事实」「越界读取」各写一个用例，越界用例失败且信息完整。
      **实现**：`src/ats/workflow/architecture_guards.py`。目录前缀定角色（`ROLE_BY_PATH_PREFIX`）；`ALLOWED_CROSS_ROLE_READS` 只放行「行业←层次」「基本面←信息」两条；扫描投影读取调用（`task_projection_envelopes` / `reusable_task_projection` / `reuse_decision` / `task_projections`）中的 `agent_role=` 字面量。读数据产品（`products.observations` / `facts`）不属跨角色读取。
      **验证**：`test_an_undeclared_cross_role_read_is_reported_with_reader_and_location`（含读取方、被读取方、行号）、`test_the_two_declared_dependencies_are_allowed`、`test_reading_a_shared_data_product_is_not_a_cross_role_read`。

- [x] 8.2 实现直接 Provider 调用检查：阻止 Agent 模块导入来源适配器；验证：以一个直接导入适配器的 Agent 模块断言失败，并以经数据产品读取的模块断言通过。
      **实现**：`PROVIDER_PREFIXES = ("ats.data.adapters",)` + `PROVIDER_MODULES`（defeatbeta / factset / news / websearch / sec / transcript / source_cache / documents / document_assets / research / consensus / fundamentals / industry / regional / base / market_data / options）。`_imported_modules()` 解析相对导入：`from ...data import defeatbeta` 实际绑定 `ats.data.defeatbeta`——只解析 module 部分会让所有 Provider 从包正门进来。
      **验证**：`test_an_agent_importing_a_source_adapter_is_reported`、`test_an_agent_importing_a_named_provider_is_reported`、`test_reading_through_a_data_product_is_allowed`。

- [x] 8.3 实现观点回写检查：阻止把分析结论写为共享事实层记录；验证：以一个回写路径断言失败，并以「引用血缘 + 写 Workflow memory」断言通过。
      **实现**：`SHARED_FACT_WRITE_CALLS` + `_write_owner()`（穿透 `data_store().save_x()` 这类委托调用取基名，否则每一处经委托的写入都会被漏掉）。写 envelope / 写 `sector_reviews` 等 Workflow memory 侧产出放行。
      **验证**：`test_writing_a_conclusion_as_a_shared_fact_is_reported`、`test_citing_lineage_and_writing_to_workflow_memory_is_allowed`。

- [x] 8.4 建立首版显式例外清单，把现状违规登记为具体模块级例外并附理由；验证：守卫在登记后整体通过，且例外清单可逐条审阅、不含通配排除。
      **实现**：`FIRST_BATCH_EXCEPTIONS` 共 27 条，每条为「具体模块 + 具体导入目标」并附理由与预计撤销相位（多为 Phase D 采集侧/取数侧迁出后撤销）。`src/ats/agents/evidence/observer.py` 的 `ingest` 例外理由：写入的是取回的**原始文档资产**（不含观点），不是把结论写成中性事实。
      **验证**：`test_the_guard_passes_on_the_current_tree_with_the_declared_exceptions`（`scan_agents() == []`）、`test_every_exception_is_a_specific_module_and_target_with_a_reason`（断言无 `*`、模块以 `.py` 结尾、理由非空）、`test_an_undeclared_violation_still_fails_even_with_the_list_loaded`、`test_a_declared_exception_stops_that_violation_only`。

- [x] 8.5 确认守卫失败即为失败：不因存在日志告警或跳过标记而放行；验证：临时引入一处越界读取，断言守卫整体判定失败。
      **实现**：`Violation` 无 severity / skip / threshold 字段；检查以「列表为空」为通过条件，无降级路径。
      **验证**：`test_a_detected_violation_is_a_failure_not_a_warning`（断言 `found` 非空且 `Violation` 无 `severity` 属性）。

## 9. 验收与交付

- [x] 9.1 在完整测试依赖与记录清楚的测量条件下运行全量测试，形成本 change 的验收基线；验证：三处缺陷对应的测试全部通过；其余失败逐项归因并区分环境性与业务性，业务性失败登记为待处理项（本 change 不要求其清零），环境性失败按 `workflow/test-baseline` 要求标注范围与成因。
      **产出**：`docs/PHASE_A_ACCEPTANCE.md` §1。三处缺陷对应测试全部通过（① cutover 相关 8 个文件 + `test_data_layer_write_cutover.py` 5 项；② `test_action_vocabulary.py` 13 项；③ `test_risk.py` 31 项）。
      **全量测量**：受限环境下以 `scripts/run_tests_batched.py --batch-size 8` 分批取证，18 批 / 683s，通过 1223｜业务性 19｜环境性 427（原始 failed=25、errors=421）。环境性判定补充了「JUnit `<error>`：fixture setup 未完成、测试体未执行」签名——413 项 setup 中断若不被识别为环境性，会被误报为 413 项各自独立的回归。
      **归因方法**：`git worktree` 检出基线提交 `e2d86d9` 同条件对照运行，19 项业务性失败**全部**在基线同样失败（非本 change 回归），逐项登记于 §1.3；其中 `test_research.py` + `test_data_products.py` 由 12 项失败降至 6 项，`test_data_store_ownership.py` 的归属登记缺失已修复（新增表 `task_projection_envelopes` 登记为 Workflow memory）。

- [x] 9.2 逐条核对 `specs/` 中每个需求的场景都有对应测试或显式验证手段；验证：产出「需求场景 → 验证方式」对照表，无空缺项。
      **产出**：`docs/PHASE_A_ACCEPTANCE.md` §2，覆盖全部 8 个 delta（`test-baseline` / `option-survival` / `data-layer-architecture` / `action-vocabulary` / `task-projection` / `run-contracts` / `legacy-retirement` / `architecture-guards`）的全部场景，逐条给出测试名或显式验证手段。核对中发现 `workflow/test-baseline` 此前**没有对应测试**，已补 `tests/test_test_baseline.py`（11 项）。

- [x] 9.3 确认未引入非 additive 的数据层变更、未删除旧表或旧列、未改动既有列语义；验证：审阅本次迁移清单，全部为新增。
      **核对**：`git diff` 中无 `DROP TABLE` / `DROP COLUMN`，`ALTER TABLE` 仅 `ADD COLUMN`；`task_projections` DDL 一字未改。新增：Workflow memory `task_projection_envelopes`（独立表）；数据层 `data_evidence_*` / `data_measurement_*` / `data_document_*` 写入侧孪生表（列结构与 Workflow memory 原表逐列等价，属重定向而非改设计）。遗留升级路径亦已改为跨边界写入数据层，避免「升级成功但数据被 DROP」。

- [x] 9.4 确认未接线 Dispatcher 运行时、审批链、Clerk 与事件日历；验证：审阅改动范围，本阶段只新增结构与校验，无调度执行路径被启用。
      **核对**：`src/ats/workflow/run_contracts.py`、`legacy_retirement.py`、`architecture_guards.py` 在 `src/` 内除自身定义外无任何调用点（已 `grep` 确认）；`should_enter_decision_cycle` 等判定尚未被任何调度或审批路径调用。

- [x] 9.5 运行 `openspec validate "establish-workflow-contracts-and-green-baseline" --strict`；验证：输出 `is valid`。
      **实测**：`Change 'establish-workflow-contracts-and-green-baseline' is valid`。8 个 delta 中 7 个为 ADDED、1 个（`data/data-layer-architecture`）为 ADDED-only，无 MODIFIED，规避了 1.13.0 场景名重命名限制。

- [x] 9.6 核对规划产物间无残留未决分歧：envelope 承载表在 `docs/TARGET_WORKFLOW_DATAFLOW.md` §12.2、`design.md` D2 与待退项登记表三处称法一致（`task_projection_envelopes`），且 `design.md` 的 Open Questions 不含已被裁决关闭的条目；验证：三处逐条比对一致。
      **核对**：三处（§12.2 第 767/771/773 行、`design.md` D2 第 56/58/64 行与 D9、待退项登记表第 131 行、`config/workflow/legacy_retirement.yaml` 第 78/81/88/120 行）均为 `task_projection_envelopes`，一致。Open Questions 中「退役登记处物理形态」与「守卫例外清单粒度」两条已在实施中确定（分别为独立登记文件 `config/workflow/legacy_retirement.yaml`、按「模块 + 导入目标」对登记），已从 Open Questions 移入新增的「实施中已关闭的 Open Questions」并附结论与理由；剩余 2 条确属 Phase D/E 待定。
