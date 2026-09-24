## 1. 基线与调度所有权

- [ ] 1.1 逐项盘点 `runtime/scheduler.py`、CLI、财报窗口、数据采集和 Clerk 作业，记录 workflow ID、当前 owner、触发语义、外部副作用及回滚入口。
- [ ] 1.2 核对 Phase D 六类分析角色入口及投影契约的可用状态；为尚未就绪的入口定义显式 `missing`/`blocked` 适配结果，不以空投影代替成功。
- [ ] 1.3 固定 AI 硬件决策 profile、现有行业 scope 配置来源和手动/cron/事件路由清单，编制旧新相同窗口的对照 fixture。

## 2. SQLite 连接与运行持久化

- [ ] 2.1 将 Workflow Memory 的 schema bootstrap/迁移与普通连接打开分离，保证重复启动和并行 worker 不重复执行破坏性迁移。
- [ ] 2.2 为 Dispatcher、Trigger Ledger 和角色适配器提供 task-local SQLite 连接，设置 WAL、busy timeout 和短事务；任务结束关闭连接，避免并行任务复用 `get_store()` 缓存连接。
- [ ] 2.3 以 additive migration 创建 `workflow_runs`、`agent_runs` 和所需唯一键/查询索引，兼容旧 CLI 和既有投影表。
- [ ] 2.4 实现运行计划、attempt 状态、输入引用、投影引用、复用判定和缺口原因的 repository 读写及 compare-and-set 终态更新。
- [ ] 2.5 增加迁移幂等、跨线程读写、并行写入、锁超时和同一 run 恢复测试；测试未通过前保持对应角色串行执行。

## 3. Task Registry 与运行计划

- [ ] 3.1 登记 `layer-review`、`information-brief`、`sector-review`、`fundamental-routine`、`fundamental-event`、`macro-review`、`technical-review` 的输入、输出 schema、依赖、触发模式和资源组。
- [ ] 3.2 实现注册表加载校验：ID 唯一、依赖存在、图无环、跨分析师边仅 Layer→Sector 与 Information→Fundamental、schema 可解析、策略字段有效。
- [ ] 3.3 实现按请求 scope 展开 task instance 的 resolver，固定 AI 硬件多 Layer fanout、Sector 所需 Layer ID 集合、Fundamental 所需 Information ID 集合及配置版本。
- [ ] 3.4 实现手动单任务、自动补齐依赖的子流程和完整分析的计划生成，保存稳定 instance key、plan hash、依赖边和运行时不可变 scope。
- [ ] 3.5 在创建决策型 run 前验证版本化 profile 和请求任务集合；局部分析带 `enter_decision_cycle=true` 时明确拒绝，不隐式追加未请求的分析类别。
- [ ] 3.6 用重复 ID、缺失依赖、环、非法跨角色边、多 Layer fanout 和配置运行中变化 fixture 验证计划行为。

## 4. Dispatcher 执行、投影复用与 Chief 门禁

- [ ] 4.1 为各角色实现适配器，将固定 scope、`as_of`、触发上下文和精确上游投影引用传入现有入口；PEAD 事件适配器调用既有可恢复图。
- [ ] 4.2 实现有界 ready queue、资源组配额与完成通知，让 Phase 0 的 Layer/Information 及独立的 Macro/Technical 可并行，下游只等待其声明的依赖。
- [ ] 4.3 增加逐任务超时、可重试错误、退避、取消或隔离迟到提交以及 attempt 记录；上游失败只阻断可达下游。
- [ ] 4.4 实现 Task Projection selector，逐项校验角色、scope、schema、时间有效性、input refs、data vintage、上游 ID/hash 和终态，保存命中或拒绝原因。
- [ ] 4.5 实现崩溃恢复：按稳定 task instance identity 找回已发布投影并补写终态，避免重新运行同一逻辑任务或接受过期 attempt 的迟到提交。
- [ ] 4.6 在聚合结果和 Chief 调用前双重验证固定 profile 的所有必需投影；失败时保存 `incomplete` 缺口并禁止创建 decision cycle。
- [ ] 4.7 将通过门禁的精确投影 ID/hash 交给既有 Chief 研究快照构建器；分析型 run 默认只发布报告，不调用 Chief。
- [ ] 4.8 验证独立并行、依赖阻断、重试/超时、投影失效、崩溃点恢复、完整流程缺项和零未授权交易副作用。

## 5. Trigger Ledger 与唤醒

