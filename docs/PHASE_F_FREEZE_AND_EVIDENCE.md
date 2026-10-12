> 2026-10-11 v6 取证完成：6.4/6.6/6.7 已交付，当前 106/117，11 待办。八个真实研究 scope 和 COHR 新运行/独立重放通过；四个同 scope 财务读取补验通过。隔离研究账本 16 条有效事件、仍缺 40 个必需证据项；生产源账本缺失，0/8 合格。九份报告 unsigned，生产部署与退役未执行。见 docs/PHASE_F_FINAL_EVIDENCE_REVIEW.md；以下早期计数为历史状态。

> 2026-10-11 v6 最新：348 个必需门禁按同 ID 最新结果全通过、0 skipped，551 路径/10 消费者指纹稳定，strict 通过，v6 冻结已登记。原完整运行 347 passed / 1 failed（进度文档计数）及整个文档模块 7 passed 补验均保留；不冒充原单次全绿。3.10/6.1/13.7 恢复完成，当前 103/117，14 待办；真实八个研究 scope 与 COHR 短 TTL 取证正在执行，报告未签核，无生产切换。以下早期计数为历史状态。

> 2026-10-11 最新：真实 MRVL Routine 暴露定性叙事被写成伪数值的问题，已登记 v4 修复影响并重开 3.10/6.1/13.7，当前 100/117，17 待办。v3 304 项通过与冻结仅为保留的历史工程结果；模型报告未签核，无生产切换。

> 2026-10-11 当前：v3 完整门禁 304 passed / 0 failed / 0 skipped，受保护指纹稳定，strict 通过，550 路径新冻结已登记；tasks 103/117 完成，14 待办。真实四标的模型取证正在进行，报告未签核，无生产切换。下文旧门禁/计数按日期保留。

# Phase F 冻结准备与最终取证

验收依据为 `docs/TARGET_WORKFLOW_DATAFLOW.md` §9.1、§9.2、§15，以及本 change 的 6.1 → 6.4 → 6.6 → 6.7 → 13.7。所选研究范围为 MRVL、MSFT、NVDA、TSM 的 Routine，每个包含 Information 依赖。其他七家公司和未选 Event 不作为本轮前置；生产 profile 不改。

## 冻结前修复

- Information 的输入范围为目标及其静态信号链配置，输出仍属于目标。Fundamental 等其他消费者不继承信号链读取授权。每个目标的 `config/pead/<SYMBOL>.yaml` 纳入 plan 指纹，配置改变后旧 plan 拒绝执行。
- 新任务在授权范围内通过 `read_input(DOC_DATA)` 读取准入文档正文，不在单目标执行中运行另一个 scope 的全库抽取。使用 monitor 的原有 lookback 配置，不延长新闻窗口来伪装新信息，也不把财务报表施加新闻时效规则。
- 新简报保存文档版本/发布引用、事实标题定位、关联公司、发布/事件/抽取时间和置信度；缺独立事件时点时明确标为原发布时间代理并列入待核验，不制造精确事件时点。Dispatcher 绑定真实 workflow/Agent attempt、实际输入 vintage，发布路径记录配置模型和提示实现版本。模型或发布失败不产生降级成功简报。Information 使用独立事实分析提示，materiality 与 confidence 分开；缺置信度、产生 Fundamental expectation_changes 时拒绝，不采用旧 PEAD 投资论点提示。模型/提示版本只由实际 target 执行路径写入，避免误标其他抽取入口。
- MRVL 旧 dossier 的财报标签为空，但其 earnings_date=2026-08-27 与原 `pead_periods` 的 Q2 FY2027 相符。读取基线时核对日历期间和记录已知时间，返回引用摘要；原 dossier 和日历不改写。日期未匹配、期间冲突或未来才已知的记录不补标签。新增 Information 证据字段对旧 v1 空值不改变序列化和历史 hash，四个原 Information 投影已只读重开，仍不允许缺血缘的旧输出作为 Dispatcher 新任务复用。四标的基线期间为 MRVL Q2 FY2027、MSFT Q4 FY2026、NVDA Q2 FY2027、TSM Q2 FY2026；这不是新生成财报前预测或 Event 验收。
- Routine 保留基线摘要引用，缺有效带标签基线时拒绝。发布日期前缀的年份不再被当作指标数值；文本中的数值及模型归类仍需结合业务口径审阅，schema/血缘通过不能代替投资结论正确性。

