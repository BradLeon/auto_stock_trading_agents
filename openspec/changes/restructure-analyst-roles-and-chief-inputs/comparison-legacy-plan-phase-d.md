# Phase D 方案对照：restructure-analyst-roles-and-chief-inputs vs refactor-workflow-dataflow-architecture

生成时间：2026-09-23
比对对象：

- **我方**：`openspec/changes/restructure-analyst-roles-and-chief-inputs`（Phase D 专项，66 任务 / 39 需求 / 96 场景 / 退役 12 条）
- **旧规划**：`openspec/changes/refactor-workflow-dataflow-architecture`（Codex 输出的 A–F 单体规划，Phase D = tasks §6 的 6.1–6.9 + §7 的 7.1–7.12，共 21 项任务；对应 spec 为 `sector/layer-analyst`、`sector/sector-allocation`、`analysis/information-analyst`、`analysis/fundamental-pead`，合计 17 条新增需求 / 19 场景，另有 6 条 MODIFIED、4 条 REMOVED）

结论摘要：**覆盖了旧规划 Phase D 的全部 21 项任务，其中 13 项完全覆盖且更细，8 项为部分覆盖（5 处真空白，见 §3）**；另有 4 处阶段/粒度差异（§4），均不影响主干。建议补齐 §3 的 G1–G4。

---

## 1. 逐条映射

| 旧任务 | 旧规划要求 | 我方落点 | 判定 |
|---|---|---|---|
| 6.1 | `LayerAnalysis` / `SectorAllocation` schema；Layer 禁止配置等级 / budget use / stance / target weight | `agent/layer-analyst`「层级分析师不拥有配置权」+ `agent/sector-allocation`「以 SectorAllocation 投影发布」+ 任务 2.1 / 3.2 | ✅ 更细（补 `layer_status` 四值枚举 + 退役墓碑） |
| 6.2 | 从 Sector 编排中**拆出 Layer workflow**，复用 hierarchy / claims / cross-section / 证据链 | 任务 2.2 / 2.8 在 `agents/sector/layer_review.py` 原地改造并发布投影 | ⚠ 部分：职责拆了，包/workflow 没拆（见 C4） |
| 6.3 | Layer 输出景气/结构、逐标的截面差异、证据强度、冲突、缺口、可证伪条件 | 「层级状态判断必须锚定议题证据并区分证据缺失与无命题」+「议题结论必须附证据链」+「截面明细须含相对命题读数」 | ✅ 更细（证据缺失 vs 无命题分列） |
| 6.4 | 每层一份报告，移除配置/权重/stance，保留截面读数与临时查询不落文件 | 「每层一份报告，结论先行」+ 任务 2.8 | ✅（旧「跨层报告收窄为轮动与索引」场景迁到 sector-allocation「跨层轮动消费层级结论而不推翻它」） |
| 6.5 | 历史 Layer 配置读路径 legacy adapter + 退役登记 + 禁止注入新 Sector | 任务 2.1（保留列、新路径不写）+ 2.9（登记待退）+ 3.2（只消费投影） | ✅ 等价实现（未建独立 adapter） |
| 6.6 | Sector 只接受共享行业事实 + 有效 LayerAnalysis，移除 Macro / PEAD / Information / 研究观点 | 「行业分析师只消费层级投影与共享事实」+ 任务 3.3 / 3.4 | ✅ |
| 6.7 | 三级配置 + **证据冲突展示** + 研究上限 + 非执行输出 | 「独占三级配置权」+「配置结论绑定预算且护栏只降不升」+「以投影发布」 | ⚠ 缺证据冲突展示（G2） |
| 6.8 | Layer 失败 / 缺失 / 过期 / **schema 不兼容** → 显式 incomplete，不伪造配置 | 「层级分析缺失时降级并留痕」（缺失 / 过期） | ⚠ 缺 schema 不兼容判定（G3） |
| 6.9 | Layer/Sector 契约、报告、依赖隔离、历史兼容测试 | 任务 2.x / 3.8 / 9.1 | ✅ |
| 7.1 | Information package + schema + **独立手动/定时/文档事件入口** | 任务 4.1 建包 + 1.1 schema 扩展 | ⚠ 缺独立入口（G1） |
| 7.2 | 复用 research / triage / monitor / digest 纯逻辑，移除直连 Provider 与改 dossier | 「复用既有抽取、triage 与 digest 能力并移除越界副作用」+ 任务 4.2–4.5 + 8.x | ✅ 更细（逐模块给迁移任务） |
| 7.3 | 文档版本/chunk 血缘、事件/发布/抽取时间、**聚类**、置信度、仅自述、待验证 | 「InformationBrief 必须携带六类要素」+ 任务 1.1 / 4.6 | ⚠ 缺聚类与三类时间区分（G5，次要） |
| 7.4 | schema guard 拒绝 action/仓位/订单字段 + **可独立终结**（不跑 Fundamental/Chief） | 「信息简报不得包含投资建议」+ 任务 4.7 | ⚠ 拦截✅，独立终结未断言（G5） |
| 7.5 | routine mode | 「例行模式只更新预期基线」+ 任务 5.2 | ✅ 更细（确认/否定/新增/待验证四类留痕） |
| 7.6 | event cutoff 冻结基线 | 「事件模式在 cutoff 冻结基线并只使用期间正确的材料」+ 任务 5.3 | ✅ 更细（期间不符材料排除并留痕） |
| 7.7 | actuals vs baseline/Consensus/implied + Scorecard + 指引 + 叙事 + 非执行建议 | 「三类差异分列并产出 Scorecard」+「保留非可执行投资观点，剥离数量与风控」+ 任务 5.4 / 5.7 | ✅ 更细（三者方向不一致时保留分歧，不取平均） |
| 7.8 | 迟到电话会版本升级，同冻结基线，不覆盖初版 | 任务 5.5 + 场景「电话会迟到产生新版本」「两种模式不互相覆盖」 | ✅ |
| 7.9 | 删除 Sector/Macro 观点 + **其他 Fundamental 结论** + pre-trade gate；上下游改中性 Data Product | 「基本面不读取行业与宏观观点」+「剥离数量与风控」+ 第 8 组 Provider 收敛 | ⚠ 缺 peer read-through 显式禁止（G4） |
| 7.10 | Chief 读六类 + Internal State + 固定研究快照 | `decision/research-snapshot`（5 需求 / 12 场景）+ 任务 7.1–7.8 | ✅ 大幅更细（旧仅 projection-store 中 1 需求 1 场景） |
| 7.11 | Phase D 守卫 + 角色输入契约测试 + **相同 vintage 新旧输出差异报告** | `workflow/architecture-guards` +2 + 任务 8.1–8.8；任务 7.4 双读差异计数 | ⚠ 完整影子差异报告按 Non-Goals 留 F（有意） |
| 7.12 | Phase D 门禁 | 任务 9.6 | ✅ |

