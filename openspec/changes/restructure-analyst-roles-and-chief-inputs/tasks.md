## 1. 地基：投影 payload、守卫角色映射、六类判定

- [x] 1.1 在 `src/ats/agent/task_projection.py` 为 `InformationBriefPayload` 增补可选字段 `fact_changes` / `impact_candidates` / `entities` / `confidence` / `freshness` / `unverified`，保持 `schema_version` 为 `v1` 且旧 payload 仍可校验通过。验证：新增测试用仅含旧字段的 payload 调用 `validate_payload("information_brief", ...)` 仍通过，含新字段亦通过。
- [x] 1.2 为 `FundamentalEventReviewPayload` 增补可选字段 `scorecard` / `guidance` / `narrative` / `confidence` / `falsifiable_conditions`，同样保持向后兼容。验证：同上，两类 payload 的旧形态与新形态均通过校验。
- [x] 1.3 在 `src/ats/workflow/architecture_guards.py` 的 `ROLE_BY_PATH_PREFIX` 中，把 `src/ats/agents/evidence` 的角色映射由 `information_analyst` 改为 `evidence_observer`，并新增 `src/ats/agents/information` → `information_analyst`。验证：`tests/test_architecture_guards.py` 仍全绿，且新映射可被 `scan_agents()` 命中。
- [x] 1.4 在 `src/ats/workflow/run_contracts.py` 把必需性判定由 task_id 改为角色：新增「角色 → 可满足它的 task_id 集合」结构，基本面由 `fundamental_expectation_update` 或 `fundamental_event_review` 任一命中即满足。验证：新增测试断言只提供事件评审时基本面判定为满足，并把命中的 task_id 记录到结果里。
- [x] 1.5 把 `technical_review` 的 `required_for_decision` 置为 `True`，使必需类别为六类。验证：新增测试断言 `default_registry()` 解析出的必需角色集合恰为六类。
- [x] 1.6 为快照消费方暴露「按角色取可用投影」的读取函数（读 `task_projection_envelopes`，按 `agent_role` + 作用域 + 有效期过滤并返回最新一条）。验证：新增测试构造两类投影，断言按角色取回的是各自最新且未过期的一条。
- [x] 1.7 为 `InformationBriefPayload` 增补可选字段 `event_time` / `published_at` / `extracted_at` / `cluster_key` / `source_count` / `independent_sources`，保持 `schema_version` 为 `v1` 且旧 payload 仍可校验通过。验证：新增测试断言旧形态与含三类时间、聚类字段的新形态均通过校验。
- [x] 1.8 在 `validate_payload("fundamental_event_review", ...)` 增加越界字段断言：payload 出现 action 词表取值（`buy|add|hold|trim|sell`）或 `qty` / `notional` / `weight` 类字段即判不合法。验证：新增测试断言含这些字段的 payload 被拒，`direction` 取 `-1|0|1` 的正常 payload 通过。

## 2. 层级分析师：撤销配置权，改出状态判断与投影

