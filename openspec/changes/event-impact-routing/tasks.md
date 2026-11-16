# Tasks

> 前置：`article-witness-observations` 已完成（三个文章证人恢复产出观测）。每组只跑直接相关的测试；写生产、改定时任务与路由开关前须经用户批准。

## 1. 投影契约

- [x] 1.1 `task_projection.py`：新增 `FundamentalMaterialReviewPayload`（拒绝动作与数量字段），以及 `InformationBrief.event_components`、`LayerAnalysis` 的风险命题状态 / 论点外冲击 / 补认领 / 下调建议、`SectorAllocation` 每层的 `downgrade_reasons`，全部 v1 可省略。验证：新增单测覆盖新字段校验、含 `target_weight` 的重大事件评审被拒；用库中一份上线前的 `LayerAnalysis` 与 `InformationBrief` 投影做回读校验并通过。
- [x] 1.2 `planning.py`：登记任务 `fundamental-material` 与角色 `fundamental_material_review`（依赖 `information-brief`，有效期 14 天）。验证：`test_workflow_planning`、`validate_task_registry` 通过（既有的名单数量失败除外）。
- [x] 1.3 文档：`docs/ARCHITECTURE.md` §7.2/§7.3（新角色与各 Payload 新字段）。验证：文档与 schema 字段一致（人工核对，或按文档一致性测试）。

  - 2026-11-13 实施记录（第 1 组）：
    - `task_projection.py`：新增 `EventComponent`（简报 `event_components`）、`RiskClaimState` / `OffThesisShock` / `CrossScopeClaim` / `UtilizationAdvice`（层分析，`direction` 只允许 down/none）、`LayerAllocation.downgrade_reasons`（嵌套字段，空时在 `SectorAllocationPayload.payload_json` 中剔除）、`FundamentalMaterialReviewPayload`、`FundamentalRiskRegisterPayload`（设计 D4 的风险项投影，规格同步补入），两者共享"拒绝仓位/动作"的校验基类；新角色加入 `AgentRole`、`AGENT_ROLES`、`PAYLOAD_SCHEMA_BY_ROLE`。
    - 兼容性：库内 173 份已发布投影（层、简报、板块、财报评审、例行）改前改后按存储字段重算 `content_hash` 全部一致（另有 1 份 10-25 的 L1 投影本来就被现 schema 拒收，与本次无关）。
    - `planning.py`：任务 `fundamental-material`（角色 `fundamental_material_review`、依赖 `information-brief`、有效期 14 天、超时 900 秒、读 `pead.yaml`），不进 `FUNDAMENTAL_TASKS`；跨分析师边 `information_brief → fundamental_material_review`。
    - 测试：新增 `tests/test_event_impact_contracts.py` 8 项；投影、规划、调度、架构守卫等相关 270 余项通过，唯一失败为既有的名单数量断言（18≠11）。
    - 顺带修复：`495c0db` 让"只改注释"不再算配置变化，但 `test_chief_snapshot` 2 项与 `test_phase_f_research_gate` 1 项仍用追加注释来模拟配置变化，改为追加一个配置值后通过；`test_phase_f_research_gate` 余下 2 项为 `TEST_BASELINE` 已登记的既有失败。
    - 文档：`docs/ARCHITECTURE.md` §7.2、§7.3。

## 2. 信息简报：事件成分与评级

