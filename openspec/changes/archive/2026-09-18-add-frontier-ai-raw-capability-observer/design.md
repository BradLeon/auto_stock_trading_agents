## Context

现有 L1 已有生产化与商业化两个独立 Evidence Observer，并已具备结构化 source/adapter、artifact、observation vintage、DataProduct、报告/图表、manifest replay 和按层编排能力。参见 `proposal.md` 的动机与范围。

本变更的特殊约束不是单次取数，而是两个异步时钟：Labs 不定期发布模型，第三方机构又在之后不定期补齐或修订 benchmark 成绩。同时 benchmark 本身会更换任务集、harness、grader 和权重。系统需要避免三种常见错误：把新模型未评测写成零分、把网页修订覆盖掉历史、把不同 benchmark 版本接成虚假的能力进步曲线。

真实源核验表明，Artificial Analysis API 在当前免费账户下不能作为必需依赖，但公开评测页面包含可复核的服务端结构化载荷，可支持一次性真实数据核验。生产方案优先使用 benchmark 维护方公开 Git/CSV/JSON/排行榜；对 AutomationBench-AA、SciCode、CritPt、MMMU-Pro 与 Humanity's Last Exam，可配置低频公开结构化页面适配器，但必须先通过条款、robots、请求预算和访问许可门禁，任一门禁失败即 fail closed，并退回官方公开替代载体或可审计人工导入。Labs 官方 release/model card 与竞对报告承担事件补充角色。Browser/Computer Use 仅用于发现和人工核验，不作为生产视觉/OCR 爬虫。

## Goals / Non-Goals

**Goals:**

- 在既有结构化平台中建立可历史重放的 model、benchmark method、score vintage、coverage state 和 comparability group，并显式区分 `model_capability_proxy` 与 `model_agent_stack_capability`。
- 以固定十一项 benchmark 和九家 Labs 形成稳定的 current flagship 能力矩阵，同时允许模型身份、旗舰有效期和成绩动态更新。
- 对 AutomationBench、OSWorld 等多口径评测同时维护统一矩阵与事件账本，不牺牲真实证据，也不制造虚假横向可比性。
- 通过异步发现、热观察和周度审计，使新模型与后续补齐成绩在有界时间内进入系统。
- 以确定性规则计算 A（global frontier 外扩）和 B（多数任务门槛跨越），区分 confirmed、provisional、not-crossed、not-applicable 与 unavailable。
- 让表格、图表、机器 packet 和 Agent context 使用同一 accepted observation set，并可离线重放。

**Non-Goals:**

- 不自行运行十一项 benchmark，也不购买算力复现实验；首版只治理公开维护者/第三方、竞对和官方结果。
- 不用综合 Intelligence Index、Arena Elo 或模型排名替代单项能力边界。
- 不把 self-reported 缺失自动判为低能力，也不对 NA 插值、前向填充或跨模型推断。
- 不在没有预定义证据时构造“人类水平”或“经济可用”门槛。
- 不把 raw capability 直接转成投资评分、Chain factor、仓位或交易信号。
- 首版不对图像、视频、语音生成 benchmark 建立观察；数据模型保留 modality 字段以便后续扩展。
- 首版不纳入 FrontierMath Erdős、FrontierScience Research、群体智慧或安全对齐：前两者分别因当前横截面区分度/覆盖共识不足，后两类需另行定义可客观、可持续更新的观测契约。

## Decisions

### 1. 建立五类版本化对象，而不是把成绩塞入通用宽表

新增逻辑对象：

1. `model_identity`：Lab、family、version、release/availability、modality、tier、用途和别名；
2. `flagship_period`：每家 Lab 的 active flagship effective interval 与选择理由；
3. `benchmark_method`：benchmark/version、任务集、harness、grader、metric semantic、range、B eligibility；
4. `benchmark_score_observation`：模型配置在某 method/source 下的点估计、统计信息和时间/vintage；
5. `benchmark_coverage_state`：预期单元无数值时的原因和生命周期。

这些对象仍通过现有 artifact/observation/relation 基础设施持久化；新增 reference entities、relations 和 metadata schema，而不是创建第二套独立数据库。

选择理由：score 的身份由模型、方法、配置、来源和时间共同决定，单纯的 `model × benchmark → score` 宽表无法表达修订、缺失原因和不可比性。

替代方案：只保存当前网页矩阵。拒绝，因为无法 as-of 重放，也无法识别网页静默修订。

