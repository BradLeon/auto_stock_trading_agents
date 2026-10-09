# Phase F 第 0 组：恢复实施前置确认

日期：2026-10-07。范围：0.1–0.6；这是恢复实施的前置确认，不是生产资格、接线或切流验收。C3 自动下单继续停用。

复现：`UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_recovery_20261007/preflight.py`。证据：[preflight-evidence.json](phase_f_recovery_20261007/preflight-evidence.json)，记录读取时点、依赖版本、文件 SHA256、实际状态和三组内存反例。生产 SQLite 连接使用 `mode=ro` 和 `query_only`；不初始化数据库、不落资格证据、不运行生产工作流、不连接券商。

## 0.1 文件 → 消费者 → 证据影响清单

当前保护面为 **10 个唯一文件路径**；JSON 的 12 行是消费者重叠项，不是 12 个文件。全部十角色：layer/information/sector/fundamental/macro/technical/chief/risk/trader/clerk。

| 文件 | 当前指纹影响 | 本轮处理与必要补验 |
|---|---|---|
| `config/data/target_dataflow_coverage.yaml` | 全部既有 evidence 的 manifest 摘要 | A 的 products/allowed_consumers/连线、版本与证据面升级；十角色结构契约、exact-scope、旧版本/漂移拒绝与最小资格重验 |
| `src/ats/data/consumer_api.py` | 十角色 dependency | A 增加受限用途/行情入口，影子仍经适配层；重验全部既有产品输入，其他角色权限不变，生产/隔离分离 |
| `src/ats/data/assurance.py` | 十角色 dependency | 优先保持实现；复用追加式 API，不豁免 manifest/dependency 漂移。确需改动时十角色补验 |
| `config/data/structured.yaml`、`unstructured.yaml` | 十角色 dependency | 本轮优先不改；切流使用覆盖层。必要修改纳入全体受影响 scope，不重采数据 |
| `config/risk.yaml` | risk | 价格政策如需落入现有配置须在冻结前完成；规则版本/审批引用/失败语义重验 |
| `src/ats/execution/state_api.py` | chief/risk | 实际消费边界接线优先覆盖层；必要改动补内部状态完整性、来源时点、跨进程与安全回退 |
| `src/ats/execution/authorization.py` | trader | 保留十字段及 revision/generation，补 A 的不可改批准订单/提交门禁否定路径 |
| `src/ats/decision/repository.py` | trader/clerk | 实际 review/approval 写点接线，审批报价审计关联；关闭写点拒绝、不可变 revision、幂等恢复 |
| `src/ats/execution/clerk.py` | clerk | 实际发布接线及回退，模拟部分/迟到成交、幂等与绩效重建 |

**当前清单以外也有真实依赖**：`contract_validation.py`、runtime 价格适配、`risk/checks.py`、`trader/execute.py`、`graph/chief.py`、broker 默认拒绝与会话账户、registry/snapshot/dispatcher、旧 scheduler/publish 仲裁。它们不是“未被指纹列出就无影响”。7.14 应将新治理白名单和价格服务/价格政策纳入依赖闭包及合理的共享/消费者指纹面；以测试决定归属，不省略真正执行依赖。

当前待改路径及入口修复详见 tasks 的 2.2/2.3/5.3/5.6/7.14–7.17；这份可审阅清单授权范围是修复既有门禁与 A 的受限读取，不允许扩大交易权限或放宽证据标准。新的契约范围例外仍需另行提出。

**证据保护结论**：保留原账本行、原测试报告、缓存与原始数据、A–E/Dataflow 历史验证；受保护文件升级后，旧证明可能不适用于当前资格，这是漂移，不是删除原材料。当前默认资格表缺失不构成作废历史材料的理由。冻结前完成修复和最小补验，冻结后追加新基线证据并查询资格；TTL 刷新不夹带代码变更。当前受保护文件 SHA256 已固定，本轮不修改这些文件。

**最小重验分层**：全十角色做清单/代码白名单/权限/漂移回归；持久数据消费者复用可核验 refs、vintages 和缓存，仅补实际读/发布/回退缺口；Chief/Risk 补状态/审批；Risk/Trader 补行情来源、时效、币种、缺价与价格漂移；Trader/Clerk 补授权/提交边界/幂等/成交链。不得重新执行 44 项建设或全量采集来替代缺失的业务入口证明。

## 0.2 PF-7-01 权威完整性规则：2026-10-08 已裁决