- [x] 2.1 简报级成分评级（D1，方案 A）：开关 `config/workflow/event_impact.yaml`（默认关闭）、路由角色 `event_component_rating`、提示词 `information-event-components`（四级重要性的"伤害性质 × 波及范围"判据、影响面定义、直接/连带、持续/了结、升级判据）、模块 `event_components.py` 在简报发布前对当期全部事实评级一次；不改 `extract.py` 与 `research-insight`。验证：单测用录制响应断言成分结构与引用由代码取自 `source_evidence`；开关关闭时无请求、简报不变；对一份 NVDA 11-12 简报做 dry-run（录制响应）。
- [x] 2.2 新颖度与事件簇（D2）：代码按簇查 7 天台账判首发/后续，规范化簇键。验证：单测覆盖首发、后续、模型判升级三种。
- [x] 2.3 发布契约（`briefs.py`）：层与成员合法性、重大及以上须有可回溯引用否则降为中等并记原因、不合规成分拒发。验证：单测覆盖降级、拒发、全部不合规判失败。
  - 2026-11-13 实施记录（2.1–2.3）：
    - 开关 `config/workflow/event_impact.yaml`（`components.enabled: false`、`novelty_window_days: 7`、`max_facts: 120`）；路由角色 `event_component_rating`（`config/settings.yaml`，flash、关闭推理）；提示词 `src/ats/skills/information-event-components/SKILL.md`；模块 `src/ats/agents/information/event_components.py`。
    - 接入点：`briefs.publish_information_brief` 在发布前调用 `event_components.attach`（只对实体简报、且有原文定位时）；开关关闭时原样返回、不发请求。`extract.py`、`target_research.py`、`research-insight` 与 `agents/base.py` 均未改动，抽取缓存版本号不变。
    - 2.2：事件簇键由代码规范化；评级上下文列出近 7 天（所有标的）已出现的簇，让模型复用同一个键；"首发 / 后续"由代码按台账判定，只有台账里已有的簇才接受模型的"升级"。
    - 2.3：层与标的须在 `ai_hardware.yaml` 已登记（成员或证人）；层面成分须给层、跨层至少两层；含交易建议措辞的成分拒发；重大及以上无原文定位降为中等并记原因；评级失败或全部不合规时简报照常发布、不含成分，原因写入 `unverified`（规格同步改为不因评级失败而让简报失败）。
    - 测试：`tests/test_event_components.py` 10 项；信息分析相关既有 188 项通过。
    - dry-run：库内 NVDA 11-12 简报（109 条事实）用录制响应校验，两个成分（公司：董事会调查 CEO；跨层：对华芯片出口审视）的引用均由代码从 `source_evidence` 取到；据此把 `max_facts` 由 40 提到 120。
- [x] 2.4 observe 产业链成员轻量简报：从 `ai_hardware.yaml` 成员与 `pead.yaml` observe 名单推导 9 家，只出成分，不触发基本面。验证：planning 单测断言范围为 9 家且无基本面下游；dry-run 一家。
- [x] 2.5 文档：`docs/agents/INFORMATION_ANALYST.md`（成分与评级、轻量简报）、`docs/ARCHITECTURE.md` §9.1、`docs/OPERATIONS.md` §3.1（轻量简报时间表）。验证：文档描述与实现一致。

  - 2026-11-13 实施记录（2.4–2.5）：
    - 2.4：`planning._chain_observe` 在开关打开时，让每日简报（`portfolio:pead-targets`）额外覆盖 observe 名单中属于产业链层成员或命题证人的标的，实测 9 家：META、ORCL、005930.KS、SPCX、INTC、AMD、AMAT、CRWV、NBIS（只算层成员时只有 6 家，ORCL、INTC、NBIS 只是命题证人；用户确认 9 家都做）。基本面例行（同一范围）与决策周期（板块范围）不受影响。成本更正：轻量简报走同一条逐篇抽取，按近 7 天 226 篇文章计约每天 32 次抽取 + 9 次评级，首次运行补抽 7 天窗口约 226 次（原估"每天 9 次"有误，用户已确认）。测试：开关关/开两种情况的范围断言。
    - 2.5：`docs/agents/INFORMATION_ANALYST.md`（字段与"事件成分"一节）、`docs/ARCHITECTURE.md` §9.1、`docs/OPERATIONS.md` §3.1。

## 3. 评级基准集与校准

- [x] 3.1 从库中挑约 60 个候选成分（黄仁勋事件、出口管制、非财报公告、噪音）导出为标注表，交用户选 40 个并标注。验证：标注表落 `var/`，用户确认完成。
  - 2026-11-14：用户批准约 12 次请求。选 12 份近两周事实最多的简报（每标的一份：NVDA 11-12、TSLA、MU、AMZN、GOOG、SKHY、MSFT、LRCX、SNDK、COHR、TSM、AVGO），各评级 1 次，共 12 次请求，得 91 个候选成分（模型分布：轻微 39、中等 48、严重 4；本公司 78、本层 7、跨层 6）。标注表 `var/manual_fixes/event-rating-benchmark/labels.csv`（不含模型评级），模型评级另存 `model_ratings.json`。
  - 过程中修了两处：同簇同影响面的两个成分编号相同（编号改为纳入所引事实），候选已离线重新编号；4 个"层面"成分没给出层被契约拒收，提示词补明"层面成分必须给层，否则按本公司或不出"。
  - 2026-11-15 用户完成标注：只选有独特信息量的 27 个（用户判断：拆解财报、报道股价变化的条目不值得分析，不必凑满 40 个），验收以这 27 个为准。
