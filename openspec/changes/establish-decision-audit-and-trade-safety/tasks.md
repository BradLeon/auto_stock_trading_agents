## 1. 决策审计存储（additive 落地）

- [x] 1.1 在 `src/ats/memory/store.py` 的 `_SCHEMA` 新增五张表 DDL（`decision_cycles`、`decision_revisions`、`decision_risk_reviews`、`boss_approvals`、`cycle_events`），列按 §12.3 与 design D1/D4 展开（含 `cycle_id`、`revision_no`、`decision_hash`、`parent_revision`、`ruleset_version`、`portfolio_snapshot_id`、`market_as_of`、`before_metrics_json`、`after_metrics_json`、`idempotency_key`、`created_at`）。验证：新建库后逐表 `PRAGMA table_info` 返回预期列，既有 store 测试全部通过
- [x] 1.2 在 `src/ats/data/stores/ownership.py` 的 `WORKFLOW_MEMORY_TABLES` 登记五张表。验证：`tests/test_legacy_sqlite_tables_have_explicit_data_or_memory_ownership` 通过
- [x] 1.3 在 `_migrate()` 中按 `PRAGMA table_info` 探测式补齐五表，兼容 Phase A 之前创建的既有库，不删任何旧列。验证：对旧库副本执行初始化后五表可用，既有表列集合逐列不变
- [x] 1.4 为 `trades` 与 `fills` 增加可空关联列 `cycle_id`、`revision_no`、`decision_hash`、`approval_id` 与关联索引（可空，强制非空属 Phase C）。验证：更新 `tests/test_trader.py::test_trades_migration_columns` 并扩展断言，旧行四列全为 NULL 且读取不报错
- [x] 1.5 为 `boss_approvals.idempotency_key` 与 `cycle_events` 的幂等键建立唯一约束。验证：同键二次插入被拒或返回既有记录的单测通过
- [x] 1.6 编写只读盘点脚本，扫描既有 `cycles` / `decisions` 输出「可映射 / 不可映射」分类计数，不写库。验证：在真实库副本上运行并产出计数报告，脚本无写入路径
- [x] 1.7 历史决策迁移：可映射者建立带 `legacy_revision` 标记的修订，不可映射者标记 `legacy_unknown` 并保留原始标识，不伪造哈希与快照/审批关联。验证：不可映射数量与 1.6 报告一致，且 `legacy_unknown` 记录无法取得执行授权（断言测试）
- [x] 1.8 确认五表不被 `_retire_data_tables()` 清除、不触发数据层写目标校验失败。验证：`_verify_data_layer_write_targets()` 与既有数据边界测试通过

## 2. 决策领域模型与内容哈希

- [x] 2.1 定义决策状态枚举与终态集合（草案、待风控、已驳回、待审批、已执行、人工复核、不行动、审批拒绝、失效）及终态判定。验证：单测覆盖每个状态的终态性，且终态不可回到可执行状态
- [x] 2.2 实现 `decision_hash` 规范化：复用 `task_projection.canonical_json()`，输入为规范化交易指令、决策理由与关键输入引用，取 sha256 前 32 位。验证：两侧夹逼单测——同语义不同键序/空白必须同哈希；任一实质字段变化必须换哈希
- [x] 2.3 实现循环内修订序号分配，保证同 cycle 内 `revision_no` 唯一且无间隙。验证：重复与并发分配的单测断言唯一性
- [x] 2.4 实现追加式修订仓库：只允许插入，禁止更新既有修订。验证：对已写入修订调用更新路径抛错，且该行逐字段保持不变
- [x] 2.5 实现 compare-and-set 状态转换与 `cycle_events` 追加写入，幂等键风格沿用 `TriggerContext.idempotency_key()`（不含请求时刻）。验证：同幂等键重复提交只产生一条事件，且在新建连接/重建对象后行为一致
- [x] 2.6 实现按 cycle 读取完整因果链（cycle + revisions + reviews + approvals + events）的只读查询。验证：单测断言能仅由存储回答「哪一版被批准、哪一版被执行」
- [x] 2.7 落实「检查点不是审计真相」：运行恢复在检查点缺失时依据领域记录判定已发生事实。验证：注入检查点丢失的用例中不产生重复审批或重复下单

## 3. 研究快照与决策过程创建

- [x] 3.1 实现 research snapshot builder：按任务注册表取被要求角色的投影，复用 `TaskProjectionEnvelope` 与 `reuse_decision()` 的理由枚举，逐项记录投影标识、内容哈希、as-of 与新鲜度状态。验证：单测断言快照每项可回指到具体 envelope
- [x] 3.2 输入不完整时拒绝创建 cycle 并阻断自动交易。验证：必需投影缺失或过期时创建被拒，且无 revision / review / order 写入
- [x] 3.3 快照失效时 cycle 转 `superseded` 并记录原因，不替换原过程的输入。验证：循环中令快照过期的用例断言状态与原因，新输入只能开启新 cycle
- [x] 3.4 No Action 作为带理由的正式终态，不调用风险审查、人工审批与执行。验证：图级单测断言三处均未被调用
- [x] 3.5 实现 cycle 与旧 `cycles` / `decisions` 表的双写适配（design D2），确认旧读入口结果不变。验证：双写后旧读入口返回内容与改造前一致，新路径只读修订表