- [x] 2.1 在 `src/ats/schemas/sector.py` 为层级结论新增 `layer_status: expanding|steady|contracting|unclear` 字段，并把 `allocation` 标记为退役（保留列、新写路径不再写入）。验证：新增测试断言新构造的 `LayerVerdict` 无 `allocation` 语义参与，且历史行的 `allocation` 列仍可读回。
- [x] 2.2 改造 `src/ats/agents/sector/layer_review.py`：输出由配置结论改为层级状态判断，保留 confidence、周期位置与逐条议题归因。验证：`tests/test_layer_review.py` 中「结论必须锚定议题结论」「证据不足时的默认」「本层没有命题」「议题冲突」四个场景改用 `layer_status` 断言并全绿。
- [x] 2.3 把证据不足与本层无命题的默认改为 `steady` 且 confidence ≤ 0.3，并保留「证据缺失」与「本层无命题」两种不同标注。验证：新增测试断言两种情形的标注文本与状态取值不同。
- [x] 2.4 确保层级上下文不含宏观判断，且相对命题读数只进入结构因子与排序理由、不进入层级状态依据。验证：新增测试断言上下文中无宏观字段，且仅由相对命题驱动时状态判断不产生方向性结论。
- [x] 2.5 实现层级评审的投影发布：评审成功后调用 `build_envelope()` 写入 `agent_role=layer_analysis`、作用域为该层的投影，并记录输入引用与数据 vintage。验证：新增测试断言写入后的投影可取回、字段完整且内容哈希稳定。
- [x] 2.6 实现同一层同内容的幂等：重复评审产出相同 payload 时不新增第二条投影。验证：新增测试连续两次发布，断言投影数量不变且返回既有标识。
- [x] 2.7 层级评审失败时登记该层缺失且不写入投影，不因单层失败中止其余层，也不用上次结论冒充本期判断。验证：新增测试让其中一层抛错，断言其余层仍有投影且失败层无投影、有留痕。
- [x] 2.8 层报告首节改为「状态判断 + 逐票相对排序」，预算与权重章节改由行业报告承载；保持每层一份、结论先行。验证：`tests/test_layer_report.py` 断言首节含状态判断且不含预算使用率明细。
- [x] 2.9 在 `config/workflow/legacy_retirement.yaml` 登记 `layer_verdict.allocation` 为待退项（替代实现、退出条件、消费方清零判据）。验证：`tests/test_legacy_retirement.py` 断言该项存在且字段完整。
- [x] 2.10 把 `src/ats/agents/sector/layer_review.py` 迁到新建的 `src/ats/agents/layer/` 包（层级分析师独立成包），同步更新 `agents/sector/review.py:74,126`、`runtime/cli.py:784,812` 与测试侧的导入，并把 `architecture_guards.py:33` 的 `ROLE_BY_PATH_PREFIX` 前缀改为 `src/ats/agents/layer`。验证：`tests/test_architecture_guards.py` 仍全绿且新前缀可被 `scan_agents()` 命中，`tests/test_layer_review.py` 全绿。

## 3. 行业分析师：承接三级配置权、预算与护栏

- [x] 3.1 把 `utilization_for` / `budget_for` / `budgets_for` 的调用主体由层级路径迁移到行业评审路径，映射关系与 `risk.yaml` 的 `layer_utilization` 保持不变。验证：`tests/test_layer_budget.py` 与 `tests/test_layer_groups.py` 在迁移后仍全绿，断言数值不变。
- [x] 3.2 行业评审改为消费 `LayerAnalysis` 投影产生三级配置（行业 / 层次 / 标的），并保证标的权重之和等于该层预算。验证：新增测试用两条层级投影驱动一次行业评审，断言三级输出齐备且数值自洽。
- [x] 3.3 移除 `agents/sector/review.py:229-241` 对宏观报告的读取（含 `regime_block()` 注入轮动的部分）。验证：新增测试断言轮动上下文与输出中不含宏观 regime，且 `tests/test_sector.py` 相应夹具更新后全绿。
- [x] 3.4 移除 `agents/sector/assemble.py:257-279` 对 PEAD dossier 的读取与 `_pead_conclusions()` 注入。验证：新增测试断言上下文不含 dossier 叙事与 Scorecard。
- [x] 3.5 行业评审在缺少某层投影或投影过期时退回「标配」与保守默认使用率，并显式标注缺失而非景气中性。验证：新增测试断言缺失层与过期层都被标注且使用率取保守默认。
- [x] 3.6 跨层轮动只回答利润池迁移方向，发现相邻层矛盾时标注待人工裁决而不改写层级状态。验证：新增测试构造矛盾层级投影，断言输出含待裁决标注且层级投影未被改动。
- [x] 3.7 行业配置以 `SectorAllocation` 投影发布，`input_refs` 含本轮消费的全部层级投影标识。验证：新增测试断言可沿输入引用取回层级投影的内容哈希与 as-of。
- [x] 3.8 在架构守卫中确认 `sector_analyst → layer_analyst` 是唯一允许的跨角色读取，并断言行业模块内无其他跨角色投影读取。验证：`tests/test_architecture_guards.py` 新增断言行业目录只命中这一条许可。
- [x] 3.9 行业配置输出保留证据冲突：层级判断与标的层面证据相反时分列两条依据，标的多条事实矛盾时标注待人工裁决，不通过加权平均消解。验证：新增测试构造冲突输入，断言输出含两条依据与待裁决标注且未合并为单一分数。
- [x] 3.10 层级投影 `schema_version` 或 payload 结构不兼容时按缺失处理并留痕，禁止字段兜底或猜测映射。验证：新增测试用旧 `schema_version` 的层级投影驱动行业评审，断言该层被标注为不可用且配置取保守默认。