- [x] 3.2 基准集评测脚本：对 40 个成分跑评级（执行前报请求数），计算相邻档一致率与"重大及以上"漏判。验证：达到 ≥85% 且零漏判；未达标则调整提示词后重测，结果与版本记入 `var/`。
  - 2026-11-15 首轮评测（27 条，用户标注）：完全一致 11（41%），相邻档以内 25（93%，达标），用户标为严重及以上而模型判低的 6 条（门槛 0，未达标）。原因：① 正面重大事件被只看伤害的口径评低（2 条）→ 用户确认改为"重要性 + 方向"（design D2b），代码、提示词、规格、文档已改；② 枢纽公司事件的传导（3 条）与 ③ 传闻级重大事件（1 条）待定。改口径后需重测。
  - 2026-11-15 第二轮（口径改为重要性 + 方向；对 27 个已标成分只评级、不重拆，9 次请求）：完全一致 15（56%，首轮 41%），相邻档以内 25（93%），漏判 6 → 3，误报（模型判重大、用户判中等）4。正面长约两条（B03-03、B06-04）与特斯拉—SpaceX 合并传闻（B02-09）已对上。剩余漏判：B01-15（中国外交部否认）所在事件簇已有 B01-01、B01-02 判为重大，簇层面会触发；真正未覆盖的是 B01-05（Megaspeed 芯片流向中国，独立事件簇）与 B01-08（neocloud 融资承压，独立事件簇）。误报：B05-02（谷歌 TPU 外售）、B06-03（SK 海力士股东回报）、B10-01（Coherent 200 亿美元长约）、B11-02（台积电购厂扩产）。结果文件 `var/manual_fixes/event-rating-benchmark/rerate_ratings.json`（已被第三轮覆盖，数字以本记录为准）``。
  - 2026-11-15 第三轮（用户指定只加两条通用判据：可信的违法违规指控判重大；回购、分红、既定计划内的扩产默认中等，除非改变业务前景；不再过度优化）：完全一致 17（63%），相邻档以内 25（93%），漏判 2，误报 3。B01-05（Megaspeed 芯片外流）已对上。剩余：漏判 B01-15（同簇已有重大成分，簇层面会触发）、B01-08（neocloud 融资承压，用户决定不为它加判据）；误报 B05-02、B06-03、B10-01。**用户接受剩余分歧，3.2 按此验收**，上线后由每周校准报告（3.3）持续观察。结果 `rerate3_ratings.json`。
- [x] 3.3 每周校准报告（纯代码）：下游复核分歧、补认领次数、按重要性分组的 1–3 日超额收益（相对层篮子等权）、当周抽检清单，并入日报或周报。验证：单测用合成数据断言分组与超额收益计算；对真实库跑一次生成报告。
- [x] 3.4 文档：`docs/OPERATIONS.md` §2（每天/每周看什么：触发台账、分歧、补认领、校准报告）。验证：文档含报告位置与解读。

  - 2026-11-15 实施记录（3.3–3.4）：`src/ats/workflow/event_calibration.py`（纯代码；2026-11-15 从 agents/information 挪出：它读多个角色的投影，是运维报告而非分析师）读一周内的事件成分、`fundamental_material_review` 与层分析的 `materiality_review`（不以"一致 / agree"开头即为分歧）、`cross_scope_claims`，按重要性分组计算 1–3 个交易日相对层内其他成员等权篮子的超额收益（行情经 `market_data._download_close_frame`，带 3896f69 的 Yahoo 修复；取不到时写明），并列出当周最高重要性的成分供抽检。由 `report_publishers.event_calibration_report` 在周六行业评审之后写 `事件评级校准-<日期>.md`（没有成分时不写）。测试 `tests/test_event_calibration.py` 5 项（含发布器跳过）、`test_report_publishers` 通过；对真实库空跑与真实行情取数均正常。3.4：`docs/OPERATIONS.md` §2（每周看什么）、§3.2（报告表）。

## 4. 基本面：公司风险项与重大事件评审

