## 1. Phase A — 权威基线与数据边界

- [ ] 1.1 新增权威基线脚本/文档，固定 `uv sync --all-extras` 与 `uv run pytest` 入口，并记录 Python、依赖、执行环境、计数和用时
- [ ] 1.2 在不受临时文件删除配额干扰的环境重跑全量测试，将失败按根因簇、所属 Phase 和验收命令登记
- [ ] 1.3 盘点并验收当前 Data Platform 中性证据 observations/facts/projections/failures 的写入 schema、幂等约束和 repository API；已有实现通过契约测试则直接接收，仅补齐缺失项，并同步清除“repository 只读、writer 仍走 legacy”等过期边界声明
- [ ] 1.4 核验并完成 `TradingMemory.save_observation` 及同族证据读写对 Data Platform repository 的委托，修复旧库迁移期间 `_data` 初始化顺序等剩余缺口，并保持原文档版本与来源血缘
- [ ] 1.5 核对 observer、chain sources/articles、scheduler 和 CLI 的证据写入调用，清除旁路写入并新增四条路径及旧数据库升级路径的整合测试
- [ ] 1.6 完成数据写侧 cutover 对账和回滚检查，验证唯一 writer、实体、时间、来源、文档版本、幂等身份和失败语义，且不恢复已退役 Workflow Memory 数据表
- [ ] 1.7 新增单一 action 词表声明 `buy|add|hold|trim|sell`，将 Decision、PEAD、Journal、Risk 和 Chief 改为共享同一类型
- [ ] 1.8 新增显式的内部 action → 券商 side 映射，保留内部 action 与实际 broker side 的账本字段并测试 legacy 大小写转换边界
- [ ] 1.9 将期权到期资金桶改为互斥且完整的区间，新增 0/30 天边界、逐头寸合计和概率来源测试

## 2. Phase A — 运行契约、存储和架构守卫

- [ ] 2.1 定义 `WorkflowTaskSpec`、`TriggerContext`、`WorkflowRunRequest`、`WorkflowRunResult` 及其序列化/schema 版本契约
- [ ] 2.2 定义 Task Projection envelope 和七类角色 payload schema，拒绝只有报告文本而无类型化 payload 的产出
- [ ] 2.3 新增 additive migration：`workflow_runs`、`agent_runs`、`task_projection_envelopes` 和 `trigger_runs`，包含唯一键、索引和不可变约束
- [ ] 2.4 实现 projection repository 的 publish/get/list 及规范化 content hash，对重试采用 insert-or-return-existing 语义
- [ ] 2.5 实现基于 role、scope、schema、`valid_until`、input refs 和 data vintage refs 的投影可复用评估及结构化失效原因
- [ ] 2.6 将 `TradingMemory` 改为每操作/工作单元独立 SQLite 连接，启用 WAL、busy timeout 和短事务，并新增并发写入测试
- [ ] 2.7 实现分析师投影允许列表与运行时 input-ref 校验，只允许 Layer→Sector、Information→Fundamental 和 Analysts→Chief
- [ ] 2.8 新增静态架构测试，阻止 Agent 直接导入 Provider、未声明跨分析师投影以及 Agent 观点写回共享事实
- [ ] 2.9 实现旧实现退役登记、在用/已退役冲突检查和 fail-closed 读取原因码
- [ ] 2.10 实现退役资产的 dry-run 清除计划、显式确认和 actor/scope/note 审计，并登记 Phase A–F 首批待退项
- [ ] 2.11 执行 Phase A 门禁：复核数据写侧、action、option-survival、契约、SQLite 并发和架构守卫测试，更新权威失败簇

## 3. Phase B — 决策审计存储与确定性风控