## 4. 信息分析师：新建角色与能力迁移

- [x] 4.1 新建 `src/ats/agents/information/` 包，实现只消费已准入文档与中性证据的输入装配（读文档资产与数据产品，不发起取回）。验证：新增测试断言该包内无 `PROVIDER_MODULES` 中的任何导入，且守卫扫描该目录无 provider 违规。
- [x] 4.2 迁移 `agents/pead/research.py` 的文章 / 通讯抽取能力到信息分析师，产出写为信息简报投影，不再直接改写 dossier。验证：新增测试断言抽取结果只产生简报投影，`pead_dossier` 内容不变。
- [x] 4.3 迁移 `agents/pead/triage.py` 的材料性评估，并把 `enrich()` 中直连取数的路径改为经数据产品入口。验证：新增测试断言迁移后无直连调用，且 `tests/test_triage.py` 对应场景全绿。
- [x] 4.4 迁移 `agents/pead/monitor.py` 的文档识别能力，移除 `_apply()` 直写 dossier 叙事与预期的副作用。验证：新增测试断言 monitor 链路运行后 dossier 未被修改，只产出简报。
- [x] 4.5 迁移 `runtime/digest.py` 的 intel digest 摘要能力，输出仍为简报投影而非直接落库结论。验证：`tests/test_digest.py` 在迁移后全绿，且产出可查回为投影。
- [x] 4.6 实现简报的六类要素校验：缺事实变化或待核验项即判该次产出失败，不以自由文本降级写入。验证：新增测试断言缺字段时抛 `EnvelopeValidationError` 且库内无残缺投影。
- [x] 4.7 实现「买卖 / 仓位 / 组合建议」拦截：抽取结果出现方向性交易建议时判为不合法并拒绝写入。验证：新增测试用含「增持 / 目标仓位」的模型输出，断言被拒且不落库。
- [x] 4.8 实现按「来源文档版本 + 抽取逻辑版本」的幂等，重复处理同一文档版本复用既有简报；文档出新版本则产出新投影并引用前一版本。验证：新增测试断言重复处理不新增投影、新版本产生第二条且旧投影保留。
- [x] 4.9 暴露信息分析师的独立入口（手动 `ats analyst information`、定时任务的显式调用、文档准入事件触发）并保证其独立终结：运行不依赖其他分析师投影，且不触发基本面或主理人流程。验证：新增测试断言单独运行信息分析可正常终结且未产生基本面或决策记录。
- [x] 4.10 为每条事实变化落三类时间（事件时间 / 发布时间 / 抽取时间），并把时效与 cutoff 判定改为以事件时间或发布时间为准。验证：新增测试断言一份「早发布、晚入库」的文档按发布时间判时效，且不因抽取时间新而被判为本期新增。
- [x] 4.11 实现同源聚类：同一事件的多份文档归入同一 `cluster_key`，简报记录文档数与独立信源数，置信度不因簇内文档数量上调。验证：新增测试用四份同源报道，断言归为一簇、独立信源数为 1 且置信度未上调。

## 5. 基本面：例行与事件双模式，剥离内部风控

