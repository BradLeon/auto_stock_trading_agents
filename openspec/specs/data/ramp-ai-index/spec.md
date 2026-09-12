# data/ramp-ai-index Specification

## Purpose

定义 Ramp AI Index 公开企业支付数据的受治理采集、版本、指标和查询契约，为 L1 AI 应用层提供独立的付费企业采用与 AI 支出证据，而不把 Ramp 网络样本误写成全体美国企业采用率。

## Requirements

### Requirement: Ramp 数据源必须以官方公开出口发现并记录访问状态
系统 SHALL 以 Ramp AI Index 页面作为来源注册依据。首版采集 SHALL 优先使用已经落盘的官方 `Get the data` 制表符分隔导出；如尚未落盘，才允许通过受控浏览器读取该可见控件。页面控件实际返回的制表符分隔文本 SHALL 作为 source-native payload 保存。若 fixture、控件或剪贴板不可读，运行 SHALL 返回 `export_unreadable`，SHALL NOT 以截图、搜索摘要、人工抄录或 API/MCP 回退替代。

#### Scenario: 网页导出成功
- **WHEN** 采集器打开 Ramp AI Index 的一个已注册图表，点击可见 `Get the data` 并读取剪贴板
- **THEN** 系统 SHALL 保存原始 TSV payload、页面 URL、图表 slug、最新发布标签、获取时间和 payload SHA-256
- **AND** SHALL 将 artifact 的 `export_method` 标记为 `official_clipboard`

#### Scenario: 已有官方 fixture
- **WHEN** 本地已存在对应 scope 的官方 TSV fixture
- **THEN** 系统 SHALL 直接校验、哈希、解析并入库
- **AND** SHALL 不启动浏览器，不访问 Ramp API，不重复下载

#### Scenario: 网页只允许截图而不能读取数据
- **WHEN** 页面渲染成功但 `Get the data` 没有可读取的官方 payload
- **THEN** 对应 slice SHALL 进入 `export_unreadable`
- **AND** 系统 SHALL 不发布从截图 OCR 或视觉估计得到的数值

### Requirement: 采集范围必须按 Ramp 的统计口径分成独立数据集
首版 SHALL 固定支持以下五个 source-native slices，并以独立 dataset/series identity 保存：`adoption_overall`（总体 adoption）、`adoption_overall_models`（Overall 中按 vendor 的 adoption breakdown）、`adoption_sector`（NAICS 行业）、`spend_per_employee_overall`（USD/employee/month 分位数）和 `model_market_share_overall`（Token Spend Management 连接企业的模型归因 API spend）。企业规模和地理图表可被发现但首版 SHALL 标记为 out-of-scope，不进入 L1 snapshot。不同 slice SHALL 不共享分母、样本说明或缺失值语义。

#### Scenario: 总体和行业同时采集
- **WHEN** 同一次运行成功读取 Overall 与 Sector 图表导出
- **THEN** 系统 SHALL 生成两个可独立重放的 artifact 和 dataset slice
- **AND** SHALL 不把行业行相加后替代 Overall 原始值

#### Scenario: adoption vendor breakdown
- **WHEN** Overall + Models 导出包含 Ramp Overall、Anthropic、OpenAI 等系列
- **THEN** 系统 SHALL 将各 vendor share 保存为同月独立 series
- **AND** SHALL 标注 vendor shares 可能重叠，禁止检查其必须加总为 100%

#### Scenario: 模型 API 份额
- **WHEN** Model market share 导出包含 provider、model、spend type 和 API spend share
- **THEN** 系统 SHALL 以 `token_spend_management` technology scope 和独立 cohort 保存
- **AND** SHALL 不将该值解释为全 Ramp 企业的模型采用率

### Requirement: 规范化指标必须保留原始字段、统计主体和分母
系统 SHALL 将 adoption rate、monthly change、yearly change、spend quantiles 和 model API spend share 映射到平台指标，同时保留 Provider 原始列名和值。每条 observation SHALL 记录 statistical unit、cohort/denominator、geography、reference month、technology scope、segment（vendor/NAICS/model/quantile）和 methodology text/version。`adoption_share` 的物理定义 SHALL 为相关 Ramp 企业中当月有 AI 产品或服务正向付款的企业比例，不得重命名为员工采用率、席位渗透率或工作流持续率。