- [x] 4.1 公司风险项投影：生成、刷新（建基线时）、读取最新版；只写公司特有风险。验证：单测覆盖刷新保留上一版引用；初始化 18 份前报请求数，经确认后执行。
- [x] 4.2 重大事件评审 runner 与 skill：读简报成分、论点基线、风险项，输出论点状态、命中项、预期影响类别、`direction/magnitude/confidence`、待核验、重要性复核、认领成分编号；无基线时 `confidence ≤ 0.3`；只评估公司特有部分。验证：单测用录制响应覆盖有基线/无基线、拒绝仓位字段；skill 文档写明认领分工。
- [x] 4.3 有效期与续期：14 天过期，同簇再触发发布新版本并续期。验证：单测覆盖过期不再被选入、续期生效。
- [x] 4.4 文档：`docs/agents/FUNDAMENTAL_ANALYST.md`（第三种产出与叠加关系、风险项）、`docs/ARCHITECTURE.md` §9.2/§9.3、`docs/OPERATIONS.md` §3.3（手动运行重大事件评审）。验证：文档与实现一致。

  - 2026-11-15 实施记录（第 4 组）：
    - `src/ats/agents/fundamental/material.py`（新）：`build_register` / `latest_register` / `init_registers`（默认 dry-run，`python -m ats.agents.fundamental.material --init [--apply]`）；`run_material_pass` 读简报里被引用的成分及其所引事实、财报基线、最新风险项，发布 `fundamental_material_review`（`valid_until` = 现在 + 14 天；无基线时信心封顶 0.3；叙述过交易建议拦截）。
    - 提示词 `skills/fundamental-material-review`、`skills/fundamental-risk-register`；路由角色 `fundamental_material`、`fundamental_risk_register`（默认中档模型，同 fundamental_analyst）。
    - `entry.py` 新模式 `material`（触发 `material_development`，`material_request`）；`dispatcher.py` 新适配器 `_fundamental_material_adapter`（需 `component_refs`）；`routine.ensure_baseline` 建新基线后刷新风险项（失败只记日志）；`architecture_guards` 把两个新投影登记为基本面自有。
    - 4.3：有效期由 `valid_until` 与任务新鲜度 14 天共同约束；同簇再触发发布新版本。过期评审不进决策快照的读取规则在第 8 组实现。
    - 测试 `tests/test_fundamental_material.py` 7 项；基本面、调度、规划、架构守卫相关 150 余项通过（名单数量断言为既有失败）。
    - 顺带修复：`1de28d6` 把校准报告放在 `agents/information/` 下，触发架构守卫（信息岗跨角色读取），已挪到 `src/ats/workflow/event_calibration.py`。
    - 4.1：2026-11-15 用户批准后初始化 18 个 target 的风险项，18 次请求全部成功，记录在 `var/manual_fixes/risk-register-init-20261115/`。抽查发现风险项只依据财报基线叙事生成、不读近期简报，NVDA 的清单里没有关键人一项（黄仁勋事件未进入），用户决定改为生成时也读近 14 天简报事实（最多 40 条）并立即重建：`recent_facts` 与 `--rebuild`，测试 2 项；重建 18 次请求全部成功（`var/manual_fixes/risk-register-rebuild-20261115/`），NVDA 清单新增"CEO 去职与治理危机""CEO 调查与国会传唤""中国出口许可与监管"。
    - 文档：`docs/agents/FUNDAMENTAL_ANALYST.md`、`docs/ARCHITECTURE.md` §9.4、`docs/OPERATIONS.md` §3.3。

## 5. 层次分析：风险命题、冲击与补认领

