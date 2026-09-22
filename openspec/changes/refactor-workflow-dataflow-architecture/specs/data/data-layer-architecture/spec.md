## ADDED Requirements

### Requirement: 中性证据的写入必须完全归属数据平台

来自文档、观察窗口、产业链来源、文章和 CLI 导入的中性证据事实 SHALL 通过数据平台写入接口持久化，SHALL NOT 写入 Workflow Memory 中已退役的数据表。写入 SHALL 保留原始文档版本、来源血缘和准入结果。

#### Scenario: 观察名单从电话会抽取证据

- **WHEN** 观察工作流从已准入文档生成中性证据观测
- **THEN** 观测、事实、投影和失败记录 SHALL 写入数据平台所有的证据存储
- **AND** SHALL NOT 尝试恢复或写入 Workflow Memory 中的旧证据表

#### Scenario: 数据写入失败

- **WHEN** 数据平台证据写入接口不可用或拒绝数据
- **THEN** 调用方 SHALL 获得显式失败和 reason code
- **AND** SHALL NOT 静默回退到旧 Workflow Memory 表

### Requirement: Workflow Memory 必须只保存观点和数据血缘引用

Workflow Memory SHALL 保存 Agent 投影、决策、审批和运行状态，并使用不可变 ID 引用数据平台的文档、事实、观测和 vintage。Workflow Memory SHALL NOT 复制或写入一份可被当作共享事实真相的数据副本。

#### Scenario: FundamentalExpectationUpdate 引用数据 vintage

- **WHEN** Fundamental 发布新预期投影
- **THEN** Workflow Memory SHALL 保存该观点 payload 和依赖的 data vintage IDs
- **AND** 被引用的原始数据 SHALL 仍由数据平台提供和解释

### Requirement: 数据写路径迁移必须可对账与回滚

中性证据写路径切换前 SHALL 验证新旧语义的实体、时间、文档版本、来源和幂等键映射。切换过程 SHALL 保留可恢复导出和明确回滚步骤，但 SHALL NOT 通过恢复已退役的 Workflow Memory 数据表来回滚边界决定。

#### Scenario: 新写路径的幂等键与旧数据不一致

- **WHEN** 切换前对账发现同一证据在新存储中会生成不同幂等身份
- **THEN** 切换 SHALL 被阻断
- **AND** SHALL 在修正映射并重新对账后才可重试

