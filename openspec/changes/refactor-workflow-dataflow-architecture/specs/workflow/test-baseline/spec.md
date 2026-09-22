## Purpose

为 Phase A–F 的长周期架构迁移建立唯一、可重建且可解释的测试基线，防止依赖缺失、受限执行环境或已知故障被误判为新回归。

## ADDED Requirements

### Requirement: 项目必须有唯一权威测试入口

权威基线 SHALL 使用项目已配置的 `uv` 环境和完整 extras 执行全量测试。任何基线记录 SHALL 同时记录命令、Python 版本、依赖范围、执行环境约束、通过/失败/错误数和用时。

#### Scenario: 重建权威基线

- **WHEN** 开发者在不受文件删除配额干扰的环境中执行 `uv sync --all-extras` 后运行 `uv run pytest`
- **THEN** 系统 SHALL 生成包含完整测量条件和计数的基线记录
- **AND** 该记录 SHALL 可被后续阶段使用同一入口复核

### Requirement: 环境错误必须与业务失败分开归因

基线报告 SHALL 将依赖未安装、权限/配额、临时目录清理或其他执行环境错误与代码行为断言失败分开归因，SHALL NOT 使用受限环境中被打断的计数替代权威基线。

#### Scenario: pytest 清理临时文件被环境拦截

- **WHEN** 测试 fixture setup 因文件删除配额被系统性打断
- **THEN** 该运行 SHALL 被标记为非权威环境对照
- **AND** SHALL NOT 将被打断的测试计为新业务回归

### Requirement: 基线失败必须按根因簇记录

系统 SHALL 将全量基线失败按可复核的根因簇分组，每个簇 SHALL 记录代表性失败、受影响范围、修复归属阶段和验收方式。

#### Scenario: 多个测试由同一退役表写路径引起

- **WHEN** 多个测试因同一已退役表的写入异常失败
- **THEN** 基线报告 SHALL 将它们归入一个根因簇
- **AND** SHALL 同时保留具体失败测试列表以便验证修复面

### Requirement: 每个迁移阶段必须有可执行门禁

每个 Phase SHALL 定义该阶段新增的测试集、不允许增加的旧失败集和阶段结束的验收条件。未满足门禁时 SHALL NOT 进入对真实交易有影响的下一阶段切流。

#### Scenario: Phase B 审批链测试失败

- **WHEN** Boss 旧 revision 回调或 Trader 幂等测试未通过
- **THEN** 系统 SHALL NOT 允许该新交易路径进入 live 模式

