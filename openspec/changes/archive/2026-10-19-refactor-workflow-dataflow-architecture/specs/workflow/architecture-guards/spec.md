## Purpose

将分析师依赖、Agent 数据访问和观点/事实隔离转化为可机器执行的架构约束，使越界依赖在合并或运行前失败。

## ADDED Requirements

### Requirement: 分析师观点依赖必须采用允许列表

系统 SHALL 只允许 Layer 投影被 Sector 读取、Information 投影被 Fundamental 读取、全部分析师投影被 Chief 读取。其他跨分析师观点依赖 SHALL 在静态检查或运行组装时失败。

#### Scenario: Sector 导入 MacroReview

- **WHEN** 架构检查发现 Sector 代码或输入组装读取 MacroReview
- **THEN** 检查 SHALL 失败并指出越界的生产者、消费者和输入类型

#### Scenario: Chief 汇总全部已要求投影

- **WHEN** Chief 组装一个完整研究快照
- **THEN** 架构检查 SHALL 允许 Chief 读取六类分析师投影

### Requirement: Agent 不得直接访问外部 Provider

Agent 和其业务 Workflow SHALL 只经 Data Products、Runtime Data Gateway 或 Internal State API 读取数据，SHALL NOT 直接导入或调用外部 Provider adapter。

#### Scenario: Technical Analyst 直接调用行情 Provider

- **WHEN** 架构检查发现 Technical Analyst 直接依赖行情 Provider adapter
- **THEN** 检查 SHALL 失败
- **AND** SHALL 指明应改由 Runtime Data Gateway 提供该输入

### Requirement: Agent 观点不得回写共享事实

含有配置、影响判断、预期、叙事、风险解释或交易建议的 Agent 产出 SHALL 只写入 Workflow Memory。只有经数据准入流程确认的中性事实才可进入共享事实层。

#### Scenario: InformationBrief 含有主观影响候选

- **WHEN** Information Analyst 发布一份带有影响候选的投影
- **THEN** 该投影 SHALL 只写入 Workflow Memory
- **AND** 共享事实写入守卫 SHALL 拒绝将该影响候选当作中性事实发布

### Requirement: 架构守卫必须在本地和 CI 中使用同一规则

依赖与写入边界规则 SHALL 作为可重复的自动化测试运行，SHALL NOT 只依赖人工 code review。

#### Scenario: 本地和 CI 检查同一越界导入

- **WHEN** 同一越界导入分别在本地和 CI 中被扫描
- **THEN** 两个环境 SHALL 使用同一允许列表并产生同类失败

