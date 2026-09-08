# evidence/ai-production-penetration-observer Specification

## Purpose

定义 AI 应用层如何以 Anthropic 1P API 的受治理数据形成可证伪、可重放、不会被误读为员工采用率的生产化与应用扩散 Evidence 结论。

## Requirements

### Requirement: Observer 使用固定且隔离的生产化命题

Observer SHALL 使用 `ai_core_production_workflow_penetration`，只消费 DataProducts 的
`core_production_workflow_proxy_v1` 结果。它 SHALL 输出 claim、1P API/GLOBAL 范围、阈值、期间、
snapshot manifest 与 lineage；不得读取 Provider/物理表、自行重算阈值或合并 Claude.ai 与 1P API。

#### Scenario: 运行固定命题
- **WHEN** L1 AI 应用层运行该 Observer
- **THEN** 输出 SHALL 具有固定命题和可重放的受治理输入清单
- **AND** SHALL NOT 声称 Usage Share 是员工或企业采用率

### Requirement: Observer 保留四项透明指标与审慎趋势状态

Observer SHALL 分别陈述 occupation/task 可见单元生产化率与生产化流量份额，不得以不透明综合分数替代。
只有至少三个连续、同 product、methodology、threshold 的月份才可判定趋势；历史不足时 SHALL 返回
`insufficient_history`，可展示最近两月比较但不得称为持续、加速或放缓。

#### Scenario: 两月数据且广度与深度不同
- **WHEN** 只有两个可比月且职业/任务广度方向不同、流量份额提高
- **THEN** 输出 SHALL 陈述月度比较中的“深度提高、广度表现混合”
- **AND** SHALL NOT 判定 `penetration_expanding`

### Requirement: Observer 提供专属方法卡、覆盖分布和结构化审阅

本 Observer 的成功或部分成功 packet SHALL 提供专属中文 `methodology_card`，包括来源、期间/release、
可见与 qualified 样本、缺失、taxonomy 映射、阈值/methodology/derivation、质量、manifest 和不可推断事项；
其他 Observer SHALL 不被要求提供该字段。Markdown SHALL 依次显示方法卡、四项指标、职业任务组合已确认
生产化覆盖分布、50% 以上分布尾部完整披露、高流量 TOP10、公式和限制；图表与表格 SHALL 使用同一数据与中文名称。

#### Scenario: 50% 覆盖职业
- **WHEN** 最新月份有职业的已确认任务组合覆盖至少为 50%
- **THEN** 输出 SHALL 在分布后列出职业、分子、分母与覆盖率，并标为 taxonomy-dependent 尾部披露
- **AND** SHALL NOT 将其称为典型职业、员工覆盖、岗位替代或完整工作流证据

### Requirement: Evidence 支持由层配置驱动的独立运行

系统 SHALL 提供 `ats evidence layer --sector <sector> --layer <layer>`，只运行该 layer 的已启用
`evidence_observers` 声明，默认持久化 Markdown 和图表并打印路径。该声明 SHALL 与 Chain `claims` 分离：
前者选择受治理只读数据 Observer，后者仍服务公司证人、归因与 Chain。有效但无声明 layer SHALL 返回
`no_registered_observers`；未知 runner、重复 claim 或不匹配的 claim/runner SHALL 返回明确配置错误；命令
不得运行其他 L1–L8 层来替代。

#### Scenario: L1 配置声明启用或停用
- **WHEN** `ai_hardware/L1_app.evidence_observers` 启用或停用
  `ai_core_production_workflow_penetration / ai_production_penetration`
- **THEN** `ats evidence layer` SHALL 相应运行该命题或返回 `no_registered_observers`
- **AND** SHALL NOT 自动把该声明加入 Chain、PEAD、Chief、组合、风控或交易 workflow