- [x] 5.1 链证据支持 `kind: risk`：独立来源计数（同出版方/同原发转载合一）、至少 1 个一手或权威来源、状态五档；经营命题门槛不变。验证：单测覆盖纯转载不达门槛、权威+独立达"承压"、经营命题行为不变。
- [x] 5.2 `ai_hardware.yaml`：L5 新增出口与市场准入风险命题（含证人、关键词、horizon）；重写 L5"刻意不立出口管制命题"注释为指向风险命题并说明门槛差异；其他层是否新增风险命题列出建议交用户确认。验证：配置加载测试通过；`ats sector` 的数据组装检查（不打 LLM）显示 L5 风险命题与候选证据。
- [x] 5.3 层次分析读取层面成分与安全网成分（D6），输出风险命题状态、论点外冲击（14 天过期、是否建议新增命题）、补认领、只下调的建议，每个认领成分附重要性复核；只评估整层共同与相对位置。验证：单测用录制响应覆盖归入命题、列为冲击、补认领、过期降为背景、无风险时不建议下调。
- [x] 5.4 文档：`docs/agents/LAYER_ANALYST.md`、`docs/ARCHITECTURE.md` §4（数据与 Agent 对接：层分析新增简报成分输入）。验证：文档与实现一致。

  - 2026-11-16 实施记录：
    - 5.1：`ClaimKind` 加 `risk`；`chain/corroborate.risk_claim` 复用去重与逐簇判读（判读器与缓存不动），计数改为独立来源（`chain/risk_sources.py`：按 `source_actor` 去掉"经…转述"取原发、别名表合并同一出版方；文章出版方的台账簇一律算一个来源且不算权威，公司或统计源的一手簇算权威）。未达门槛记 `unresolved_reason=source_gate`。别名表与权威名单在 `event_impact.yaml` 的 `risk_claims` 段。在 11-08 以来真实简报上试算：106 个原发方，WSJ/IBD/Barron's 的多种写法各自合并。
    - 5.2：L5 新增 `l5_export_market_access_risk`（维度 `xpu_export_market_access`），"刻意不立"注释改写为经营命题与风险命题的门槛差异。**加开关**：`event_impact.yaml` 新增 `layer.enabled`（默认 false）；关闭时 `load_sector_config` 滤掉所有 `kind: risk`，`planning._file_hash` 也按滤后的内容算血缘——实测 ai_hardware.yaml 仍是 438828…，提交本组不会让存量层评审/行业评审看起来过期（避免重演 11-12 chief-daily 失败）。数据组装检查（开关临时打开、不打 LLM）：台账 0 簇、简报成分 0 条——维度在抽取时分配，存量观测没有这个维度；成分评级也还没开。**上线时的证据缺口**：已入库的出口管制报道（如"Jensen Huang Affair Revives Push for Chip Restrictions"）不会自动补上这个维度，回放验收（9.2）时需决定是否对近 14 天相关文章重抽（报请求数后再定）。其他层暂不新增风险命题，候选建议见 wrap-up 交用户确认。**2026-11-16 追记（d7e97e6）**：常驻 dispatcher 仍是 11-13 启动的旧代码而 YAML 实时读取，旧 schema 不认 `kind: risk`，加载配置会失败——命题已从 `ai_hardware.yaml` 撤下，**上线前暂存于 `pending/l5_export_market_access_risk.yaml`**，9.3 时贴回。
    - 5.3：`agents/layer/event_inputs.py`：读近 14 天简报成分（本层/跨层点名本层 + 成员"重大"及以上的本公司成分，按实体+簇+内容去重，最多 30 条），渲染输入与规则；`apply` 核对输出（承压/兑现过来源门槛否则降为出现、冲击需可读成分且≥中等、14 天过期降为背景、补认领需传导理由、使用率建议只下调且须引用合格依据），改动写入 `gaps`。开关打开才换用 `LayerVerdictEventView`，关闭时提示词与输出结构不变。风险命题不参与层状态锚定，也不进 `common_evidence`/证据块。架构守卫：`layer_analyst` 允许读 `information_brief`。
    - 测试：`tests/test_layer_risk_and_events.py`（15 项）。

## 6. 板块配置：只下调传导

- [x] 6.1 板块配置按触发来源区分临时/例行复盘，代码钳制方向只下调、临时单次最多一档，被截断的建议与原因记入 `downgrade_reasons`；`weight_cap` 与风控静态上限不变。验证：单测覆盖临时两档被截为一档、例行两档放行、风险解除不触发上调、风控读值不变。
- [x] 6.2 文档：`docs/agents/SECTOR_ANALYST.md`（配置结论 → 预算使用率一节补下调规则）。验证：文档与实现一致。

  - 2026-11-16 实施记录：`agents/sector/review.py` 新增 `ALLOCATION_LADDER`、`apply_downgrade`；`_sector_allocations_from_projections` 读层投影的 `utilization_advice` 沿档位向下调整（`ad_hoc` 时按 `routing.ad_hoc_max_notches` 钳制，例行不钳制），原因与被截断部分写入 `downgrade_reasons`，`allocation_extras` 发布下调后的档位。`run(..., trigger_source=)`；Dispatcher 的 `sector-review` 适配器从任务输入 `trigger_source` 取值（默认 `scheduled`，第 7 组路由传 `material_development`）。层投影只有在层开关打开后才可能带 `utilization_advice`，所以本组提交不改变现行配置结果。测试：`tests/test_sector_downgrade.py`（6 项）。`tests/test_sector_allocation.py::test_sector_allocation_projection_published_with_input_refs` 在改动前的 HEAD 上同样失败（既有问题，与本组无关）。