上述共享实现及 manifest 影响十消费者的证据指纹。旧证据原样保留，不沿用其资格或签核。本轮必须在必要回归通过后冻结，任何后续受保护改动都要求重验和新的冻结。

## 真实输入与隔离取证

`scripts/capture_phase_f_research.py` 先准备选定实体的结构化原始行，保留全部文档、版本、准入和历史记录及完整 Workflow Memory。只排除无关实体的结构化 observations/candidates 和未使用的查询缓存；逐表比对复制内容，恢复原 triggers/indexes。源库通过只读连接访问，不改变原值、hash、时间、质量标记或旧投影。原始文档文件读取后，其正文进入固定输入包供离线重放。

需求矩阵在输出前冻结。实际 Dispatcher capture 使用真实配置模型；new/replay 分别由独立进程调用真实入口，禁止重放网络访问。通知在 runner 的隔离 transport 中抑制，不发送消息；研究范围不调用 broker 提交或创建生产审批。失败保留原始输出与原因；只有实际新运行与重放全部必需断言通过才能生成可供审阅的 unsigned 报告。报告 unsigned、数据资格和部署批准分别记录，不自动签核。

本地材料目录：`docs/validation/phase_f_final_gate_20261010/`。原始日志和输入库按用户规则留本地，不加入 Git。准备阶段的失败日志也保留，后续复验不覆盖历史结果。

## 当前进度

**2026-10-11 子进程环境缺陷已修复，159 项受影响回归与 strict 通过，v2 最终冻结已登记，6.1、13.7 再次完成。历史 v2 登记时为 103/117；真实 Routine 暴露多投影验收缺陷后，3.10、6.1、13.7 已重开，当前 100/117 完成、17 项待办。6.4 继续推进，6.6、6.7 待最终材料。下文旧冻结与审查拒绝为历史，用户已明确批准本轮外发。**

2026-10-10T14:40:00.196345+00:00 登记最终实现冻结，包含 550 个路径及全部十消费者。冻结文件为本地 `docs/validation/phase_f_final_gate_20261010/final_implementation_freeze.json`。登记时业务代码与广域门禁开始时一致；唯一后续保护面变化为下述正向测试函数的夹具修正，已完成对应复验。任何新的受保护修改仍须重新验证并登记新冻结。

广域门禁实际收集并执行预先指定的 583 项，**582 passed / 1 failed / 0 errors / 0 skipped**，用例无遗漏，执行期间保护指纹稳定。唯一失败是 `test_actual_routine_consumes_published_brief_and_preserves_refs` 只准备 Information brief、缺上一有效预期基线，按 TARGET §9.2 被真实 Routine 拒绝。补齐隔离夹具的带期间基线并检查输出的基线引用后，整个受影响模块、缺基线负向入口及架构/切流/记录/schema 守卫 **88 passed**。逐用例采用对应最后结果，583 个必需用例均有通过记录；不是把广域原结果重写为一次“583 passed”，也不把两轮重叠测试相加。测试函数 AST 和完整保护指纹对比证明修正未改变业务实现、其他测试函数或共享 fixture。原始失败 XML/log、诊断堆栈和逐项核对保留。

最终冻结后，最初五类审计反例的真实入口回归 **25 passed**：边界关闭拒绝实际发布、无初始化 capability 拒绝 broker session、缺报告/投影的 CLI 切换拒绝、部分资格不扩大路由范围、首个权威提交后重验失败保持联合冻结。使用隔离库和 FakeIB，没有连接 TWS。OpenSpec strict 与 `git diff --check` 通过。记录分别为 `final_current_engineering_gate.json`、`final_fixture_and_guards.*`、`final_five_audit_counterexamples.*`；这些证明工程行为，不赋予生产资格。

