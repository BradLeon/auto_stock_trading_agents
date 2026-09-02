## Purpose

层级子行业分析师：站在**单一产业链层**回答两个决策——这一层该给多少钱（超配/标配/低配/清仓），
以及这一层内部买谁；并把配置结论绑定到该层的截面预算，同时永不突破风险限额。

## Requirements

### Requirement: 层级评审的输入契约

层级分析师 SHALL 只接收本层作用域内的上下文：本层的共同议题（common claims）当期结论、
本层的判据知识库、本层与 `cohort_extra` 的截面快照与排名、本层的相对命题（relative claims）
逐家读数、以及本层上一次的层级结论。上下文 SHALL NOT 包含其他层的原始素材，
且 SHALL NOT 包含宏观判断（利率、风险偏好、板块倾斜）。

#### Scenario: 上下文按层隔离

- **WHEN** 对某一层运行层级评审
- **THEN** 该次调用的上下文 SHALL 只含该层及其 `cohort_extra` 的公司素材
- **AND** 跨层共同因子（如资本开支链判据）SHALL 作为共同背景注入所有层

#### Scenario: 证据块按层切分且保持分账

- **WHEN** 组装某层的议题结论
- **THEN** 上下文 SHALL 只含该层命题的结论，SHALL NOT 含其他层的命题结论
- **AND** 共同需求命题（common）与截面比较命题（relative）的结论 SHALL 保持分为两块
- **AND** 共同需求的结论 SHALL NOT 被表述为「谁在赢」

#### Scenario: 两类命题各自定向

- **WHEN** 产出层级配置结论（这一层该给多少钱）
- **THEN** 依据 SHALL 来自共同需求命题（common）的结论
- **AND** 相对命题（relative）的读数 SHALL NOT 单独作为配置结论的依据

#### Scenario: 相对命题只喂结构因子与选股理由

- **WHEN** 相对命题产出逐家读数
- **THEN** 这些读数 SHALL 可进入截面的结构因子，并 SHALL 可作为同层选股的理由
- **AND** 共同需求命题的结论 SHALL NOT 进入任何结构因子

#### Scenario: 不做宏观判断

- **WHEN** 层级分析师判断本层的周期位置
- **THEN** 依据 SHALL 是产业证据（资本开支指引、订单与交期、库存、产能投放）
- **AND** 输出 SHALL NOT 包含利率、风险偏好或大盘走向的判断
- **AND** 宏观影响 SHALL 由上游的宏观评审在组合层面作用，不在层级结论内重复计价

#### Scenario: 上一轮结论回灌

- **WHEN** 该层存在上一次的层级结论
- **THEN** 上下文 SHALL 包含上次的配置结论与其反转触发条件
- **AND** 本次输出 SHALL 逐条说明这些触发条件是否已被触发

### Requirement: 层级配置结论

层级分析师 SHALL 为每层输出一条配置结论，取值限定为 `超配 | 标配 | 低配 | 清仓`，
并附带 0-1 的 confidence、周期位置、以及**逐条议题的归因**（每条 common claim 一行：
该命题的当期结论及其对本层配置的含义）。

#### Scenario: 结论必须锚定议题结论

- **WHEN** 层级分析师给出「超配」
- **THEN** 输出 SHALL 至少引用一条当期为「支持」的共同议题作为依据
- **AND** SHALL NOT 在没有任何议题结论支撑时给出超配或清仓

#### Scenario: 证据不足时的默认

- **WHEN** 本层有命题但本期无一产出结论，或截面与快照数据大面积缺失
- **THEN** 配置结论 SHALL 为「标配」且 confidence SHALL ≤ 0.3
- **AND** 输出 SHALL 显式说明是**证据缺失**而非景气中性

#### Scenario: 本层没有命题

- **WHEN** 本层的命题列表为空
- **THEN** 输出 SHALL 显式标注「本层无命题，结论仅来自快照与判据笔记」
- **AND** 该标注 SHALL 与「证据缺失」相区分——前者是配置缺口，后者是本期没人发声
- **AND** 配置结论 SHALL 为「标配」且 confidence SHALL ≤ 0.3

#### Scenario: 议题冲突

- **WHEN** 本层的共同议题结论互相矛盾（部分支持、部分反驳）
- **THEN** 输出 SHALL 保留两侧结论而非压成单一分数
- **AND** confidence SHALL 相应下调

### Requirement: 反转触发条件

每条层级配置结论 SHALL 附带一组**可证伪的反转触发条件**：具体到可在下一轮直接核对的观察项，
说明什么读数出现会让该结论改变方向。

#### Scenario: 触发条件可核对

- **WHEN** 层级分析师输出「超配」
- **THEN** 触发条件 SHALL 写成具体的观察项（如某类读数的方向反转、某个供给缺口收敛）
- **AND** SHALL NOT 是「基本面恶化」这类无法在下一轮判定的表述

### Requirement: 同层选股

