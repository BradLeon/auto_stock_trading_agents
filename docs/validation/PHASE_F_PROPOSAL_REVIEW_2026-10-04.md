# Phase F proposal 审阅报告

审阅日期：2026-10-04。审阅对象：`implement-phase-f-shadow-run-and-cutover` 的 proposal、design、tasks 和全部 7 份 delta spec。对照范围：`refactor-workflow-dataflow-architecture` 的 F.0/11.x、目标架构文档、相关主规范及当前实现。

实际 Git 分支为 `feat/rebuild_workflow_dataflow`；`refactor-workflow-dataflow-architecture` 是当前仓库中的 OpenSpec change 名称。本次未切换分支、未修改规划或实现、未执行生产操作。报告中的缺口是规划契约与当前实现之间的分析，不是对未来实现已发生故障的断言。

**结论：方向合理，但建议修订后执行，不建议直接按当前版本启动完整实施。**

`openspec validate implement-phase-f-shadow-run-and-cutover --strict --json` 通过，issues 为零。该结果证明规划格式有效，不证明下面的运行时不变量已被完整定义。没有运行全量应用测试：本次审阅未改变应用代码，且测试不能替代尚未定义的验收契约。

值得保留的设计包括：复用既有 assurance API；按 exact scope 接收证据；不重复采集；隔离账本与生产资格分开；部署授权与实盘授权分开；未达退役判据的项保持 pending。这些均与总规划一致，无需推翻方案。

下列 P1 表示执行前应补齐的关键契约；P2 表示会造成漏实现、误判验收或范围歧义的规划问题。

**1. P1：进程内能力签发不足以定义跨进程单活，路由绑定也缺少切换代次。**

位置：`design.md:64–70`、`tasks.md:77–79`、`specs/execution/live-route-switch/spec.md:9–23`、`specs/execution/authorization-gate/spec.md:9–23`。

设计明确把 broker write capability 签发到进程内，但没有说明多个 CLI/常驻进程共用的权威状态、旧 grant 如何被撤销、提交与切换如何互斥。假设旧进程已持有 grant，新进程完成切换并取得 grant，旧进程仍可能提交。仅校验两个配置入口，不能处理尚未退出的旧进程。

授权目前只绑定 route ID。A→B→A 回滚后，仅比较 ID 无法区分第一次 A 和第二次 A，不能证明此前授权永久失效。

最小修改：规定持久化、共享的活跃 route 与单调递增的 generation；能力和授权绑定 route、generation、运行环境及账户；每次实际提交重新验证；切换和提交建立互斥边界，旧代能力不得再提交。可以使用适合本机 SQLite 部署的机制，无需引入分布式平台。既然设计否定以端口作为 paper/live 授权依据，也应明确如何验证实际券商账户与能力的环境匹配。

验收至少覆盖：两个进程竞争、旧进程延迟提交、仲裁状态不可读、切换中崩溃恢复、A→B→A 后旧授权拒绝、paper 能力连接到不匹配账户时拒绝。

**2. P1：“在途授权清空”缺少可查询的生命周期和原子切换协议。**

位置：`specs/execution/live-route-switch/spec.md:25–39`、`specs/execution/authorization-gate/spec.md:25–39`、`tasks.md:80`。

当前 `src/ats/execution/authorization.py:45–107` 的授权由审查和审批记录即时构造；模型没有独立授权 ID、有效期字段或完成状态。新 spec 要求枚举“有效且未完成的授权”并返回剩余有效期，却未说明以什么持久化状态判定、如何识别尚未构造但已可构造的授权、完成是否等于订单提交完成。

此外，“检查为空”之后到路由切换之间仍可能签发授权。授权过期也不等于券商订单终结：`IBKRBroker.place_orders` 只短暂轮询，后续成交和不确定提交由对账处理。

最小修改：明确权威查询与生命周期，可复用 decision/cycle 状态，不必另建重复账本；先冻结该 route 新授权签发和新提交，再核验排空，再切换代次并开放新 route。对 submitted、partial、unknown 和迟到成交规定阻断或显式承接策略；授权过期不得让未知提交从检查范围消失。旧订单可以被只读对账承接，但不得使旧 route 继续拥有新增提交能力。

验收至少覆盖：检查后并发签发、提交超时但券商已接收、快照过期但订单未终结、部分/迟到成交、冻结中重启恢复。

**3. P1：计划中的 catalog 配置切换会使资格指纹自失效。**

位置：`proposal.md:36`、`design.md:60`、`tasks.md:19,27,61`。代码依据：`config/data/target_dataflow_coverage.yaml:416–420`、`src/ats/data/assurance.py:61–75,609–624`。