用户已确认采用 **CATEGORY + 所选 TASK 依赖闭包** 的双层规则，复用现有 A–E 规范，不采用“所有注册任务都必须成功”。

权威依据：`openspec/specs/workflow/dispatcher-runtime/spec.md` 的“统一执行入口必须显式声明决策请求”和“Chief 门禁必须以固定必需类别复查完整性”；后者已规定 AI 硬件完整流程必须具备 Layer、Information、Sector、Fundamental、Macro、Technical 六类，Fundamental 按请求语义选择例行或事件其一。`docs/TARGET_WORKFLOW_DATAFLOW.md` §6/§10.1 要求全部被要求任务成功且快照固定。

建议裁决：

1. 普通单任务/子流程只展开请求与其依赖闭包，按 TASK 判运行完整性，默认不进入决策；不得把“局部运行 complete”当作“可开周期”。
2. 完整决策请求在启动前固定必需 CATEGORY、每类的任务绑定、各任务 scope、Fundamental mode、schema、输入/vintage 及新鲜度规则。AI 硬件固定六类；其他 workflow 如不支持某必需类，先显式声明可审阅的决策契约，不静默删除类别。
3. 所选 TASK 及依赖必须成功或合法复用，固定必需 CATEGORY 也必须在 Chief 开周期前全部满足。Fundamental 不强制两个 mode 同时完成；但请求选 event 时，不能以 routine 的结果掩盖 event 失败。新增未被请求的注册任务不会使已有完整决策永久 incomplete。
4. 快照、run contract、Dispatcher 与实际 Chief 开周期入口使用同一固定需求描述，复查 scope/schema/血缘/时效/hash。缺失、失败、blocked、stale 或校验不可确认时不创建周期；中途依赖失效按已有 supersede/重新审批规则处理。

现有分歧：`decision/snapshot.py` 按类别接受任一候选；`intake_verification.verify_incomplete_input_blocks` 把 registry 全部任务作为 request，`run_contracts.build_run_result` 按 requested TASK 判完整。因此例行 Fundamental 已有投影而 event 缺失时，snapshot 可 complete、run 却 incomplete。只改为 CATEGORY 会掩盖“所选 event 失败”；只要求 registry 全部 TASK 会错误地要求两种 mode，并让无关新任务成为永久阻塞。

**裁决状态：2026-10-08 用户明确确认。** 用户认可：“本次选 Event，就必须满足 Event；选 Routine，就必须满足 Routine。两者都不替代其他必需分析类别。”该裁决要求所选任务及依赖成功或合法复用，不能用另一模式的结果掩盖缺失/失败；不要求两种模式同时运行。上述请求范围、必需类别、模式绑定和验证条件作为 7.4 的权威实施规则。0.2 已完成；本轮只记录规范，7.4 仍未完成。三个现有实现反例保留在 `phase_f_recovery_20261007/preflight-evidence.json`。

## 0.3 业务范围、实际入口与兼容矩阵

### 范围表达与 owner

资格主键沿用 `domain_id + consumer_id + contract_version + scope`。`scope` 是规范化业务对象，包含实际 kind/id、排序去重的 entities、明确 UTC 时间窗；事件请求另有 event_id/version，运行输入还绑定 snapshot/data-vintage refs。投影的 kind/id 与资格业务范围分别记录并显式映射，不能将产品名列表、笼统 portfolio 字符串或消费者名单替代实体/时间。精确范围失败不自动找更宽范围。

时间窗来自冻结请求/批次，不能随 wall clock 漂移；行情真实时点另行检查，不把短 TTL 与逐报价 freshness 混为一谈。跨层范围列出每个任务实际 scope；macro 的 portfolio 投影即使允许合法复用，也不扩大其他消费者资格。实体集合或事件版本变化视为新范围，不沿用旧范围证明。