#### Scenario: 采用率规范化
- **WHEN** 导出行的 Adoption rate (%) 为 56.13
- **THEN** 系统 SHALL 保存 value=56.13、unit=percent、metric=`ai.ramp.paid_business_adoption_share`
- **AND** SHALL 保存分母为 Ramp 相关企业 cohort 及“当月 positive AI transaction”判定

#### Scenario: 人均支出规范化
- **WHEN** 导出含 Median、Top 10% 和 Top 1% USD/employee/month
- **THEN** 系统 SHALL 分别保存三个 metric，unit=`usd_per_employee_month`
- **AND** SHALL 保留分位数标签而不是把 Top 1% 当成平均企业支出

### Requirement: 版本、修订和 payload 血缘必须可重放
每个 Ramp artifact SHALL 以 `page_url + chart_slug + latest_release_label + payload_sha256` 或 API 请求身份建立内容身份，并保存抓取时间、页面/文档版本、解析器版本、过滤条件和保存策略。相同 payload 重跑 SHALL 幂等；同一期间 payload 改变 SHALL 追加 vintage 而不覆盖旧值。查询 `as_of` 时 SHALL 返回当时已经可见的 payload/vintage。

#### Scenario: 页面历史值被修订
- **WHEN** 后续采集同一 chart/period 的 TSV 值发生变化
- **THEN** 系统 SHALL 追加新 artifact 和 observation vintage
- **AND** 旧 `as_of` 查询 SHALL 仍返回旧 payload 的值

#### Scenario: 反复点击导出无变化
- **WHEN** 同一发布标签和 chart 的 TSV SHA-256 未变化
- **THEN** 系统 SHALL 记录 `no_change` run
- **AND** SHALL 不新增重复 artifact、observation 或 manifest 行

### Requirement: Ramp 质量门必须识别范围、重复、逻辑和方法漂移
默认发布 SHALL 验证：百分比位于 `[0,100]`；人均支出非负且 Median ≤ Top 10% ≤ Top 1%；同一 dataset/period/segment 无未经解释的重复；历史表与 `Get the data` payload 的 header、行数和数值可对账；日期可解析且单调性按真实日期检查。vendor/model shares SHALL 不执行强制 100% 加总。Ramp 官方披露的样本规模、定义或技术范围改变时 SHALL 生成 `methodology_drift`，停止跨 regime 趋势派生。

#### Scenario: 分位数顺序异常
- **WHEN** 某月 Top 10% 支出小于 Median 或 Top 1% 小于 Top 10%
- **THEN** 该 spend slice SHALL 阻止发布并返回质量失败
- **AND** adoption slice SHALL 可独立继续发布

### Requirement: Ramp 必须支持周期探测和不覆盖的增量入库
系统 SHALL 为 `ramp_ai_index` 登记独立的十天探测调度意图（`P10D` 或等价的外部 scheduler 表达），不得把 `ai_adoption` 其他来源的频率强行改成十天。每次探测 SHALL 以图表参考期间、方法/schema 指纹和 payload SHA-256 比较库内最新 accepted 数据；报告查询 SHALL 继续只读统一结构化库，不因报告运行重复触发网页采集。

#### Scenario: 探测到新月份
- **WHEN** 五个白名单 scope 中某个 scope 的最新参考期间晚于库内最新期间，且质量门通过
- **THEN** 系统 SHALL 追加该 scope 的 artifact 与 observation
- **AND** SHALL 保留其他 scope 的异步期间，不以前一期间补齐

#### Scenario: 同期间内容没有变化
- **WHEN** scope 的参考期间和 payload SHA-256 与最近 accepted artifact 相同
- **THEN** 系统 SHALL 返回 `no_change`
- **AND** SHALL 不新增 artifact、observation 或 manifest