## 7. 事件路由、去重与台账

- [x] 7.1 `event_routes.yaml` 新增 `material_development` 与两个 scope resolver（`component_entity`、`component_layers`），层复盘后接 `sector-review`；产业链成员的本公司重大成分同时路由到其所在层的 `layer-review`（D2b）；路由带总开关，默认关闭。验证：路由加载与解析单测；未登记层被拒并记原因。
- [x] 7.2 简报发布后把合格成分（重大及以上、首发或升级）写为事件候选；dispatcher 在 :05/:35 消费；去重键为簇 + 下游 + 范围 + UTC 日，升级或新成分可再触发；触发与"去重拦下"均入台账并带成分编号。验证：单测覆盖中等不触发、后续报道被拦、新成分再触发、不进入决策周期。
- [x] 7.3 文档：`docs/ARCHITECTURE.md` §2（新事件类型与路由）、§8.3（触发去重）；`docs/OPERATIONS.md` §3.1（事件表）、§9（排障：如何查一个成分触发了什么）。验证：文档与实现一致。

  - 2026-11-16 实施记录：`workflow/material_events.py`（`load_routes`、`chain_index`、`candidates`、`targets`、`dedupe_key`、`dispatch_material_developments`、`ledger_rows`）。**与原计划的差异**：没有另建"事件候选"表——消费节拍直接扫描近 `routing.lookback_hours`（6）小时创建的简报投影，候选就是简报里的合格成分；去重靠触发台账的 event_id/version 与 `material_event_ledger`，重扫同一简报不重复记账。`event_routes.yaml` 新增 `material_development` 段（`component_entity` → fundamental-material；`component_layers` → layer-review，`then: sector-review`）；`workflow_owners.yaml` 登记 `fundamental-material`（YAML 只是初始值，生产负责人在 `var/phase_f_dispatch.sqlite` 的 `schedule_scope_owners`，现无该行——**上线时需新增 `fundamental-material,*,dispatcher`，属生产写入，9.3 时报批**）。**2026-11-16 追记（d7e97e6）**：旧 dispatcher 读到 YAML 里的 `fundamental-material` 而 SQL 无对应 owner 行，日历消费从 09:05 起报错——**owner 行已从 `workflow_owners.yaml` 撤下**，9.3 时与 SQL owner 行一起登记。挂到 `dispatcher_daemon` 的日历消费节拍。总开关 `event_impact.yaml` 的 `routing.enabled`（关）。测试：`tests/test_material_events.py`（7 项）。

## 8. Chief：可选类别与事件对齐