---

## 2. 我方超出旧规划的部分

1. **守卫例外清零**：旧规划只有 2.8 一条静态守卫需求，没有「27 条 Phase D 例外」概念（那是 Phase A 实施时形成的欠账）。我方第 8 组 8 项任务完成采集侧迁出 `agents/`、5 处取数改经数据产品、例外清零，并新增「例外必须声明收敛阶段」机器校验。
2. **必齐判定改按角色**：旧规划在 Phase E（8.7）才做完整性门禁，且未规定判定单位；我方在 D 就把 `required_for_decision` 改为按角色，基本面以「例行或事件二者之一」满足，`technical_review` 置为必需。
3. **投影发布与幂等**：Layer / Sector / Information / Fundamental 各自给出发布、幂等键与「不覆盖旧版本」的可验证断言（任务 2.5/2.6/3.7/4.8/5.9）。
4. **退役登记**：`legacy_retirement.yaml` 登记 4 项 Phase D 待退（层级 allocation、PEAD 内部风控与 sizing、Chief 旧表直读、旧 sector_reviews/pead_dossier 读模型），旧规划仅 6.5 提到一层。
5. **端到端正反验收**：任务 9.4 / 9.5 构造六类齐备与缺一类两个场景，断言订单可追溯到快照条目、缺口时阻断下单。
6. **开关一并拆除**：任务 5.8 明确「移除 `inject_prep` 开关而非保留为可开启选项」，避免欠账复活。

---

## 3. 真空白（建议补）