## 实际材料准备与资格缺口

按最终冻结时点和真实 plan 派生八个完整 RouteIdentity，保存为本地 `selected_exact_scopes.json`。每个目标都有窗口内 3 份完整准入文档，正文、版本/hash、模型上下文预览已只读整理；四份有效历史基线期间及引用也已核验。预览过程没有模型调用、模型结果、投影发布或签核，不冒充 6.4 的真实执行。源数据与 Workflow Memory 在准备前后的文件摘要一致，旧投影和原始数据未修改。

实际源库 `var/data.sqlite` 缺少 `dataflow_assurance_events`，八个研究 scope 当前 **0/8 eligible，原因均为 assurance_ledger_missing**。两次独立最小刷新均逐项拒绝 56 个无可接受原始证据的必需类型，追加 0 个事件；不初始化生产账本、不改变日期/hash、不全量采集。当前资格预检为 `pending`：账本缺失和实际报告绑定缺失分别列出，不写成“只等部署批准”。本地记录为 `final_source_qualification_preparation.json`、`minimal_refresh_one.json`、`minimal_refresh_two.json`、`final_preflight_pending.json`。

## 真实模型取证的授权阻塞

MRVL Information capture 被自动审批审查拒绝，命令未启动、没有外发。拒绝原因为缺少向具体第三方目的地发送准入文档 payload 的明确授权。本次需要批准的范围已具体准备为本地 `outbound_approval_manifest.json`，逐标的 `*_outbound_payload_preview.json` 可审阅完整上下文：

- 目的地：`https://openrouter.ai/api/v1`。
- Information：`deepseek/deepseek-v4-flash-0731`；四目标各一次独立 Information、一次 Information → Routine capture，共计划 8 次调用（失败可能重试）。内容为目标/静态信号链、窗口内新闻标题和时间及准入正文，每份最多 12,000 字符。
- Routine：`anthropic/claude-opus-5`；四目标共计划 4 次调用（失败可能重试）。内容为实际文档事实标题及本目标既有基线指标、叙事。
- new/replay 使用固定输入包中的模型响应，离线执行；账户、仓位、订单、审批记录和密钥不进入上述模型上下文。研究取证不调用 TWS、不下单、不发送通知、不自动签核或变更生产路由。

明确授权前不运行依赖外发的 capture。批准后按冻结继续八次独立取证；如果运行暴露新的受保护实现缺陷，则如实重开冻结、修复并重验。之后才能完成 6.4 原始材料接收、6.6 最终逐 scope 资格报告、6.7 当次刷新/预检。当前工程通过不消除原始证据与有效报告签核缺口，尚未进行生产读/调度切换或旧入口退役。

## 2026-10-11 授权后取证与 v2 冻结

用户原文批准「批准本轮所列内容向 OpenRouter 的两种模型外发，继续取证」，具体范围留本地 `explicit_outbound_approval.json`，不包含报告签核或生产部署。MRVL 第一轮真实 Information 的 Agent attempt succeeded 并发布新投影，但独立子进程继承父隔离路径而被 `_assert_distinct_destinations` 拒绝；MSFT/NVDA 的同版运行被停止，未覆盖其日志/部分库。旧尝试见 `original_real_capture_failures.json`。

修复仅为进入父隔离环境前保存启动环境，传给 new/replay 子进程，由每个进程自行建立完整隔离；不改核心保护、不允许生产别名。新增回归在实际父脚本边界验证环境，并启动两个真实 Python 子进程验证物理隔离；其业务/model 适配是明示的 orchestration fixture，不当作真实研究成功证明。完整受影响范围、守卫和五类反例 `replay_child_final_affected_gate.*` **159 passed**；strict 与 diff 检查通过。新冻结 `final_implementation_freeze_replay_child_v2.json` 的登记时点为 2026-10-10T23:50:01.591642+00:00，保护面仍 550 路径，仅脚本与对应测试指纹变化，影响十消费者；旧输入响应不沿用新指纹。实际研究输入仍显式固定已批准的 2026-10-10T14:40:00.196345+00:00，体现实现冻结时点和逻辑输入时点分离。