- [x] 8.1 `decision_profiles.yaml` 登记可选类别 `fundamental_material`；`decision_requirements` 冻结与校验有效的重大事件评审，缺失不算缺口，单份血缘失效只剔除该份不阻断周期；财报口径二选一不变。验证：单测覆盖无评审时快照完整、有评审时被冻结、血缘失效被剔除；用 495c0db 的只读重放脚本对真实库校验通过。
- [x] 8.2 `assemble.py` 按 `component_refs` 渲染事件簇对齐块；主理人 skill 加"同簇评估不重复计算"。验证：单测断言同簇的基本面评审、层风险、板块下调原因出现在同一块。
- [x] 8.3 文档：`docs/ARCHITECTURE.md` §10.1（决策快照的可选类别与事件对齐）、Chief skill 说明。验证：文档与实现一致。

  - 2026-11-16 实施记录：
    - 8.1：`decision_profiles.yaml` **顶层**新增 `optional_categories.ai_hardware: {fundamental_material: fundamental_material_review}`（刻意不放进 `profiles.ai_hardware`：`restore()` 用当前配置重建计划并比对 `plan_hash`，计划又哈希档案本身，放进去会让已冻结、待审批的周期报"configuration drift"作废；已核对档案哈希不变，旧进程只读 `profiles`，多一个顶层键无害）；`assemble.material_reviews` 对计划内标的取最新评审，要求已发布、在 `valid_until` 内、≤14 天、所引成分仍在已发布简报里，合格的冻结进 `ResearchSnapshot.optional_items`，其余写入 `optional_excluded`（含原因）。**与原计划的差异**：没有放进 `items` 与 `decision_requirements` 的 manifest——`validate` 要求 items、manifest、计划任务一一对应，塞进去会改变现行 chief-daily 的严格校验；可选类别因此独立冻结，不计缺口、不参与完整性判断，`to_payload` 在为空时不写这两个键（存储形状不变）。`read_scope` 登记 `fundamental_material_review` 的生产方为 fundamental，架构守卫允许 chief 读取。Dispatcher 的快照记录也带上这两项。
    - 8.2：`assemble.event_alignment_block` 按 `component_refs` 回查简报的 `cluster_key`，把重大事件评审、层的风险命题/冲击/补认领、引用这些编号的行业配置 `downgrade_reasons` 归入同一「事件簇」标题；`material_block` 列出可选评审与未纳入原因。Chief skill 信号层级新增第 4 条「同一事件簇的评估不得重复计算」（原第 4 条顺延为第 5 条）。
    - 验证：`tests/test_chief_material_alignment.py`（7 项）；只读重放（`chief_assemble_check8.py`：standalone → snapshot → attach → validate，不调模型、不连券商、不建周期）在真实库上快照完整、optional 为空、对齐块为空、payload 键仍为 built_at/items/requirements/scope。validate 报 `technical-review GOOG freshness_unconfirmed`，在 HEAD 上同样复现（周一 04:09 UTC，当天的技术面尚未运行），与本组无关。

## 9. 总览文档与回放验收

- [x] 9.1 `README.md` 分析岗一览与数据流概览加入事件成分分发；`docs/ARCHITECTURE.md` §3（Dataflow：简报成分 → 事件 → 下游）与 §16（记录本 change 的六项决策）；`docs/data/` 补简报成分字段说明。验证：README、ARCHITECTURE、OPERATIONS、角色文档之间对新流程的描述一致（逐项核对清单记入任务说明）。
  - 2026-11-16 实施记录：README（分析岗一览：基本面第三种产出、层次分析的风险与只下调建议；运作节奏图新增"重大事件成分"子图与说明）；ARCHITECTURE §3（Dataflow 图加 Memory→Workflow 的成分触发虚线，图后补成分数据流一段）、§16（第 12–17 条记录本 change 的六项决策）；`docs/data/DATA_ARCHITECTURE.md` §12.3a（成分只在 Memory、不回写共享事实，与原文定位和来源计数的关系）。一致性核对（逐文档）：触发门槛"重大及以上、首发或升级"——README、ARCHITECTURE §2/§8.3、OPERATIONS §3.1、INFORMATION/FUNDAMENTAL/LAYER 角色文档一致；"临时复审单次最多一档"——README、ARCHITECTURE §16、OPERATIONS §3.1、SECTOR 一致；"14 天有效期"——ARCHITECTURE、OPERATIONS、FUNDAMENTAL、LAYER 一致；"不临时启动 Chief"——README、ARCHITECTURE §2/§8.3/§16、OPERATIONS 一致；开关位置均指向 `config/workflow/event_impact.yaml`。顺带发现的既有不一致（非本 change）：README 写基本面例行 09:00、技术面 10:00，OPERATIONS §3.1 写两者 08:30 纽约，未改。
