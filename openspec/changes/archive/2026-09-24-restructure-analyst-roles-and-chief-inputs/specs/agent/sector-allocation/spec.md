## Purpose

行业分析师独占行业 / 层次 / 标的三级配置权：它把层级分析师给出的层级状态与证据翻译成资金分配结论，并承接层级预算与护栏不变式。它是唯一被允许消费层级分析师观点的角色，除此之外只消费共享事实，不消费任何其他分析师的观点。

## ADDED Requirements

### Requirement: 行业分析师独占三级配置权

行业分析师 SHALL 产出行业、层次、标的三级配置结论，取值限定为 `超配 | 标配 | 低配 | 清仓`（层次与行业层）与逐标的权重。配置结论 SHALL 由行业分析师自行产出，SHALL NOT 由层级分析师提供，也 SHALL NOT 由基本面、宏观、技术面或信息分析师提供。

#### Scenario: 配置结论只来自行业分析师

- **WHEN** 需要决定某层或某标的的资金分配
- **THEN** 该结论 SHALL 来自行业分析师对 `LayerAnalysis` 与共享事实的消费
- **AND** 系统 SHALL NOT 从层级投影中读取任何配置结论字段

#### Scenario: 三级配置同时产出

- **WHEN** 一次行业评审完成
- **THEN** 输出 SHALL 同时覆盖行业层、层次层与标的层的配置
- **AND** 三者 SHALL 在数值上自洽（标的权重之和等于该层的层配置所给预算）

### Requirement: 行业分析师只消费层级投影与共享事实

行业分析师 SHALL 只消费 `LayerAnalysis` 投影与共享数据事实（行业横截面、产业链知识、一致预期、价格等数据产品）。它 SHALL NOT 读取宏观评审、基本面 / PEAD、信息简报或技术面评审的观点，SHALL NOT 读取其他行业或研究结论的汇总文本。

跨层轮动所需的背景 SHALL 来自共享事实与本轮各层的层级投影，SHALL NOT 来自宏观判断。

#### Scenario: 不读取宏观观点

- **WHEN** 行业分析师组装跨层轮动上下文
- **THEN** 上下文 SHALL NOT 包含宏观 regime、利率、风险偏好或板块倾斜的判断
- **AND** 跨层轮动 SHALL 只回答利润池在层间的迁移方向

#### Scenario: 不读取基本面与其他分析师观点

- **WHEN** 行业分析师组装上下文
- **THEN** SHALL NOT 包含 PEAD dossier、预期基线、财报 Scorecard、信息简报或技术面结论
- **AND** 上下游信号若要进入行业判断，SHALL 以共享事实或数据产品形式输入，
  SHALL NOT 以另一个分析师的观点形式输入

#### Scenario: 只消费层级投影

- **WHEN** 行业分析师需要层级结论
- **THEN** SHALL 经 `task_projection_envelopes` 读取 `LayerAnalysis` 投影
- **AND** 该读取 SHALL 是架构守卫显式允许的唯一跨角色读取

### Requirement: 配置结论绑定预算且护栏只降不升

配置结论 SHALL 映射为该层的**预算使用率**，该层截面 basket 的权重之和 SHALL 等于 `weight_cap × 预算使用率`。映射关系 SHALL 由配置声明（默认：超配 100%、标配 60%、低配 30%、清仓 0%）。

预算使用率 SHALL 只能**下调**本层预算。任何情况下 basket 的权重之和 SHALL NOT 超过 `risk.yaml` 中该层的 `weight_cap`；行业分析师 SHALL NOT 具备抬高该上限的能力。

#### Scenario: 低配收缩预算

- **WHEN** 某层 `weight_cap` 为 30% 且配置结论为「低配」（使用率 30%）
- **THEN** 该层 basket 的权重之和 SHALL 为约 9% NAV
- **AND** 层内各标的的相对权重比例 SHALL 由层级投影给出的截面排序决定，不因使用率而改变

#### Scenario: 清仓

- **WHEN** 配置结论为「清仓」
- **THEN** 该层 basket 的建议权重 SHALL 全为 0
- **AND** 系统 SHALL NOT 自动执行卖出，仍走既有的提案与人工审批路径

#### Scenario: 超配不突破上限