| Workflow / owner（当前均 legacy） | 实际新入口及 task 依赖 | 业务范围 / 必需类别 |
|---|---|---|
| layer-review / Layer | `phase_e.phase_e_registry` → Dispatcher → Layer adapter；legacy `cli.run_layer_review` | layer/sector + 展开的实体与时间；Layer |
| information-brief / Information | Dispatcher → Information；legacy `cli.run_information_pass` | entity/portfolio 展开实体与时间；Information |
| sector-review / Sector | `sector-review ← layer-review`；legacy `cli.run_sector_review` / `run_sector_html` | sector、其 layers/entities 与时间；Layer+Sector；旧读模型责任任务 **9.6** |
| fundamental-routine / Fundamental | `fundamental-routine ← information-brief`；legacy PEAD research 路径 | entity、报告期/信息引用/时间；Information+Fundamental routine |
| fundamental-event / Fundamental | `fundamental-event ← information-brief`；legacy PEAD event 路径 | entity + event_id/version + 时间；Information+Fundamental event |
| macro-review / Macro | Dispatcher Macro adapter；legacy `cli.run_macro_review` / scheduler `_macro_weekly` | portfolio + 时间/vintage；Macro |
| technical-review / Technical | Dispatcher Technical adapter；legacy `cli.run_technical_review` / `_technical_daily` | entity 或 portfolio 展开 + 行情时间；Technical，固定 runtime 输入 |
| 完整决策 / Chief → Risk → Boss → Trader → Clerk | `cli.run_chief` / `_chief_daily` → `graph/chief.py`；审批 repository；`trader.execute.place_orders` / `IBKRBroker.place_orders`；`execution.clerk.clerk_run` | 六类别及所选 Fundamental mode；portfolio/entities + snapshot + revision/approval/account；Chief 投影/渲染责任任务 **9.3**；不可用局部研究替代完整决策 |

调度入口 inventory：legacy `_daily`、`_weekly_review`、PEAD event/score windows、手动 CLI；新 `phase_e_schedules.yaml` 有五条 schedule，owner 有七条 workflow。它们不是七个同质 cron，必须从实际 job/event 配置生成独立 expected-trigger 集合，按 workflow+scope+planned instant 或 event/version 归一；Layer 依赖展开及 Fundamental event 不能靠计数映射猜测。采集/FactSet ingest/journal 对账不随研究 scheduler 重启或迁移。

### 六边界、权威状态及证明责任

| 边界 | 实际强制点（待接线/补验） | 权威 owner / 状态 | 所需任务与证明 |
|---|---|---|---|
| projection_read | CLI/Workflow/Dispatcher 治理读，Chief 渲染、Sector HTML | 逐 consumer/scope 路由 + qualification；release overlay 仅发布映射 | 5.6/5.14、9.2/9.3/9.6、13.1；缺资格/投影/回退拒绝 |
| analyst_output | 真实 task projection 发布点 | Analyst publisher + 当前 claim owner/generation | 5.2/5.3、4.5、7.2/7.3；旧 worker 迟到发布拒绝 |
| dispatcher_schedule | legacy cron/event/manual 与新 Dispatcher 执行/发布点 | dispatch owner/generation/claim；YAML 是意图，重载/重启生效可查 | 4.2–4.7、10.2/10.3、13.2；共同遗漏及重复拒绝 |
| approval_lifecycle | `DecisionAuditRepository.record_review/record_approval` 与 Chief 开周期 | 审批写方 + revision/snapshot + boundary state | 5.3/5.8、7.4/7.5、13.10；disabled 真实写点拒绝 |
| clerk_publication | `execution.clerk.clerk_run` 及 ledger/state 发布 | Clerk 唯一发布者 + boundary state | 5.3、7.5、13.10；幂等/迟到/恢复与关闭拒绝 |
| live_trader | Trader 授权入口和每次 broker submit | route/generation/freeze + 独立 write grant + 实际会话账户 | 2.2/2.3/2.8、11.2/11.3、13.3；缺 grant/旧代拒绝，C3 保持 disabled |

当前六边界 wired 声明不等于上述点已 enforced。控制表、覆盖层、owner/schedule YAML 涉及多个存储时，由 5.15 的冻结/可恢复协议协调；不能把逐表 COMMIT 称为事务。

### 独立/联合批次

| 实际范围 | 允许独立的条件 | 必须联合/停止的条件 |
|---|---|---|
| 已发布研究产品/投影的手动读取 | 该 consumer/scope 的 target 投影与旧发布者 schema/ref 已证明兼容，读资格/报告/回退齐备；调度及审批/Clerk 保持原路由 | 若旧发布者不产生 target 可读产物，则将其发布者/读取者显式纳入联合批次；没有兼容证明就拒绝独立请求 |
| 研究 scheduler owner 迁移 | 新旧 owner 使用同一发布契约、读范围已就绪、claim/publish fencing 与回退去重均闭合 | 若迁移同时改变输入/发布格式，联合 projection_read/analyst_output；列出所有实际消费者，不允许 consumers=[] |
| 审批/内部状态/Clerk | 双方可解析同一不可变 snapshot/revision/review/approval 链，有实际恢复兼容证明时可分别迁移 | 无跨版本解析/发布证明时 approval_lifecycle+clerk_publication 联合；涉及研究格式不兼容时加 analyst_output 及依赖读取；不隐式开启 live |
| Trader A 的契约/报价实现 | 取证前开发和隔离补验；C3 关闭，无 production route 变化 | 当前不是 live 批次，不得以 MARKET_DATA 可读自动开放 submit；真实 live 在本 change 之外 |
| 已运行的采集发布 | 核验接收，不重复切换 | 缺 owner/发布/回退证明则登记缺口，不能随研究批次重启全部采集 |

