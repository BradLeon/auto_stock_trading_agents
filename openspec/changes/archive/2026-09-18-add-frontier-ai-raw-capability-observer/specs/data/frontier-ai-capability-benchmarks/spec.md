## Purpose

定义 Frontier AI 最新通用旗舰模型与能力 benchmark 的受治理观测契约，使不定期发布的模型和评测成绩能够被持续发现、版本化保存、按口径比较并在任意历史时点准确重放。

## ADDED Requirements

### Requirement: 公共来源名称与内部兼容标识分离
系统 SHALL 将 AutomationBench-AA、Toolathlon Verified、SpreadsheetBench 2 与 Humanity's Last Exam 作为正式报告名称；EnterpriseOps-Gym SHALL 退出默认面板，其历史数据与内部 id MAY 保留但不得继续占用当前矩阵行。现有 `automationbench_aa` 仅作为向后兼容内部 id，不改变展示名称和方法身份。首选结果 SHALL 来自 benchmark 维护者或独立第三方的公开、可复核载体；Artificial Analysis 免费 API entitlement SHALL NOT 成为必需依赖。

#### Scenario: 公共结果源无需 Artificial Analysis 订阅
- **WHEN** 运行环境没有 Artificial Analysis Pro/API entitlement
- **THEN** LiveBench、Terminal-Bench、Terminal-Bench Science、Toolathlon、SpreadsheetBench 等公开来源仍 SHALL 能独立采集，公开 Artificial Analysis 页面 MAY 在合规门禁通过时解析其结构化载荷
- **AND** 不得因为 AA 不可用而把公开结果写成零分或伪造分数

### Requirement: 按公开载体保存来源契约
每个来源 SHALL 声明自己的采集契约：Git 结果保存 repository、commit/blob SHA 和文件 hash；公开 JSON 保存 URL、ETag/Last-Modified（若有）和 payload hash；README/HTML 表格或服务端渲染结构化页面保存 URL、解析范围、结构/schema 指纹和内容 hash；论文、release 与 model card 事件保存文档版本、发布日期和引用切片。浏览器截图、OCR 或页面视觉元素 SHALL NOT 作为正式分数的唯一证据。

#### Scenario: 公开载体发生修订
- **WHEN** 同一 Git 文件或 JSON endpoint 的内容 hash 发生变化
- **THEN** 系统 SHALL 追加新的 immutable artifact/vintage 并重新执行方法与质量检查
- **AND** 未变化的来源 SHALL 记录 `no_change`，不重复插入 observation

#### Scenario: 公开页面采集不满足许可门禁
- **WHEN** 自动化访问会绕过登录、订阅、付费墙、robots 或已知站点条款限制
- **THEN** 该自动 adapter SHALL fail closed 并记录 `source_policy_blocked`
- **AND** 系统 MAY 使用官方公开替代载体或带完整 lineage 的人工导入，但不得用截图 OCR 或搜索摘要补值

### Requirement: 成绩必须标注测量范围
每条 observation SHALL 标注 `measurement_scope` 为 `model_capability_proxy` 或 `model_agent_stack_capability`。LiveBench、SciCode、CritPt、MMMU-Pro、Humanity's Last Exam 等模型代理结果与 AutomationBench-AA、Terminal-Bench、Terminal-Bench Science、OSWorld、Toolathlon Verified、SpreadsheetBench 2 等 agent stack 结果不得跨 scope 排序、平均或合成单一能力分数。

#### Scenario: 同一模型同时有两类结果
- **WHEN** 一个模型在 LiveBench 与 Terminal-Bench 均有公开成绩
- **THEN** 查询与报告 SHALL 并列展示两个 scope
- **AND** 不得把二者相加、平均或生成未注册的综合排名

### Requirement: 首版能力矩阵固定覆盖九家 Labs 和十一项 benchmark
数据产品 SHALL 注册 OpenAI、Anthropic、Google/Google DeepMind、xAI、DeepSeek、Moonshot/Kimi、Tencent、Z.ai/GLM、Alibaba/Qwen 九家 Labs，并注册 LiveBench、AutomationBench-AA、Terminal-Bench 4.0、Terminal-Bench Science 0.1、SciCode、CritPt、OSWorld 2.0、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam 十一项 benchmark。展示层 SHALL 对每个有效时点形成十一项 benchmark × 九家 Labs 的完整矩阵；无可用成绩的单元格 SHALL 返回可解释缺失状态并显示为 `NA`，不得省略行列或填零。EnterpriseOps-Gym、FrontierMath Erdős、FrontierScience Research、群体智慧与安全对齐 benchmark SHALL NOT 出现在本版默认矩阵。

