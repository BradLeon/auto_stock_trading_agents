## ADDED Requirements

### Requirement: 查询返回测量范围与来源载体
能力查询结果 SHALL 返回 `measurement_scope`、source transport、source URL/仓库、commit 或 HTTP validators、artifact hash 和 coverage state。查询层 SHALL 能按 `model_capability_proxy` 与 `model_agent_stack_capability` 过滤，默认不得跨范围聚合。

#### Scenario: 读取公开 benchmark 结果
- **WHEN** 消费者查询 LiveBench 或 Terminal-Bench
- **THEN** 结果 SHALL 显示对应的 proxy/agent-stack scope 与真实来源 lineage
- **AND** 不得将来源缺失或未授权 API 解释成数值零

### Requirement: 查询支持版本化模型与 benchmark 能力矩阵
结构化查询 SHALL 支持按 Lab、模型/旗舰 effective period、benchmark/version、harness、grader、推理配置、comparability group、来源策略和历史 `as_of` 返回能力成绩矩阵。结果 SHALL 包含数值或覆盖状态、单位/值域、方法版本、来源身份、新鲜度、质量状态和 observation lineage；查询 SHALL 能要求每家 Lab 在指定时点只返回一个 active flagship。

#### Scenario: 查询当前十一乘九矩阵
- **WHEN** 消费者请求当前九家 Labs 与十一项 benchmark 的旗舰能力矩阵
- **THEN** 查询 SHALL 返回固定行列集合并为缺失单元返回具体 coverage state
- **AND** SHALL NOT 因某模型没有成绩而删除其整列

#### Scenario: 查询事件型 benchmark 证据
- **WHEN** 消费者查询 AutomationBench 或 OSWorld 的证据
- **THEN** 查询 SHALL 分别返回默认统一矩阵候选与按 comparability group 分组的事件账本
- **AND** SHALL NOT 把不同 harness、metric 或 task set 的事件折叠成单一模型分数

#### Scenario: 重放历史 cohort
- **WHEN** 消费者以旧日期作为 `as_of` 查询能力矩阵
- **THEN** 查询 SHALL 使用当时有效的旗舰和当时已经 known 的成绩 vintage
- **AND** SHALL NOT 使用后来发布的新模型或修订分数

### Requirement: 查询层强制执行 benchmark 可比性边界
横截面、时间序列、frontier 和增量查询 SHALL 默认只在同一 comparability group 内排序、聚合或计算变化。消费者显式请求跨组数据时 SHALL 获得分组结果和 non-comparable 标记；只有注册 bridge 后才能请求桥接派生值，且结果 SHALL 返回 bridge version 和不确定性。

#### Scenario: 查询跨版本趋势
- **WHEN** 用户请求 Terminal-Bench 2.1 与 4.0 的完整历史
- **THEN** 查询 SHALL 返回两个独立系列及版本断点
- **AND** SHALL NOT 默认计算从 2.1 末值到 4.0 首值的增长率

#### Scenario: 使用注册桥接关系
- **WHEN** 用户显式请求已存在有效 bridge 的跨版本比较
- **THEN** 查询 SHALL 返回原始两条系列与单独的桥接派生结果
- **AND** 派生结果 SHALL 显示 bridge 方法、版本和适用范围

### Requirement: 查询可以复算 global frontier 与门槛输入
能力数据产品 SHALL 提供按 benchmark、comparability group 和 `as_of` 选择 current/previous global frontier 的受治理查询，并返回计算门槛所需的点估计、样本数、置信区间或缺失原因。查询 SHALL 保留全部候选值和选择理由，SHALL NOT 只返回最终最大值而隐藏来源冲突、配置差异或覆盖缺口。

#### Scenario: 同一模型存在第三方与自报告结果
- **WHEN** current frontier 候选包含第三方统一评测和 Lab 自报告
- **THEN** 查询 SHALL 按声明来源策略返回 selected frontier 并列出未选候选与原因
- **AND** SHALL NOT 对候选成绩取平均

#### Scenario: 无法计算置信区间
- **WHEN** selected frontier 只有点估计而没有样本级统计
- **THEN** 查询 SHALL 返回点估计和 `confidence_unavailable`
- **AND** 下游 SHALL 能据此区分 provisional 与 confirmed 判定
