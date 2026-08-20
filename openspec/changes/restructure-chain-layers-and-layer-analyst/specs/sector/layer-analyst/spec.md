## Purpose

层级子行业分析师：站在**单一产业链层**回答两个决策——这一层该给多少钱（超配/标配/低配/清仓），
以及这一层内部买谁；并把配置结论绑定到该层的截面预算，同时永不突破风险限额。

## ADDED Requirements

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

### Requirement: 层级结论的留痕与注回

每条层级结论 SHALL 落库并可按层查询历史；最新一轮结论 SHALL 可注入下游智能体的上下文
（一句自包含的层级判断 + 其配置结论）。

#### Scenario: 按层查询历史

- **WHEN** 查询某层的历史配置结论
- **THEN** SHALL 返回按时间排序的结论序列（含 `legacy_keys` 解析到的拆分前记录）

#### Scenario: 注回下游

- **WHEN** 下游智能体请求某只标的的行业上下文
- **THEN** SHALL 收到该标的所在层的最新配置结论及其一句话依据