### 网络恢复后的实际结果与内容审阅

MRVL、MSFT、NVDA 的独立 Information 报告均已生成，new/replay 的必需机械断言通过，源库未变化，报告均为 unsigned。MRVL Routine 的真实 capture 已完成；其后独立进程验证、其他 Routine 和 TSM 仍在执行。所有新输出留在 `tmp/phase_f_final_research/*_v2/`，不改历史投影。

内容抽查发现 MSFT Information 的 summary 将原文 `1.44 million` 芯片写成“1.44 万颗”：正确换算为 144 万，差异为 100 倍。具体原文、输出、文档预览摘要和投影引用保存在本地 `actual_information_numeric_content_failure.json`。这是真实内容失败，不能因为 schema、血缘和重放断言通过而签核。机械通过和业务内容失败必须分列；其余运行继续取证，不能由此宣布本轮业务已达标。

无模型 COHR Technical 使用真实公开 Yahoo 行情完成 capture/new/replay，断言通过，保持 unsigned。对其实际运行证据登记的 `runtime_query`、`timestamp` 两类一日 TTL 事件，在独立隔离账本复用后原时点未变；两日后的真实资格查询拒绝过期事件，scope 仍 ineligible。记录为本地 `actual_short_ttl_v2.json`。其余必需证据未伪造补齐，不赋予生产资格；这是短 TTL 验证材料，不扩大四标的研究部署范围。

生产退出只读盘点仍为 pending：缺 `production_consumer_receipts` 和 `schedule_scope_owners`，登记的 16 项退出对象没有当前生产清零观察证明。记录为本地 `production_inventory_after_outbound_approval.json`。既有隔离演练不替代生产切换、停止与观察窗口。


### 多投影与数量条件修复进行中

MRVL、NVDA Routine 的真实 capture/new/replay 均完成，但最终必需 analysis 断言失败，原因为验收器把每个任务的输出数固定为 1。追查发现 Chief manifest 同样覆盖掉同 role/scope 的其他更新，冻结核验也硬性按任务数计算投影数；跨运行复用还可能从同一生产者的一组更新中只选择一份。为满足 TARGET §9.2、§15 的实际输出完整性，3.10、6.1、13.7 已重开。旧冻结、三份通过的 Information 报告、两个失败的 Routine 报告与被停止候选均保留，不沿用其签核或资格。

修复范围事先记录为本地 `multi_projection_and_numeric_impact.json`，共 8 个受保护路径，影响十消费者。Routine 的完整唯一输出集合须逐份核对 scope/schema/hash/时效/输入 vintage/生产者与当前 attempt 的完整引用；Chief 不丢成员，其他角色仍执行单投影契约。跨运行复用若只有单份选择能力，则对多份 Routine 生产者重新计算，不能宣称部分成功。同输入的新 capture 前从已批准、同 entity/as-of 的原文预览冻结明确数量/单位条件；模型输出的百万、万、亿等显式数量须落在原文集合内。此条件只证明数量单位一致，不保证所有语义、归属或投资判断正确。

首轮三份更新的隔离诊断为 9 passed / 1 failed；失败是新复用拒绝分支遗漏 json 导入，已修正。首次失败 XML/log 不覆盖；多输出、各成员损坏、数量约束和子进程隔离正在补验。此阶段尚未重新冻结，不将夹具测试、受保护修改前的真实模型响应或原报告改标为最终资格证据。


### 同批更新的审批检查及最终复验

继续检查实际 revision/review/Boss 审批链，发现 `frozen_snapshot_stale_reasons` 仍把同一生产者、同一冻结快照中其他 Routine 更新误判为新版本替换。修复前扩展影响清单为 `multi_projection_complete_bundle_impact_v3.json`，受保护路径增至 9 个。只对同一非空 workflow/agent producer 且已完整引用的成员免除相互替换判断；每份的撤销、hash、到期检查继续执行，真正的新生产者仍使旧快照失效。