- [ ] 3.1 定义 decision cycle/revision/risk review/Boss approval/cycle event 的 schema、状态枚举、decision hash 规范化规则和 `ExecutionAuthorization`
- [ ] 3.2 新增 additive migration：`decision_cycles`、`decision_revisions`、`decision_risk_reviews`、`boss_approvals`、`cycle_events`，保留现有组合 `risk_reviews`
- [ ] 3.3 实现 decision repositories 的追加式写入、compare-and-set 状态转换、幂等事件和完整周期读取
- [ ] 3.4 为可验证的历史决策生成 legacy revision，对无法还原 hash/快照的记录标记 `legacy_unknown`而不伪造关联
- [ ] 3.5 新增 research snapshot builder，固定被要求角色的 projection ID、hash、as-of 和新鲜度，并在不完整时拒绝创建 cycle
- [ ] 3.6 将当前 pre-trade 路径拆成不修改提案的确定性 review API，返回前后风险向量、违规、verdict 和允许边界
- [ ] 3.7 将 order caps 和 event-notional clip 改为结构化 findings/counterproposal，测试原 revision 内容和 hash 不被改写
- [ ] 3.8 改造 Risk Agent 只解释确定性结果，移除 Macro 观点输入，并测试 LLM 文本不能覆盖硬规则 verdict
- [ ] 3.9 新增 Risk review 单元/属性测试：单票、行业层、关联簇、事件、期权、组合状态和无法推导交易后仓位

## 4. Phase B — Chief—Risk—Boss—Trader 状态机

- [ ] 4.1 扩展 Chief graph state，加入 research snapshot、revision no/hash、risk round/review、snapshot IDs、max rounds、authorization 和 cycle status
- [ ] 4.2 将 Chief 首次决策写为不可变 revision，并将 No Action 作为带理由的正式终态落库
- [ ] 4.3 新增 `chief_revise` 和 conditional edges，实现 reject→revise→re-risk 循环与默认 3 轮上限
- [ ] 4.4 将每轮 revision/risk review/event 在转移前幂等持久化，测试各节点崩溃恢复不重复写入
- [ ] 4.5 将 Boss approval 输入收窄为 exact revision hash 的 approve/reject，将修改意见保存为 reject note 并禁止修改后直接下单
- [ ] 4.6 将 Boss webhook 去重从进程内集合迁移到持久化 idempotency key，测试重复、旧 revision、已终结 cycle 和跨进程回调
- [ ] 4.7 新增执行前 freshness check，从 risk policy 读取默认 60 秒阈值，失效时回到 Risk 并要求新 Boss approval
- [ ] 4.8 将 Trader 入口改为只接收完整 `ExecutionAuthorization`，拒绝未批准、hash 不匹配、过期或字段被修改的指令
- [ ] 4.9 将 client order ID/order reference 稳定派生为 cycle + revision + order sequence，对超时重试先查本地和券商状态
- [ ] 4.10 新增端到端状态机测试：首轮通过、一/多次驳回、轮次用尽、No Action、Boss reject、stale re-risk 和不确定券商提交
- [ ] 4.11 执行 Phase B 门禁，确认新交易路径仍处于 paper/shadow 模式且所有审批链测试通过

## 5. Phase C — Clerk、对账与内部账本

- [ ] 5.1 为 orders/fills 增加可空 cycle ID、revision no、decision hash、approval ID 和关联索引，并对新系统订单强制非空
- [ ] 5.2 新建 Clerk 编排服务，读取 decision/audit 记录与券商回报，不通过 LLM 产生账本事实
- [ ] 5.3 整合现有 reconcile 的 order reference/perm ID 匹配，对系统、人工和 unattributed 订单生成显式分类与异常
- [ ] 5.4 增加部分成交、迟到成交、撤单、拒单、进程重启和漏跑日期的幂等重放测试
- [ ] 5.5 将 journal entries、episodes、marks、predictions、performance 和 attribution 改为 Clerk 编排的派生读模型，保留现有确定性公式
- [ ] 5.6 新增从不可变原始记录重建指定期间绩效/归因的命令与方法版本校验
- [ ] 5.7 发布带 as-of、完整性和对账缺口的 Internal State API，并迁移 Chief/Risk 的交易历史和绩效读取
- [ ] 5.8 将可选 critic 复盘存为链接不可变账本事实的 commentary，测试它无权更改金额或归因
- [ ] 5.9 执行 Phase C 门禁：新系统订单全部审批链完整、对账可重放、绩效可重建且异常缺口可见

## 6. Phase D — Layer 与 Sector 职责拆分