#### Scenario: 无头调度复用官方导出入站目录
- **WHEN** 浏览器 runner 将五个官方 TSV 写入受管 `ramp_exports/<scope>.tsv` 目录
- **THEN** Ramp adapter SHALL 优先读取这些文件完成 discovery
- **AND** SHALL 将同一轮 discovery 的 payload 直接交给 ingest，不得为入库再次打开浏览器
- **AND** SHALL 将文件内容的 artifact、observation 和 payload hash 写入统一结构化库

#### Scenario: 同期间发生修订
- **WHEN** scope 的参考期间相同但 payload SHA-256 变化
- **THEN** 系统 SHALL 追加新的 revision vintage
- **AND** SHALL 保留旧 artifact/observation，`as_of` 查询仍可重放旧值

#### Scenario: 方法或 schema 漂移
- **WHEN** header、方法文本、technology scope 或 methodology fingerprint 改变
- **THEN** 系统 SHALL 返回 `methodology_drift` 并停止该 scope 的自动 platform 发布
- **AND** SHALL 保留诊断和已有数据，不覆盖历史值

#### Scenario: 行业导出缺失一个月份
- **WHEN** Overall 有某月而 Sector 导出缺少该月
- **THEN** Sector SHALL 报告 period gap
- **AND** 系统 SHALL 不以前一月值填充，也不阻塞 Overall slice

#### Scenario: 官方口径从 50,000 变为 70,000
- **WHEN** API 文档和网页最新方法说明的 sample count 或 methodology text 不一致
- **THEN** 系统 SHALL 保留各自文档指针并标记 methodology drift/warning
- **AND** 跨版本趋势 SHALL 只有在显式批准 regime 后才可计算

### Requirement: Ramp DataProducts 必须返回独立采用、支出和模型份额结果
系统 SHALL 提供 `ramp_paid_adoption_snapshot`、`ramp_spend_per_employee_series` 和 `ramp_model_market_share_series`（名称可由实现适配，但契约语义不变）。结果 SHALL 包含 latest/as-of period、source scope、statistical unit、denominator、raw provider fields、quality、freshness、artifact/observation IDs 和 derivation versions。默认查询 SHALL 只返回 accepted/warning，隔离记录仅由审计入口读取。

#### Scenario: 查询最新企业采用
- **WHEN** 消费者请求 Ramp Overall、Overall + Models 或 Sector 的最新月度采用率
- **THEN** DataProduct SHALL 返回各 segment 的原始采用率、月变化、年份变化及其独立 lineage
- **AND** SHALL 明确结果代表 Ramp 付款网络中的企业 cohort

#### Scenario: 查询模型市场份额
- **WHEN** 消费者请求 2026-08 的模型 API spend share
- **THEN** DataProduct SHALL 返回 provider/model/spend_type/API share 及 Token Spend Management cohort 说明
- **AND** SHALL 不与企业 adoption share 连接成一个分数

### Requirement: Ramp 只能作为 L1 的补充商业化证据
Ramp 结果 SHALL 在 L1 Observer 中作为“付费企业采用与 AI 支出补充轴”单独呈现，保留 Ramp 自身分母、地区、技术范围和期间。其结果 SHALL 不改变既有 BTOS/RPS/Anthropic 三轴的数值、阈值或整体状态，除非未来另一个经审阅的 change 显式修改 claim contract。Ramp 样本偏向 Ramp 网络、高增长和技术导向客户，报告 SHALL 披露免费工具、个人账户、非 Ramp 付款造成的低估风险。

#### Scenario: Ramp 与 BTOS 同期上升
- **WHEN** Ramp paid-business adoption 和 BTOS 企业自报采用率在各自序列均上升
- **THEN** L1 packet MAY 标记为方向性印证
- **AND** SHALL 不计算两者差值或平均值，也不将 Ramp 值替代 BTOS

#### Scenario: Ramp 网络样本偏差
- **WHEN** Ramp 采用率明显高于 Census/BTOS
- **THEN** 报告 SHALL 解释网络覆盖、付费交易定义和企业构成差异
- **AND** SHALL 不直接推断美国全部企业采用率