- [x] 9.2 回放验收：隔离环境回放 2026-11-09 至 11-12 的已入库新闻（执行前报请求数）。验证：拆出 NVDA 公司成分与出口管制层面成分；NVDA 重大事件评审与 L5（及涉及层）复盘各触发一次不重复；L5 使用率只下调且临时复盘不超过一档；Chief 只读重放显示事件对齐块。
  - 2026-11-16 回放（最小版：NVDA/AVGO/MRVL/TSM × 11-09…11-12，隔离根 `var/isolated/event-impact-replay-20261116/`，记录见其 `evidence/README.md`）。已验证：拆分（NVDA CEO 调查为本公司重大、逐日升级；11-12 出现跨层重大"出口法案"）、后续报道拦下 22 次、不进决策周期、NVDA 重大事件评审质量、Chief 快照纳入可选类别与事件簇对齐块。回放发现并已修复三处：①同一节拍同一下游重复触发（L5 一拍 4 次）→ 改为同节拍按"任务+范围"合并，4 天下游运行 36→24，规格同步修订；②重大事件评审要求所引简报是"最新可读"，触发与运行之间有新简报即失败 → 按编号读所引简报；③层次分析忽略事件成分 → 事件块前置、`risk_claims` 必填、未归宿成分写入 gaps。修复后 L5 重跑：出口准入风险命题判"承压"（独立来源 8、权威 2），选举类成分未归宿被逐条标出。**新发现④**：`layer_analyst` 输出触顶 max_tokens（12000，含推理 4000），尾部的冲击与使用率建议被截断成默认值，导致"承压却无下调建议"，待定修法后与行业临时复审一起重跑。已发请求约 35 次（含 4 次连接失败的重试）。
    - 续：发现④已修——`config/settings.yaml` 的 layer_analyst max_tokens 12000→24000（不在任何任务的配置血缘内，不影响存量投影复用）；`event_inputs.apply` 对“承压/兑现却无下调建议”写缺口。重跑 L5：风险命题承压（来源 9、权威 3），选举抛售列为中等负面冲击且注明不作为下调依据，建议下调 1 档、只依据风险命题；遥测仍报触顶 24000 但字段完整，列为上线观察项。行业临时复审：L5 超配→标配，正好一档，其他层不变。Chief 只读：三个事件簇归组正确；顺带修了下调原因被挂到“仅被提及”冲击所在簇的问题（只按“依据 …”匹配）。四项验收标准全部满足。请求合计约 72 次（原估 30，追加后约 47）；超支在行业复审（8 层证据判读 22 次 + 结构分析 8 次 + 轮动 1 次），预估时误按缓存命中计算。上线后需观察：NVDA、MRVL 簇被模型逐日判“升级”导致每天各触发一次评审；“中期选举数据中心审查”连续 4 天被评为跨层重大，进每周校准报告。
- [ ] 9.3 上线（经用户批准，依次）：① 先重启 dispatcher，让常驻进程换上新代码（开关全关时新代码与现状一致）；② 把 `pending/l5_export_market_access_risk.yaml` 贴回 `config/sectors/ai_hardware.yaml` 的 L5；③ 在 `workflow_owners.yaml` 登记 `fundamental-material`，并在 `var/phase_f_dispatch.sqlite` 的 `schedule_scope_owners` 新增 `fundamental-material,*,dispatcher`（生产写入）；④ 把 `event_impact.yaml` 纳入 information-brief 与 layer-review 的血缘，打开 `components`、`layer`、`routing` 开关，启用轻量简报定时，必要时再重启一次。顺序不能反：旧进程实时读 YAML，却不认识新 schema 与新 owner（11-16 d7e97e6 的教训）。之后观察第一次真实触发与当日 Chief 周期。验证：触发台账、下游投影、Chief 渲染各核对一次，记入观察记录。
  - 2026-11-16 用户选定"分两步上线"。发现：打开层开关并贴回风险命题后，8 层评审的配置血缘会变，当天主理人运行（台北 22:45）会因血缘不符被阻断（同 11-12 事故），故改为：
    - **第一步（已完成，11-16 15:58）**：重启常驻调度进程换上新代码，开关全关。为此先改了两处：事件配置只在对应开关打开后才纳入血缘（185a285，核对开关全关时血缘不变、ai_hardware.yaml 仍为 438828…）；`ats dispatch resume` 能为初始化之后才加入的研究工作流登记负责人（ddc21cb）。16:05 的消费节拍正常完成，无报错。
    - **第二步（待用户在周五当晚主理人运行之后触发）**：`var/manual_fixes/event-impact-golive/golive.py --approval <批准原文> --apply`——备份三份配置与生产负责人库 → 贴回 L5 风险命题 → 登记 workflow_owners → 生产负责人表 fundamental-material 切到 dispatcher（走 owner_switch，带批准原文与健康检查）→ 打开三个开关 → 重启常驻进程 → 核对。预演已通过（未写任何东西）。周六台北 09:30 的例行层评审按新血缘重算，不额外花费。

