## Purpose

将新闻、公告、研报、文章和电话会中的投资相关信息抽取为来源可追溯的信息变化和影响候选，但不产生交易或仓位决策。

## ADDED Requirements

### Requirement: Information Analyst 只能消费已准入的数据资产

Information Analyst SHALL 只读取由 Data Products 发布的文档版本、chunk 和中性证据事实，SHALL NOT 在 Agent 运行中直接调用新闻、研报、公告或电话会 Provider。

#### Scenario: 输入文档未通过准入

- **WHEN** 文档只存在于 quarantine 或原始抓取层而未发布为 Data Product
- **THEN** Information Analyst SHALL NOT 读取或引用该文档
- **AND** 运行结果 SHALL 将该材料视为不可用而非自行抓取替代来源

### Requirement: InformationBrief 必须保留逐项来源和时间语义

每个信息变化或影响候选 SHALL 引用文档 ID、文档版本、片段位置、来源时间、事件时间和抽取时间，并标记置信度、时效、关联实体和待核验项。

#### Scenario: 同一事件被多个文档报道

- **WHEN** 多个来源描述同一事件
- **THEN** InformationBrief SHALL 保留每个独立文档引用并标记聚类关系
- **AND** SHALL NOT 将转载数量当作多个独立事实证人

### Requirement: Information Analyst 必须区分事实变化与影响候选

InformationBrief SHALL 将可核验的事实变化与 Agent 推断的影响候选分开表达，并对仅自述、尚未交叉验证或来源冲突的内容显式降级。

#### Scenario: 公司管理层单方声明

- **WHEN** 某个影响判断只由受益公司的自述支撑
- **THEN** InformationBrief SHALL 标记为仅自述或未交叉验证
- **AND** SHALL 保留可用于后续验证的反证条件

### Requirement: Information Analyst 不得生成投资或执行指令

InformationBrief SHALL NOT 包含买入、加仓、减仓、卖出、目标仓位、订单数量、价格或订单类型。它可描述潜在影响方向，但必须标记为待 Fundamental 或 Chief 判断的候选。

#### Scenario: 模型输出含有目标仓位

- **WHEN** Information Analyst 的原始模型输出包含目标仓位或订单建议
- **THEN** 系统 SHALL 将该输出判定为 schema 违规
- **AND** SHALL NOT 发布为成功 InformationBrief

### Requirement: Information Analyst 必须可独立运行

Information Analyst SHALL 可以作为手动、定时或文档事件驱动的独立 Workflow 运行，其成功输出 SHALL 在不运行 Fundamental 或 Chief 的情况下仍被存储和查询。

#### Scenario: 只更新信息快报

- **WHEN** 定时任务只请求更新 InformationBrief
- **THEN** 系统 SHALL 发布新投影并终结运行
- **AND** SHALL NOT 自动调用 Fundamental、Chief、Risk 或 Trader