这是待实现的 **逐 workflow/scope 条件矩阵**，不是当前全局三组配对代码已通过兼容验收。`cutover.INCOMPATIBLE` 当前仍强制 read/schedule、analyst/approval、approval/clerk 成组；5.4/5.14/5.15 须按此矩阵实现和验证，修复前不执行生产切换。

所有联合批次在 8.1/8.6 登记全部实际边界与 consumer/domain/contract/scope、各资格与适用报告、原/新 owner 和 generation、生效/冻结点、覆盖整个动作的部署授权、观察/停止阈值、逐边界回退证明及负责人。缺任一项停止该联合范围；依赖闭合的独立研究范围可推进。中途崩溃保持全范围冻结或完整回滚，恢复后重验；不得自动缩小已声明的联合范围来宣告成功。

## 0.4 Git/uv/配置基线与授权来源核对

- 当前 Git：`feat/rebuild_workflow_dataflow`，HEAD `02c81f260718ee0ecdb78a63f2ef96d133a1e7d7`。本轮起点含前次审计和 OpenSpec 未提交修改，HEAD 不代表所有工作区内容；当前受保护文件、uv.lock/pyproject、settings/owner/schedule/retirement 配置摘要见 JSON。
- 真正前置代码：`6b72d05b79774b9369d324da94da15dd56b56fa0`，位于 Phase F proposal 和首个实现 `7ff59b6` 之前；源码树不存在 broker_write_guard。不是当前 HEAD 自比较。
- **可复现性限制**：该前置提交跟踪 tests 文件数为 0。首次测试入库为 `374bcf1184941142d9a0089bfcd00718c3e73118`，已经位于首个 F 实现之后。13.6 必须固定其使用的兼容既有测试集及 SHA256、fixtures、依赖和配置，并分别报告新增 F 测试/不能在旧代码收集的测试；不能声称重建了前置历史完整测试集，不能以此认定原 45 项失败全部早于 F。原 JUnit 与报告保留，当前不重跑全量、不宣称全绿。
- uv 入口 `uv run --offline --no-sync`；uv/Python/OS、完整已安装包版本、当前锁文件与前置锁文件 SHA256 均已记录。本轮仅验证 uv 现有环境，不安装、不同步、不自行更换依赖。后续对照如锁文件不同，明确两种实验：各提交自己的锁用于历史重现、同一固定依赖用于代码差分；不得混用结果。

| 记录 | 只读核对结果 | 来源与适用范围结论 |
|---|---|---|
| DEP-2026-10-06-A | `phase_f_batches.sqlite` 有 1 条；六分析消费者；有效期标称 2026-12-31 23:59:59 +08:00；actor/note 非空 | 原 GROUP_PROGRESS 的 PF-B-01 声称用户于 10-06 批准，可作为文档来源线索；本会话没有原始批准凭据。不能据该记录推断 Chief/Risk/Trader/Clerk、审批/发布、联合批次全动作已授权 |
| 调度授权 | `schedule_cutover` 使用同一 batch DB 部署授权对象，并没有独立 schedule 授权行 | 有效期/六消费者名单不是动作范围证明；必须在 8.6 核对原始授权是否涵盖对应 workflow owner 迁移、重载/重启、观察与回退，以及联合边界。不能笼统写成“无授权”，也不能认定全调度已获授权 |
| LIVE-X | live DB 有 1 条，issuer 标称 human、scope=trader、environment=live、到 2099 年，note 空 | 缺原始批准来源、记录时间与用途说明；搜索跟踪源码/测试未找到可验证来源。账户/名字脱敏为摘要。不能认定实盘授权，也不删除或改写该行；C3 保持停用 |

