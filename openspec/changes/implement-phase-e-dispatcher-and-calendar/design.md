## Context

动机见 `proposal.md`，行为约束见本 change 的三个 delta spec。`docs/TARGET_WORKFLOW_DATAFLOW.md` §8、§13、§14.5 是目标边界；`refactor-workflow-dataflow-architecture` 的 Phase E 是总体任务来源，本文件给出该阶段的可实施方案。

截至 2026-09-24，Phase A–C 的专项 change 已归档，Phase D 的角色入口正在实施。代码库已有 `workflow/run_contracts.py` 的 `WorkflowTaskSpec`、`TaskRegistry`、`TriggerContext`、`WorkflowRunRequest/Result`，已有类型化 `task_projection_envelopes` 和 Chief 研究快照校验；这些契约尚未接入运行调度。`runtime/scheduler.py` 仍采用 `BlockingScheduler` 的单 worker 和 `_daily`/`_weekly_review` 顺序调用，事件来自静态 `config/events.yaml`。PEAD 财报窗口包含“实际公布后才能评分”、未知 session 双窗口探测和补打逻辑，迁移必须保留这些业务门槛。`get_store()` 缓存 `TradingMemory`，它持有可跨线程使用的同一个 SQLite 连接，不能直接并行运行旧入口。

Phase E 的交付边界为分析调度及事件日历。真实交易仍由既有 Chief—Risk—Boss—Trader 授权链控制；新的自动调度默认不取得券商写权限。数据采集、Clerk 对账和独立运维作业保留各自的 owner，逐项登记后才考虑迁移。

## Goals / Non-Goals

**目标：**

- 让手动、cron、事件入口调用同一个 Dispatcher，并按固定的 scope 与任务图产出可追溯结果。
- 完成分析任务的有限并发、可靠投影复用、失败隔离和 Chief 完整性门禁。
- 用持久 Trigger Ledger 处理重复投递、worker 竞争、重启和 misfire。
- 在 Data Platform 维护自动更新、可版本化的事件日历，并清楚表达日期、session 和发布确认的不确定性。
- 以 workflow ID 逐项切换调度所有权，保留回滚能力与独立的领域作业。

**非目标：**

- 不重写 Phase D Agent 的分析逻辑、类型化 payload 或 Chief 审批图。
- 不让日历计划时间充当财报、宏观数据或 FOMC 材料已经发布的证明。
- 不把所有采集和账本作业塞进分析师依赖 DAG。
- 不引入外部队列或网络数据库；当前规模先使用现有进程、SQLite 和 APScheduler 唤醒能力。
- 不在 Phase E 开启新的实盘 Trader 路由；该切流属 Phase F 验收。

## Decisions

### D1. 注册表描述任务类型，运行计划描述具体作用域

复用 `WorkflowTaskSpec`、`TaskRegistry` 和现有请求/结果类型。初始稳定任务 ID 为 `layer-review`、`information-brief`、`sector-review`、`fundamental-routine`、`fundamental-event`、`macro-review`、`technical-review`；一个 task ID 可以产生多个按 scope 区分的 task instance。`sector-review` 声明依赖 `layer-review`，两个 Fundamental 模式声明依赖 `information-brief`。其余分析任务没有分析师观点依赖。

注册表加载时验证任务 ID 唯一、依赖存在、DAG 无环、跨角色边合法、输出 schema 可解析、触发模式和资源组有效。现有 `TaskRegistry.resolve_order()` 可作为契约起点，但不能以返回顺序直接表示并发 DAG；实施时建立按入度/就绪集合计算的执行计划并增加真正的环检测。

运行计划在启动时把任务类型展开成具体实例。例如 AI 硬件 Sector 的 scope resolver 从受治理的行业配置列出应分析的每个 layer，并生成多份 `layer-review` 实例；Sector 等待这些明确列出的投影 ID。Fundamental 的 entity scope 对应同一 entity 的 Information 候选集合。scope resolver 的输出和配置版本写进运行计划；运行中配置变化不偷换依赖。

备选是为每层、每只标的生成静态任务 ID；配置更新会引发任务注册表膨胀，并让同一个角色的路由代码到处复制，因此不采用。

### D2. 决策所需类别是运行开始时固定的显式清单

`enter_decision_cycle=false` 为默认。局部分析即使成功，也只发布投影。要求进入 Chief 的请求必须选择一个完整决策 profile；AI 硬件 profile 固定六类：Layer、Information、Sector、Fundamental、Macro、Technical。Fundamental 由触发语义选例行或事件模式，只需要其中一种有效结果。其他行业的必需角色清单必须由版本化 profile 明确给出，不能通过“当前注册了什么”推断，否则漏登记角色会被误当为不需要。