### 2. 固定研究面板，动态维护模型版本

九家 Labs 与十一项 benchmark 作为版本化配置注册表固定；每家 Lab 的模型实体和 flagship effective period 动态更新。旗舰选择器按照：公开可用 → 通用能力层级 → Lab 预定义产品 tier → availability time → 明确人工 override 的顺序决定 active flagship。配置需要显式定义可替换 lineage；“发布时间更晚”本身不足以替换旗舰，近似名称或同 family 型号也不得替代精确旗舰版本。

首版种子 cohort 使用已审阅名单，Alibaba/Qwen 替代 ByteDance。每次旗舰切换生成 cohort version，旧 cohort 不被改写。

替代方案：自动选择每家 Intelligence Index 最高模型。拒绝，因为会把专用、旧版或不同 effort 的模型混入，并把第三方综合指数变成研究对象。

### 3. 模型发现和成绩发现由同一调度框架中的两个任务族承担

新增任务族：

- `frontier_model_discovery_p1d`：批量读取 Labs 官方目录/release 与第三方模型目录；
- `frontier_benchmark_probe_p1d`：批量读取第三方成绩和当前热观察切片；
- `frontier_benchmark_full_audit_p7d`：完整快照和方法论审计；
- 动态热观察记录：D0–30 每日，D31–90 每三日，之后每周。

调度表只保存 next-check 策略；每次运行仍通过现有 ingestion run、artifact 和 quality pipeline。优先一次获取来源全量/批量响应并在本地 diff，避免逐模型逐 benchmark 发起请求。

替代方案：固定月报时重新抓取。拒绝，因为会有最长一个月滞后，且不能区分模型发布与成绩补齐。

### 4. 来源适配器按公开载体分流，结构化页面受合规门禁保护

适配器输出统一的 model candidates、benchmark methods、score observations、coverage hints 和 artifacts。来源优先级在 DataProduct selection 中执行，adapter 不删除低优先级候选。

| 来源组 | 首选载体 | 更新检测 | scope |
|---|---|---|---|
| LiveBench | 官方 Git 的版本化 CSV + category JSON | remote HEAD/blob SHA、文件 hash | model proxy |
| Terminal-Bench 4.0 | Harbor Hub 匿名 `leaderboard-read` JSON（主）；官方 Git `leaderboard/submissions/*.json`（审计回退） | Hub `updated_at`、row id/update time、payload hash；Git 提交文件 hash | agent stack |
| Terminal-Bench Science | 官方公开 `/api/leaderboard` JSON | ETag/Last-Modified、payload hash | agent stack |
| OSWorld 2.0 | 官方结果载体 + Labs release/model card 事件 | ETag/Last-Modified、文档与引用切片 hash | agent stack；多 comparability group |
| AutomationBench-AA | Artificial Analysis 公开结构化页面；Zapier/Labs 结果进事件账本 | 结构/schema 指纹、payload/文档 hash | agent stack；partial/composite A-only |
| SciCode、CritPt、MMMU-Pro、HLE | Artificial Analysis 公开结构化页面；Scale/官方 release 为补充 | 结构/schema 指纹、payload/文档 hash | model proxy |
| Toolathlon Verified | 官方公开 Verified leaderboard | ETag/Last-Modified、表格/内容 hash | agent stack |
| SpreadsheetBench 2 | 官方项目页/论文 + Labs model card 事件 | 页面/论文版本、引用切片 hash | agent stack；事件驱动 |

机器可读 JSON/CSV/Git 结果直接持久化原始 artifact；README/HTML/服务端结构化载荷保存 URL、解析范围、结构指纹和 hash；论文/release/model card 保存文档版本与最小引用切片；无法获得结果时创建 coverage state。官方 Lab adapter 解析公开正文证据，不使用搜索摘要替代正文。

Browser/Computer Use 可用于发现公开载荷、验证筛选条件和人工核验，但不得绕过登录、订阅、付费墙、robots 或服务条款，也不得把截图/OCR直接作为正式分数。公开结构化页面 adapter 必须低频批量获取、条件更新、设置请求预算并在结构漂移时 fail closed；许可门禁不通过时只能使用官方替代源或带完整 lineage 的人工导入。

来源选择在 DataProduct 层固定执行：`benchmark_maintainer/independent_third_party > competitor_reported > lab_self_reported`。适配器保留所有候选，不因优先级删除低级来源；默认矩阵选择最高优先级的同口径结果，事件账本展示其他真实结果及其差异。