层级分析师 SHALL 为本层每只标的输出一条取舍理由，依据的优先级为：
① 相对命题（relative claims）的逐家读数 → ② 截面排名与结构因子 → ③ 判据知识库。
当上位证据与下位证据冲突时，SHALL 以上位为准并说明分歧。

#### Scenario: 读数优先于笔记

- **WHEN** 某只标的的相对命题读数与判据笔记里的结论方向相反
- **THEN** 输出 SHALL 以本期读数为准
- **AND** SHALL 明确说明与笔记的分歧及其可能原因

#### Scenario: 仅自述的读数

- **WHEN** 某条读数标记为「仅自述」（只有该公司自己这么讲）
- **THEN** 输出 SHALL 标注其为未经交叉验证
- **AND** SHALL NOT 单凭该条读数改变该标的的取舍

#### Scenario: subgroup 内比较

- **WHEN** 本层配置了 subgroup（如存储层的 HBM / 常规DRAM / NAND / HDD）
- **THEN** 逐票取舍 SHALL 先在 subgroup 内比较，再跨 subgroup 说明本期该偏向哪个 subgroup
- **AND** 因 z 分在整层计算，跨 subgroup 的排名差异 SHALL 被视为可能含有分组间的因子分布差异，
  分析师 SHALL NOT 仅凭排名先后断言跨 subgroup 的优劣

#### Scenario: 未设 subgroup 的层内存在异类标的

- **WHEN** 本层未设 subgroup，但某标的的定价机制与同层其余标的不同（其 `note` 已写明）
- **THEN** 逐票取舍 SHALL 依据该 `note` 说明其可比性限制
- **AND** SHALL NOT 仅凭它在层内 z 分表中的名次给出取舍结论

### Requirement: 配置结论绑定预算使用率

层级配置结论 SHALL 映射为本层的**预算使用率**，该层截面 basket 的权重之和 SHALL 等于
`weight_cap × 预算使用率`。映射关系 SHALL 由配置声明（默认：超配 100%、标配 60%、
低配 30%、清仓 0%）。

#### Scenario: 低配收缩预算

- **WHEN** 某层 `weight_cap` 为 30% 且层级结论为「低配」（使用率 30%）
- **THEN** 该层 basket 的权重之和 SHALL 为约 9% NAV
- **AND** 层内各标的的相对权重比例 SHALL 由截面排名决定，不因使用率而改变

#### Scenario: 清仓

- **WHEN** 层级结论为「清仓」
- **THEN** 该层 basket 的建议权重 SHALL 全为 0
- **AND** 系统 SHALL NOT 自动执行卖出，仍走既有的提案与人工审批路径

### Requirement: 护栏不变式

预算使用率 SHALL 只能**下调**本层预算。任何情况下 basket 的权重之和 SHALL NOT 超过
`risk.yaml` 中该层的 `weight_cap`；层级分析师 SHALL NOT 具备抬高该上限的能力。

#### Scenario: 超配不突破上限

- **WHEN** 层级结论为「超配」且 confidence 为 1.0
- **THEN** 该层 basket 权重之和 SHALL 等于 `weight_cap`，SHALL NOT 超过它
- **AND** 即使配置中把使用率误设为大于 100%，系统 SHALL 将其钳制到 100%

#### Scenario: 层级评审失败

- **WHEN** 某层的层级评审调用失败或产出不合法
- **THEN** 系统 SHALL 回退到该层的上一次配置结论；若没有上一次，SHALL 使用保守默认使用率
- **AND** SHALL NOT 因单层失败而中止其余层的评审
- **AND** 失败的层 SHALL NOT 被写入本次结论存档

### Requirement: 跨层轮动消费层级结论

行业分析师 SHALL 由「一次性合成全部层」改为**消费各层已产出的层级结论**，其职责限定为
跨层轮动与一致性检查：利润池在层间的迁移方向、相邻层结论的矛盾、以及一条可执行的层间加减建议。

#### Scenario: 轮动基于层级结论

- **WHEN** 全部层的层级结论就绪
- **THEN** 轮动建议 SHALL 引用具体层的配置结论与周期位置作为依据
- **AND** SHALL NOT 重新推翻某一层的配置结论；发现矛盾时 SHALL 标注为待人工裁决
- **AND** 上下文 SHALL NOT 包含宏观判断；轮动 SHALL 只回答利润池在层间的迁移方向

#### Scenario: 部分层缺失

- **WHEN** 部分层的层级结论缺失（评审失败或被跳过）
- **THEN** 轮动建议 SHALL 仍然产出，并显式列出缺失的层
- **AND** 涉及缺失层的加减建议 SHALL 标注为证据不足

### Requirement: 每层一份报告，结论先行

系统 SHALL 为每一个产出了层级结论的层写出**一份**报告文件，并 SHALL NOT 为同一层同时
产出第二份文件。报告 SHALL 以**结论**开篇：本层配置结论与该层逐票的建议权重、stance
排在最前；证据、因子明细与取舍理由 SHALL 排在其后。