## 4. 确定性风险审查改造

- [x] 4.1 新增不修改提案的只读审查入口：以**整条修订**为评估单元，返回合并后的逐单判定、结构化 `violations`（`rule_id` / `limit` / `actual` / `severity`）、`allowed_boundary`（`max_additional_notional` / `blocked_actions`）与执行前后的风险指标。验证：单测断言传入的修订对象逐字段未被改动，且判定基于该修订全部指令的合并后果而非逐笔独立判定
- [x] 4.2 审查结果写入 `decision_risk_reviews`，绑定 revision hash、ruleset version、portfolio snapshot id 与 market as-of。验证：四字段任一缺失即视为无效审查的单测通过
- [x] 4.3 把 `_apply_order_caps()` 的裁剪改为反向建议，不再改写名义金额与目标权重。验证：改写 `tests/test_risk.py` 中裁剪相关断言为「提案金额不变 + 边界给出」并通过
- [x] 4.4 把 `_clip_event_notional()` 的裁剪改为反向建议。验证：`tests/test_risk.py::test_pre_trade_event_clip`（96-103）改断言原金额不变且给出可接受上限后通过
- [x] 4.5 把审查主循环中的 `continue` 丢弃改为带原因码的驳回明细（含未知动作、无法推导交易后仓位）。验证：`tests/test_risk.py::test_pre_trade_blocks_buy_in_derisk`（86-93）与 `tests/test_action_vocabulary.py` 改为断言驳回留痕后通过
- [x] 4.6 LLM 文字解释不得覆盖硬规则判定。验证：构造「文本判断可接受而硬规则违规」的用例，断言 verdict 为驳回且文本被记录为可观测异常
- [x] 4.7 将 `pre_trade()` 退化为 deprecated 适配层（内部由新审查结果生成旧式输出），供未改造调用点在切换期运行。验证：`runtime/scheduler.py` 与 `runtime/cli.py` 的调用点仍可运行，适配层单测通过
- [x] 4.8 保持决策级审查与组合级 `risk_reviews` 的隔离。验证：同一 as-of 下多次决策审查各自留痕互不覆盖，组合级读取内容不受影响
- [x] 4.9 使未接线的 `src/ats/agents/risk_validator.py` 静默改写路径在默认流程不可达，并处置 `tests/test_risk_validator.py`。验证：默认路径无引用、架构守卫与全量相关测试通过
- [x] 4.10 跨单规则整体判定：同一修订内同属一个行业层、同一相关簇或依赖组合状态方可判定的多笔订单必须合并评估，不得逐笔独立放行。验证：构造行业层合计超限、相关簇集中度超限两类用例，断言以整条修订为对象驳回并给出该层/簇的规则标识、上限值与可接受的最大增量
- [x] 4.11 交易前后风险指标落库并可复核：由确定性计算产出执行前后指标，随修订内容变化、同一修订重复审查保持稳定。验证：单测断言 `before_metrics_json` / `after_metrics_json` 齐备、同修订重复审查指标一致、修订变化后指标不同，且仅凭审查记录即可说明该修订通过或驳回时的风险变化幅度

## 5. Chief—Risk 多轮 Loop 状态机

- [x] 5.1 扩展 `graph/chief_state.py` 的 `ChiefDecisionState`：research snapshot、revision no/hash、risk round 与 review、portfolio snapshot id、max rounds、authorization、cycle status。验证：新字段单测通过且既有图测试不破
- [x] 5.2 把 `persist_decision` 拆为「按轮持久化不可变修订」与「持久化风险审查」，转移前完成幂等持久化。验证：每轮各产生一条修订与一条审查，重复执行不产生重复行
- [x] 5.3 新增 `chief_revise` 节点与 `risk_gate` 后的条件边（approve → `boss_review`、reject → `chief_revise`、exhausted → `manual_review`）。验证：三条路径各有单测可达
- [x] 5.4 有界自动修订：默认 3 轮后进入 `manual_review`，不进入审批与执行。验证：三轮驳回用例断言终态且未调用审批与执行节点
- [x] 5.5 Chief 接受反向建议时生成引用父修订的新修订并重新审查。验证：父修订逐字段不变、新修订哈希不同
- [x] 5.6 终态副作用只在进入终态时执行一次（PEAD 信号消费、终态报告生成）。验证：多轮修订后副作用计数为 1，中间轮次不重复消费上游信号
- [x] 5.7 崩溃恢复：在各节点前后模拟进程崩溃，恢复后不重复写修订、审查、审批与订单。验证：按 §15.2 的注入用例全绿
- [x] 5.8 保护审批恢复通道：`Command(resume=...)` 与 `interrupt` 位置稳定。验证：`tests/test_chief_graph.py::test_trader_thread_resumes_on_chief_graph` 在每次节点改动后重跑通过
- [x] 5.9 移除或改造旧直通执行路径，使唯一可下真单入口在图中。验证：全仓库扫描断言只有一条提交真实订单的路径

