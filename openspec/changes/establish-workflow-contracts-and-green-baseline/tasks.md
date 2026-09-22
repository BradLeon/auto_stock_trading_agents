## 1. 环境入口与测试基线（对应 `workflow/test-baseline`）

- [ ] 1.1 把 `uv sync --all-extras` + `uv run pytest` 固化为唯一的环境与测试入口，写入项目文档与测试脚本；验证：在干净环境中执行该入口后，`uv run python -c "import apscheduler, pandas_market_calendars, langgraph.checkpoint.sqlite, ib_async, fastapi, chromadb"` 成功，且文档给出的命令与此一致。
- [ ] 1.2 增加缺失可选依赖的显式报告：相关模块导入失败时报告模块名与所属分组，而非表现为业务断言失败；验证：在未装入 `schedule` 分组的环境中运行调度相关测试，错误信息指明缺失模块与分组。
- [ ] 1.3 形成一份基线测量记录，包含命令、依赖范围与执行环境条件，并显式记录执行环境对文件删除与临时目录的任何限制及其影响范围；验证：记录三项齐备，且受限项被标注受影响范围。
- [ ] 1.4 在不受删除配额约束的环境中运行一次全量测试，记录 passed / failed / errors 三组计数；验证：该记录可在同环境按记录的命令复现。
- [ ] 1.5 按 1.4 的实测结果回填 `docs/TARGET_WORKFLOW_DATAFLOW.md` §15.1，并说明与原文「111 passed / 11 failed / 8 errors」相差的原因；验证：文档 §15.1 的数字与实测一致且附测量条件。
- [ ] 1.6 提供受限环境下的分批取证方式（按文件分批运行并聚合结果），并要求结果标注该方式的局限；验证：分批聚合结果与全量结果不出现方向性背离，且标注存在。
- [ ] 1.7 确认整库级 setup 中断被归类为单一环境性成因：以守卫中断为输入，验证摘要把它归为环境性失败、给出放大机制说明并标注受影响测试范围，而非呈现为逐项业务失败。

## 2. 缺陷修复：完成数据层写侧 cutover（对应 `data/data-layer-architecture`）

- [ ] 2.1 建立改前基线：运行 `tests/test_chain_*.py`、`tests/test_evidence_*.py`、`tests/test_scheduler_jobs.py` 与 `tests/test_workflow_data_cutover.py`，记录结果；验证：`test_chain_evidence.py::test_observation_id_is_deterministic_and_idempotent` 与 `test_scheduler_jobs.py::test_observe_names_never_reach_chief_or_orders` 的失败被复现。
- [ ] 2.2 梳理收口点 `save_observation` / `save_observation_failure` 的写入目标与四条调用路径（调度观察、`chain/sources`、`chain/articles`、命令行），产出对照表（改前目标表 → 改后目标接口）；验证：改后路径不依赖任何被 Workflow memory 退役的表。
- [ ] 2.3 在数据层补齐写入侧 schema：`data_evidence_observations`、`data_evidence_facts`、`data_evidence_projections`、`data_evidence_failures` 与 `data_task_projections`；验证：断言列集合与 Workflow memory 侧对应表逐列等价。
- [ ] 2.4 在数据层补齐写入接口（观测、失败记录、事实与投影），保留原始文档版本与来源血缘引用；验证：写入后能经数据层读入口按同一标识与血缘读回。
- [ ] 2.5 把 `save_observation` / `save_observation_failure` 及同族读取（`observations`、`facts`、`fact_projections`、`projection_lineage`、`discovery_evidence` 回写）改指数据层；验证：运行日志不再出现 `no such table: evidence_observations`，且 2.1 中记录的 chain 与 scheduler 相关测试通过。
- [ ] 2.6 核对四条调用路径无旁路写入；验证：静态扫描确认无其它代码引用被退役的 `evidence_*` 表。
- [ ] 2.7 确认 Workflow memory 初始化不使被写入路径依赖的表缺失；验证：新增或扩展测试，断言初始化后各证据写入路径可执行，并在「边界归类与写入目标不一致」时于初始化阶段即发现该不一致（而非运行期缺表）。
- [ ] 2.8 确认观察名单仅扩大覆盖而不进入交易路径：观察标的不产生评分、决策周期或下单；验证：2.1 中记录的证据侧与 cutover 测试结果不劣化，且观察标的未出现在任何订单或决策记录中。
- [ ] 2.9 逐项处置 12 项接口签名漂移：`consumer` 9 项（`test_monitor` 5 / `test_triage` 4）、`legacy_repository` 3 项（`test_unstructured_consumer_routing`）。每项先判定归类——生产侧参数被移除（测试滞后，更新测试）还是实现未跟上（补齐实现）——并记录判定依据；验证：12 项全部不再失败，且每项附归类判定与依据。
- [ ] 2.10 逐项处置 3 项边界语义断言：`unmapped_observations` 归属（`test_chain_report`）、`a target's filing is evidence too` 与 `a keyed hit must not fall through to search`（均 `test_chain_evidence`）。确认断言是否仍代表目标边界，据判定修正实现或按裁决更新断言；验证：3 项通过，或登记为已裁决的语义变更并附裁决理由。

