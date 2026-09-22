## ADDED Requirements

### Requirement: 层次分析师的职责必须限定为产业层研究

层次分析师 SHALL 只回答 AI 硬件产业链的层级结构、单层中的标的横截面对比、证据强度、差异来源和可证伪条件。层次分析师 SHALL NOT 输出行业、层级或标的的配置等级、预算使用率、建议权重或可执行交易指令。

#### Scenario: 证据显示某层需求改善

- **WHEN** 共同需求命题和横截面证据显示某产业层景气改善
- **THEN** LayerAnalysis SHALL 表达周期位置、证据、置信度和反转条件
- **AND** SHALL NOT 因此输出超配、标配、低配、清仓或目标权重

#### Scenario: 下游 Sector 需要配置结论

- **WHEN** Sector Analyst 消费有效 LayerAnalysis
- **THEN** Sector Analyst SHALL 自行产生行业、层级和标的的配置结论
- **AND** SHALL NOT 从 LayerAnalysis 读取已预先决定的配置等级

## MODIFIED Requirements

### Requirement: 层级评审的输入契约

层级分析师 SHALL 只接收本层作用域内的上下文：本层的共同议题（common claims）当期结论、
本层的判据知识库、本层与 `cohort_extra` 的截面快照与排名、本层的相对命题（relative claims）
逐家读数、以及本层上一次的 LayerAnalysis。上下文 SHALL NOT 包含其他层的原始素材，
SHALL NOT 包含宏观判断（利率、风险偏好、板块倾斜），也 SHALL NOT 包含其他分析师的观点。

#### Scenario: 上下文按层隔离

- **WHEN** 对某一层运行层级评审
- **THEN** 该次调用的上下文 SHALL 只含该层及其 `cohort_extra` 的公司素材
- **AND** 跨层共同因子（如资本开支链判据）SHALL 作为共享中性背景注入各层

#### Scenario: 证据块按层切分且保持分账

- **WHEN** 组装某层的议题结论
- **THEN** 上下文 SHALL 只含该层命题的结论，SHALL NOT 含其他层的命题结论
- **AND** 共同需求命题（common）与截面比较命题（relative）的结论 SHALL 保持分为两块
- **AND** 共同需求的结论 SHALL NOT 被表述为「谁在赢」

#### Scenario: 两类命题各自定向

- **WHEN** 层级分析师判断本层的共同景气与截面差异
- **THEN** 共同景气依据 SHALL 来自 common claims
- **AND** 标的间差异依据 SHALL 优先来自 relative claims 和截面因子
- **AND** 两者 SHALL NOT 被压缩成一个配置或买卖结论

#### Scenario: 相对命题只喂结构因子与选股理由

- **WHEN** 相对命题产出逐家读数
- **THEN** 这些读数 SHALL 可进入截面结构因子和同层标的比较理由
- **AND** 原「选股理由」 SHALL 解释为研究比较而非买卖、stance 或权重建议
- **AND** 共同需求命题的结论 SHALL NOT 进入任何结构因子

#### Scenario: 不做宏观判断

- **WHEN** 层级分析师判断本层的周期位置
- **THEN** 依据 SHALL 是产业证据（资本开支指引、订单与交期、库存、产能投放）
- **AND** 输出 SHALL NOT 包含利率、风险偏好或大盘走向的判断

#### Scenario: 上一轮 LayerAnalysis 回灌

- **WHEN** 该层存在上一次有效 LayerAnalysis
- **THEN** 上下文 SHALL 包含上次的产业判断、截面差异和可证伪条件
- **AND** 本次输出 SHALL 逐条说明这些条件是否已被触发

#### Scenario: 上一轮结论回灌

- **WHEN** 该层存在上一次层级分析结论
- **THEN** 上下文 SHALL 将其按 legacy 配置结论或新 LayerAnalysis 区分标记
- **AND** 新运行 SHALL 只将产业判断、截面差异和可证伪条件注入当期分析
- **AND** SHALL NOT 将 legacy 配置等级当作当期输出要求