#### Scenario: 第三方尚未完成某旗舰模型评测
- **WHEN** 最新旗舰模型已经公开可用，但 benchmark 维护方公开结果载体尚未发布其 SciCode 结果
- **THEN** 矩阵 SHALL 保留该 Lab 与 SciCode 单元格并显示 `NA`
- **AND** 底层状态 SHALL 为 `not_evaluated` 或等价明确状态，而不是数值零

#### Scenario: ByteDance 模型被来源页面列出
- **WHEN** 来源包含 ByteDance 模型成绩但首版 Labs 注册表未纳入 ByteDance
- **THEN** 原始来源 SHALL 可被完整保存
- **AND** 正式十一乘九矩阵 SHALL NOT 用 ByteDance 替换 Alibaba/Qwen 或扩展既定 cohort

#### Scenario: 近似模型名称存在公开成绩
- **WHEN** 来源只包含 `GLM-5.3-Flash` 而固定旗舰列要求 `GLM-5.3`
- **THEN** 系统 SHALL 保存前者的原始 observation 和 model identity，但固定矩阵对应单元格 SHALL 保持 `NA`
- **AND** SHALL NOT 通过 family、发布日期或名称相似度静默替换精确模型版本

### Requirement: 当前旗舰模型由有效期和资格规则决定
系统 SHALL 为每家 Lab 保存模型 lineage、公开可用状态、发布/可用时间、能力层级、用途类别和 flagship effective period。只有已经公开可用、被注册为通用旗舰且满足该 Lab 预定义层级规则的模型才能替换当前旗舰；预告、撤回、轻量、专用、成本优化或实验模型 SHALL NOT 静默替换当前旗舰。每个历史 `as_of` 时点每家 Lab 最多 SHALL 有一个 active flagship。

#### Scenario: Lab 发布新的轻量模型
- **WHEN** Google 发布晚于当前旗舰的 Flash-Lite 模型，但该型号被注册为轻量层级
- **THEN** 系统 SHALL 保存该模型实体和来源证据
- **AND** 当前通用旗舰 SHALL 保持不变

#### Scenario: 新通用旗舰公开可用
- **WHEN** 一家 Lab 发布并开放符合预定义资格的新通用旗舰
- **THEN** 系统 SHALL 结束旧旗舰的 effective period 并开启新旗舰的 effective period
- **AND** 历史 `as_of` 查询 SHALL 继续返回当时有效的旧旗舰

#### Scenario: 已发布模型被撤回
- **WHEN** active flagship 被官方撤回或停止公开可用
- **THEN** 系统 SHALL 记录撤回事件而不删除其历史成绩
- **AND** 当前 cohort SHALL 按声明规则回退或标记该 Lab 暂无 eligible flagship

### Requirement: 模型发现与成绩发现使用独立增量生命周期
系统 SHALL 独立运行模型目录/官方发布发现与 benchmark 成绩发现。正常来源至少每日探测一次；新 eligible model 在首次发现后 30 天 SHALL 每日检查成绩，31–90 天 SHALL 至少每三日检查，之后 SHALL 至少每周检查；全部模型、benchmark 和方法论 SHALL 每周执行全量兜底审计。新增模型、NA 变为数值、成绩修订、方法论换版或撤回 SHALL 形成明确 change event。

#### Scenario: 新模型发布但第三方成绩尚未出现
- **WHEN** 模型发现器确认新旗舰公开可用而成绩发现器没有返回结果
- **THEN** 系统 SHALL 创建 coverage pending 状态并进入热观察期
- **AND** SHALL NOT 延迟模型注册直到第三方完成全部评测

#### Scenario: 热观察期间出现新成绩
- **WHEN** 某 `NA` 单元格在后续每日探测中出现可验证分数
- **THEN** 系统 SHALL 增量保存该 observation 并发出 score-added event
- **AND** 无需等待月度或全量任务才允许下游重算

#### Scenario: 定期探测无变化
- **WHEN** 来源内容身份、方法论和标准化结果均未变化
- **THEN** 运行 SHALL 记录 `no_change`
- **AND** SHALL NOT 新增重复 observation vintage