在任务启动前验证 profile 覆盖；若调用方只选 Macro 又要求决策，直接拒绝请求。运行结果聚合前及 Chief 启动前再按固定清单复查每个投影的 scope、schema、输入引用、data vintage、hash 和有效期。复查失败时持久化 `incomplete` 与逐项缺口，不创建 decision cycle。通过后把被选 ID/hash 交给已有 Chief 快照构建器，由该构建器固定 cycle 的研究快照。

备选是根据最新可用报告自动补齐交易决策；这会让请求范围和审计输入不可预期，因此不采用。

### D3. 执行器使用有界 ready queue 和角色适配器

执行器只向已就绪且持有资源配额的 task instance 派发工作。每个适配器接收明确的 scope、`as_of`、触发上下文和已固定的依赖投影引用；其返回值必须转为 `TaskResult`，并验证成功投影确实已发布。适配器包装 Phase D 的现有角色入口；PEAD 事件适配器调用现有可恢复图，不在 Dispatcher 中复制 PEAD 状态机。Chief 只在完整性门禁后调用已有决策入口。

Phase D 仍在推进：未就绪的角色入口必须报告 `missing`/`blocked` 并进入缺口报告，不能以空白成功投影通过。任务的失败/超时只阻断其可达下游；其他 ready 任务继续。重试上限、可重试原因、退避和超时由任务策略声明，attempt 独立记录；超时的同步 Agent 不得在后台继续写入同一逻辑结果，适配器须支持取消或隔离迟到提交。

初始并发按角色/外部预算和 SQLite writer 资源组限制，而不是简单提高全局线程数。采集控制器不作为分析师任务；Agent 仍从 Data Product 读入已发布事实。

备选是把全部分析任务塞进一张 LangGraph；这样会让局部调度、独立重试和角色扩展都依赖图状态版本，因此保留 Dispatcher + 两个既有有状态图的边界。

### D4. 投影选择与运行记录在同一审计视图中

复用 `TaskProjectionEnvelope` 和现有读取助手，增加 Dispatcher 专用 selector。候选按角色、scope、schema、`as_of`、有效期、依赖投影 ID/hash、关键数据 vintage、终态逐项判断；选中后保存精确投影 ID/hash，未选中记录结构化原因。若某类上游输入没有可验证 vintage，则不把“未发现变化”视为缓存可用。执行前后检测投影是否被撤回、替代或过期；若无法确认输入一致，则重新运行或标 `incomplete`。

新增 Workflow Memory 表建议如下：

```text
workflow_runs(run_id PK, trigger_run_id, request_json, plan_json,
              profile_version, plan_hash, status, started_at, ended_at,
              result_json, created_at)
agent_runs(agent_run_id PK, run_id, task_instance_key, task_id, scope_json,
           attempt_no, status, input_refs_json, data_vintage_refs_json,
           projection_refs_json, reuse_decision_json, error_code,
           started_at, ended_at,
           UNIQUE(run_id, task_instance_key, attempt_no))
```

每个状态转移采用短事务与唯一约束。Agent/LLM/远程数据读取在事务外运行。投影已写入而结果尚未落库时，恢复过程依据稳定 task instance identity 和投影输入血缘找回结果，再补写终态；不能再调用 Agent 制造第二份逻辑产出。

备选是只保存最终报告；那样无法解释复用、缺口、重试和崩溃点，因此不采用。

### D5. SQLite 隔离先解决旧共享连接再启用并发

Dispatcher/Trigger/Calendar 新 repository 每操作开连接，使用 WAL、busy timeout、短事务和 CAS/唯一键。旧 `TradingMemory` 的任务执行必须脱离 `get_store()` 的进程共享连接：将 schema bootstrap/迁移与普通打开分离，确保并发 worker 不重复执行 `DROP`/迁移；适配器通过显式 store 注入或 task-local store scope 获得独立连接，并在任务结束关闭。保留旧 CLI 的兼容调用，但同一并发 task 不能借用缓存的全局连接。

在启用两个并行 Agent 前设置门禁：并发写入、同一 run 恢复、跨线程读取、锁超时与迁移幂等测试必须通过。若某旧入口尚不能做到独立连接，就把它放进串行资源组；不得用 `check_same_thread=False` 充当并发正确性的证据。

备选是一次性更换数据库。当前问题可以先用连接与事务边界解决，并且数据库迁移会扩大 Phase E 的故障面，因此暂不采用。

### D6. Trigger Ledger 持有逻辑触发，APScheduler 只负责唤醒

`TriggerContext` 已定义三类幂等键，新增持久 ledger 保存请求指纹和状态。建议表：