## 6. Boss 审批收窄与持久化幂等

- [x] 6.1 审批输入面收窄为对 exact revision hash 的批准/拒绝，修改意见只作拒绝备注。验证：提交不同数量的用例被记录为拒绝且执行环节未收到指令
- [x] 6.2 审查通过后任一实质字段变化即令审查失效并需重新审查与重新批准。验证：字段变化后原审批不再有效的单测通过
- [x] 6.3 `boss_approvals` 持久化幂等键，`runtime/server.py` 去重改为查存储并移除进程内 `_RESUMED`。验证：改写 `tests/test_server.py::test_duplicate_callback_executes_once`（40-63）为跨进程/重启可复现，并新增「重启后重复回调不产生第二次执行」
- [x] 6.4 拒绝针对非当前修订、已终结 cycle、已失效审查的回调。验证：三类回调各自单测返回拒绝且不产生新授权
- [x] 6.5 审批记录完整写入 `boss_approvals`，并与既有 `cycles.approval_status`、journal 审批列写入保持一致。验证：既有审批回填与 divergence 测试通过，且新表记录字段齐备

## 7. 执行授权网关

- [x] 7.1 新增执行授权结构（§10.4 的十个字段）与构造函数，由当前修订 + 通过审查 + 有效审批派生。验证：任一前置条件缺失时构造失败的单测通过
- [x] 7.2 `trader/execute.py::execute()` 改为只接受完整授权并拒绝裸指令。验证：裸指令调用被拒的测试通过，既有执行路径改为先构造授权
- [x] 7.3 实现执行前的逐项校验：授权完整性、哈希匹配、是否为当前被批准修订、审查与审批是否仍有效。验证：四项拒绝场景各有单测
- [x] 7.4 在 `config/risk.yaml` 的 `limits` 段新增 `max_snapshot_age_seconds: 60` 并由 `RiskConfig` 承载。验证：加载单测通过，配置缺键时回落默认 60
- [x] 7.5 执行前重新取时并校验组合快照与行情快照新鲜度，超期即拒绝当前授权并回到待审查。验证：过期快照用例断言拒绝且 cycle 状态回到待审查
- [x] 7.6 授权失效后必须重新审查并重新取得人工批准，不沿用旧批准。验证：新快照下通过路径产生新审查与新审批，驳回路径按驳回处理
- [x] 7.7 订单标识改为由 cycle + revision + 订单序号稳定派生，同步 `store.client_order_id()` 与 `broker/ibkr.py::order_ref()` 并保持券商字段长度约束。验证：同 cycle 不同修订的同标的同向订单得到不同标识；`tests/test_order_tagging.py` 与 `tests/test_journal_idempotency.py` 更新后通过
- [x] 7.8 重试幂等：先查本地记录与券商侧状态，只在确认未提交时继续；结果不确定时保留待对账。验证：超时重试用例不产生第二笔逻辑订单
- [x] 7.9 人工入口（`execute.manual()` 等）走同一链路并标注来源为人工。验证：人工单同样要求完整授权，缺授权即被拒绝

## 8. 待退旧实现登记

- [ ] 8.1 在 `config/workflow/legacy_retirement.yaml` 追加本阶段待退项：`risk/checks.py` 的就地裁剪路径、`agents/risk_validator.py::apply_guardrails`、`runtime/server.py` 的进程内回调去重、`cycles`/`decisions` 旧决策写路径、`trades.client_order_id` 旧派生式、`pre_trade()` 适配层；每项填写 `replaced_by`、`target_phase: B`、`exit_condition`、`consumers`、`consumer_zero_criterion`、`location`。验证：`RetirementRegistry` 加载无冲突且 `tests/test_legacy_retirement.py` 通过
- [ ] 8.2 确认登记只登记不清除。验证：`purge()` 默认干跑的单测通过，且仓库内不存在确认式清除调用

## 9. 门禁与验收

- [ ] 9.1 端到端审批链测试全绿：首轮通过、一次驳回后通过、多次驳回后通过、全部驳回、超过三轮、No Action、审批拒绝、快照过期后重新风控、券商提交结果不确定。验证：新增端到端测试文件整体通过（§15.2 清单逐项覆盖）
- [ ] 9.2 保真性对照：对本阶段触及的测试文件用基线提交的独立 worktree 同条件对照运行，确认失败只来自预期变更。验证：对照结果写入本 change 的验收记录，无未归因失败
- [ ] 9.3 确认新路径仍为 paper / dry-run 默认，且任一时刻只有一条可执行真实交易的入口。验证：默认配置下执行路径不提交真实订单的测试通过，路径扫描无第二入口
- [ ] 9.4 文档同步：在 `docs/TARGET_WORKFLOW_DATAFLOW.md` §12.3 补注「本仓库以 `trades` 承担订单职责」，并把 Phase B 的实施状态写入 §14.2。验证：文档表名与实现一致
- [ ] 9.5 收口校验：`openspec validate establish-decision-audit-and-trade-safety --strict` 输出有效，且本文件所有任务已勾选。验证：命令输出 `is valid`