### Requirement: 第三方统一评测优先且竞对与官方自报告保持独立身份
同一 benchmark/config 的来源优先级 SHALL 固定为 benchmark 维护者或独立第三方统一评测，其次 `competitor_reported`，最后 `lab_self_reported`。系统 SHALL 保存每一来源身份，不得取平均、不得把竞对或自报告升级为独立评测；来源间差异 SHALL 可见。高优先级结果缺失时 MAY 选择低优先级真实结果作为带标签候选，但 SHALL NOT 将其伪装为统一横截面。第三方缺失、竞对缺失与 Lab 自报告缺失 SHALL 记录为不同覆盖状态。

#### Scenario: 第三方与 Lab 对同一模型报告不同分数
- **WHEN** benchmark 官方结果与 Lab release 对同一命名 benchmark 返回不同结果或不同配置
- **THEN** 系统 SHALL 同时保留两项 observation 及其 harness/config 血缘
- **AND** 默认统一矩阵 SHALL 按来源优先级选择可比的第三方结果并显示冲突提示

#### Scenario: Lab 未在 release 中报告某项 benchmark
- **WHEN** 官方 release 没有该项自报告且第三方也尚未评测
- **THEN** 底层 SHALL 可记录 `not_self_reported` 与 `not_evaluated`
- **AND** 报告 SHALL NOT 将该缺失解释为低分或零能力

### Requirement: 多口径 benchmark 使用统一矩阵与事件账本双轨保存
AutomationBench 与 OSWorld 的维护者、独立第三方、竞对和 Lab 自报告结果 SHALL 按 benchmark version、task set、harness、tool setting、grader 与 metric semantic 建立 comparability group。满足统一指纹的 observation MAY 进入默认横截面；其他真实结果 SHALL 进入事件账本并保留原始口径，SHALL NOT 覆盖、平均或与默认横截面混排。

#### Scenario: AutomationBench 出现 partial 与 strict 两种分数
- **WHEN** AutomationBench-AA 返回 partial-objective composite，而公开或厂商 release 返回 strict full-task pass
- **THEN** 两者 SHALL 写入不同 comparability group，默认矩阵 MAY 选择声明的统一 AA 横截面，strict 结果 SHALL 进入事件账本
- **AND** Observer SHALL NOT 用任一结果覆盖另一结果或计算二者平均值

#### Scenario: OSWorld 使用不同 offline 或 batch-tool 设置
- **WHEN** 多家 Lab 的 OSWorld 结果使用不同 task snapshot、offline/online 或 batch-tool 配置
- **THEN** 系统 SHALL 将这些结果作为事件证据展示并将同口径覆盖记为不足
- **AND** SHALL NOT 生成虚假的九家横截面排名

### Requirement: 每项成绩绑定完整评测身份和不可变来源
每条成绩 observation SHALL 绑定稳定 model id、Lab、模型版本、benchmark id/version、任务集、harness、grader、reasoning effort、其他关键推理配置、评分语义、值域、样本数（若可得）、点估计、置信区间（若可得）、来源类型、source URL、score-as-of、published-at、known-at、fetched-at、artifact identity 和内容 hash。原始 API 响应、网页切片或允许保存的最小证据 SHALL 在标准化前不可变保存。

#### Scenario: 网页后来修订相同模型成绩
- **WHEN** 后续探测发现相同评测身份的数值从 52 改为 55
- **THEN** 系统 SHALL 追加新 vintage 并保留旧值、首次可见时间和原始 artifact
- **AND** SHALL NOT 就地覆盖使旧报告无法重放

#### Scenario: 页面未提供样本级数据
- **WHEN** 来源只披露总分而没有任务结果或置信区间
- **THEN** observation SHALL 保存点估计并标记统计证据不完整
- **AND** SHALL NOT 伪造样本数或置信区间

### Requirement: Benchmark 方法论和可比组必须版本化
系统 SHALL 为每项 benchmark 保存评估方向、值域、分数含义、任务集版本、harness、grader、推理配置约束和方法论来源。任务集、harness、grader或评分语义发生可能影响结果的变化时，系统 SHALL 创建新的 benchmark method version 和 comparability group；不同组的成绩 SHALL NOT 默认拼接、计算增量或排序。只有显式注册的桥接关系和重叠评测才能支持跨组说明。

