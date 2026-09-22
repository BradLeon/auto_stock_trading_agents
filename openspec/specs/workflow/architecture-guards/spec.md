# workflow/architecture-guards Specification

## Purpose

把「分析师互不读取彼此观点」「Agent 不得直接调用数据 Provider」「Agent 观点不得回写为共享事实」三条架构约束表达为可机器验证的判据，使后续的角色重构与依赖调整有可执行的守卫，而不是依赖人工审阅发现问题。

## Requirements

### Requirement: 分析师输入边界必须可机器验证

系统 SHALL 以可执行检查验证每个分析角色的输入来源集合。除「层次分析师到行业分析师」与「信息分析师到基本面分析师」两条明确依赖外，一个分析角色 SHALL NOT 读取另一个分析角色的产出。当某角色新增或扩大了跨角色读取时，检查 SHALL 失败并指出读取方、被读取方与读取位置。

#### Scenario: 新增一处跨角色读取

- **WHEN** 某个分析角色开始读取另一分析角色的产出投影，而该依赖不在两条明确允许之内
- **THEN** 边界检查 SHALL 失败并报告读取方角色、被读取方角色与代码位置
- **AND** 该变更 SHALL NOT 仅凭审阅通过而进入主线

#### Scenario: 两条明确依赖被保留

- **WHEN** 行业分析师读取层次分析师的产出，或基本面分析师读取信息分析师的产出
- **THEN** 边界检查 SHALL 允许该读取
- **AND** 检查 SHALL NOT 因这两条依赖而误报

#### Scenario: 分析角色读取共享事实

- **WHEN** 任一分析角色读取共享数据事实或数据产品
- **THEN** 边界检查 SHALL 允许该读取
- **AND** 检查 SHALL 区分「读取共享事实」与「读取另一分析角色的观点」

### Requirement: Agent 不得直接调用数据 Provider

Agent 与 Workflow SHALL 只通过数据产品与统一运行时入口读取数据。系统 SHALL 以可执行检查阻止 Agent 模块直接导入来源适配器或访问 Provider 特定接口，并在检出时指出违规模块与被导入的适配器。

#### Scenario: 直接导入来源适配器

- **WHEN** 某个 Agent 模块直接导入结构化或非结构化来源适配器
- **THEN** 边界检查 SHALL 失败并指出该模块与实际导入目标
- **AND** 该导入 SHALL NOT 被登记为合法的数据获取路径

#### Scenario: 经数据产品读取

- **WHEN** 某个 Agent 需要持久化研究数据或即时市场输入
- **THEN** 该 Agent SHALL 分别经数据产品或统一运行时入口读取
- **AND** 边界检查 SHALL 允许该读取

### Requirement: Agent 观点不得回写为共享事实

含观点的 Agent 结论 SHALL 只写入 Workflow memory。系统 SHALL 以可执行检查阻止 Agent 写成或覆盖共享事实层的事实、观测与文档资产。

#### Scenario: 试图把分析结论写成共享事实

- **WHEN** 某处代码把分析角色的结论写为共享事实层的中性事实记录
- **THEN** 边界检查 SHALL 失败并指出写入点与目标记录类型
- **AND** 该写入路径 SHALL NOT 被视为合法的事实发布

#### Scenario: 引用共享事实并写出观点

- **WHEN** 分析角色引用数据层的观测、文档或事实血缘并产出自身结论
- **THEN** 结论 SHALL 写入 Workflow memory 并保留对数据层血缘的引用
- **AND** 数据层 SHALL NOT 因此新增该结论对应的输入数据集

### Requirement: 边界守卫失败必须阻断而非告警

边界检查 SHALL 作为测试套件的一部分执行，其失败 SHALL 使对应检查项判定为失败。系统 SHALL NOT 以日志告警、跳过标记或容忍清单替代检查失败，除非该例外在同一次变更中被显式声明并附理由。

#### Scenario: 守卫检出违规

- **WHEN** 边界检查检出未声明的跨角色读取、直接 Provider 调用或观点回写
- **THEN** 该项检查 SHALL 判定失败
- **AND** 失败 SHALL NOT 因存在日志记录而被视为已处理

#### Scenario: 声明有理由的例外

- **WHEN** 确有需要保留的例外，且该例外在同一次变更中被显式声明并附理由
- **THEN** 检查 SHALL 允许该例外并继续对其它路径生效
- **AND** 例外清单 SHALL 可被审阅，SHALL NOT 表现为无上限的通配排除