| 编号 | 缺口 | 建议补法 | 工作量 |
|---|---|---|---|
| G1 | Information 无独立入口（手动 / 定时 / 文档事件触发），旧 7.1/7.4 明确要求 | 在 `agent/information-analyst` 加 1 需求 / 2 场景（独立终结、文档准入事件触发），任务加 1 项 CLI/入口 | 小 |
| G2 | Sector 未要求「证据冲突展示」（层级景气与标的竞争位置冲突须分别呈现，不得被不透明总分消除） | `agent/sector-allocation` 加 1 需求 / 1 场景，任务加 1 项报告/输出断言 | 小 |
| G3 | Layer 依赖只判缺失/过期，缺 schema 版本不兼容 | 在 `agent/sector-allocation`「层级分析缺失时降级并留痕」补 1 场景（schema 版本不兼容按缺失处理并留痕） | 小 |
| G4 | Fundamental 未显式禁止消费**其他 Fundamental 结论**（peer read-through 须中性化为共享事实） | `agent/fundamental-pead`「基本面不读取行业与宏观观点」扩为「不读取行业/宏观观点与其他基本面结论」并补 1 场景 | 小 |
| G5 | （次要）InformationBrief 未要求聚类与事件/发布/抽取三类时间区分；Information 独立终结无可断言场景 | 视需要并入 G1 | 极小 |

---

## 4. 差异与冲突（中立看法）

**C1 — Risk 移除 Macro 观点的阶段归属**
旧规划放在 Phase B（任务 3.8，与「Risk Agent 只解释确定性结果」捆绑）；目标文档 §14.4 明确列在阶段 D；我方在 D。
看法：只差记账位置。旧规划把两件事捆在一条任务里，而前者确属 B（已落地）。按文档归 D 更准确，无实质冲突。

**C2 — 研究快照的阶段与能力归属**
旧规划放在 Phase B 的 `workflow/projection-store`（1 需求 / 1 场景）；我方在 Phase D 新建 `decision/research-snapshot`（5 需求 / 12 场景）。
看法：能力归属上，快照是决策周期的一部分，归 `decision/` 比塞进投影存储更贴切。阶段上，旧的「B 就拒绝创建 cycle」在实际实施中只建了 builder 没接线（Phase B 遗留），我方把接线明确写进 D 的 8 项任务，避免了「建好不接」的状态被当成已完成。

**C3 — 事件模式的「投资建议」边界**
旧规划写「不可执行投资建议」（决策 10 同）；我方按用户裁决写「保留 direction / magnitude / 信心 / 理由 / 可证伪条件，剥离数量推导与风控调用」。
看法：方向一致，粒度不同。旧措辞容易被解释成「连方向都不给」，我方措辞可机器校验（断言输出无股数/金额/权重字段、不触碰风控模块）。我方更明确，但要注意：保留方向意味着 Chief 侧必须自己完成从「预期差」到「行动」的转换，Phase D 的 Chief 装配若要消费 `direction`，需在 `decision/research-snapshot` 之外再确认消费方式。

**C4 — Layer 是否拆成独立 workflow / 包**
旧 6.2 要求拆出 Layer workflow；我方在 `agents/sector/layer_review.py` 原地改造。
看法：守卫的 `ROLE_BY_PATH_PREFIX` 已把 `src/ats/agents/sector/layer_review` 单独映射为 `layer_analyst`，职责隔离可被机器校验，不拆包的隔离代价很小。但代价是「Layer 是独立角色」在目录结构上不显性，后续新增 Layer 模块容易误放进 `agents/sector/` 而被判成 `sector_analyst`。若你希望角色边界在目录上自解释，可在实施时顺手落 `agents/layer/`（约 1 项任务，非阻塞）。

**C5 — Provider 收敛是否属于 Phase D**
旧规划只有一条静态守卫需求，27 条例外是 Phase A 实施产生的欠账，不在旧规划范围。按你的裁决纳入本阶段。
看法：代价是任务量增加约 12%（8 项），收益是守卫例外不再跨阶段挂起；且新 Information Analyst 的输入本身就是「已准入文档」，与「不直连 Provider」强耦合，一起做反而少一次返工。

---

## 5. 判定

- 覆盖性：**完全覆盖**旧规划 Phase D 的 21 项任务，无遗漏项。
- 细化度：需求 17 → 39，场景 19 → 96，且每条都落到具体文件行号与可断言测试。
- 需补：G1–G4（共约 4 需求 / 5 场景 / 4 任务），补完后我方对旧规划呈严格超集。

> **状态更新（2026-09-23 裁决后）**：G1–G5 已全部补进 change，见 §7；下方 §3 表格保留作为「缺口是如何被发现的」记录。

---

## 6. 补充分析（用户追问 G4 / G5 / C3 / C4）

### G4 — 跨标的信号链是否属于「其他基本面结论」

