## Purpose

定义 L1 原始能力边界 Observer，使系统围绕能力前沿外扩和任务门槛跨越形成可复算、可解释、可视化且与生产化及商业化证据相互隔离的正式结论。

## ADDED Requirements

### Requirement: Raw capability 是 L1 的独立证据命题
系统 SHALL 在 `ai_hardware/L1_app` 中新增独立 `raw_capability` Observer，固定回答：A. 前沿生成式 AI 的原始能力边界是否在可比 benchmark 上外扩；B. 对评分语义允许的 benchmark，是否首次观测到多数任务能力门槛被跨越。Observer SHALL 将 benchmark 视为能力边界的量化 proxy，而不是把综合榜单排名本身作为研究对象。

#### Scenario: 运行 L1 应用层报告
- **WHEN** 用户运行 L1 `ai_hardware/L1_app` Evidence 报告
- **THEN** 输出 SHALL 分别包含 raw capability、生产化与应用扩散、商业化能力的独立结论
- **AND** raw capability 的状态 SHALL NOT 改写另外两个 Observer 的 claim、version 或状态

### Requirement: 命题 A 只在相同可比组内判断 global frontier 外扩
对每项 benchmark，Observer SHALL 在指定历史 `as_of` 下选择当时已知且通过质量门的成绩，并在同一 comparability group 内比较 current global frontier 与 previous global frontier。正式“已外扩” SHALL 要求 `LCB95(current - previous) > delta_min`；默认 `delta_min` SHALL 为 2 个百分点与该系列历史波动标准差 0.2 倍中的较大值。缺少可复算置信区间但点估计超过阈值时只能返回 `provisional_expansion`；覆盖不足、版本断序或无历史基线时 SHALL 返回明确的非结论状态。

#### Scenario: 新模型在同一 harness 上显著领先
- **WHEN** 新旗舰成绩与历史 frontier 同属一个 comparability group，且差值 95% 置信区间下界超过 `delta_min`
- **THEN** A SHALL 返回 `confirmed_expansion`
- **AND** SHALL 展示新 frontier 模型、当前值、先前值、增量、统计依据和期间

#### Scenario: 只有汇总点估计
- **WHEN** 新成绩比旧 frontier 高 7pp，但来源没有任务级结果或可复算置信区间
- **THEN** A SHALL 返回 `provisional_expansion`
- **AND** SHALL 明确说明缺少置信区间，不得写成统计确认

#### Scenario: Benchmark 发生破坏性换版
- **WHEN** current 和 previous 成绩位于不同 comparability group 且没有有效 bridge
- **THEN** A SHALL 返回 `non_comparable_version_change` 或等价状态
- **AND** SHALL NOT 计算跨版本能力增量

### Requirement: 命题 B 按评分语义执行三级递进判断
Observer SHALL 只对预注册为 strict success、binary reward 或可解释 accuracy 的 benchmark 计算 B，并按要求递进判断：（1）多数任务解锁：默认要求 current frontier 的 95% 置信区间下界高于 50%；（2）人类基准跨越：仅在 benchmark 注册表预先定义阈值、口径和来源时判断；（3）经济可用门槛：仅在 benchmark 注册表预先定义阈值、口径和来源时判断。缺少置信区间而点估计超过已定义阈值时 SHALL 标记 `provisional_crossing`。报告 SHALL 展示当前已定义的最高层级、跨越模型、得分和置信依据，不得用“相对 50% 幅度”替代三级门槛语义；未预定义的人类/经济层级 SHALL 完全省略。

#### Scenario: 严格成功率置信下界超过 50%
- **WHEN** Terminal-Bench 4.0 当前 frontier 为约 57.9%，且可复算的 95% 置信区间下界高于 50%
- **THEN** B SHALL 返回 `confirmed_crossing`
- **AND** 报告 SHALL 展示模型、点估计、置信下界和“多数任务解锁”层级；具体数值必须来自最新官方提交 artifact，不得使用本例文字作为数据

#### Scenario: 点估计过半但置信区间不可得
- **WHEN** Terminal-Bench current frontier 为 59%而任务级统计不可得
- **THEN** B SHALL 返回 `provisional_crossing`
- **AND** SHALL 说明只有点估计、未获得 95% 置信区间，不得写成已确认跨越

#### Scenario: 当前前沿未过半
- **WHEN** CritPt current frontier 为 32%
- **THEN** B SHALL 返回 `not_crossed`
- **AND** 报告 SHALL 显示当前模型、得分和未跨越“多数任务解锁”层级

### Requirement: 不适合统一门槛的评分不得强行判断 B
LiveBench 与 AutomationBench-AA 默认 SHALL 只报告 A；OSWorld Partial SHALL 只报告 A，只有 Binary/Strict reward MAY 报告 B。Terminal-Bench、Terminal-Bench Science、SciCode、CritPt、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2 或 Humanity's Last Exam 只有在其官方指标语义满足 strict/binary/accuracy 且已注册 B 资格时才报告 B。人类基准跨越和经济可用门槛只有在 benchmark 注册表预先定义了门槛、口径和来源且当前存在可复核观测时才生成；没有定义或数据时报告 SHALL 完全省略对应栏目，而不是填充空白结论。

#### Scenario: Partial completion 分数超过 50
- **WHEN** AutomationBench-AA partial-objective score 为 69
- **THEN** Observer SHALL NOT 据此宣称多数任务被完整解锁
- **AND** B 状态 SHALL 为 `not_applicable`

#### Scenario: 没有人类或经济门槛定义
- **WHEN** 某 benchmark 只有模型分数且未预先注册人类基准或经济可用门槛
- **THEN** 正式报告 SHALL 不生成这两类门槛栏目
- **AND** SHALL NOT 用临时人工判断补齐