专项诊断 `v3_sibling_diagnostic.*` **3 passed**：实际三更新快照可创建 revision、登记风控 review 与隔离人工审批；最新成员的生产者损坏和后来另行发布的新投影均触发 supersede。此处人工审批是明确 fixture，不是用户签核或生产订单授权。

初次扩大到整个历史模块的探索性回归 `multi_output_v3_final_gate.*` 在停止前 93 passed / 11 failed；11 项均在旧 Chief 测试夹具的 qualified-scope 前置拒绝，尚未进入多输出逻辑，原诊断记录不删除。它不是既定的最终必需用例选择。完整新入口六角色/模式/缺项/成员损坏由新入口必需模块覆盖，不以旧双运行比较重新阻塞新设计。

`v3_required_gate.*` 在受保护修改期间执行，停止前 135 passed / 1 failed；指纹稳定检查为 false，不计为最终门禁。失败的部分资格切流用例另行诊断 `v3_partial_diagnostic.log` 1 passed；不据单次重跑删除失败。当前正在执行固定选择的 `v3_bundle_final_gate.*`，包含前次 159 项精确选择及完整受影响新入口、checker、Chief/Dispatcher、reports、独立 runner、隔离集成和数量预声明模块。待其全部通过并确认指纹稳定后再登记新冻结和更新任务状态。


### 无 manifest 的完整复用

继续沿 Chief 直接读取现有投影的入口检查，发现未提供 Dispatcher manifest 时仍只选择最新一条 Routine 更新。该读取本身属于被要求的研究快照完整性问题；不恢复旧双读业务验收。修复前记录 `multi_projection_complete_bundle_reuse_impact_v3.json`，文件并集仍为原定 9 条。现在从成功且同 scope 的真实 Agent attempt 解析完整唯一引用，逐份通过 Chief 治理读接口重开；缺成员、失败/缺失生产者、重复引用或其他生产者成员均为不完整。旧无生产者记录的兼容读取不作为本轮新 Dispatcher 证据。

`v3_standalone_diagnostic.*` 5 passed，新增的其他生产者成员反例由最终门禁覆盖。前一轮 `v3_bundle_final_gate.*` 停止前 **244 passed / 0 failed**，因受保护补充修复指纹不稳定，仍是诊断记录，不登记冻结。最终固定选择重验改为 `v3_complete_final_gate.*`，包含全部新增反例；已获外发授权的后续控制器只有在该门禁 exit=0、指纹稳定、strict 通过且影响清单一致后才登记 v3 冻结并启动原范围模型 capture。当前没有 v3 模型外发、签核或生产切换。

### 最终回归发现的边界及后续重验缺口

`v3_complete_final_gate.*` 最终 **300 passed / 1 failed**。架构守卫发现 Chief 直接导入 sqlite3 并执行生产者 SQL；此失败属于新改动，应修复，不归入旧入口缺陷。原失败与稳定指纹记录保留，未登记 v3 冻结，后续模型控制器在门禁失败处退出，没有外发。完整生产者查询已移到工作流层，Chief 仅调用工作流接口；不添加架构例外。

另有隔离实际运行反例 `v3_late_original_diagnostic_retry2.log`：无 Dispatcher manifest 的快照首次组装有效，后来生产者标记 failed 时校验未拒绝。修复后直接复用的 Routine 在每次决策/审批使用前重开生产者成功状态、任务身份和完整有序引用；不以首次读取代替后续验证。新增失败生产者、缺引用和缺生产者三种反例，同时检查真实审批写入被拒绝且没有审批落库。影响仍在登记的九文件范围内。重新执行固定完整门禁后才允许冻结和已授权的模型取证，不将 300 项通过宣称为最终门禁通过。