- [ ] 6.1 新增 `LayerAnalysis` 和 `SectorAllocation` payload schemas，在 Layer schema 中禁止配置等级、budget use、stance 和 target weight
- [ ] 6.2 从现有 Sector 编排中拆出 Layer workflow，复用 hierarchy、common/relative claims、cross-section 和证据链计算
- [ ] 6.3 重写 Layer 输出为层级景气/结构、逐标的截面差异、证据强度、冲突、缺口和可证伪条件
- [ ] 6.4 调整 Layer 报告为每层一份研究报告，移除配置/权重/stance，保留截面读数和临时查询不落文件规则
- [ ] 6.5 为历史 Layer 配置读路径新增 legacy adapter 与退役登记，禁止将 legacy 配置字段注入新 Sector
- [ ] 6.6 重构 Sector 输入组装，只接受共享行业事实和有效 `LayerAnalysis`，移除 Macro、PEAD、Information 和其他研究观点
- [ ] 6.7 实现 Sector 的行业/层级/标的三级配置、证据冲突展示、研究上限和非执行性输出
- [ ] 6.8 实现 Sector 对 Layer 失败/缺失/过期/schema 不兼容的显式 incomplete 行为，不伪造配置
- [ ] 6.9 新增 Layer/Sector 契约、报告、依赖隔离和历史兼容测试

## 7. Phase D — Information、Fundamental 与 Chief 汇总

- [ ] 7.1 新增 Information Analyst package、`InformationBrief` schema 和独立手动/定时/文档事件入口
- [ ] 7.2 从 PEAD research/triage/monitor 和 digest 提取纯抽取/材料性逻辑，移除直接抓 Provider 和修改 dossier 的副作用
- [ ] 7.3 实现 Information 的文档版本/chunk 血缘、事件/发布/抽取时间、聚类、置信度、仅自述和待验证标记
- [ ] 7.4 新增 Information schema guard，拒绝 action、仓位、订单数量/价格/类型字段，并测试 Information 可不运行 Fundamental/Chief 而独立终结
- [ ] 7.5 实现 Fundamental routine mode，基于上一有效基线、InformationBrief、Consensus、公司数据和中性产业链事实发布 `FundamentalExpectationUpdate`
- [ ] 7.6 实现 Fundamental event cutoff 和冻结预期基线，禁止用事件后材料回写财报前预期
- [ ] 7.7 实现 event mode 的 actuals vs baseline/Consensus/implied expectation、Surprise Scorecard、指引、叙事变化和非执行建议
- [ ] 7.8 实现迟到电话会/指引的版本升级，共享同一冻结基线且不覆盖初版投影
- [ ] 7.9 从 Fundamental 删除 Sector/Macro 观点、其他 Fundamental 结论和最终 pre-trade risk gate，将上下游读数改为中性 Data Product
- [ ] 7.10 扩展 Chief assembler 读取 Layer、Information、Sector、Fundamental、Macro、Technical 投影及 Internal State，并生成固定 research snapshot
- [ ] 7.11 新增 Phase D 架构守卫和角色输入契约测试，对相同 data vintages 产生新旧输出差异报告
- [ ] 7.12 执行 Phase D 门禁：所有未声明观点依赖清零，角色 schema 拒绝越界字段，人工审阅影子差异

## 8. Phase E — Workflow Dispatcher

- [ ] 8.1 建立 workflow registry，登记 Layer、Information、Sector、Fundamental routine/event、Macro、Technical、Chief 的 specs、依赖、freshness、retry 和 resource group
- [ ] 8.2 实现 registry DAG 校验，拒绝循环、未知 workflow、未声明跨分析师依赖和不兼容输出 schema
- [ ] 8.3 实现手动、schedule、event 到 `TriggerContext` 的归一化与稳定幂等键
- [ ] 8.4 实现 Dispatcher 依赖展开和就绪队列，支持 Phase 0/Macro/Technical 并发与 Sector/Fundamental 各自等待唯一依赖
- [ ] 8.5 实现按 resource group 限制的 worker pool、单任务超时、重试和失败隔离，禁止重跑已成功依赖
- [ ] 8.6 将 projection selector 接入依赖解析，实现可复用原因、重跑原因和 agent-run 记录
- [ ] 8.7 实现完整性门禁和 `incomplete` 缺口报告，任一必需投影失败/缺失/过期/不兼容时禁止创建 cycle
- [ ] 8.8 新增 CLI/API 入口：单 Agent、自动补依赖子流程、全分析流程和显式 `enter_decision_cycle`
- [ ] 8.9 将现有 Scheduler 任务逐项改为 Dispatcher 调用，对每个迁移任务建立旧 job 退役登记和单活跃所有者检查
- [ ] 8.10 新增并发、依赖补齐、缓存失效、重启幂等、失败隔离和不完整阻断的端到端测试