### Requirement: 反转触发条件

每条层级景气、产业结构或标的横截面判断 SHALL 附带一组可证伪的反转触发条件：具体到可在下一轮直接核对的观察项，说明什么读数出现会使该研究判断改变方向。

#### Scenario: 触发条件可核对

- **WHEN** 层级分析师判断某层需求正在改善
- **THEN** 触发条件 SHALL 写成具体观察项（如订单方向反转或供给缺口收敛）
- **AND** SHALL NOT 是「基本面恶化」这类无法在下一轮判定的表述

### Requirement: 同层选股

原同层选股能力 SHALL 收窄为同层标的截面比较。层级分析师 SHALL 为本层每只标的输出竞争位置、优势/劣势与可比性限制，依据优先级为：① relative claims 逐家读数 → ② 截面排名与结构因子 → ③ 判据知识库。输出 SHALL NOT 包含买入/卖出 stance 或建议权重。

#### Scenario: 读数优先于笔记

- **WHEN** 某只标的的 relative claim 读数与判据笔记的方向相反
- **THEN** 截面比较 SHALL 以本期读数为准
- **AND** SHALL 明确说明与笔记的分歧及可能原因

#### Scenario: 仅自述的读数

- **WHEN** 某条读数标记为「仅自述」
- **THEN** 输出 SHALL 标注其为未经交叉验证
- **AND** SHALL NOT 单凭该读数断言标的必然胜出或落后

#### Scenario: subgroup 内比较

- **WHEN** 本层配置了 subgroup
- **THEN** 截面比较 SHALL 先在 subgroup 内进行，再说明跨 subgroup 可比性
- **AND** SHALL NOT 仅凭全层 z 分名次断言不同 subgroup 的优劣

#### Scenario: 未设 subgroup 但存在异类标的

- **WHEN** 某标的的定价机制与同层其他标的不同且 `note` 已声明
- **THEN** 输出 SHALL 显示该可比性限制
- **AND** SHALL NOT 仅凭它在层内 z 分表的名次给出投资取舍

#### Scenario: 未设 subgroup 的层内存在异类标的

- **WHEN** 本层未设 subgroup，但某标的的定价机制与同层其余标的不同
- **THEN** 截面比较 SHALL 使用已声明的 `note` 说明可比性限制
- **AND** SHALL NOT 将层内名次转换为买卖取舍或配置结论

### Requirement: 每层一份报告，结论先行

系统 SHALL 为每一个成功产出 LayerAnalysis 的层写出一份报告，SHALL NOT 为同一层同时产出第二份相互独立的截面报告。报告 SHALL 以层级景气/结构判断、标的截面结论、置信度和证据缺口开篇，SHALL NOT 将配置等级、建议权重或 stance 作为本报告结论。

#### Scenario: 每层一份

- **WHEN** 一次评审成功产出 N 个 LayerAnalysis
- **THEN** 系统 SHALL 写出 N 份层报告，每层一份
- **AND** 该层的截面排序、证据和可比性限制 SHALL 包含在该报告内

#### Scenario: 研究结论先行

- **WHEN** 渲染一份层报告
- **THEN** 首节 SHALL 同时给出层级判断和逐标的截面结论
- **AND** 首节 SHALL 显式说明它不是配置或交易建议

#### Scenario: 结论先行

- **WHEN** 渲染一份层报告
- **THEN** 首节 SHALL 先呈现层级景气/结构判断、逐标的截面证据和主要缺口
- **AND** 证据簇、因子明细和逐条判读 SHALL 位于首节之后
- **AND** 首节 SHALL NOT 包含预算、建议权重或 stance

#### Scenario: 临时截面查询不产出文件

- **WHEN** 单独运行截面排序用于调试或临时查看
- **THEN** 结果 SHALL 输出到临时返回介质
- **AND** SHALL NOT 写出第二份持久化层报告

#### Scenario: 跨层报告收窄为轮动与索引

