## ADDED Requirements

### Requirement: 不同公开来源使用不同的增量更新检测
结构化采集 SHALL 按来源载体选择条件更新策略：Git 来源比较 remote HEAD、commit/blob SHA 与文件 hash；公开 JSON 比较 ETag/Last-Modified 与规范化 payload hash；README/Markdown 表格比较 commit、解析区间和表格 hash；公开 HTML/服务端结构化载荷比较 URL、结构/schema 指纹与规范化内容 hash；论文、release 和 model card 事件比较文档版本与引用切片 hash。一次运行 SHALL 先获取来源批次再本地解析，避免按模型逐一请求。

#### Scenario: 免费公开来源替代付费 API
- **WHEN** Artificial Analysis API 未授权或返回 entitlement failure
- **THEN** 采集 SHALL 继续探测已注册的官方 Git/JSON/README 来源
- **AND** 只有真实公开结果可解析时才写入 observation，否则写 coverage state

#### Scenario: 公开结构化页面低频探测
- **WHEN** 来源没有免费 API 但公开页面提供无需登录的结构化数据载荷，且许可门禁允许自动访问
- **THEN** 采集 SHALL 使用低频条件探测、请求预算和 parser drift 检查保存原始 artifact
- **AND** 若许可或结构检查失败 SHALL fail closed，保留最近有效值并创建来源状态，不得绕过访问控制

### Requirement: 事件型评测结果独立于统一矩阵增量入库
对于 AutomationBench、OSWorld、SpreadsheetBench 2 等由 release、model card、论文或不同公开设置零散披露的结果，结构化采集 SHALL 追加带完整评测指纹的事件 observation，并将其与统一第三方矩阵分开。新事件 SHALL 触发受影响 benchmark 的重算，但 SHALL NOT 改写其他 comparability group 的当前值。

#### Scenario: 厂商发布新的不同 harness 分数
- **WHEN** Lab release 披露一个真实分数但其 harness 与默认第三方横截面不一致
- **THEN** 系统 SHALL 追加 `lab_self_reported` 事件及独立 comparability group
- **AND** 默认统一矩阵 SHALL 保持原值或 NA，并在报告事件账本中展示该证据

### Requirement: 夹具只能用于解析测试
带有 `fixture=true`、测试路径或 synthetic 标记的 artifact SHALL 被标记为 test-only，不得进入 platform 默认数据集、正式报告或 frontier 选择。正式入库必须具有可访问的公开来源 URL/仓库身份与内容 hash。

#### Scenario: 测试夹具通过解析
- **WHEN** 测试用 fixture 能够完整解析十一乘九矩阵
- **THEN** 测试 SHALL 验证 contract、NA 和方法校验
- **AND** platform release SHALL 拒绝该 fixture 作为正式数据

### Requirement: 不规则发布的数据源支持发现、热观察和兜底审计
结构化采集 SHALL 允许数据集分别声明实体发现频率、数据更新频率、发现后热观察窗口、降频窗口、全量审计频率和新鲜度阈值。发现任务与数据任务 SHALL 独立记录状态；新实体或新版本出现时 SHALL 能动态提高相关切片的探测频率，并在热观察期结束后按声明规则降频。

#### Scenario: 新模型触发热观察
- **WHEN** 每日模型发现任务确认一个新的 eligible flagship
- **THEN** 结构化调度 SHALL 为该模型创建热观察窗口并按窗口频率检查关联 benchmark
- **AND** 其他历史模型 SHALL 保持其正常探测频率

#### Scenario: 周度全量审计发现漏过的变化
- **WHEN** 增量探测没有产生事件但周度全量快照与上次内容 hash 不同
- **THEN** 系统 SHALL 生成差异事件并进入正常解析、校验和 vintage 流程
- **AND** SHALL 记录增量路径的 coverage miss 供运维修复

### Requirement: 方法论变化必须与数值变化分别检测
对声明为方法敏感的数据集，采集 SHALL 同时保存并比较方法论文档、任务集、harness、grader、评分语义和版本信息。无法判定影响范围的方法论变化 SHALL 在新数据发布前触发隔离或人工审阅；数值未变 SHALL NOT 使方法论变化被忽略。

#### Scenario: 分数不变但 grader 被替换
- **WHEN** 来源方法论显示 grader model 已更换而页面分数暂时相同
- **THEN** 系统 SHALL 保存新的方法论 artifact 并产生 method-changed event
- **AND** 受影响成绩 SHALL NOT 继续被默认视为同一可比系列，除非兼容规则明确允许

### Requirement: 预期观测可以保存语义化覆盖状态
对于预先知道应当存在的实体×指标或实体×benchmark 单元，平台 SHALL 能在没有数值时保存带来源、原因、首次观察时间、最后检查时间和下一次检查计划的 coverage state。缺失状态 SHALL 与 observation 数值分离并可随来源变化追加历史，不得以零值模拟缺失。

#### Scenario: 预期成绩仍未发布
- **WHEN** 数据产品预期某模型应有一项评测但当前来源没有结果
- **THEN** 系统 SHALL 保存 `pending_publication` 或 `not_evaluated` coverage state
- **AND** 默认数值查询 SHALL 返回无值及该原因

#### Scenario: 撤回已发布成绩
- **WHEN** 来源删除或明确撤回先前的 benchmark 成绩
- **THEN** 系统 SHALL 追加 `withdrawn` coverage state 并保留原 observation vintage
- **AND** 最新默认视图 SHALL 不再把已撤回值作为有效当前成绩