- [ ] 5.1 以 additive migration 创建 `trigger_runs`、唯一触发键、租约/状态索引和请求指纹，保留完整状态历史。
- [ ] 5.2 实现手动、cron 和事件 TriggerContext 归一化：cron 使用原计划时刻；事件使用 `event_id + event_version + workflow_id`；同键异参显式冲突。
- [ ] 5.3 实现原子 claim、续租、过期租约接管、终态幂等和 run 绑定；双 worker 竞争只能建立一个逻辑 run。
- [ ] 5.4 为各 workflow 配置 misfire 宽限、补跑/跳过、最大回溯窗口、交易时段和补偿预算；重启时对持久计划与 ledger 差集作可审计决议。
- [ ] 5.5 实现事件改期的旧触发 `superseded` 处理；在途旧版研究可完成，但进入 Chief 前必须重新校验当前事件版本。
- [ ] 5.6 提供按 workflow、事件、计划窗口和状态查询触发记录及同键补偿的运维命令，输出迟到、跳过和冲突原因。
- [ ] 5.7 用重复投递、双 worker、claim 后崩溃、续租失败、misfire、改期及手动补偿测试验证一次逻辑触发语义。

## 6. 自动 Schedule Calendar 数据产品

- [ ] 6.1 在 `config/data/` 登记财报、Fed、BLS、BEA 来源的预算、抓取周期、质量规则、许可和来源血缘；保留 `config/events.yaml` 作为人工覆盖及自定义事件层。
- [ ] 6.2 新增 `schedule_event_candidates`、版本化 `schedule_events` 及必要的来源/覆盖审计表，支持 as-of 查询且不篡改旧版本。
- [ ] 6.3 实现稳定事件身份：财报实体+财年/财季+子事件，宏观系列+统计期+发布阶段，FOMC 会议+子事件；身份不足的候选进入待核验。
- [ ] 6.4 接入现有 Finnhub/yfinance 财报计划采集，并建立 Fed 会议、BLS CPI/非农、BEA PCE/GDP 日程来源适配器；抓取失败保留最后可核验版本并记告警。
- [ ] 6.5 实现来源候选准入、同源去重、跨源冲突裁决、决定触发字段变化才增版、取消/改期状态与人工覆盖的 actor/理由/证据记录。
- [ ] 6.6 规范 IANA 时区、原样本地时间、UTC、日期精度、BMO/AMC/盘中/unknown session 和交易所休市；只有日期时不伪造精确 UTC 时刻。
- [ ] 6.7 发布只读 Calendar Data Product：版本和 as-of 读取、来源血缘、质量状态、刷新时间及 freshness；不包含投资观点或实际业绩值。
- [ ] 6.8 使用固定来源 fixture 验证重复、改期、取消、来源失败、跨源冲突、人工覆盖撤销、DST、休市和未知财报 session。

## 7. 事件路由与旧 Scheduler 逐项切流

- [ ] 7.1 建立可校验的事件类型/状态/子事件到 workflow ID、scope resolver、窗口策略和必需材料的显式路由；Calendar payload 不得携带任意命令。
- [ ] 7.2 将计划财报仅路由到 prep/check，将已准入的财报披露/电话会/指引材料路由到 Fundamental event，将确认发布的 FOMC 与 CPI/PCE/非农/GDP 路由到 Macro。
- [ ] 7.3 保留既有 PEAD“实际公布后才能评分”、未知 session 双窗口探测和补打门槛；材料未发布时只记录待重试，不产出虚假事件后分析。
- [ ] 7.4 让手动 CLI、cron 唤醒和事件唤醒经 Trigger Ledger 调用同一个 Dispatcher；更新 `ats schedule`/`ats events` 的兼容输出与操作说明。
- [ ] 7.5 实现每个 workflow ID 的 `legacy | shadow | dispatcher` owner 模式；shadow 隔离投影/运行命名空间并禁止券商写入，切流时停止该 workflow 的旧直调。
- [ ] 7.6 确认数据采集、Clerk 对账、券商相关作业仍由各自 owner 运行；自动分析路由默认不进入 Chief，Phase E 不开启新的实盘 Trader 路由。
- [ ] 7.7 对每个迁移 workflow 对照新旧计划窗口、输入快照、投影、漏跑和重复执行；记录切流与回滚证据后逐项启用 Dispatcher owner。

## 8. 可靠性验收与交付

- [ ] 8.1 建立三个 delta spec 的需求—测试矩阵，运行单元、集成及重启恢复测试并保留可复现命令和结果。
- [ ] 8.2 验证所有自动事件路径在缺少任一必需分析、投影过期、日历冲突或材料未准入时不会创建决策周期或提交订单。
- [ ] 8.3 演练 workflow ID 级回滚：暂停新 claim、处理在途租约、恢复旧 owner，并确认运行、投影和事件历史仍可查询。
- [ ] 8.4 更新调度/日历的配置与运维文档，说明数据来源、刷新失败、人工覆盖、补偿命令、owner 切换及 Phase F 实盘边界。