- **WHEN** 迁移期的兼容入口仍请求跨层报告
- **THEN** 它 SHALL 只提供各 LayerAnalysis 的索引和明确的退役提示
- **AND** 新的跨层轮动与配置内容 SHALL 由 SectorAllocation 提供
- **AND** 兼容报告 SHALL NOT 重复或伪造 Layer 配置结论

### Requirement: 候选追踪议题

层级分析师 SHALL 在报告中提出尚未预设但值得追踪的议题候选。这些候选 SHALL 仅作为待人工策展的研究建议，SHALL NOT 写入配置或自动影响本期截面判断。

#### Scenario: 候选只提议不生效

- **WHEN** 层级分析师提出候选议题
- **THEN** 它 SHALL 呈现为待人工评估的建议
- **AND** SHALL NOT 自动修改命题配置、标的排序或任何配置结论

#### Scenario: 候选须说明可证伪性与证人

- **WHEN** 提出一条候选议题
- **THEN** SHALL 说明它可以由谁作证以及什么读数会证伪
- **AND** 无法指出证人或证伪条件的候选 SHALL NOT 提出

#### Scenario: 与既有归纳机制并存

- **WHEN** 系统同时存在从未映射观测归纳命题的机制
- **THEN** 两者 SHALL 各自独立产出建议而互不覆盖
- **AND** 二者都 SHALL NOT 直接修改配置

### Requirement: 层级结论的留痕与注回

每个 LayerAnalysis SHALL 以 Task Projection 形式不可变落库并可按层查询历史。最新有效投影 SHALL 可作为 Sector 的唯一分析师上游，并 SHALL 可被 Chief 作为独立研究输入读取。

#### Scenario: 按层查询历史

- **WHEN** 查询某层的历史 LayerAnalysis
- **THEN** SHALL 返回按时间排序的投影序列和内容 hash
- **AND** 旧式配置结论若保留作为历史记录 SHALL 被明确标记为 legacy

#### Scenario: 注入 Sector 上下文

- **WHEN** Sector 请求某层的上游分析
- **THEN** SHALL 收到该层最新且兼容的 LayerAnalysis 引用
- **AND** SHALL NOT 收到已退役的 Layer 配置或预算字段作为当期输入

#### Scenario: 注回下游

- **WHEN** Sector 或 Chief 请求某只标的的产业层上下文
- **THEN** SHALL 收到该标的所在层的最新 LayerAnalysis 引用、一句自包含层级判断和截面证据摘要
- **AND** SHALL NOT 收到已退役的层级配置等级或建议权重

## REMOVED Requirements

### Requirement: 层级配置结论

**Reason**: 层级配置结论与目标架构中 Sector Analyst 的三级配置职责重叠，会使两个分析师对同一配置问题产生冲突结论。

**Migration**: 新 LayerAnalysis 仅保留层级景气、结构、截面证据和可证伪条件；新配置结论由 `sector/sector-allocation` 生成。历史配置记录可作为 legacy 读数据保留，但不再注入新 Sector 运行。

### Requirement: 配置结论绑定预算使用率

**Reason**: 层级分析师不再输出配置结论，因此不应将其观点直接映射为预算使用率。

**Migration**: 预算和权重建议迁移至 SectorAllocation；硬风险上限仍由 Risk rules 管理，并在交易提案后由 Risk Officer 审查。

### Requirement: 护栏不变式

**Reason**: 该要求是围绕 Layer 预算使用率建立的护栏，其主体已从 Layer Analyst 移除。

**Migration**: SectorAllocation 的研究权重不得声称突破配置上限；交易后硬上限、失败处理和批准由 `decision/approval-lifecycle` 与 Risk rules 负责。

### Requirement: 跨层轮动消费层级结论

**Reason**: 跨层轮动和层间加减属于 Sector Analyst 的行业/层级配置职责，不应作为 Layer Analyst capability 的一部分。

**Migration**: Sector Analyst 消费各层 LayerAnalysis，独立处理利润池迁移、层间矛盾和配置建议。Layer Analyst 不再生成跨层报告或轮动结论。
