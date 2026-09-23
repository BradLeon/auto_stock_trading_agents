## 1. 存储与归属登记（additive，不改行为）

- [ ] 1.1 新增 `ledger_exceptions` 审计异常表（断链、无法归因成交、漏跑窗口、持仓资金差异），含类型/标的范围/两侧数值/依据/时点字段，并在 `src/ats/data/stores/ownership.py` 登记为 Workflow memory —— 验证：迁移后表与索引存在，`tests/test_legacy_retirement.py` 与归属守卫测试通过
- [ ] 1.2 新增 `clerk_runs` 运行留痕表（运行种类、对账窗口、as-of、幂等键、起止与状态、汇总计数）—— 验证：同窗口重复运行只产生一条留痕，表已登记归属
- [ ] 1.3 新增 `ledger_read_models` 派生读模型表（kind ∈ performance|attribution、period、as_of、method_version、source_facts_hash、payload、rebuilt_at）—— 验证：表与唯一约束存在，已登记归属
- [ ] 1.4 编写只读历史盘点脚本（统计断链订单行、无依据 `manual` 成交行、未对账会话窗口）—— 验证：以 `mode=ro` 打开真实库副本输出分类计数，脚本不产生任何写入
- [ ] 1.5 新增与修改的列除主键外一律可空，不引入 `NOT NULL` 或条件 `CHECK` 约束（强关联由写路径校验承担，裁决 A）—— 验证：断言相关列的非空约束为空，且历史行、人工单、无法归因单均能正常写入

## 2. 强关联与归属三类化

- [ ] 2.1 新系统订单与成交的写路径强制 `cycle_id` / `revision_no` / `decision_hash` / `approval_id` 齐全，缺任一即拒绝写入并登记审计异常（列保持可空，见 1.5）—— 验证：新增单测断言缺链写入被拒且异常项落地，不得以空值或推测值补齐
- [ ] 2.2 成交匹配到系统订单标识后链接完整决策链（cycle / revision / hash / risk review / boss approval），并保留券商订单号、成交号、时间、数量、价格、费用 —— 验证：单测断言链接字段与券商身份字段均落库
- [ ] 2.3 历史行缺链登记为 legacy 缺口并保留可空字段，不伪造 cycle / revision / approval 链接 —— 验证：单测断言迁移前后历史行的链接字段仍为空且异常项存在
- [ ] 2.4 `origin` 改为 system / manual / unattributed 三类，复用 `link_confidence` 四级证据链，把 `none` 从「归为 manual」改为「一律归为 unattributed 并生成异常项」，不给实现者保留归入 manual 的选择（裁决 B）—— 验证：单测覆盖四类证据分别落到正确归属与异常项
- [ ] 2.5 人工订单保留券商身份与判定依据，在内部状态中与系统交易分开呈现，不计入系统交易绩效 —— 验证：单测断言人工单可见但不进入系统绩效口径
- [ ] 2.6 改写 `tests/test_reconcile.py` 中断言「无匹配即 manual」与相关计数的用例，注释说明旧行为为何违反 §11.1 —— 验证：该文件全绿且注释留痕

## 3. 补偿语义