`v3_final_repair_diagnostic.log` **4 passed / 36 deselected**：当前树架构守卫及三个实际后续生产者损坏反例通过；反例包含审批拒绝、无 boss_approvals 写入。固定完整门禁改为 `v3_repaired_final_gate.*`，所有修复均在开始前完成；本轮采用同一最终状态的完整重验，取消此前分主门禁/补验后合并方案。已批准范围的模型控制器只在该新门禁全部通过、指纹稳定、strict 有效且九文件影响一致时运行。旧失败记录不覆盖。

门禁期间源 `var/data.sqlite` 文件哈希与原授权记录不同，故未直接继续外发。只读重新生成四标的原时点预览，`outbound_source_payload_recheck_20261011.json` 证明四份完整 payload 逐字节 SHA256 相同，文档引用/日期/正文和 Routine 基线均未变，模型及目的地也未变；`var/ats.sqlite` 未变。保留原授权及原 source 哈希，追加当前源状态的内容等价核对记录，不伪称整个源数据库未变化。外发内容仍使用原批准预览，控制器逐次核验经此只读核对的当前源哈希；之后若再漂移即拒绝，不扩大授权。

### v3 最终冻结已登记

`v3_repaired_final_gate.*` **304 passed / 0 failed / 0 skipped**，执行耗时 2600.22s；运行前后受保护指纹稳定，strict 有效，九文件变化与登记影响清单一致。`v3_final_gate_review.json` 与 `final_implementation_freeze_multi_output_v3.json` 保存 550 路径、十消费方及完整用例引用，冻结时点 2026-10-11T10:51:14.419332+00:00。3.10、6.1、13.7 恢复完成；未将工程门禁当作生产数据资格。后续四标的真实模型取证仍按 6.4 单独核验，所有原失败保留、报告待显式签核。

### v3 真实内容发现 Routine 数值语义缺陷，冻结再次重开

MRVL Routine 的实际三条更新中，未匹配基线 KPI 的第三方标题 `$40B` 被确定性 `build_expectation_updates` 的首数字解析写为 `metric=narrative,new_value=40`，无读数的叙事写为 0。独立最小反例 `routine_narrative_value_failure_v3.log` 复现。304 项工程检查未覆盖此业务语义；不能据原断言通过记为有效预期更新。已登记 `routine_value_semantics_impact_v4.json`，3.10/6.1/13.7 在实施前再次重开，当前 100/117。保留 v3 冻结和原始成功/失败报告，不修补其 payload。批次控制器已停止；停止前已启动的 MSFT Routine 子进程自然完成后才修改源。新增修复不计划改变外发模型或上下文，后续继续沿用原精确内容授权。

v4 初步值语义诊断 `v4_value_semantics_diagnostic.log` 67 passed；为确保历史审计可读，值语义契约随后移到新发布/复用/当前决策与 checker 边界，`v4_value_contract_diagnostic.log` 再次 67 passed。schema 为 narrative 允许显式 null，但保留历史数字记录的解析，不重写旧行；numeric KPI 无读数仍拒绝。Routine 对数字采用 KPI 后局部读数，拒绝未绑定标题数字、日期/年份及无单位契约的货币/数量级读数；未量化结果保留分类 driver，两个数值 null。Chief 明确显示定性更新，当前快照会标记伪数值成员 `value_unconfirmed`。模型分类 prompt、baseline 构建及外发角色配置未改，后续实际取证仍为原批准内容。

v4 真实快照语义反例首次 1 passed / 1 failed，是反例用 dict compact JSON 与规范 payload spaced JSON 两种哈希计算路径不一致，先触发完整性拒绝；原日志 `v4_actual_value_guard_diagnostic.log` 保留。改为使用正式 `validate_payload` 规范对象重算正确 hash 后，`v4_actual_value_guard_diagnostic_retry.log` 1 passed：Chief 快照不完整且 `value_unconfirmed`，Dispatcher 拒绝复用，analysis checker 和 Chief 固定要求校验均拒绝。有效多成员正向用例在首轮已通过。此处没有弱化完整性断言。