**文档事实**：§7.2（:561）规定 Fundamental 输入为「公司研究包、**产业链事实**、`InformationBrief`」，禁止项只列「Sector 或 Macro 观点」与「最终风控」；数据流图（:307）明确 `HIER_DATA -->|产业链上下游信号| FUND`。旧规划 design 决策 10 一边要求删「其他 Fundamental 结论」，一边写「Peer read-through 必须表示为中性共享事实或 Data Product」——**两者并不矛盾：允许跨标的信号，禁止它披着"另一份基本面结论"的外衣进入**。

**代码事实**：`graph/pead.py:180-206` `_peer_report()` 读信号链上另一个标的的 **scored dossier**，取出三样东西喂给目标标的的 LLM（`prep.py:159-168`，注释明写 "peers are read-throughs"）：

| 字段 | 性质 | 判定 |
|---|---|---|
| `actuals.guidance` | 上游公司已披露的指引/产能（事实） | 可保留 |
| `scorecard.band` | LLM 逐维打分 → 代码按阈值分档（`score.py:147,176`） | 半确定性，**属结论派生** |
| `decision_summary` | LLM 结论摘要 | 纯观点，须移除 |

**三方案**

| 方案 | 做法 | 代价 |
|---|---|---|
| ① 一刀切禁止（旧规划字面） | 移除 `_peer_report` 全部跨标的读取 | 信号链退化为 20 日价格动量（`price_chg_pct` 即 fallback），丢失 TSM CoWoS→NVDA 这类最强领先信号 |
| ② **按载体形态设禁（推荐）** | 保留跨标的信号，但只允许中性事实（已报实际值、官方指引区间、产能、财报日期）；`_peer_report` 改从产业链数据产品读取，丢弃 `decision_summary` 与 `band`；spec 明写「禁止消费其他标的的 Fundamental 投影，允许消费产业链数据包中性事实与其他标的的 InformationBrief」 | 约 20 行改动 + 1 需求改写 + 1 场景 |
| ③ 显式登记第 3 条跨角色读取（fundamental→fundamental 跨标的） | 守卫许可表新增同角色跨标的读取 | 需处理 A↔B 循环依赖与结论传染（一个标的的评分错误沿链放大）；若想保留 `band` 这类派生值，这是唯一干净做法 |

### G5 — 简报场景与「事件 / 发布 / 抽取」三类时间

- **简报场景**：一批已准入文档 → 一条 `InformationBrief`。典型：上游公司盘后发布财报稿 + 8-K + 电话会纪要 + 3 篇媒体 + 1 份券商快评，简报要回答「发生了什么事实变化、可能影响谁、置信度、时效、还待核验什么」。
- **三类时间**：① 事件时间（事情真发生的时刻，如财报期/电话会召开）② 发布时间（文档对外可见，如 8-K 16:05 挂网）③ 抽取时间（系统入库并抽取，如次日 03:00 定时任务）。
- **现状**：载体只有 `published_at`（`data/news.py:84,144`、`data/admission.py:32`、`source_acceptance.py:206-208` 另有 `published_at_exact` / timezone），**无事件时间与抽取时间的显式字段**；聚类方面仓库**没有文档级聚类**（只有知识库 `cluster_key` 与风控相关簇）。

**缺了的影响**（按严重度）

1. **cutoff 判定失真（最要紧）**：事件模式要在 cutoff 冻结基线，判断"哪些材料属于 cutoff 前"必须靠事件/发布时间。只记入库时间，凌晨入库的盘后材料会被误判成"次日新材料"，或反过来把 cutoff 后才到的旧材料当成可用 → 我方「只使用报告期间正确且通过准入的材料」这条无法稳定实现。
2. **迟到材料识别不了**：电话会迟到 3 天时，事件时间=财报日、发布时间=3 天后——没有这两个字段就无法自动识别"这是迟到材料，要出新版评审"（对应旧 7.8 / 我方任务 5.5）。
3. **新鲜度与幂等失真**：`freshness` 按入库时间算会把"早发布、晚入库"判成新信息，触发无谓的例行重跑；幂等键（文档版本 + 抽取逻辑版本）也会因重复入库失效。
4. **同源重复放大（聚类缺失）**：同一事件被 5 家报道，会被记为 5 条事实变化，置信度被虚假放大（5 篇同源 ≠ 5 个独立信源），简报条数膨胀。

**建议**：三个时间字段本阶段补齐（改动小，且是事件模式的前置）；聚类降为「简报记录来源文档列表 + 同源标记」，真聚类留 Phase E 并登记为 Open Question。

### C3 — 事件模式「投资建议」的两方案差异