- [x] 5.1 定义双模式的触发契约与显式入口（命令行 `ats analyst fundamental --mode routine|event` 与显式运行请求），例行由信息简报或预期数据变化触发，事件由财报 / 公司事件触发。验证：新增测试断言两种入口分别产出对应模式的运行请求且互不混淆。
- [x] 5.2 例行模式：读取上一有效基线，把新信息分为确认 / 否定 / 新增 / 待验证，更新市场隐含预期、叙事、关键 KPI 与可证伪条件，产出 `FundamentalExpectationUpdate`。验证：新增测试断言四类归类随投影留痕且输出不含交易动作。
- [x] 5.3 事件模式：在 cutoff 冻结基线与其引用，并只使用报告期间正确且通过准入的 actuals、财报稿、指引与电话会。验证：新增测试断言 cutoff 后到达的信息不改写基线，期间不符材料被排除并留痕。
- [x] 5.4 事件模式分别计算对冻结基线、Consensus 与市场隐含预期的差异并产出 Surprise Scorecard、指引评估与叙事更新，三者方向不一致时保留分歧。验证：新增测试构造三者方向不一致，断言输出保留三项且未取平均。
- [x] 5.5 电话会迟到时生成事件评审新版本，早期版本保留不被覆盖。验证：新增测试断言同一报告期存在两条事件评审投影且早期版本可查回。
- [x] 5.6 移除 `graph/pead.py:473-519` 中 `risk_agent.assess` / `review_guardrails` / `pre_trade` 的调用与 `PeadRecommendation → TradeDecision` 的转换。验证：新增测试断言事件模式全流程不触碰风控模块，且 `tests/test_pead_graph.py` 相应场景更新后全绿。
- [x] 5.7 移除 `agents/pead/score.py:189-231` 依据 `portfolio` / `net_liquidation` 推导数量的分支，改为只输出方向、幅度、信心、理由与可证伪条件。验证：新增测试断言输出无目标股数 / 金额 / 权重字段。
- [x] 5.8 移除 `agents/pead/monitor.py:85-95` 的 sector / macro 提示注入与 `graph/pead.py:125-144` 的 sector / macro 准备块注入（含移除对应注入开关，而非保留为可开启选项）。验证：新增测试断言配置中的 `inject_prep` 开关已不存在，且上下文无行业 / 宏观内容。
- [x] 5.9 双模式产出分别写为 `FundamentalExpectationUpdate` 与 `FundamentalEventReview` 投影，同一报告期两类投影各自保留。验证：新增测试断言两类投影可分别按角色查回且互不覆盖。
- [x] 5.10 改造 `graph/pead.py:180-206` 的 `_peer_report()`：跨标的信号只取中性事实（上游已报实际值、指引区间、财报日期，取自 `data/fundamentals.py` / `data/consensus.py` 等数据产品或该标的的 `InformationBrief`），丢弃 `decision_summary`、`scorecard.band` 与 `guidance` 自由文本。验证：新增测试断言信号链上下文不含上游评审结论与分档字段，且仍能取到已报实际值。
- [x] 5.11 在架构守卫中断言基本面模块不得读取 `agent_role` 为 `fundamental_analyst` 且作用域非本标的的投影。验证：`tests/test_architecture_guards.py` 新增断言此类读取被判为违规。

## 6. 风控输入收口

- [x] 6.1 移除 `agents/risk_officer/review.py:29-39` 对 `latest_macro_review()` 的读取与宏观 regime / 象限简报的上下文注入。验证：新增测试断言风控 memo 上下文中无宏观字段，且 `tests/test_risk.py` 相应夹具更新后全绿。
- [x] 6.2 断言确定性审查的输入只含提案、组合快照、市场快照与规则包，且审查结果不引用任何宏观观点。验证：新增测试断言 `review_revision` 的输入集合中无宏观项。
- [x] 6.3 在架构守卫中断言 `risk_officer` 无任何被允许的跨角色读取（当前许可表已不含它），并扫描其模块无投影读取调用。验证：`tests/test_architecture_guards.py` 新增断言命中零许可且扫描无违规。
- [x] 6.4 风控结论记录其依据的组合快照标识、市场 as-of 与规则集版本，三者缺一即判该审查不可用于放行。验证：新增测试断言缺任一项时放行判定为否。

## 7. 主理人：固定快照、六类齐备、缺口阻断

- [x] 7.1 接线 `decision/snapshot.py::build_research_snapshot()`：在 `graph/chief.py` 的 `assemble_context` 之前构建快照，逐项记录六类的投影标识、内容哈希、as-of 与新鲜度。验证：新增测试断言快照含六类条目且每条字段完整。
- [x] 7.2 接线 `open_decision_cycle()`：快照不完整时在写入决策周期之前拒绝，不产生空周期记录。验证：新增测试断言不完整时不创建 cycle 且返回明确原因。
- [x] 7.3 把 `state.research_snapshot` 真实传入 `repo.create_cycle`（当前 `chief.py:214-216` 传入空 dict）。验证：新增测试断言持久化后的 cycle 可取回完整快照。
- [x] 7.4 在 `agents/chief/assemble.py` 新增从投影读取六类分析的路径，与现有直读旧表路径双读比对，差异按类别计数并记录。验证：新增测试断言同一输入下两条路径给出的类别集合一致，差异计数为 0。
- [x] 7.5 某类投影取不到时判定为缺口，不以空字符串或省略章节的方式让决策照常进行。验证：新增测试断言缺失类别被记录为缺口且决策流程被阻断。
- [x] 7.6 修订轮次内复用同一份快照，不重跑分析师、不偷换研究输入。验证：新增测试断言多轮循环后快照标识不变。
- [x] 7.7 快照失效（关键数据 vintage 变化或投影被撤销）时把 cycle 转为 `superseded` 或人工处理。验证：新增测试断言两类情形下 cycle 状态转为失效且未沿用原 revision。
- [x] 7.8 实现缺口报告产出：列出缺失 / 过期 / 失败的类别与原因，且该报告不作为可决策输入进入主理人。验证：新增测试断言报告内容完整且不出现在决策上下文中。
- [x] 7.9 主理人上下文把基本面 `direction` 作为研究输入呈现，不提供把该取值直接映射为交易动作的路径。验证：新增测试断言决策上下文中存在方向字段但提案生成不以其为唯一依据，且代码中无 `direction → action` 的映射表。