v4 最终预声明改动相对 v3 为 9 路径，相对 v2 累计 13 路径，`v4_full_impact.json` 保存并集。`v4_final_gate.*` 覆盖前次完整新入口/守卫/集成集合及完整 Fundamental/schema 模块和新增语义反例，所有修复在运行前完成。strict 与 diff-check 已通过；后续已授权模型控制器只在最终完整门禁全部通过、指纹稳定并核对两个影响清单后才登记 v4 冻结、调用原批准精确内容。不改模型分类 prompt/baseline，不签核、不部署、不向生产账本伪造补齐。

v4 冻结核对将实际修改与运行时依赖闭包区分：9 个修改文件中，8 个属于现有 550 路径保护面，`tests/test_task_projection_envelope.py` 是新增覆盖的补充测试、原运行时闭包不含它；相对 v2 的 13 个修改文件中为 12 个保护路径及该补充测试。原预声明九文件清单保留并增加 `protected_paths/supporting_test_paths`，不把未在旧指纹中的路径伪称为保护变化。补充模块位于完整门禁末尾；确认其尚未执行后登记 `v4_supporting_test_before.json`，记录执行前精确文件 SHA，结束时再次核对，并附入最终冻结的 supporting_test_fingerprints。实现/schema/checker 的运行时完整指纹面仍为 550，测试文件核对为额外门禁，不绕过必需断言。

### v4 已通过，真实输出暴露 confidence 请求契约缺口

`v4_final_gate.*` 344 passed / 0 failed / 0 skipped，2679.88s，指纹稳定；v4 冻结已于 2026-10-11T12:01:23.603854+00:00 登记。真实 MRVL Information/Routine、MSFT Information 均通过；MSFT Routine 三次因上游缺独立 confidence 失败并保持依赖阻断，未复用旧 brief。发布拒绝正确，但共享 ContextUpdateView 的 schema 将 confidence 设为 optional/nullable，与 Information 必填契约不一致。已登记 `information_required_confidence_impact_v5.json`：仅 Information 专用响应 subtype 将 confidence 必填且非 null，旧 PEAD 类型不改；不推断、不补填，不放宽发布门禁。v4 批次控制器停止，已有 NVDA Information 子进程自然完成后才实施。所有原证据保留；报告未签核、生产资格未赋予、无路由切换。


### v5 完整回归的模型替身修复

原 v5 回归中的 `tests/test_phase_f_research_gate.py` 模型替身以精确类名接受旧 `ContextUpdateView`，因此拒绝 Information 新的必填置信度子类。独立诊断已复现 `unexpected model schema: InformationContextUpdateView`。原运行已中止并保留日志与稳定性记录，不作为通过或冻结依据。替身改为接受该契约的子类；仍通过请求传入的 schema 构造输出，置信度和 materiality 保持独立，角色计算、发布器和业务断言未放宽。此测试文件已纳入 v5 预声明影响范围，重新运行使用 `v5_repaired_final_gate.*` 独立记录。

只读外发核对 `outbound_business_content_equivalence_v5.json` 证明四个标的的原批准业务内容、系统提示、文档、基线、端点、模型配置与 Routine schema 均相同；仅 Information 响应 schema 的标题、说明及必填非空置信度定义变化。完整预览字节不同，不冒充原预览字节相同，原批准预览保留。

替身修复后的独立快照依赖诊断 27 passed（777.04 秒），覆盖整组 Routine 真实发布、Chief 绑定/复用、成员损坏拒绝及独立后续审批/授权重查。仅为定向诊断，最终冻结仍等待完整 `v5_repaired_final_gate` 的无失败、无跳过及指纹稳定结果。更新后的 OpenSpec strict 已通过。


### 治理目录 CLI 查询的可写初始化与只读内容重核

本轮尝试使用消费 skill 推荐的 `ats data catalog/describe` 盘点源记录，发现 CLI 默认调用 `get_platform_data_products()`，经可写 structured 仓库执行 `bootstrap_catalog()`，不能把这个目录查询视为物理只读证明。源库字节指纹发生变化；此行为不作为生产证据追加、路由或部署结果。后续核对统一使用显式只读仓库，不继续依赖该 CLI 查询保证只读。