关键区分：`FundamentalEventReviewPayload.direction` 是 `Literal[-1,0,1]`（**预期差方向**：利好/中性/利空），而 `score.py:189-231 decide()` 产出的是 `action="buy"/"trim"` + `notional_hint`/`qty_hint`（**动作 + 数量**，由 `portfolio` / `net_liquidation` 推导）。

| | 方案 A（我方，用户裁决） | 方案 B（完全剥离） |
|---|---|---|
| 保留 | `direction(-1/0/1)`、`magnitude`、信心、理由、可证伪条件 | 只保留预期差与 Surprise Scorecard、指引、叙事更新 |
| 删除 | `action` / `notional_hint` / `qty_hint`（`score.py:218,229-231`）与 `graph/pead.py:473-519` 的 risk assess / review_guardrails / pre_trade | 同上，且再删 direction/magnitude |
| Chief 负担 | 直接拿到"这个预期差是好是坏、多大" | 需自行从 Scorecard 推断方向 |
| 风险 | direction 若被 Chief 直接映射为 action，会形成"事实上的第二个方向出口"（风控出口已剥离） | Chief 在缺少基本面判断的情况下解释财报，同一 Scorecard 在不同轮次可能被解释成不同方向，可复现性下降 |
| 可校验性 | 高：断言输出无股数/金额/权重字段、不触碰风控模块 | 高，但 Chief 侧解释过程无法断言 |

**建议**：维持 A，并补一条约束——payload 不得携带 action 词表取值与任何数量字段，Chief 不得把 `direction` 直接映射为 action（1 个场景即可）。

### C4 — Layer 是否拆包的差异

现状：`src/ats/agents/sector/` 共 15 个文件，Layer 只有一个 `layer_review.py`（11.5 KB）；守卫按文件名前缀把它单独映射为 `layer_analyst`（`architecture_guards.py:33`），引用方为 `sector/review.py:74,126`、`runtime/cli.py:784,812`，测试侧 5 个文件约 47 处引用（`test_layer_review.py` 38 处）。

| | 方案 A（不拆，我方） | 方案 B（拆到 `src/ats/agents/layer/`） |
|---|---|---|
| 改动面 | 只改 `layer_review.py` 输出语义 + 发布投影 | 移动 1 文件 + 5 处 src import + 5 个测试文件 import + 守卫前缀改写 |
| 隔离可校验性 | 够用（守卫按路径前缀映射，跨角色读取仍可校验） | 同样可校验，且可用目录级映射替代文件名前缀 |
| 隐性成本 | 目录语义不自解释：新增 Layer 模块若放进 `sector/` 会被判成 `sector_analyst`，反而获得"读 layer 投影"的许可（自读）；守卫依赖"文件名前缀"这一脆弱约定，每加一个 `layer_*.py` 都要手工登记 | 与正在进行的改动叠加，import 冲突概率上升 |
| 建议 | 本阶段采用；代价可控 | 非阻塞，约 1 项任务，可顺手做 |

---

## 7. 决议（用户裁决，已落进 change）

| 议题 | 决议 | 落点 |
|---|---|---|
| G1 | 补：独立入口（手动 / 定时 / 文档准入事件）+ 独立终结 | `agent/information-analyst` +1 需求 / 2 场景；任务 4.9 |
| G2 | 补：证据冲突必须分列，不得被总分压平 | `agent/sector-allocation` +1 需求 / 2 场景；任务 3.9 |
| G3 | 补：schema 不兼容按缺失处理，禁止字段兜底 | `agent/sector-allocation` +1 场景；任务 3.10 |
| G4 | 取②：按**载体形态**设禁——跨标的信号允许，但只准中性事实（数据产品 / 该标的 InformationBrief），禁其他标的的 Fundamental 投影 | `agent/fundamental-pead` 改写 1 需求 + 2 场景；任务 5.10 / 5.11 |
| G5 | 三类时间与同源聚类**都要** | `agent/information-analyst` +1 需求 / 3 场景；payload 6 字段（任务 1.7）；任务 4.10 / 4.11 |
| C3 | 维持 A：保留 direction / 幅度 / 信心 / 理由 / 可证伪条件；新增约束——payload 不得含 action 词表与数量字段，主理人不得把 `direction` 直接映射为动作 | `agent/fundamental-pead` +2 场景；任务 1.8 / 7.9 |
| C4 | 拆包：Layer 迁到 `src/ats/agents/layer/`，守卫前缀同步 | 任务 2.10 |

**更新后规模**：42 需求 / 108 场景（另退役 12 条）/ **77 任务**；`openspec validate --changes --strict` 通过。
