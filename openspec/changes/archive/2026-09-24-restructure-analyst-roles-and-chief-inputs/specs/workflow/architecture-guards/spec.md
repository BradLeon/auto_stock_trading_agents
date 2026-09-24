## ADDED Requirements

### Requirement: 采集侧确定性组件不得位于 agents 目录

取回、解析、入库等采集侧确定性组件 SHALL NOT 位于 `agents/` 目录下。分析角色 SHALL 只读取采集结果经数据产品暴露的入口，SHALL NOT 在角色模块内保留采集路径。

守卫 SHALL 不再为「采集侧直连来源」保留例外：这类例外 SHALL 随采集组件迁出而失效，SHALL NOT 改标为更晚的阶段继续挂起。

#### Scenario: 采集模块仍在 agents 目录内

- **WHEN** 守卫扫描发现某角色模块直接取回原始来源或写入原始资产
- **THEN** SHALL 判定为违规
- **AND** SHALL NOT 因「该调用发生在采集阶段」而放行

#### Scenario: 采集已迁出后读取

- **WHEN** 某角色需要采集结果
- **THEN** SHALL 经数据产品入口读取
- **AND** 守卫 SHALL 判定该读取合规

### Requirement: 守卫例外必须声明收敛阶段且不跨阶段挂起

每条守卫例外 SHALL 声明其收敛阶段与理由。例外到达所声明的阶段时 SHALL 被清退；SHALL NOT 以「后续阶段处理」为由连续改标而持续挂起。

新增例外 SHALL 在实际引入违规的同一批次内声明，SHALL NOT 先违规后补登记。

#### Scenario: 例外已到收敛阶段

- **WHEN** 某例外声明的收敛阶段成为当前阶段
- **THEN** 该例外 SHALL 被移除且对应违规 SHALL 被修复
- **AND** SHALL NOT 被改标为更晚阶段保留

#### Scenario: 新增违规未登记

- **WHEN** 代码引入一处新的边界违规
- **THEN** 守卫 SHALL 判失败
- **AND** 该违规 SHALL 在同批次内连同理由与收敛阶段一并登记，否则不得合入