矩阵的模型列采用 **release-level identity**，而不是 evaluation-configuration identity。同一精确 release 在同一 benchmark/comparability group、同一来源优先级下若公开了 `low`、`high`、`max`、`xhigh` 或不同 harness 配置，矩阵以其中最高观测分代表该 release 的 raw capability；原始 configuration、reasoning effort、harness、score 和来源候选全部保留在 observation/sidecar，不平均也不分别扩成模型列。该规则只允许合并已经由权威 details URL 或显式 alias registry 绑定到同一精确 release 的配置，不得用字符串模糊规则把 `GLM-5.3-Flash` 合并进 `GLM-5.3` 等不同产品变体。

### 5. Comparability group 是任何趋势和 frontier 计算的硬边界

`benchmark_method` 的 task set、harness、grader、metric semantic 或关键 inference protocol 变化时，method diff classifier 产生：

- `compatible_revision`：文案或非实质元数据变化，可保持 group；
- `breaking_revision`：新 comparability group；
- `review_required`：隔离新结果，等待明确决策。

默认不提供跨组归一。未来如同一批代表性模型在新旧方法上均有结果，可新增 `comparability_bridge` 派生定义；bridge 不改变原始系列。

选择理由：benchmark 换版造成的分数变化往往大于模型迭代，必须优先防止虚假前沿外扩。

### 6. A/B 判定由纯函数派生并保存规则版本

`raw_capability` DataProduct 先生成指定 `as_of` 的 accepted score set、current cohort、coverage matrix 和每项 benchmark 的 current/previous frontier，再交给 Observer 纯函数判定：

- A：同 comparability group 内，`LCB95(current - previous) > max(2pp, 0.2 × historical_sd)` 为 confirmed；只有点估计证据则为 provisional；
- B：method registry 标记 eligible 后，`LCB95(current) > 50%` 为 confirmed；点估计大于 50% 为 provisional；否则 not-crossed；
- partial/composite/Elo 默认不参与 B；OSWorld Partial 与 Strict 分为不同 metric identity；
- human/economic thresholds 使用独立、预先注册的 threshold definition，首版没有定义就不渲染。

每次派生保存 rule version、输入 observation IDs、accepted rows hash 和缺失统计。A 使用全球可见 frontier 历史，而报告矩阵只展示九家 current flagships；若 frontier 来自面板外但符合来源和方法规则，报告必须明确标注，以避免研究结论被面板边界截断。首版验收同时输出“面板 frontier”和“global frontier”差异检查；默认命题采用 global frontier。跨 scope 不得排序或合并：model proxy 与 agent stack 只能并列展示。

替代方案：只比较本期九个模型的最高分。拒绝，因为旧模型仍可能是历史/当前 frontier，且面板变化会制造假增量。

### 7. 统一矩阵与事件账本共享 observation 模型

对方法指纹稳定且覆盖足够的结果生成十一乘九统一矩阵。AutomationBench 与 OSWorld 的异构设置，以及 SpreadsheetBench 2 等通过 release/model card 零散发布的结果，同时写入事件账本。事件键至少包含 benchmark/version、task set、harness、tool setting、grader、metric semantic、model/config 与 source identity；只有完整指纹相同才可进入同一 comparability group。

选择理由：强行只保留统一横截面会丢失真实突破证据，直接混合事件又会制造伪排名；双轨结构允许两类信息并存。

### 8. NA 是 coverage state，不是格式化细节

coverage state 独立于 score observation，状态包括 `not_evaluated`、`pending_publication`、`not_self_reported`、`not_applicable`、`non_comparable`、`source_unavailable`、`withdrawn`。报告统一渲染 `NA`，sidecar 与 packet 保留具体状态和时间。

选择理由：第三方排队未评与厂商不披露具有不同研究含义；来源故障也不能覆盖最近有效数值。

### 9. Observer 作为 L1 第三个独立运行单元

在 `EvidenceObserverRef.evidence_sections`/layer runner 的既有扩展点注册 `raw_capability`，新增独立 evaluator、report renderer 和 visualization module。它读取 governed DataProduct，不直接调用来源。失败只返回自己的 `unavailable/stale/partial`，不修改 production/commercialization packet。

报告结构：

