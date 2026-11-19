# Spec Delta

## ADDED Requirements

### Requirement: 看板必须在固定本机网址上只读呈现最新状态
看板 SHALL 由常驻服务在 `http://127.0.0.1:8000/dashboard` 提供，每次请求 SHALL 现查各状态库。看板 SHALL NOT 监听非本机地址，SHALL NOT 提供任何改变系统状态的操作。

#### Scenario: 刷新看到最新运行
- **WHEN** 一个采集任务在 10:00 完成，用户在 10:01 刷新数据源页
- **THEN** 该任务的"最近尝试"SHALL 显示 10:00 的结果

### Requirement: 数据源必须分开显示最近尝试、最近成功与数据最新时间
数据源页 SHALL 对每个采集任务分别显示最近尝试的状态、最近一次成功、上次拿到新数据的时间，以及数据本身的最新时间。数据最新时间 SHALL 按源类型从数据库取得；取不到时 SHALL 显示"未登记"，SHALL NOT 以运行时间代替。

#### Scenario: 运行成功但无新数据
- **WHEN** 一个任务最近 30 次运行都是 `no_change`
- **THEN** 最近尝试 SHALL 显示为正常
- **AND** "上次拿到新数据"SHALL 显示最后一次 `succeeded` 的时间，或显示"窗口内无"

### Requirement: 新鲜度必须按任务声明的 SLO 判定
采集任务的新鲜度 SHALL 按 `freshness_slo_days` 判定。以距最近一次正常运行（`succeeded` 或 `no_change`）的时长计：超过 SLO 为红，超过 SLO 的一半为黄，停用的任务为灰。"上次拿到新数据" SHALL 单独显示，SHALL NOT 参与着色。

#### Scenario: 超过 SLO
- **WHEN** 一个 SLO 为 3 天的任务最近一次正常运行在 4 天前，之后的运行都失败
- **THEN** 该任务 SHALL 标红

#### Scenario: 财报间隙无新数据
- **WHEN** 一个 SLO 为 3 天的任务每天都是 `no_change`，上次拿到新数据在 40 天前
- **THEN** 该任务 SHALL 标绿
- **AND** "上次拿到新数据" SHALL 显示 40 天前

### Requirement: 流程页必须显示按计划应跑而未跑的格子
流程页 SHALL 按流程配置的计划，对近 14 天中本该运行、却没有运行记录的日期标"缺跑"。休市日与调度暂停期间 SHALL NOT 标缺跑。

#### Scenario: 静默未运行
- **WHEN** technical-review 计划在周一至周五运行，某个周三没有任何运行记录，当天也不是休市日
- **THEN** 该格 SHALL 显示为"缺跑"

### Requirement: 每次模型调用必须入账且失败不影响调用
每次模型调用 SHALL 记录角色、模型、token 数、费用与所属流程运行。费用 SHALL 标明来源：供应商返回的为"provider"，按单价计算的为"estimate"，无法得知时为空。入账失败 SHALL NOT 使模型调用失败，也 SHALL NOT 触发额外的模型请求。

#### Scenario: 供应商未返回费用
- **WHEN** 一次调用的响应不含费用，但配置中有该模型的单价
- **THEN** 记录的费用 SHALL 按单价计算，来源为 `estimate`
- **AND** 看板 SHALL 标"估算"

#### Scenario: 写库失败
- **WHEN** 入账时数据库被锁超时
- **THEN** 模型调用 SHALL 正常返回结果，只记录一条警告日志

### Requirement: 每个采集任务与流程必须有中文注释
看板中每个采集任务与流程的名称旁 SHALL 提供可点击展开的中文注释，内容包括：是什么、来源、谁在用、坏了影响什么。缺注释的条目 SHALL 显示"缺说明"。

#### Scenario: 新增任务未写注释
- **WHEN** 调度配置新增了一个启用的任务，但注释文件里没有它
- **THEN** 覆盖率测试 SHALL 失败
- **AND** 看板上该任务 SHALL 显示"缺说明"

### Requirement: 决策溯源必须与首席报告的依据清单口径一致
决策溯源页 SHALL 用与首席报告「依据清单」相同的解析逻辑，展示决策、被引分析师结论及其数据版本。同一决策在两处显示的时间 SHALL 一致。

#### Scenario: 同一决策两处对照
- **WHEN** 用户打开 11-18 首席决策的溯源页
- **THEN** 每条被引结论的截至时间与距决策时长 SHALL 与该轮首席报告的依据清单一致