- [ ] 3.1 同一订单多笔部分成交按成交号幂等累计数量与按成交量加权均价，未收口前保持 `partial` —— 验证：单测断言两笔部分成交后的累计数量、加权均价与状态仍为 partial
- [ ] 3.2 迟到成交回填到原订单且不新增第二笔订单，记录迟到补偿标记与回填时点 —— 验证：单测断言订单数与成交数不变且回填字段正确
- [ ] 3.3 券商明确回报的撤单与拒单产生显式终态并记录券商依据 —— 验证：单测断言终态与依据来源为 broker
- [ ] 3.4 无券商证据的推定终态标记 `basis=inferred` 并保留依据文本与判定时点，且可被后续券商证据修正 —— 验证：单测断言推定标记存在且后续证据可覆盖终态
- [ ] 3.5 同一成交号被重复返回时幂等更新对账时点或返回已有记录，不新增成交、不重复计入绩效 —— 验证：单测断言二次对账后成交条数与绩效数值不变
- [ ] 3.6 对账中途进程重启后重入同一窗口继续，不重复计入也不丢失已处理记录 —— 验证：单测在补偿中途注入中断后重跑，断言结果一致
- [ ] 3.7 未对账且券商已无法返回的会话日登记为对账缺口并给出影响范围，不得以空结果伪造「该日已对账」；本阶段不提供替代历史 statement / 对账单导入通道（裁决 D）—— 验证：单测断言缺口项存在且完整性标记降级，且无导入写入路径
- [ ] 3.8 券商持仓与资金与本地账本比对，差异以显式差异项落地（保留两侧数值、标的、时点），不静默覆盖任一侧 —— 验证：单测断言差异项存在且原始账本数值未被改写
- [ ] 3.9 order attempt 序列纳入重放口径（D14）：以 `client_order_id`（意图）+ `trades.attempt` 计数为单位，重放不因重试产生第二笔订单或重复计入成交与绩效；对账时核对本地 attempt 计数与券商侧同一意图的回报次数，不一致登记异常 —— 验证：单测断言重试后订单行数与绩效数值不变、attempt 计数被保留，计数不一致时产生异常项

## 4. Clerk 编排入口

- [ ] 4.1 新建 `src/ats/execution/clerk.py` 编排流水线，串联 reconcile、marks、episodes、predictions、performance 的既有确定性公式，并把 order attempt 序列（意图 + attempt 计数）纳入重放范围 —— 验证：单测断言一次编排按序产出对账、派生与发布结果，且不调用任何 LLM
- [ ] 4.2 Clerk 运行留痕与窗口幂等键（运行种类 + 对账窗口 + as-of，不含请求时刻），重复执行同一窗口不产生新事实 —— 验证：单测断言第二次运行复用同一幂等键且账本状态不变
- [ ] 4.3 Clerk 只补充对账、归属、补偿与派生所需字段，不改写订单的提交意图、数量、标的与决策链标识 —— 验证：新增测试断言领域事实字段在编排前后逐字节一致
- [ ] 4.4 提供 CLI 与调度入口（`clerk run`），scheduler 的既有 cron 在过渡期保留并登记为待退 —— 验证：命令可运行且双轨触发同一窗口不产生重复事实
- [ ] 4.5 派生计算按 `source_facts_hash` 命中即跳过重算 —— 验证：单测断言相同输入指纹下读模型不被重复生成

## 5. 绩效与归因重建

- [ ] 5.1 新建 `src/ats/execution/rebuild.py`：从不可变订单与成交、marks、费用、决策与审批记录重建绩效，带 `method_version` 与 `rebuilt_at` —— 验证：单测断言重建结果与首次生成一致且记录了版本与重建时点
- [ ] 5.2 归因重建首版复用 `src/ats/trader/analytics.py` 的既有确定性公式，不引入新口径 —— 验证：单测断言归因结果与既有公式输出一致
- [ ] 5.3 同一方法版本 + 同一组原始事实产生相同结果（确定性） —— 验证：单测断言两次重建 payload 的哈希相同
- [ ] 5.4 方法版本变更产生新的版本标识，不静默覆盖旧版本结果 —— 验证：单测断言新旧版本读模型可共存且可区分
- [ ] 5.5 重建或生成失败时不回写原始事实，也不产出部分写入却标记为完整的读模型 —— 验证：单测断言失败路径下原始记录不变且读模型状态为失败
- [ ] 5.6 CLI 增加重建与缺口查看入口（重建仅写派生读模型） —— 验证：命令可运行且输出包含方法版本与重建时点

## 6. Internal State API 与消费方迁移