1. 自然语言 A/B 结论与关键证据；
2. 十一乘九 current flagship 矩阵；
3. AutomationBench、OSWorld、SpreadsheetBench 2 等事件证据账本；
4. A 判定表；
5. B 门槛表，只显示适用类别；
6. benchmark 方法卡；
7. frontier small multiples、coverage heatmap、B 当前分数与最高已定义门槛三类图；
8. 数据与方法注解、lineage/manifest。

### 10. 直接 platform 但使用发布质量门保护最近有效结果

实现完成后先在隔离数据库执行真实源验收和 frozen-fixture replay。所有 gate 通过后配置直接为 `platform`，不引入长期 shadow 状态。后续某次新 revision 质量失败时，只隔离该 revision 并继续提供最近有效 platform 结果，同时显示 stale/failed-probe 警告。

选择理由：用户要求验收后进入正式报告；安全性由 revision-level admission 与最近有效快照实现，而不是永久 shadow。

## Risks / Trade-offs

- [公开结果更新不同步] → 各来源按载体使用条件请求或 Git SHA 探测；未发布/未匹配保持 coverage state，不用旧版本补齐。
- [公开 JSON/Git 结构发生变化] → 保存原始 hash 与 schema version，解析失败隔离新 revision，保留最近有效 platform 快照。
- [公开结构化页面变化或访问政策不明确] → 保存结构指纹并在 drift 时 fail closed；自动化须通过条款/robots/请求预算门禁，否则退回官方载体或可审计人工导入，最近有效 platform 快照继续可用。
- [“最新旗舰”具有产品语义歧义] → 为每家 Lab 配置 tier/lineage 规则，自动发现只产生 candidate；无法唯一判定时进入 `review_required`，不自动替换。
- [第三方评测覆盖滞后导致大量 NA] → 报告覆盖率和 pending age；热观察提高发现速度，低优先级真实结果可进入带标签事件账本，但不以旧版、近似模型或自报告静默补齐第三方矩阵。
- [Benchmark 方法论变更被误判为兼容] → 对 task set、harness、grader 和 metric semantic 使用保守 breaking 默认；不确定即隔离审阅。
- [点估计跨过已定义门槛但统计不稳] → 必须标记 provisional，并明确区分点估计与置信下界；报告按“多数任务解锁→人类基准→经济可用”三级语义呈现，不用单一 50% 幅度替代层级判断。
- [公开 benchmark 污染或训练泄漏] → 方法卡记录是否公开题集、污染说明和来源警告；单项结果只作为 proxy，不生成跨项综合“智能分”。
- [定时探测给来源带来过多请求] → 使用批量获取、条件请求、content hash、本地 diff、退避和声明的请求预算。
- [面板只含九家 Labs，可能漏掉真正 global frontier] → global frontier 查询扫描所有已治理可比成绩；九家矩阵负责稳定展示，二者差异必须显式报告。

## Migration Plan

1. 增加 benchmark/model/coverage/comparability 配置与结构化 schema migration，不改变既有 observation 读取。
2. 使用真实来源快照完成 LiveBench、Terminal-Bench、Terminal-Bench Science、AutomationBench-AA、SciCode、CritPt、OSWorld、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2 与 Humanity's Last Exam 的 adapter、事件账本、方法论 diff 和身份归一测试；fixture 仅用于解析单元测试并强制标记为测试数据。
3. 在隔离数据库运行十一项 benchmark 的历史回填，建立当前旗舰 effective periods、method versions、coverage states、event observations 和 raw snapshots。
4. 对 2026-09-17 真实核验报告逐项对账：十一乘九矩阵、事件证据、A/B 适用性、Qwen 替代 ByteDance、精确模型版本、NA 原因、来源和分数；旧 synthetic 九项表不得作为 platform 验收输入。
5. 实现 DataProduct、Observer、图表、packet、manifest replay，并执行既有 production/commercialization 隔离回归。
6. 使用真实源运行每日 discovery/probe 与周度 audit，验证 no-change、score-added、score-revised、method-changed 和 source-unavailable 路径。
7. 全部门禁通过后将数据源和 Observer 注册为 `platform`，加入 `ai_hardware/L1_app` 正式报告。
8. 回滚时禁用 raw-capability Observer/source schedule 并恢复上一配置；保留 schema、artifacts、observations、coverage history 和发布记录，既有两个 L1 Observer 不受影响。

## Open Questions

- 首版不建立跨 benchmark-version bridge；未来是否采用重叠模型回归、IRT 或 rank-preserving bridge，需要独立研究与 proposal。