#### Scenario: Terminal-Bench 从 2.1 升级到 4.0
- **WHEN** 方法论探测发现任务集和 harness 均已变化
- **THEN** 系统 SHALL 为 4.0 建立新 comparability group
- **AND** SHALL NOT 将 2.1 与 4.0 的数值连接成连续能力曲线

#### Scenario: 同一批模型在新旧版本均被评测
- **WHEN** 有足够重叠模型形成可复核桥接样本
- **THEN** 系统 MAY 注册带方法、适用范围和不确定性的 bridge
- **AND** 原始新旧系列仍 SHALL 独立保存和展示

### Requirement: 同一模型 release 的评测配置归并

默认矩阵 SHALL 每个精确模型 release 只展示一列，不得把 `Max`、`High`、`xhigh` 或其他 reasoning effort/harness 配置当成不同模型版本。系统 SHALL 保存每个配置的独立 observation；在同一精确 release、同一 benchmark method/comparability group 与同一来源优先级内，矩阵 SHALL 选择公开观测到的最高分作为该 release 的 raw-capability 代表值，并在结构化输出与 sidecar 中返回被选中的 evaluation variant、reasoning effort、harness、来源和选择理由。系统 SHALL NOT 对配置分数取平均，亦 SHALL NOT 跨来源优先级用低优先级高分覆盖高优先级结果。

#### Scenario: Max 高于 High

- **WHEN** 同一精确 release 的 `Max` 与 `High` 配置在同一 benchmark 可比组中都有合格结果，且 `Max` 得分更高
- **THEN** 默认矩阵 SHALL 仅在该 release 列展示 `Max` 得分
- **AND** `High` 结果 SHALL 继续作为可查询候选保留
- **AND** 矩阵/sidecar SHALL 标明选中配置而不把 `Max` 写成新的模型 release

#### Scenario: 相近产品变体不能归并

- **WHEN** 两个名称只在字符串上相近，但没有权威 details URL 或显式 alias registry 证明属于同一 release（例如 `GLM-5.3` 与 `GLM-5.3-Flash`）
- **THEN** 系统 SHALL 保持两个独立模型身份
- **AND** 相近变体的得分 SHALL NOT 填入目标 flagship release 的矩阵单元格

### Requirement: 缺失、不可比和来源故障具有明确状态
每个预期矩阵单元 SHALL 区分 `not_evaluated`、`pending_publication`、`not_self_reported`、`not_applicable`、`non_comparable`、`source_unavailable`、`withdrawn` 和有效数值。正式表格 MAY 将无数值状态统一显示为 `NA`，但机器输出、方法卡和质量报告 SHALL 保留具体原因、新鲜度与最后成功时间。

#### Scenario: 来源临时不可达但已有最近有效成绩
- **WHEN** 当次探测返回 `source_unavailable` 且历史已有通过质量门的 observation
- **THEN** 系统 SHALL 保留最近有效成绩并标记其新鲜度与本次失败
- **AND** SHALL NOT 删除、置零或用失败响应覆盖该成绩

#### Scenario: 有分数但 harness 不可比
- **WHEN** 官方 OSWorld 分数来自修改后的任务或评分设置
- **THEN** 系统 SHALL 保存该分数并标记 `non_comparable` 或独立 comparability group
- **AND** 默认横向排名 SHALL NOT 把它与标准设置混排

### Requirement: 质量门阻止身份不清或口径不完整的成绩发布
成绩进入默认平台视图前 SHALL 通过模型身份、benchmark 版本、分数单位/值域、配置、来源证据、时间字段和可比性校验。无法解析模型、版本混淆、值域越界、方法论未知、重复 active flagship 或破坏性换版未分组的记录 SHALL 被隔离并报告原因。

#### Scenario: 来源只写简称而无法唯一映射模型
- **WHEN** 来源将模型写为 `Gemini Flash` 且存在多个候选版本
- **THEN** 记录 SHALL 被标记 model identity unresolved 并排除在默认矩阵之外
- **AND** 原始证据 SHALL 保留供后续映射

#### Scenario: 分数超出注册值域
- **WHEN** 百分制 benchmark 返回 146 且来源没有说明尺度转换
- **THEN** 记录 SHALL 质量失败并隔离
- **AND** SHALL NOT 通过截断或自动除法猜测修正