```text
trigger_runs(trigger_key PK, kind, workflow_id, request_hash,
             schedule_id, scheduled_for, event_id, event_version,
             status, run_id, owner_id, lease_until, attempt_count,
             reason_code, actual_lag_seconds, policy_version,
             created_at, updated_at)
```

claim 使用唯一键和 compare-and-set；同键同参数返回原记录，同键不同参数显式冲突。worker 在运行时续租，恢复者只能在过期后认领非终态任务。外部副作用另由目标领域的幂等键约束；ledger 只能保证一次逻辑触发，不能保证网络调用物理上只发生一次。

cron 的 key 包含原计划时刻而非唤醒时刻；事件 key 为 `event_id + event_version + workflow_id`。每种 workflow 在策略表中声明宽限、补跑、跳过、最大回溯窗口和交易时段策略。重启扫描持久计划与 ledger 的差集，形成补偿或 skip 记录；APScheduler 的 `coalesce`/misfire 设置只是唤醒优化，不是审计依据。

改期时对未认领旧触发标 `superseded`；已在运行的旧触发允许完成研究但在 Chief 门禁前查当前 event version，过期则标 `incomplete`。已完成旧触发保留历史。运维查询暴露预期/实际窗口、重试、迟到和跳过原因，并提供同键补偿命令。

备选是直接用 APScheduler job store 充当去重和恢复权威；它不包含分析 task 的输入/结果身份，也不足以处理双 worker 与改期审计，因此不采用。

### D7. Calendar 由 Data Platform 发布，来源观察与事件版本分开

Data Platform 的 refresh controller 使用受治理适配器：现有 Finnhub/yfinance 财报计划能力作为财报候选基础；FOMC 从联储发布的会议/日程材料获取；CPI/非农从 BLS 发布日程，PCE/GDP 从 BEA 发布日程。各来源的 URL/格式、预算、抓取周期、许可和质量门在 `config/data/` 注册。来源获取失败记录为失败并保留上一版，不把“本轮没抓到”解释成取消。自动来源与手工覆盖都走同一准入、版本发布接口；Data Product 只提供日历元数据和 as-of 查询。

持久模型至少区分：

```text
schedule_event_candidates(source_id, source_native_id, event_identity,
                          raw_time, raw_timezone, raw_payload_hash,
                          fetched_at, quality_status, reason_codes, lineage_ref)
schedule_events(event_id, event_version, event_type, entity_or_symbol,
                reporting_period, subevent, local_date, local_time,
                timezone, scheduled_at_utc, session, state,
                source_refs_json, announced_at, fetched_at, revised_at,
                payload_hash, override_actor, override_reason,
                PRIMARY KEY(event_id, event_version))
```

`event_id` 基于不随日期变化的身份：财报为实体+财年/财季+财报子事件，宏观为系列+统计期+发布阶段，FOMC 为会议身份+决议/发布会子事件。候选缺少足以确定身份的期间或实体时进入待核验，不用日期拼一个不稳定 ID。来源原样时间、IANA 时区、UTC、BMO/AMC/盘中/unknown session 一并保留。日期只有 day precision 时 `scheduled_at_utc` 留空，通过窗口策略安排检查，不生成一个虚假的精确时刻。

同源重复不增版；时间/session/状态或决定触发条件的字段变化才增版。不同来源差异只在可解释的时区/一日边界内归并；更大未裁决冲突停在 candidate/conflict 状态。人工覆盖写新版本，保留 actor、理由、证据和被覆盖版本；删除 overlay 后重新发布来源得出的新版本，不抹去覆盖历史。

已有 `data.runtime.earnings` 把财报日期当作高波动 runtime 输入。Phase E 以其采集器产生候选，再把**可复核的计划快照**发布到持久 Calendar；对实际财报数字仍按正常准入读取，绝不从日历读实际值。计划事件可安排 prep/check；只有已准入的 release、filing、宏观数据 vintage 等确认信号能启动事件后分析。电话会迟到时由新文档/数据版本的准入触发后续运行，保留最初分析版本。

备选是继续把 YAML 当主要日历，或者把 runtime `next_earnings()` 的即时返回直接当持久真相；前者无法自动跟踪改期，后者没有可审计版本和冲突处理，因此都不采用。

### D8. 事件路由和旧 scheduler 按 workflow ID 迁移

新增显式事件路由表：事件类型/状态/子事件 → workflow ID、scope resolver、窗口策略、所需材料与是否仅分析。首期典型路由：财报计划 → Fundamental prep/check；已确认财报披露 → Fundamental event；FOMC 决议和 CPI/PCE/NFP/GDP 已发布 → Macro；人工事件只能引用已登记 workflow。路由表在启动时校验，Calendar payload 不携带研究结论或任意可执行命令。