JSON 保存的是记录摘要与字段存在性，**provenance_verified=false**；非空字段不能证明 human 身份。授权语义/来源未闭合的生产动作继续阻塞；此项完成的是基线固定与只读核对，不是发出新的生产许可。

## 0.6 A 契约与价格政策：实施基线 v1

以下是可审阅的工程默认值，供 7.14–7.17 实现与隔离验证；不是生产开通批准。字段不足时补 gateway/适配，不能将旧日线重命名为可执行报价。调整默认值属于显式 policy_version 变更，必须在最终冻结前完成。

### 契约、scope 与证据

- Trader consumer `contract_version` 计划升为 `target-dataflow-v2`，domain 保持 `execution_authorization`；products 为 APPROVED_EXECUTION_AUTHORIZATION + MARKET_DATA，TTL 仍 1 天。MARKET_DATA 的已有 Technical 日线/期权入口不变，新增按 purpose 路由的 `runtime-execution-price-v1` payload；无需将它持久化为 Data Product。
- Risk 已允许 MARKET_DATA，保留其产品/内部状态 domain，因新增行情证明及消费语义将 Risk consumer 升为 v2，TTL 仍 7 天；其他八角色不扩大权限。所有角色 manifest 漂移仍按现有规则重验，不拿版本未变掩盖全文件变化。
- 资格 scope：kind/id、entities、currency、明确时间窗、purpose（`preapproval_normalization` / `approved_execution_check`）规范化并精确查询；审批依据/订单 scope 另外关联 cycle、pending-intent 或 revision、approval、account/environment。资格证明验证的是治理能力与拒绝行为，**不是对本次待审订单的批准**；因此审批前读权限不会循环依赖该订单先获 Boss approval。审批后读取须再核对本次已批准修订，不能用资格替代授权。
- Trader required_evidence 保留 authorization/revision_hash/broker_submit_boundary/idempotency/fallback，并增加 runtime_query/timestamp/failure_semantics/no_persistence；Risk 保留内部状态证明并增加同样 runtime 证明。每个证据说明覆盖的两个用途和产品；交易端 fallback 是拒绝/重新审查，不允许以可读旧 close 证明“当前执行报价回退可用”。
- 7.14 必须同步 YAML、白名单、allowed_consumers/Target 连线、API/守卫/文档；新 quote adapter、白名单和真正消费/价格政策依赖纳入指纹闭包。0.1 的受影响面必须在实现后重算，不能提前写死仍为十路径。

### 价格种类与适用阶段

| 阶段 / price_kind | 用途与时点 | 策略 |
|---|---|---|
| 审批前当前 bid/ask | 买单用 ask，卖单用 bid；Risk/Trader 使用同一规范化输入引用 | 只接受可验证 source_as_of 的非延迟价格，买/卖方向清楚；股数换算及限价保护完成后再固定 revision/hash 和审查批准 |
| 隔夜 `previous_session_close` | 隔夜限价/金额规范化可用最近完整正常交易时段的未复权 Close；必须披露为历史参考价 | 保留供方真实 bar 时点/日期及精度，不伪造 tick 时间；要求确为最近已完成 session，且年龄不超过 4 个日历日，超过/无日历/复权状态不可确认时拒绝。审批记录显式声明延后到正常交易时段执行及价格校验条件，原授权/规则有效期仍适用；到执行时必须取得新鲜 bid/ask，过期授权重新审批 |
| 提交前当前 bid/ask | 只校验批准订单的可执行性；不得重新换算 qty 或修改 limit/type/direction | 必须符合正常交易时段与以下时效/偏离限制；超条件返回重新审查批准。限价买 ask>批准 limit、限价卖 bid<批准 limit 时保持未提交/不可执行，不用原授权改价 |
| `last`、mid、adjusted_close、缺时点价格 | 可作为研究/诊断信息，不替代上述执行基线 | 不作为本轮自动股数换算/提交校验的回退；不能把买卖成交时点或 queried_at 当作当前 bid/ask 的 source_as_of |

### 可机器验证的默认政策

`policy_version=phase-f-execution-price-v1`；时间统一 UTC，正常交易时段用既有交易日历/交易所 session，日历不可确认则拒绝执行。市场外执行默认关闭；隔夜订单保留为条件待执行，不能以市价降级扩大时段。