- **WHEN** 配置结论为「超配」
- **THEN** 该层 basket 权重之和 SHALL 等于 `weight_cap`，SHALL NOT 超过它
- **AND** 即使配置中把使用率误设为大于 100%，系统 SHALL 将其钳制到 100%

### Requirement: 配置结论必须保留证据冲突

行业分析师 SHALL 保留不同层级或不同标的之间的证据冲突，SHALL NOT 用一个不透明的总分把分歧压平。当层级判断与标的层面的证据给出相反指向时，配置结论 SHALL 分别呈现两条依据，并说明三级配置如何保留该分歧。

#### Scenario: 层级状态与标的竞争位置冲突

- **WHEN** 层级投影显示本层需求改善，但该层内某标的的相对位置恶化
- **THEN** 配置输出 SHALL 分列层级依据与标的依据
- **AND** SHALL 说明该分歧如何体现在层配置与标的权重的取值上，SHALL NOT 只给一个合并后的分数

#### Scenario: 标的多条证据互相矛盾

- **WHEN** 同一标的的两条共享事实给出相反指向
- **THEN** 配置结论 SHALL 标注该矛盾为待人工裁决
- **AND** SHALL NOT 通过加权平均静默消解该矛盾

### Requirement: 跨层轮动消费层级结论而不推翻它

行业分析师 SHALL 消费各层已产出的层级投影，其职责限定为跨层轮动与一致性检查：利润池在层间的迁移方向、相邻层结论的矛盾、以及一条可执行的层间加减建议。它 SHALL NOT 重新推翻某一层的层级状态判断。

#### Scenario: 轮动基于层级投影

- **WHEN** 全部层的层级投影就绪
- **THEN** 轮动建议 SHALL 引用具体层的状态判断与周期位置作为依据
- **AND** 发现矛盾时 SHALL 标注为待人工裁决，SHALL NOT 静默改写该层的状态判断

#### Scenario: 部分层缺失

- **WHEN** 部分层的层级投影缺失（评审失败或被跳过）
- **THEN** 轮动建议 SHALL 仍然产出，并显式列出缺失的层
- **AND** 涉及缺失层的加减建议 SHALL 标注为证据不足

### Requirement: 层级分析缺失时降级并留痕

行业分析师 SHALL 在缺少某层层级投影时以保守默认值完成该层配置，并 SHALL 显式留痕，SHALL NOT 把缺失呈现为中性判断或伪造层级结论。

#### Scenario: 某层无层级投影

- **WHEN** 某层不存在未过期的 `LayerAnalysis` 投影
- **THEN** 该层配置 SHALL 退回「标配」与保守默认使用率
- **AND** 输出 SHALL 标注该层为本期缺失层级分析，SHALL NOT 标注为景气中性

#### Scenario: 层级投影过期

- **WHEN** 某层存在层级投影但已超出有效期或关键数据 vintage 已变
- **THEN** 该投影 SHALL 被判定为不可复用
- **AND** 配置 SHALL 按缺失处理并留痕，SHALL NOT 沿用过期结论

#### Scenario: 层级投影 schema 不兼容

- **WHEN** 某层的层级投影 `schema_version` 或 payload 结构与行业分析师当前期望不兼容
- **THEN** 该投影 SHALL 被判定为不可用（与缺失同等处理）并留痕
- **AND** 系统 SHALL NOT 尝试字段兜底、猜测映射或降级解析来伪造该层配置

### Requirement: 配置结论以 SectorAllocation 投影发布

行业分析的唯一对外产出 SHALL 是写入 `task_projection_envelopes` 的 `SectorAllocation` 投影，payload SHALL 至少包含行业、立场、目标权重、理由与驱动因素，并 SHALL 经角色 schema 校验。

投影 SHALL 记录所消费的层级投影标识作为输入引用，使配置结论可追溯到具体的层级判断。

#### Scenario: 产出一条配置投影

- **WHEN** 一次行业评审成功产出配置结论
- **THEN** 系统 SHALL 写入一条 `agent_role=sector_allocation` 的投影
- **AND** 其 `input_refs` SHALL 含本轮消费的全部层级投影标识

#### Scenario: 配置结论可追溯到层级判断

- **WHEN** 复核某条行业配置结论
- **THEN** SHALL 能沿输入引用取回该结论所依据的层级投影、其内容哈希与 as-of
- **AND** 层级投影缺失时 SHALL 能看到本期的缺失留痕而非一条无来源的结论
