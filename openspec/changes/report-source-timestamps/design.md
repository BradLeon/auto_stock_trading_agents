# Design

## Context

动机与已定决策见 proposal.md。与实现相关的现状：

- **首席**
  - 决策引用的是研究快照里的投影 ID。快照条目（`decision_cycles.research_snapshot.items`）带有：
    - `projection_id`、`agent_role`、`task_id`；
    - `scope_kind/scope_id`、`as_of`。
  - 投影本身的数据版本引用在 `task_projection_envelopes.data_vintage_refs`，是异构字符串，例如：
    - `consensus:AAPL@2026-11-18T07:02:18+00:00`
    - `market-session:AAPL:2026-11-18:before-close`
    - `financials:AAPL:1554@7e33…`
    - `evidence:L8_equipment:1605@18b1…`
- **层分析**
  - 判读（`ClusterJudgement`）和逐家读数（`EntityReading`）带观测 ID。
  - 观测可经 `UnstructuredReadRouter.observations_by_id / documents_by_id` 批量解析到文档。
  - `sector/viz.py` 已用同一方式一次性批量取数。
- **基本面**
  - 档案各段（`ExpectationSet`、`MarketSetup`、`Actuals`）各有 `as_of`。
  - 预期表的「依据/来源」是模型写的自由文本。

## Goals / Non-Goals

**Goals**

- 三类报告的引用都能看到信息时间，且时间由代码解析。
- 首席在下判断时能看到每条投影的截至时间。

**Non-Goals**

- 不改观测/文档表结构，不回填，不重做历史报告。
- 不让首席引用审计拦截出单（D2）。
- 不给模型写的自由文本补时间。不能可靠对应到数据行的文字，就不硬标日期。
- 不改 `agents/base.py`，避免多数角色的提示缓存失效。

## Decisions

### 1. 新增共用模块 `src/ats/agents/provenance.py`

报告渲染器都只调它，口径集中在一处。它提供：

- `info_time(observation, document) -> InfoTime(kind, at, period)`：按以下顺序取第一个可用值（proposal 第 5 项）：
  1. 文档 `published_at`，`kind` 为"发布"；
  2. 文档 `fetched_at`，`kind` 为"采集"，并标注"发布时间未登记"；
  3. 文档 ID 中的 `YYYYMMDD`，`kind` 为"文档日期"。旧版财报观测的 ID 形如 `AMD:20260804`。
  - 能解析成日期的期间（`YYYY-MM` 或 `YYYY-MM-DD`）作为"数据期"附注；三者都没有时，单独显示"数据期"。
  - 季度标签、文章编号、预测区间这类期间不显示。
  - **从不使用 `observed_at`**。
  - 实测（2026-11-19）：旧版本地纪要文件（如 `AMD-Q2-FY2026-transcript.md`）没有任何日期，如实显示"信息时间未知"。
- `observation_sources(ids, reader) -> dict[obs_id, SourceStamp]`：一次批量查观测和文档，返回来源标签与 `InfoTime`。
- `age_text(at, ref) -> str`：输出"1.3 小时前""4.5 天前"这类文字。
- `describe_vintage_ref(ref) -> str`：把数据版本引用翻成人话；能解析出时间的带时间，解析不出的如实写条数或"未带时间"。
  - 示例：`consensus:AAPL@…` 写成"一致预期 AAPL · 采集 11-18 07:02"。
  - 示例：`evidence:L8_equipment:1605@…` 写成"产业链观测 L8_equipment · 1605 条"。
- `cite_projection(item, decided_at) -> str`：输出"层分析·L5_chip_design · 截至 11-14 01:30 · 4.5 天前"。
  - 超过该任务新鲜度窗口时加 ⚠️，窗口沿用 `workflow/planning.py` 的 `FRESHNESS_SECONDS`。

**备选方案：**在各渲染器内各写一套。没选，原因是三处口径会漂移，而且"信息时间取值顺序"本身就是需要统一的规则。

### 2. 首席报告：由 graph 准备引用索引，渲染器保持纯函数

- `ChiefResult` 新增字段 `citations: dict[projection_id, dict]`，内容为：
  - 角色、作用域、`task_id`、`as_of`；
  - `data_vintage_refs`（解析后的文字列表）。
- `graph/chief._write_chief_report` 负责准备这份索引：用本轮快照条目，加一次投影表查询取 `data_vintage_refs`。
- `report.render` 只做格式化：
  - 「决策」表的引用列改用 `cite_projection`；
  - 「证据权衡」表恢复引用列；
  - 「逐个持仓复查」表新增引用列；
  - 文末新增「依据清单」：按首次被引顺序列出每个被引投影，下挂其数据版本说明。
- 不在索引里的引用显示为 `❓ 未知引用 <前 8 位>`，并在「依据清单」顶部汇总条数（D2）。

**备选方案：**渲染器自己查库。没选，因为渲染器目前是纯函数，测试只需构造 `ChiefResult`；查库放进 graph，和已有的 `reference_audit` 放在一起。

### 3. 首席上下文：补 `as_of` 与距今时长，去掉截断 ID

- `projection_context_block` 的逐条投影行，在 `status` 之后插入 `as_of=<时间>（距本轮 X）`。
- 类别标题行改为 `最新 @ <as_of>（X 前）`，不再出现截断 ID。
- "距本轮"的参照时间取上下文拼装时刻，与现有风控和净值块的"距今"口径一致。
- 影响：首席的提示缓存会失效一次。没有其他角色读这个函数。

### 4. 层分析报告：判读行和逐家读数行加「来源 · 信息时间」列

- `render_layer` 新增可选参数 `source_stamps`。
- `sector/review.py` 在写报告前，对本层全部观测 ID 批量调用 `observation_sources`。做法同 `viz.py`，8 层共用一批。
- 一行引用多份文档时，显示最新那份，后缀"等 N 份"。
- 解析不到时显示"—（未找到来源）"，不留白。
- 没传 `source_stamps` 时（旧调用方、测试），该列显示"—"，保持向后兼容。

### 5. 基本面报告：按段标信息时间

- 已核实：档案各段的 `as_of` 都是运行时间，不是数据时间。不能把它们当信息时间标。
- 共识：新增 `ExpectationSet.consensus_as_of`，取自共识快照的 `_data_as_of`，标为"共识快照采集于 …（距本次运行 …）"。
- 行情：标"取于本次运行"，这是实时拉取的，运行时间就是数据时间。
- 预期表：「依据/来源」保持原样；表下注明"建于本次运行"，并说明该列是分析师自述。
- 实际值、财务快照：不改。前者已有"纪要来源"，后者原本就带 `as_of` 和逐期期末。

## Risks / Trade-offs

- **[`data_vintage_refs` 格式会新增类型]**
  - 应对：`describe_vintage_ref` 对未知格式原样输出前缀和作用域，并注明"未登记格式"，不抛错。单测覆盖当前库里出现过的全部前缀。
- **[约 1/3 的观测联不到文档（如 L1 观测器序列）]**
  - 应对：退到观测自带的 `period`；再缺就显示"信息时间未知"。不拿抽取时间冒充。
- **[层报告多一次批量查询]**
  - 应对：与 viz 同批量方式，8 层一次。查询失败只让该列显示"—"，不阻断报告写出。
- **[首席上下文变长]**
  - 应对：每条投影只多约 40 个字符；按 192 条投影计，增加约 8k 字符。可接受。

## Migration Plan

- 纯渲染与上下文改动，无迁移。
- 上线后观察下一次首席决策和周六层分析的产出。
- 回退：还原提交即可，不涉及数据。