### Requirement: 正式报告统一展示能力矩阵、事件账本和方法卡
报告 SHALL 在一个表格中展示十一项 benchmark 与九家 Labs 当前旗舰成绩；覆盖不足显示 `NA`。每项 benchmark SHALL 附评估方向、值域、数值含义、评分/运行方法简述、A/B 适用性、版本/harness、来源和最新更新时间。矩阵 SHALL 标识维护者/独立第三方、竞对、自报告和不可比结果，不得把不同 comparability group 的值作为同口径排名。AutomationBench、OSWorld 等多口径结果 SHALL 另附事件账本。

#### Scenario: 生成当前能力核验报告
- **WHEN** 当前 cohort 中部分模型只有四项第三方成绩、部分只有 Lab 自报告
- **THEN** 报告 SHALL 保持完整十一乘九表格并以 `NA` 补足缺口
- **AND** SHALL 通过脚注或标签说明来源身份和不可比限制

### Requirement: 结论先用自然语言回答再列关键证据
Observer SHALL 分别用文字回答 A 与 B，而不是输出笼统 `ok`。A 结论 SHALL 说明外扩集中在哪些能力、哪些尚无确认；B 结论 SHALL 说明已确认、暂定、未跨越和不适用项目，并附最少必要的模型、得分、增量/缺口、版本和覆盖证据。`NA` SHALL 从计算分母中排除，不得解释为失败或零分。

#### Scenario: 只有部分 benchmark 出现突破
- **WHEN** 终端代理和科学代码出现外扩，但研究物理与严格 GUI 操作未过半
- **THEN** 报告 SHALL 表述为能力进步不均衡并列出两组关键证据
- **AND** SHALL NOT 将总状态简化为 `ok` 或“全面提升”

### Requirement: 可视化必须表达前沿变化、覆盖和门槛
正式报告 SHALL 从同一 accepted observation set 至少生成：（1）按 benchmark 分面的可比 global frontier 历史与模型标注；（2）十一乘九模型覆盖/得分热力图，NA、事件证据与不可比状态具有不同视觉编码；（3）B 适用 benchmark 的当前分数与最高已定义门槛图，区分 confirmed、provisional 与 not-crossed。图表 SHALL 使用稀疏时间刻度、可辨识配色、中文字体校验和可见的方法版本/来源注释，并提供 CSV/JSON 与 sidecar。

#### Scenario: 方法论换版出现在历史图中
- **WHEN** 某 benchmark 在时间范围内更换 comparability group
- **THEN** 历史图 SHALL 断开系列并标注新版本起点
- **AND** SHALL NOT 用连续线暗示可直接比较

#### Scenario: 中文字体缺失
- **WHEN** renderer 无法验证中文 glyph 或检测到缺字占位符
- **THEN** 图表 SHALL 质量失败并降级为表格
- **AND** SHALL NOT 发布乱码图片

### Requirement: 数据事件触发重算且报告具有新鲜度状态
新 eligible flagship、成绩新增或修订、benchmark 方法论换版、来源撤回、A 状态变化或 B 门槛状态变化 SHALL 触发受影响 benchmark 的增量重算。Observer SHALL 同时报告 overall data-as-of、每个来源最后成功时间、cohort 生效时间、覆盖率、pending/NA 数量和 stale/unavailable 警告；无实质变化时 SHALL 保持已有报告内容身份。

#### Scenario: NA 变为有效成绩
- **WHEN** 第三方补发某旗舰的 SciCode 分数
- **THEN** 系统 SHALL 重算 SciCode 的 current frontier、A 和 B
- **AND** SHALL NOT 重写其他未受影响 benchmark 的 observation 历史

#### Scenario: 来源超过新鲜度阈值
- **WHEN** 第三方来源连续超过声明时限未成功探测
- **THEN** Observer SHALL 标记来源 stale 并保留最近有效数据
- **AND** SHALL 不得将 stale 自动解释为能力不再进步

### Requirement: Packet、报告、图表和重放共享同一证据集合
Observer SHALL 输出机器 packet、compact Agent context、Markdown、图表、CSV/JSON、sidecar 和 snapshot manifest；全部产物 SHALL 共享 claim/version、cohort version、benchmark method versions、observation IDs、accepted rows hash 和 lineage pointer。离线 manifest replay SHALL 在不访问网络时重现相同矩阵、A/B 状态和图表数据。

#### Scenario: 审阅者离线复核报告
- **WHEN** 审阅者使用报告 manifest 和已保存 artifacts 执行 replay
- **THEN** 重放 SHALL 产生相同 accepted rows hash、十一乘九矩阵、事件账本和 A/B 判定
- **AND** 任一差异 SHALL 作为可复现性失败报告

### Requirement: 通过验收后直接进入 platform 且故障隔离
数据源与 raw capability Observer SHALL 在真实源采集、解析、质量、查询、A/B 计算、报告、图表、lineage、离线重放和既有 L1 回归全部通过后直接发布为 `platform` 并进入正式报告，不保留额外 shadow promotion。其不可用、覆盖不足或版本冲突 SHALL 只降级 raw-capability 子结论，不得破坏生产化、商业化或其他 sector/layer Observer，也不得进入 Chain、评分、组合、风控或交易路径。

#### Scenario: Raw capability 来源全部不可用
- **WHEN** 当次运行无法获得任何通过新鲜度门的数据且无可发布历史快照
- **THEN** raw capability Observer SHALL 返回 `unavailable` 及原因
- **AND** 既有生产化和商业化报告 SHALL 继续正常生成

#### Scenario: 首次端到端验收通过
- **WHEN** 所有规定测试和真实源验收通过
- **THEN** L1 正式报告 SHALL 默认包含 raw capability 章节
- **AND** 无需额外 release-check 或 publish 命令才能显示