## 3. 缺陷修复：action 词表单一声明（对应 `agent/action-vocabulary`）

- [ ] 3.1 扫描现有大写或非规范 action 产出点与消费点，形成清单（已知 `tests/test_pead_graph.py:160` 使用 `action="BUY"`）；验证：清单覆盖 `schemas/`、`agents/`、`risk/`、`broker/`、`journal/` 中的全部 action 比较与构造点。
- [ ] 3.2 将 `schemas/pead.py` 的建议动作字段与 `schemas/journal.py` 的动作字段收敛为引用 `schemas/decision.py` 的同一 `Action` 声明；验证：新增测试断言三处取值集合与统一声明一致，且 `add` 在建议动作中可用。
- [ ] 3.3 在进入领域对象之前实现一次大小写归一，并确保领域对象只接受规范小写；验证：以大写与混合大小写输入构造建议与决策，归一后取值与统一声明一致。
- [ ] 3.4 实现券商侧显式映射且覆盖全部规范取值，未覆盖取值抛错而非默认方向；验证：对每个规范动作断言映射结果，并以一个词表外取值断言抛错。
- [ ] 3.5 使风险检查遇到词表外动作时判定失败，而非按默认方向处理；验证：以一个词表外动作运行风险检查，结果为失败而非「无需风控」。
- [ ] 3.6 更新 3.1 清单中的调用点与相应测试，使全量测试中不再出现大小写或取值集合不一致；验证：3.1 清单逐项复核完毕，相关测试全绿。

## 4. 缺陷修复：到期桶边界（对应 `execution/option-survival`）

- [ ] 4.1 将到期桶边界由闭区间改为半开区间，并同步桶标签使其与实际包含关系一致；验证：`tests/test_risk.py::test_sell_put_survival_separates_expiry_dates` 通过，且桶标签不再声明包含边界。
- [ ] 4.2 保证超过全部配置上界的到期日仍被纳入至少一个桶；验证：新增或扩展测试，含一个天数超过最大上界的到期日，断言其出现在某桶明细中。
- [ ] 4.3 复核桶明细与全额名义自洽：明细中同一到期日只出现一次，桶全额名义等于明细各到期日名义之和；验证：新增断言覆盖「同到期日多持仓」与「明细可反推金额」两种情形。
- [ ] 4.4 复核概率缺失时显式标记未知且名目金额不计为零；验证：构造一个缺少指派概率的持仓，断言汇总标记存在未知概率且该持仓名目金额非零。
- [ ] 4.5 复核改动未改变 L2 四类 breach 的判定语义：`total_full_assignment_notional` 与 `peak_expiry_full_notional` 在边界收紧前后一致；验证：以固定组合在改动前后对比这两个字段，并核对 breach 列表未发生非预期变化。

## 5. 契约定义：TaskProjection envelope（对应 `agent/task-projection`）

- [ ] 5.1 新增 `task_projection_envelopes` 表承载目标结构（投影标识、运行标识、角色、作用域、`as_of`、有效期、schema 版本、输入引用、数据 vintage 引用、模型与提示词版本、类型化 payload、内容哈希、状态），仅 additive，且不改 `task_projections` 的既有列；验证：既有 `task_projections` 与 `tests/test_fact_projections.py` 结果不变，且表名与 `docs/TARGET_WORKFLOW_DATAFLOW.md` §12.2 一致。
- [ ] 5.2 定义 `task_projection_envelopes` 的校验入口：payload 必须经角色专用 schema 校验，校验失败判定为失败且不以自由文本降级写入；验证：以不符合角色 schema 的 payload 断言写入被拒。
- [ ] 5.3 实现内容哈希：覆盖规范化 payload 与关键输入引用，不因无关序列化差异变化；验证：语义相同且输入相同 → 哈希相同；输入引用变化 → 哈希变化。
- [ ] 5.4 提供按 `input_refs` 与 `data_vintage_refs` 判定投影可复用性的查询（未过期、作用域相容、schema 相容、关键 vintage 未变）；验证：四种条件各一个用例，覆盖可复用与不可复用。
- [ ] 5.5 记录新增表对既有迁移计数断言的影响并同步更新；验证：`tests/test_structured_foundation.py` 中硬编码的迁移行数断言通过。

## 6. 契约定义：WorkflowRun 与 TriggerContext（对应 `workflow/run-contracts`）

