## ADDED Requirements

### Requirement: 同一采集批次中的记录精确绑定命名 artifact slice

结构化适配器 SHALL 为每个 artifact 提供批次内唯一、稳定的 artifact key，并为每条 observation input 和 relation input 提供对应 slice key。Pipeline SHALL 仅把记录绑定到 key 精确匹配的 artifact；slice key 缺失、重复或不存在时，相关记录 SHALL 被隔离并产生校验失败，SHALL NOT 回退到批次中的第一个 artifact。

#### Scenario: 两个产品位于同一批次
- **WHEN** 一个适配器批次同时返回 Claude.ai 与 1P API artifacts 和 observations
- **THEN** 每条 observation SHALL 绑定其 source product 对应 artifact
- **AND** 任一产品的 observation SHALL NOT 指向另一个产品 artifact

#### Scenario: observation 引用未知 slice key
- **WHEN** observation input 的 slice key 在批次 artifact keys 中不存在
- **THEN** 该 observation SHALL 被隔离并记录 missing artifact mapping
- **AND** Pipeline SHALL NOT 自动使用首个或任意其他 artifact

#### Scenario: artifact key 重复
- **WHEN** 同一批次返回两个相同 artifact key
- **THEN** 批次 SHALL 在发布前失败 key 唯一性校验
- **AND** 受影响记录 SHALL NOT 被赋予不确定血缘

### Requirement: 适配器可以返回 reference entities 和 versioned relations

结构化采集契约 SHALL 允许适配器除 observations 与 artifacts 外返回 reference entities 和 entity relations。每条 relation SHALL 明确 dataset、source、parent entity、child entity、relation type、source version、known time、artifact key 和可选 metadata，并在实体准入成功后才能发布。

#### Scenario: 同批次发布 SOC 与 task 层级
- **WHEN** taxonomy slice 返回 major group、occupation、task entities 及父子关系
- **THEN** Pipeline SHALL 先准入或确认全部引用实体，再发布有效 relations
- **AND** 每条 relation SHALL 绑定产生该关系的 taxonomy artifact

#### Scenario: relation 引用未知实体
- **WHEN** relation 的 parent 或 child entity 无法从已存实体或本批次 reference entities 解析
- **THEN** 该 relation SHALL 被隔离并显示 entity resolution failure
- **AND** SHALL NOT 创建悬空关系或静默丢弃失败信息

### Requirement: 实体关系按来源版本追加且保持幂等

系统 SHALL 以 dataset、source、父实体、子实体、关系类型和来源版本的内容身份保存关系。完全相同的关系重跑 SHALL 幂等去重；来源版本、关系目标或 metadata 发生有效变化时 SHALL 追加新关系版本而不覆盖旧版本，并保留 known time 和准确 artifact 血缘。

#### Scenario: taxonomy 重跑无变化
- **WHEN** 相同 taxonomy 版本产生完全相同的实体关系集合
- **THEN** 系统 SHALL 不新增重复 relation rows
- **AND** 运行结果 SHALL 可报告 `no_change`

#### Scenario: taxonomy 发布新版本
- **WHEN** 新来源版本改变某 task 的 occupation 归属
- **THEN** 系统 SHALL 追加新版本关系并保留旧关系
- **AND** SHALL 能按 known time 区分两个版本

### Requirement: 多 slice 运行支持独立准入与部分成功

一个数据源运行包含多个相互独立的命名 slices 时，系统 SHALL 分别保存下载、解析、质量和发布状态。单一 slice 失败 SHALL NOT 阻止不依赖该 slice 且已通过全部质量门的其他 slice；总体运行 SHALL 清楚表达 partial，而非完全成功或完全失败。

#### Scenario: observation slice 通过但 taxonomy slice 失败
- **WHEN** 月度 observation slice 本身有效，但其必需 taxonomy 依赖未达到关系准入门槛
- **THEN** 依赖该 taxonomy 的 observation SHALL 不得发布为完整可查询结果
- **AND** 不依赖该失败 taxonomy 的独立 slice MAY 按声明的依赖图继续发布

#### Scenario: 一个 source product schema 漂移
- **WHEN** 多产品批次中只有一个产品违反 schema 契约
- **THEN** 该产品 slice SHALL 为 `validation_failed`，通过的产品 SHALL 可发布
- **AND** 总体运行 SHALL 为 `partial` 并保留逐 slice 状态