资格政策强制包含整个 `config/data/structured.yaml` 的文件指纹；proposal 又计划在资格通过后修改其中的消费者模式。于是：登记证据→qualification eligible→修改消费者开关→文件指纹变化→后续 qualification ineligible。该文件是多个消费者的共同依赖，改一个消费者还可能使其他消费者的证据失效。这与逐范围切换的目标冲突。

最小修改：优先明确运行时切流只使用既有 release overlay 和独立控制状态，不改已取证的 catalog 基线。若确实修改这些受指纹约束的文件，则必须在最终代码/配置上重验、追加证据，并重算受影响消费者资格，不能修改旧证据或放宽指纹标准。第 9 组对 authorization 的代码修改也会影响 Trader 指纹，应纳入最终资格复核顺序。

验收至少覆盖：首批切换后的再次 qualification、第二批消费者资格、回滚后的资格与生效路由、最终代码变更后的重新取证。

**4. P1：影子证据尚未形成完整的输入固定和切流通过判据。**

位置：`specs/workflow/shadow-reconciliation/spec.md:9–27,79–93`、`design.md:72–78,143–146`、`tasks.md:43–45,56,61`。

“同一已发布数据快照”不足以固定全链输入。当前 consumer API 的 MARKET_DATA、BROKER_STATE 和内部状态会读取运行时值，Chief 旧上下文也读取当前时间和账户状态（`consumer_api.py:173–199`、`agents/chief/assemble.py:45–59`）。新旧分析顺序运行期间，行情、持仓、成交和时间可能改变；持久化 vintage 相同仍不能把风控差异归因于新旧路径。

报告只有 matched/diverged/not-compared，尚未规定：各批次的必需比较面；允许的语义或数值差异；diverged 的接受权限；签核、驳回、撤销的有效状态；报告与当前 scope/代码/配置的匹配；批次执行器必须引用有效报告。现在明确拒绝 not-compared，但未闭合其他通过条件。LLM 输出与角色重构也不适合默认逐字相等。

最小修改：定义可重放的影子输入包，固定发布 refs、运行时行情/账户/历史状态、逻辑评估时间、规则版本与模型/提示配置，并保存 hash。定义逐批比较矩阵及差异接受规则；切流执行时必须校验有效签核、完整覆盖、无未接受差异及证据仍适用。独立研究读取批次可不要求实际交易比较，但必须在矩阵中明确其适用面；完整交易批次不可省略必要面。

验收至少覆盖：持久化 vintage 相同但 runtime snapshot 不同、已撤销报告、scope 不匹配、未接受风控差异、合法角色语义变化、补齐报告后重新签核。

**5. P1：调度切换缺少旧入口接入统一逻辑触发标识与所有权检查的任务。**

位置：`design.md:88–94,135,141`、`specs/workflow/dispatcher-runtime/spec.md:9–45`、`tasks.md:68–73`。

当前旧调度 `src/ats/runtime/scheduler.py:882–930` 注册自己的 APScheduler jobs，`_daily` 和 `_event_triggers` 直接调用角色入口；它没有使用 `owner_mode` 或统一 `trigger_key`。Phase E 是另一条显式 `--phase-e` 启动路径。只切换 YAML owner/enabled，无法证明已运行的旧 scheduler 停止触发，也无法让旧路径在回滚后按新账本去重。

spec 的目标正确，但 tasks 尚未明确改造旧调度拦截点、映射旧 job/event 到共同逻辑 trigger，以及旧进程如何收到切换状态。“未完成触发逐个声明处置”也不能单独解决清点后又产生新触发的竞态。

最小修改：新增旧 scheduler/手动调度适配任务，统一 workflow、scope、计划时点或 event/version 的逻辑身份；在执行前读取共享 owner/代次并登记认领；冻结新认领后清点与移交；运行任务承接时必须证明旧执行方不能继续发布同一结果。配置变更的重载或受控重启方式要明确。遗漏比较应包含独立预期触发集合，否则新旧同时遗漏不会被双方差异捕获。

验收至少覆盖：旧常驻进程未退出、旧 daily cascade 与新 schedule 对应同一工作、认领期间切换、重启/misfire/DST、回滚后旧入口跳过新路径已执行 trigger。

**6. P1：资格失效时的回退要求缺少“无安全旧路由”分支。**

位置：`specs/workflow/cutover-control/spec.md:41–55`、`design.md:56–62,116`、`tasks.md:19,55`。