## 9. Phase E — Schedule Calendar 与 Trigger Ledger

- [ ] 9.1 新增 Data Platform `schedule_events` schema/repository，支持 stable event ID、version、source lineage、timezone、session、status 和 payload hash
- [ ] 9.2 实现财报日历刷新/修订适配器，保留 BMO/AMC/盘中/未知 session 和公司实体血缘
- [ ] 9.3 实现 FOMC 及 CPI/PCE/NFP/GDP 刷新/修订适配器，保留官方时区和发布类型
- [ ] 9.4 将 `config/events.yaml` 改为 manual override/custom event overlay，对改期、取消和补充记录 actor、reason 和被覆盖版本
- [ ] 9.5 实现 Calendar 发布的版本对比，对时间/状态/关键 payload 修订生成新 version 而不覆盖历史
- [ ] 9.6 实现 `event_id + event_version + workflow_id` 的 trigger claim，确保重启、多 worker 和补跑不产生第二个逻辑触发
- [ ] 9.7 在事件改期/取消时失效未执行旧触发并保留已执行历史，为新 version 建立新触发
- [ ] 9.8 实现 timezone/session/休市/misfire/restart 策略与 Trigger Ledger 决定记录，并确保 Calendar 不注入研究观点
- [ ] 9.9 新增 Calendar/Trigger 测试：重复源、修订、人工覆盖、取消、时区、休市、misfire 和进程重启
- [ ] 9.10 执行 Phase E 门禁，确认每个已迁移 workflow 只有一个调度所有者且 Dispatcher/Calendar 恢复测试通过

## 10. Phase F — 影子运行、切流与回滚

- [ ] 10.1 新增 projection read、analyst output、Dispatcher schedule、approval lifecycle、Clerk publication 和 live Trader 的独立功能开关
- [ ] 10.2 实现功能开关与配置校验，保证旧/新 live Trader 路径互斥，冲突配置启动时 fail closed
- [ ] 10.3 新增影子运行模式，在相同 data vintages 下运行新旧分析/风控但从进程能力上禁止新路径 broker write
- [ ] 10.4 实现影子差异报告，比较输入快照、投影、调度遗漏、风控 verdict/counterproposal、审批链和归因
- [ ] 10.5 为 read path 切换和回滚建立 runbook，验证新 projection 读取及旧读模型兼容
- [ ] 10.6 为 schedule path 切换和回滚建立 runbook，验证无尚未完成 trigger 被双重所有
- [ ] 10.7 为 live trade path 切换和回滚建立 runbook，强制先关闭当前 live route、确认无 in-flight authorization，再开启另一 route
- [ ] 10.8 执行旧实现消费者清零、数据对账、回滚窗口和墓碑登记检查，只将满足全部条件的项标记已退役
- [ ] 10.9 演练 read/schedule/trade 三个边界的独立回滚，保留影子、审批和账本记录

## 11. 最终验收与文档同步

- [ ] 11.1 运行全量 `uv run pytest` 和所有 Phase 专项测试，对比 Phase A 权威基线并对任何剩余失败给出根因和处置
- [ ] 11.2 运行 OpenSpec strict validation、架构守卫、schema migration/reopen 和 SQLite 并发压力测试
- [ ] 11.3 验证每笔新系统影子/实际订单都可追溯到 research snapshot、decision revision、risk review 和 Boss approval
- [ ] 11.4 验证完整流程在任一必需分析缺失/失败/过期时只产生 incomplete 报告且无法下单
- [ ] 11.5 更新 `TARGET_WORKFLOW_DATAFLOW.md`、运维 runbooks、CLI 帮助和配置文档，确保实现状态、新鲜度、轮次、Calendar 和单 live route 规则一致
- [ ] 11.6 审阅每个 Phase 的待退清单，为未满足项保留具体缺口，不在本 change 中执行未经确认的物理清除