- 当前 bid/ask 最大 source age **30 秒**，source_as_of 最多领先 queried_at **2 秒**（时钟容差）；缺实际 source timestamp、供方标识 delayed/unknown、非有限/非正价格均拒绝。实际提交前再次检查年龄，查询时间不刷新 source age。
- 必须返回 symbol、currency、source、source_as_of、queried_at、price_kind、session/market-data-mode、adjusted 标识和用途；双边 quote 须 ask≥bid，spread/mid 不超过 **100 bps（1%）**。币种与订单/批准账户资金语义不匹配时拒绝，不隐式 FX。
- 提交方向价格与审批参考价的绝对偏离 `abs(execution-reference)/reference` 不超过 **100 bps（1%）**；批准中如有更严限制取更严者，不放宽已批准限价/最大金额/风险阈值。相同 qty 在当前价格下超过批准资金/风险边界即拒绝；不自动缩量。
- 数量按已声明 instrument 的股数/lot 精度确定；缺 instrument/最小交易单位则拒绝。完整金额型订单需在审批前规范化，合法零股数产生可见的不可执行/No Action 原因，不静默丢弃；现有批准后更改任何意图必须新 revision/hash 并重新审查批准。
- 缺价/失败/stale/symbol-scope/currency/session/spread/deviation/funds 分别记录可读 reason；审批依据与执行校验 quote refs/hash 分开，并记录政策版本。读取结果不能签发 write grant，所有模拟验证不解除 C3。

**现有能力核验**：`consumer_api` MARKET_DATA 当前走 `fetch_close_history_many`（只有 bar_as_of date、closes/status/source/queried_at）或 options runtime；没有本政策要求的当前买卖双边报价、币种、非延迟身份及真实 quote 时点。`IBKRBroker` 有 session 和持仓/greeks 查询能力，但没有可直接接受为治理股票执行报价的 typed API。7.15 应优先复用已有 IBKR 集成增加只读报价 adapter，由 runtime gateway 作为唯一查询 owner；供方无真实时点/权限或 TWS 不可达时返回 unavailable，不能拿本地接收时间/持仓 marketPrice 冒充行情时点。隔离 fake adapter 用于工程验证，真实只读证明由 7.13 承接。隔夜 close 的日期/未复权/session 语义也须补验证，不因现有 history 函数存在即通过。

## 第 0 组验证与遗留

结论：0.1/0.3/0.4/0.6 的前置方案、清单和只读核对已形成；0.2 尚待用户裁决。0.5 的 SUMMARY/ACCEPTANCE/runbook/进度记录已同步，记录守卫已支持显式历史重开并要求当前每个勾选任务具有可定位记录与范围结论，但须在其前置全部闭合后才能勾选。三个反例均由现有函数在内存复现，不是生产六类别验收。各 schema/权限/报价的新规则均尚未实现。

本轮验证：[verification.json](phase_f_recovery_20261007/verification.json) 与 [记录守卫 JUnit](phase_f_recovery_20261007/record-guards.junit.xml)。通过 uv 执行记录/进度守卫 **20 passed**；负向覆盖新勾选缺记录、丢失链接、空结论、未勾选却列完成、历史重开缺明确标记。生产状态比对使用只读 SQL，未调用会初始化数据库的 list/bootstrap helper。116 项任务中 37 项勾选、79 项待办，原编号保留，依赖图无循环；两个 change strict 验证通过，父 change 仍保留原 `sector/layer-analyst` 归档 INFO。受保护文件/配置摘要、生产授权记录及六边界路由与本轮读取基线一致；未重跑全量套件。

**后续注意/待修复项**：0.2 裁决及其后 0.5 最终验收，以及第 1/2/4/5/7 组接线与报价、最终冻结/资格、真实 TWS 只读证明、授权动作来源核对。

**为何现在不修**：本轮限第 0 组；完整性语义必须先取得 0.2 裁决，代码/配置实施在对应依赖任务进行，生产交易不在本轮范围。来源无法核实的授权维持未核实，不删记录、不借字段非空认定有效。


## 2026-10-08 第 0 组收尾

用户已明确裁决 0.2，0.5 的全部前置闭合；SUMMARY/ACCEPTANCE/runbook/进度计数及验证索引已同步，第 0 组 **6/6 完成**，全 change **39/116 完成、77 项待办**。上节 37/116 和“待裁决”保留为 2026-10-07 的验证时点记录，本节为当前状态。

后续仍需 7.4 实际开周期门禁、A 及业务接线实现、影子/回退/资格等任务；本裁决不构成生产切流或实盘授权，C3 保持停用。收尾验证见 `phase_f_recovery_20261008/verification.json` 与 `record-guards.junit.xml`。