#### Scenario: 每层一份

- **WHEN** 一次周度评审产出了 N 个层级结论
- **THEN** SHALL 写出 N 份层报告，每层一份
- **AND** 该层的截面排序与建议权重 SHALL 包含在这份报告内，SHALL NOT 另出一份截面文件

#### Scenario: 结论先行

- **WHEN** 渲染一份层报告
- **THEN** 首节 SHALL 同时给出层配置结论（含预算）与逐票建议权重/stance
- **AND** 议题证据、因子明细与逐票理由 SHALL 位于首节之后
- **AND** 首节 SHALL 自足到可据以决策，不依赖后续章节

#### Scenario: 临时截面查询不产出文件

- **WHEN** 单独运行截面排序（调试或临时查看）
- **THEN** 结果 SHALL 输出到终端
- **AND** SHALL NOT 写出文件，以免同一层出现两份互相不同步的文档

#### Scenario: 跨层报告收窄为轮动与索引

- **WHEN** 渲染跨层报告
- **THEN** 它 SHALL 包含 regime、轮动建议、矛盾与缺失层，以及各层结论的索引
- **AND** SHALL NOT 重复各层报告里的证据明细

### Requirement: 议题结论必须附证据链

报告中每一条共同需求议题的结论 SHALL 附带其**判读依据**：证人覆盖率、独立证据簇数量、
立场类别数、支持与反驳的累计、**未发声的已声明证人**，以及逐条判读（说话人、所属维度、
判读理由）。SHALL NOT 只给出结论词。

#### Scenario: 逐条议题可核对

- **WHEN** 某层有产出结论的共同需求议题
- **THEN** 报告 SHALL 逐条列出该议题的结论及上述判读依据
- **AND** 每条判读 SHALL 标明说话人与其所属维度

#### Scenario: 沉默必须可见

- **WHEN** 某条议题存在已声明但本期未发声的证人
- **THEN** 报告 SHALL 列出这些证人
- **AND** SHALL NOT 把沉默呈现为中性或略去不提

#### Scenario: 依据强度随结论呈现

- **WHEN** 某条议题的结论仅由利益相关方自述支撑
- **THEN** 报告 SHALL 标注其为「仅自述」
- **AND** 与有交叉印证的结论在呈现上 SHALL 可区分

### Requirement: 截面明细须含相对命题读数

报告的截面章节 SHALL 同时给出量化因子明细与**相对命题的逐家读数**（每家的位置、依据强度、
说话人、判读理由）。SHALL NOT 只呈现结构因子的分数而隐藏支撑它的读数。

#### Scenario: 结构因子分数可回溯

- **WHEN** 某标的带有结构因子分数
- **THEN** 报告 SHALL 同时给出支撑该分数的相对命题逐家读数
- **AND** 读数与分数冲突时 SHALL 以读数为准并说明分歧

#### Scenario: 本层无相对命题

- **WHEN** 本层没有相对命题或其本期无结论
- **THEN** 报告 SHALL 说明截面排序仅由量化因子决定
- **AND** SHALL 提示读者不要把排序当作竞争位置的判断

### Requirement: 候选追踪议题

层级分析师 SHALL 在报告中提出**尚未预设但值得追踪**的议题候选，依据其本层上下文的阅读。
这些候选 SHALL 仅出现在报告中，SHALL NOT 写入配置，也 SHALL NOT 影响本期任何结论或权重。

#### Scenario: 候选只提议不生效

- **WHEN** 层级分析师提出候选议题
- **THEN** 它们 SHALL 呈现为待人工评估的建议
- **AND** SHALL NOT 参与本期的配置结论、截面排序或权重计算

#### Scenario: 候选须说明可证伪性与证人

- **WHEN** 提出一条候选议题
- **THEN** SHALL 说明它可以由谁来作证、以及什么读数会证伪它
- **AND** 无法指出证人或证伪条件的候选 SHALL NOT 提出

#### Scenario: 与既有归纳机制并存

- **WHEN** 系统同时存在从未映射观测归纳命题的机制
- **THEN** 两者 SHALL 各自独立产出建议，互不覆盖
- **AND** 二者都 SHALL NOT 直接修改配置——只有人能把议题写进配置

### Requirement: 层级结论的留痕与注回

每条层级结论 SHALL 落库并可按层查询历史；最新一轮结论 SHALL 可注入下游智能体的上下文
（一句自包含的层级判断 + 其配置结论）。

#### Scenario: 按层查询历史

- **WHEN** 查询某层的历史配置结论
- **THEN** SHALL 返回按时间排序的结论序列（含 `legacy_keys` 解析到的拆分前记录）

#### Scenario: 注回下游

- **WHEN** 下游智能体请求某只标的的行业上下文
- **THEN** SHALL 收到该标的所在层的最新配置结论及其一句话依据