## 8. 边界收敛：采集侧迁出与 Provider 直连清退

- [x] 8.1 把 `agents/evidence/observer.py` 的采集侧取数与原始资产写入迁出 `agents/` 目录至数据层取数入口。验证：新增测试断言 `src/ats/agents/` 下不再存在这些取数调用，且迁移后功能用例全绿。
- [x] 8.2 把 `agents/macro/assemble.py` 的 factset / regional / websearch 取数改经数据产品入口。验证：新增测试断言该模块无 provider 导入且取数结果不变。
- [x] 8.3 把 `agents/sector/assemble.py` 与 `agents/sector/` 其余模块的 factset / consensus / fundamentals / industry / regional 取数改经数据产品入口。验证：新增测试断言这些模块无 provider 导入。
- [x] 8.4 把 `agents/pead/` 的 news 取数、财年标签解析与研究文章取数改经数据产品入口或工具层。验证：新增测试断言 `agents/pead/` 无 provider 导入。
- [x] 8.5 把 `agents/technical/` 的行情取数改经数据产品入口。验证：新增测试断言该模块无 provider 导入。
- [x] 8.6 清退 `FIRST_BATCH_EXCEPTIONS` 中全部标注「Phase D」的条目（预期 27 条），使清单只减不增。验证：新增测试断言任一例外的 reason 中不再出现「Phase D」，且守卫在当前树上运行零违规。
- [x] 8.7 为守卫新增「例外必须声明收敛阶段」的机器校验：无阶段或阶段已过期即判失败。验证：新增测试构造一条无阶段例外，断言守卫报错。
- [x] 8.8 全量重跑架构守卫测试与既有分析师测试，确认迁移未改变分析输出语义。验证：`./scripts/run_tests.sh tests/test_architecture_guards.py tests/test_sector.py tests/test_layer_review.py tests/test_pead_graph.py` 全绿。

## 9. 登记、文档同步与保真性验收

- [x] 9.1 在 `config/workflow/legacy_retirement.yaml` 新增 Phase D 段，登记待退项：层级 `allocation` 字段、PEAD 内部风控与 sizing 路径、Chief 旧表直读入口、旧 `sector_reviews` / `pead_dossier` 读模型（每项写明替代实现、退出条件与消费方清零判据）。验证：`tests/test_legacy_retirement.py` 断言各项存在且字段完整。
- [x] 9.2 同步 `docs/TARGET_WORKFLOW_DATAFLOW.md` §14.4 的实施状态，并补记 §7.2 契约的落地载体。验证：文档中 §14.4 不再描述为未实施，且引用的模块路径与代码一致。
- [x] 9.3 编写 `verification.md`：记录本 change 的验收结果、与基线（`git worktree` 对照）的保真性比对、以及受限环境计数不可作判据的说明。验证：文件中含对照组命令与结果数字。
- [x] 9.4 端到端验收：用完整 fixture 构造六类齐备场景，跑通「分析 → 快照 → 主理人 → 风控 → Boss → Trader」并断言每笔订单可追溯到快照条目。验证：新增 `tests/test_phase_d_e2e.py` 全绿。
- [x] 9.5 端到端反向验收：构造缺一类分析的场景，断言运行判为不完整、缺口报告产出、且不进入决策周期与下单。验证：同一测试文件中反向场景断言通过。
- [x] 9.6 全量测试与校验：执行 `./scripts/run_tests.sh` 全量（受限环境计数不作判据），并确认 `openspec validate restructure-analyst-roles-and-chief-inputs --changes --strict` 通过。验证：两项命令输出均无失败项。
