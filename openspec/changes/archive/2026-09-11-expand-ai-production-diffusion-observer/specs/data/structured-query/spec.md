## ADDED Requirements

### Requirement: Snapshot 与 Agent context 按消费目的收窄血缘
L1 主结论 snapshot SHALL 只平铺固定三轴 headline 与趋势判断直接依赖的 observations。明细表、TOPN 和图表 SHALL 以独立 derivation/rows hash 和 lineage pointer 引用其精确输入；compact/review context SHALL 返回输入数量、版本、hash 和可查询指针，不得把未参与相应结论的全部来源 observations 平铺为上下文。

#### Scenario: Anthropic 明细包含数千任务 cell
- **WHEN** 任务生产化聚合由数千个叶子 observations 计算
- **THEN** 主结论 context SHALL 引用聚合 derivation、输入计数和可审计 lineage pointer
- **AND** 明细导出仍 SHALL 能按需展开全部叶子输入并离线复算

### Requirement: AI adoption evidence bundle 保持三条证据轴独立
系统 SHALL 提供受治理的 AI adoption evidence bundle，分别返回企业采用广度、员工持续使用和任务生产化结构。每条轴 SHALL 保留自己的来源、统计主体、分母、地区、技术范围、period、methodology、质量和 lineage；系统 SHALL NOT 对不同轴加权、平均或生成统一 penetration score。ONS BICS SHALL NOT 进入本 bundle，但既有历史数据 MAY 保留供独立审计。

#### Scenario: 三条轴同时存在
- **WHEN** 消费者请求最新 L1 AI adoption evidence bundle
- **THEN** 系统 SHALL 返回三条独立 axis results 及各自 status
- **AND** SHALL NOT 将企业百分比、就业人口百分比和 Claude 流量份额转换成同一数值

### Requirement: 跨来源印证通过可比性矩阵而非数值融合
bundle SHALL 为任意并列来源返回 comparability matrix，至少说明 statistical unit、denominator、geography、technology scope、reference period、frequency 和 methodology regime 是否相容。不同主体或地区的值 MAY 用于方向性印证，但只有全部声明维度兼容时才可计算差值、相关性或共同趋势。

#### Scenario: BTOS 与 RPS 同时上升
- **WHEN** 美国企业采用率与美国就业者工作使用率在各自可比序列中上升
- **THEN** bundle MAY 标记 `directionally_corroborating`
- **AND** SHALL NOT 计算两者差值或声称员工率解释了企业率变化

#### Scenario: BTOS 与 ONS 并列
- **WHEN** 消费者查看美国和英国企业采用证据
- **THEN** 系统 SHALL 展示两者不同的企业规模、行业覆盖、AI 定义和参考期
- **AND** 默认 comparability SHALL 为 contextual-only 而非 level-comparable

### Requirement: 异构 cadence 使用每来源最新时点并显式表达时间错位
最新 bundle SHALL 选择每个来源在查询 `as_of` 前已经可见的最新合格 observation 或 snapshot，并返回各自 data period、published/known time 和 age。系统 SHALL NOT 为获得共同日期而前向填充、插值或伪造同周期值；整体 history status SHALL 展示各轴时间错位和历史充足性。

#### Scenario: BTOS 已到八月而 RPS 只有二季度
- **WHEN** 同一次查询的 BTOS 最新 period 晚于 RPS 最新 quarter
- **THEN** bundle SHALL 返回两个真实期间和 `asynchronous_periods` 诊断
- **AND** SHALL NOT 把 RPS 二季度值标为八月值或视为缺失零

### Requirement: Agent context packet 在紧凑性和证据完整性之间可控
系统 SHALL 为 L1 Agent 提供 `compact` 和 `review` 两档 context packet。compact SHALL 包含 claim、三轴 latest status、可证伪事实、主要矛盾、方法/缺失警告、来源引用和 manifest ID，并通过确定性行数或字符预算避免注入全部原始表；review SHALL 额外提供历史表、关键行业/规模/职业/任务切片和 visualization descriptors。两档 SHALL 指向同一 observations 和 derivations。

#### Scenario: Agent 请求 compact context
- **WHEN** L1 workflow 请求 compact bundle
- **THEN** 系统 SHALL 返回足以形成事实判断的结构化摘要和可追溯 IDs
- **AND** SHALL NOT 省略分母、期间、质量或将截断内容伪装为完整覆盖

### Requirement: 人类表格和图表与结构化结果同源可复算
bundle SHALL 提供稳定的 table rows 和 visualization descriptors，使 Markdown、Pandas/Seaborn 或等价 renderer 从同一有序数据生成图表。每张图 SHALL 保存中文标题、指标定义、单位、来源、期间、观察值、data hash、manifest ID 和 renderer version；图表数据 SHALL 与表格及 Agent packet 的 observation IDs 对账。

#### Scenario: 图表渲染失败
- **WHEN** 受治理数据和表格已成功生成但 renderer 不可用
- **THEN** bundle SHALL 继续返回结构化表格、facts 和 manifest，并标记 `visualization_warning`
- **AND** SHALL NOT 将数据判断整体标记为失败

### Requirement: 多来源 snapshot manifest 支持离线重放
一次 bundle SHALL 固定所有来源的 observation IDs、artifact IDs、source release identities、query parameters、methodology/derivation versions、comparability results、selected periods 和生成时间。按 manifest 重放 SHALL 不访问外部来源，也不因后来某一来源发布新值而改变其他轴或整体判断。

#### Scenario: ONS 后续发布新 AI wave
- **WHEN** 用户重放在新 wave 发布前生成的 manifest
- **THEN** 系统 SHALL 继续返回旧 manifest 固定的 ONS snapshot 和其他来源 versions
- **AND** SHALL NOT 自动混入新 wave 或重算原结论