`outbound_business_content_equivalence_v5_post_cli.json` 保留前一核对记录，并关联新只读 recheck：四个完整 v5 预览逐字节等于目录查询前的 v5 预览；原批准业务上下文、系统提示、文档、基线、目的地和模型仍完全一致，只有此前声明的 Information 响应 schema 变化。只读 recheck 前后源库 hash 一致，当前 data.sqlite hash 为 `22c37710f7c1c42e251f911b8c325801088a852e37de7179848cd9acb170f3f5`，原审批 manifest/hash 及旧预览不改写。后续 capture 使用新的源库指纹检查；再次变化则拒绝，不把整个数据库未变当作事实。该 CLI 初始化不触及受保护实现闭包，当前取证入口已经显式只读，本轮不为旧目录 CLI 另做受保护实现变更。


### Information 响应契约的跨进程位置修复（v6）

`v5_repaired_final_gate` 的 Routine 独立 capture/replay 失败。原 `fixed.sqlite` tape 中的类型为 `ats.agents.information.documents:InformationContextUpdateView`，只读重开直接复现 `frozen type outside data/schema contracts`（`v5_recorded_schema_decode_failure.json`）。此为本次新契约的真实重放缺陷，不能排除为旧流程故障。v5 门禁已中止，状态 143、原日志/tape 保留，不登记冻结；门禁等待程序明确拒绝外发及 Technical capture。

按原显式重放白名单把响应类移到 `ats.agents.information.outputs`，documents 仅导入类型。模型 JSON schema 和业务内容不变，不扩大解码白名单。`information_replay_contract_impact_v6.json` 预声明四条修改路径，包含新增 outputs 模块。诊断 36 passed（18.63 秒），新增契约往返与 documents 类型地址仍拒绝的反例；完整门禁使用独立 `v6_repaired_final_gate.*` 记录，最终 source 闭包因新增模块增长一条，冻结及真实取证仍等待完整回归。

新增 outputs 后，第一次 v6 启动在 `freeze_snapshot()` 预检查被拒绝：模块尚未登记进受保护路径，pytest 尚未启动，未产生冻结或外发。预检查拒绝记录单独保留。已先扩展 v6 影响清单，登记 `config/data/target_dataflow_coverage.yaml` 的 required fingerprint path；v6 完整回归现在启动，闭包为 551 路径、10 消费者，计划改动为 5 路径。manifest 变化影响全部消费者证据，不沿用旧资格，原 v4/v5 状态保留。


### v6 最终门禁、冻结及真实取证启动

完整权威 uv 门禁 348 用例：347 passed / 1 failed / 0 skipped，2593.43 秒（43 分 13 秒）；唯一失败是进度文档当前保护路径计数 550 未更新为新增契约后的 551。仅修正未受指纹保护的进度文档，整个文档模块权威 uv 补验 7 passed（2.65 秒）。补验首次因未设置 UV_CACHE_DIR 无法初始化受限默认缓存而在 pytest 前退出，原错误日志保留；使用既定临时 uv cache 后通过。运行代码、配置与测试在完整门禁后未修改。

`v6_final_gate_accepted.json` 以同 ID 最新结果覆盖唯一文档失败，348 个原必需用例均有当前通过结果，原失败未抹去；保留完整运行、补验及合并依据。前后 551 条依赖指纹及支持测试 hash 均稳定，strict 通过。`final_implementation_freeze_information_confidence_v6.json` 登记于 2026-10-11T14:03:40.586641+00:00，完整十消费者闭包；`v6_repaired_final_gate_review.json` 带合并来源。

3.10/6.1/13.7 恢复完成，103/117、14 待办。沿用原用户外发授权，核对业务内容一致后启动独立 v6 八个原始研究 capture；COHR 公开 Yahoo Technical 与真实原时点短 TTL 验证同时启动。报告签核、生产资格及路由/调度部署均未执行；6.4/6.6/6.7 仍等待实际新证据与复算结论。