旧 scheduler 作业逐项归档其 owner：研究分析、数据采集、Clerk/券商对账、运维维护。只迁移研究分析调用和财报研究窗口；来源采集仍由 Data Platform controller 负责，Clerk 对账仍由 Clerk owner 负责。兼容 CLI 经 Dispatcher 创建手动触发。`legacy | shadow | dispatcher` 模式按 workflow ID 保存；shadow 使用隔离的运行与投影命名空间，禁用实盘写入。正式切给 Dispatcher 前先记录同一时间窗的新旧计划、输入和结果差异，并验证旧入口已停止对该 workflow 直接调用 Agent。

Phase E 可以让用户显式请求完整分析并进入现有 Chief 审批链，但自动触发默认 `enter_decision_cycle=false`；开启任何自动组合决策需要单独、显式的路由策略和完整性门禁。真实券商写路由保持 Phase F 的单独切流门禁。

备选是一口气替换整个 `runtime/scheduler.py`；它混有 PEAD、数据采集、账本和运维作业，整体替换容易丢失独立补偿语义，因此按 owner 和 workflow ID 逐项迁移。

## Risks / Trade-offs

- [Phase D 部分角色入口尚未就绪] → 注册/接线逐角色验收，缺席任务形成可见缺口；完整交易请求在六类角色齐备前 fail closed。
- [同一 Sector 依赖多个 Layer 实例] → 运行计划固定 fanout scope 和配置版本，Sector 消费精确 ID 集合；添加跨 layer 输入缺失测试。
- [SQLite 旧连接在并发下交错写入] → 先完成连接隔离及迁移/普通打开分离，未达门禁的适配器仅在串行资源组运行。
- [LLM 超时后仍在后台写入] → task-local 资源与 attempt token；迟到提交被身份/CAS 拒绝并记录，不让新 attempt 与旧 attempt 同时成为成功结果。
- [来源日历相互冲突或不更新] → candidate/conflict 状态与 freshness 告警；不把失败当取消，不猜测财报时刻。
- [计划事件被误认为实际发布] → 路由按计划/已确认状态分离；事件后分析在已准入材料存在前进入待重试。
- [旧新作业重复执行] → workflow ID 级 owner 开关及 trigger identity 对照；切流前停止旧直调，影子运行只写隔离命名空间。
- [自动补偿造成大量旧任务同时重跑] → 每 workflow 限制最大回溯/并发和补偿预算，超限持久记为需人工处理。

## Migration Plan

1. **盘点与验收基线：** 锁定目标文档和 Phase D 可用入口；列出旧 scheduler/CLI 每条分析调用、采集作业、Clerk 作业、交易副作用及 owner。建立旧新相同窗口的计划对照样本。
2. **连接与存储：** 完成 SQLite bootstrap 与普通连接分离，加入 `workflow_runs`、`agent_runs`、`trigger_runs` 的 additive migration、索引和并发/CAS 测试。任何真实并发启用前通过旧入口连接隔离测试。
3. **注册与执行：** 登记七类稳定任务、scope resolver、角色适配器和决策 profile；实现 DAG 校验、ready queue、资源组、attempt 与恢复、投影选择、完整性门禁。先开放手动分析型运行。
4. **日历数据：** 建立 candidate/版本表与 Calendar Data Product；接入财报、Fed、BLS、BEA 计划来源和 YAML 人工覆盖；用固定 fixture 验证重复、改期、冲突、取消、时区、休市和来源失败。
5. **触发与补偿：** 将 cron/event wake-up 转为 Trigger Ledger claim；实现租约、恢复、misfire、改期失效和运维查询。保持自动路由仅分析，纸面/影子验证 PEAD 已公布门槛。
6. **逐项切流：** 每个 workflow 先在隔离空间影子对照，再停止对应旧直调并启用 Dispatcher owner；保持 Data Platform 采集与 Clerk 对账各自的调度 owner。对 `ats schedule`、`ats events` 与手动 CLI 输出更新兼容说明。
7. **验收门禁：** 运行注册/依赖、并发失败隔离、缓存新鲜度、全流程缺口、崩溃恢复、双 worker、事件修订/取消、DST/休市/misfire、旧新互斥、无未授权订单的集成测试；发布需求场景与验证证据对照表。

**回滚：** 对受影响 workflow ID 暂停新 claim，等待或显式终结在途租约，核对旧新 trigger ledger 后恢复旧 owner；新增表与事件版本保留。若日历刷新失败，只暂停受影响事件路由并保留最后可核验版本，不撤回其他手动/cron 分析能力。实盘 Trader 路由不在本阶段变更。