- [ ] 6.1 新建 `src/ats/execution/state_api.py` 发布 `InternalState`（as-of、交易历史、绩效、归因、组合口径、审计异常） —— 验证：单测断言各段字段与 as-of 齐全
- [ ] 6.2 `completeness` 含未对账窗口、无法归因成交与历史断链的计数及 complete / degraded 状态 —— 验证：单测断言三类缺口分别正确计入
- [ ] 6.3 存在缺口时完整性标记为不完整，不得呈现为已完整对账 —— 验证：单测断言缺口存在时状态为 degraded 且缺口范围可见
- [ ] 6.4 Chief 的**交易历史读取段**迁移到 Internal State（`agents/chief/assemble.py` 的 `recent_trades` / `recent_fills`），先双读比对再切换；assembler 整体重构属 Phase D，本阶段不在同一文件内做结构改动（裁决 C）—— 验证：单测断言切换后 Chief 上下文含 as-of 与完整性标记
- [ ] 6.5 Risk 的**绩效读取段**迁移到 Internal State（`risk/assess.py` 的 `performance_history`），完整性不足时显式降级或阻断（裁决 C）—— 验证：单测断言 degraded 状态下不会视为完整数据
- [ ] 6.6 `channel/context.py` 的交易读取迁移 —— 验证：单测断言展示内容来自读模型且保留完整性标记
- [ ] 6.7 旧直读点登记为待退项（退出条件：消费方全部切换且直读调用清零） —— 验证：登记条目可通过注册表校验

## 7. LLM 边界与架构守卫

- [ ] 7.1 定义标注类字段白名单并要求来源与生成时点标记，把 `invalidation_triggered` 归入白名单 —— 验证：单测断言写入该字段时来源标记存在
- [ ] 7.2 新增架构守卫：LLM / critic 相关模块不得引入账本写路径 —— 验证：守卫测试通过，且人为引入 import 时守卫失败
- [ ] 7.3 新增守卫：LLM 分支只允许更新白名单字段，禁止写入或修改金额、数量、成交身份、持仓与归因数值 —— 验证：守卫测试覆盖正向与越界两侧
- [ ] 7.4 critic 复盘存为链接不可变账本事实的 commentary，不修改账本金额与归因 —— 验证：单测断言复盘写入前后账本数值不变

## 8. 待退旧实现登记

- [ ] 8.1 在 `config/workflow/legacy_retirement.yaml` 追加 Phase C 六项（`reconcile.origin_manual_fallback`、`reconcile.partial_fill_as_filled`、`scheduler.adhoc_journal_jobs`、`store.direct_trade_reads`、`journal.report.render_ledger`、`performance.legacy_snapshot`），逐项填写 replaced_by / exit_condition / consumers / consumer_zero_criterion / location —— 验证：`tests/test_legacy_retirement.py` 通过
- [ ] 8.2 同步更新 `tests/test_legacy_retirement.py` 中登记表的期望集合（登记表被机器校验，增项须同批改） —— 验证：该测试通过且新旧条目一一对应
- [ ] 8.3 确认 `purge()` 仍为默认干跑且仓库内无确认式清除调用 —— 验证：检索确认无 confirm=True 的清除调用

## 9. 门禁与验收

- [ ] 9.1 新增专项测试并全部通过：Clerk 编排幂等、强关联拒绝与断链异常、归属三类（unattributed 唯一归宿）、补偿重放（部分 / 迟到 / 撤单 / 拒单 / 重启 / 漏跑 / 重试 attempt 序列）、重建确定性、Internal State 完整性 —— 验证：新测试文件全绿
- [ ] 9.2 端到端验收：paper 模式一次真实下单链路 → 成交 → 对账 → 补偿 → 读模型 → 下一轮 Chief 读到带完整性标记的状态 —— 验证：端到端测试通过，全程无真实下单
- [ ] 9.3 保真性对照：以 `git worktree` 对本阶段触及的测试文件做基线与当前同条件运行，结果写入 change 目录 `verification.md` —— 验证：无未归因失败，且只用权威基线口径（不使用受限环境计数）
- [ ] 9.4 确认真实下单仍为 paper / dry-run 默认且可执行真实交易的入口唯一 —— 验证：架构守卫与既有测试通过
- [ ] 9.5 文档同步：在 `docs/TARGET_WORKFLOW_DATAFLOW.md` §11 与 §14.3 写入 Phase C 实施状态，表名与实现保持一致 —— 验证：文档与实现逐项核对
- [ ] 9.6 收口校验 —— 验证：`openspec validate establish-clerk-and-trade-ledger --strict` 输出 valid