- [ ] 6.1 定义运行请求结构（运行标识、触发上下文、被请求任务集合、作用域、`as_of`、是否进入决策周期）；验证：构造请求的校验测试通过，缺字段被拒。
- [ ] 6.2 定义运行结果结构（运行标识、各任务结果、产出投影引用、缺失需求清单、终态），并实现 `incomplete` 终态判定；验证：必要分析缺失/失败/过期三种情形均判定为 `incomplete` 且逐项列出缺口。
- [ ] 6.3 定义任务注册表结构（依赖、触发模式、输入契约、输出 schema、新鲜度策略、超时、重试策略、资源分组）；验证：注册表可解析出一个任务的依赖集合，且未登记任务不可被调度。
- [ ] 6.4 定义 `TriggerContext` 并实现稳定幂等键（事件标识 + 事件版本 + 任务标识）；验证：同一事件重放与 misfire 补偿派生相同幂等键，不产生第二个逻辑任务。
- [ ] 6.5 实现「分析型子流程默认在产出投影后结束」：未要求进入决策周期时不产生提案、风控或下单；验证：以未要求决策周期的请求运行，断言无决策侧副作用。
- [ ] 6.6 确认与失败任务无依赖关系的分析不被一并取消；验证：构造一个失败任务，断言无依赖关系的其它任务仍完成且终态为 `incomplete`。

## 7. 旧实现退役登记（对应 `workflow/legacy-retirement`）

- [ ] 7.1 建立登记处与墓碑记录结构（标识、能力域、替代实现、目标相位、退出条件）；验证：登记后可查询到该墓碑记录。
- [ ] 7.2 实现互斥校验：同一标识同时存在于在用清册与已退役登记时，配置加载失败并指出冲突标识；验证：构造两处并存的配置，断言加载失败且报出该标识。
- [ ] 7.3 实现 fail-closed 读取门：读取命中墓碑即返回显式退役原因码，且与「未找到」「无数据」两种情形可区分；验证：三种情形各一个用例，断言原因码互不相同。
- [ ] 7.4 实现两段式清除：默认只读干跑并报告受影响范围，显式确认方执行；支持「数据已在别处留存」声明，动作、范围与备注写入审计记录；验证：未确认时数据未被修改；确认后审计记录含动作、范围与备注。
- [ ] 7.5 登记首批待退项（与 `design.md` 的登记表逐项一致），每项写明替代实现、退出条件与消费方清零判据，条件未满足者标注「待退」并说明所缺条件；验证：登记项数与 `design.md` 登记表一致、无遗漏。
- [ ] 7.6 明确 `task_projections` 旧列本阶段只登记、不删除；验证：审阅本次改动，无任何旧列被删除，且对应登记项存在。
- [ ] 7.7 确认本阶段未执行任何物理清除；验证：审阅清除审计记录，本期为空。

## 8. 架构守卫（对应 `workflow/architecture-guards`）

- [ ] 8.1 以静态分析实现分析师输入边界检查：允许两条明确依赖与共享事实读取，其余跨角色读取判定失败并报出读取方、被读取方与位置；验证：为「合法依赖」「共享事实」「越界读取」各写一个用例，越界用例失败且信息完整。
- [ ] 8.2 实现直接 Provider 调用检查：阻止 Agent 模块导入来源适配器；验证：以一个直接导入适配器的 Agent 模块断言失败，并以经数据产品读取的模块断言通过。
- [ ] 8.3 实现观点回写检查：阻止把分析结论写为共享事实层记录；验证：以一个回写路径断言失败，并以「引用血缘 + 写 Workflow memory」断言通过。
- [ ] 8.4 建立首版显式例外清单，把现状违规登记为具体模块级例外并附理由；验证：守卫在登记后整体通过，且例外清单可逐条审阅、不含通配排除。
- [ ] 8.5 确认守卫失败即为失败：不因存在日志告警或跳过标记而放行；验证：临时引入一处越界读取，断言守卫整体判定失败。

## 9. 验收与交付

- [ ] 9.1 在完整测试依赖与记录清楚的测量条件下运行全量测试，形成本 change 的验收基线；验证：三处缺陷对应的测试全部通过；其余失败逐项归因并区分环境性与业务性，业务性失败登记为待处理项（本 change 不要求其清零），环境性失败按 `workflow/test-baseline` 要求标注范围与成因。
- [ ] 9.2 逐条核对 `specs/` 中每个需求的场景都有对应测试或显式验证手段；验证：产出「需求场景 → 验证方式」对照表，无空缺项。
- [ ] 9.3 确认未引入非 additive 的数据层变更、未删除旧表或旧列、未改动既有列语义；验证：审阅本次迁移清单，全部为新增。
- [ ] 9.4 确认未接线 Dispatcher 运行时、审批链、Clerk 与事件日历；验证：审阅改动范围，本阶段只新增结构与校验，无调度执行路径被启用。
- [ ] 9.5 运行 `openspec validate "establish-workflow-contracts-and-green-baseline" --strict`；验证：输出 `is valid`。
- [ ] 9.6 核对规划产物间无残留未决分歧：envelope 承载表在 `docs/TARGET_WORKFLOW_DATAFLOW.md` §12.2、`design.md` D2 与待退项登记表三处称法一致（`task_projection_envelopes`），且 `design.md` 的 Open Questions 不含已被裁决关闭的条目；验证：三处逐条比对一致。