这些要求统一描述为资格未通过就回旧路由；但本 change 同时允许旧实现退役，而且 F.0 要求旧入口是否稳定以实际核验为准。资格过期/撤销、回退证据自身失效或旧路由已 retired 时，强制退回旧入口可能触发墓碑拒绝，或恢复已知故障路径。单纯 `qualification()` 返回 ineligible 不能选择安全处置。

最小修改：明确只有当前 scope 的回退证明有效、目标未退役且实际可用时才能回退；否则停止受影响范围、记录 blocked/unavailable 并阻断依赖该输入的交易。已完成发布的历史结果、下游审批是否仍有效，也应按既有依赖失效规则重验。资格漂移不得仅在下次读时留下日志后继续使用旧分析结果提交。

验收至少覆盖：缺 rollback 证据、回退目标 retired、回退目标不可用、资格撤销后已组装 snapshot 的下游执行、无关消费者继续运行。

**7. P2：六个开关中三个仍以状态模型为主，实际作用点不够具体。**

位置：`specs/workflow/cutover-control/spec.md:9–23`、`tasks.md:17–21,58–84`。

projection read、schedule、trade 有专门执行任务；analyst output、approval lifecycle、Clerk publication 没有同样明确的路由接入、兼容组合与回滚验收。只验证“切换一条不影响另外五条”，可能实现出可查询开关，却未证明角色发布、审批写入与 Clerk 发布真的受其控制。

最小修改：为每条边界列出 scope、实际调用点、legacy/shadow/new/off 语义和权威写入方，并补接线任务及跨边界兼容矩阵。开关状态可以独立，但不兼容组合必须拒绝。特别说明 Chief 的 projection 渲染和 Sector CLI 旧读模型迁移由哪项任务完成，避免第 10 组只检查 pending 而无人完成迁移。

**8. P2：最终影子验收用真实 trades 表无新增成交作为下单证明，观测对象不充分。**

位置：`tasks.md:100–101`、`specs/workflow/shadow-reconciliation/spec.md:47–55`。

券商可能已接收订单而本地因异常未写入 trades，此时表无新增不能证明无真实下单。反过来，影子期旧活跃 route 的合法成交或切换前订单的迟到成交，可能使表新增，但并不是新影子路径越权。

最小修改：以新路径的 broker submit 调用/拒绝审计、能力验证和隔离演练中的券商模拟接收记录为主要证据；trades 无污染作为另一条验收。真实只读核验按 route/账户/订单归因排除既存合法成交，不要求生产账本在整个影子窗口完全静止。意料之中的模拟提交被能力层阻断可以算成功；意外真实提交尝试必须成为独立差异或停止条件。

**另有三处应明确的范围语义。**

第一，`cutover-control/spec.md:35–39` 把交易开关开启且任一研究消费者无新读取资格定义为全局启动失败，但 `design.md:125` 又要求默认保持旧交易 route、此阶段不改变生产行为。应明确这是新 route 激活门禁还是也约束现有旧 route，并定义关闭交易后允许研究服务启动的状态。未知 scope 的资格不能在启动时被假定为已全面验证，提交前仍须按本次决策 scope 重验。

第二，总规划 design 明确允许未签发生产资格时通过受限候选/影子路径补验；本 change 的读解析则强制 qualification。不降低门禁的前提下，需要明确仅用于隔离接入验收的入口，避免“先资格才能接入、先接入才能资格”的循环。第 4 组开始前应已具备进程禁写和隔离运行条件，不依赖第 5/9 组稍后补建的保护。

第三，`design.md:114` 允许全部 ineligible 时交付门禁与演练，而任务 7.4/8.6 要求实际授权后的切换。应明确关闭 change 时如何呈现未授权或未就绪的实际切换任务，并向总 change 回写状态。“门禁实现验收完成”“生产读/调度切流完成”“实盘切流完成”应分别列示。保留外部实盘授权是合理边界，但本子 change 完成不能代表整个 Phase F 已完成。

**建议修订顺序与执行判定。**

先补交易仲裁/代次与授权生命周期，再解决配置指纹和安全回退；随后补影子输入与通过政策、旧调度适配；最后完善其余开关接线和验收范围。把这些要求分别写回对应 spec、design、tasks，并添加上述否定与恢复场景，无需扩大到重建 A–E。

修订后，可按该 proposal 的分阶段方式实施基础设施与隔离演练。实际读/调度切流仍以对应范围的资格、接入、有效影子证据、回退和明确部署授权为执行条件；真实 live 切换继续遵守本 proposal 已声明的外部实盘授权边界。
