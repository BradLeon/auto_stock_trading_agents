# data/ramp-ai-index Specification

## Purpose

定义 Ramp AI Index 公开企业支付数据的受治理采集、版本、指标和查询契约，为 L1 AI 应用层提供独立的付费企业采用与 AI 支出证据，而不把 Ramp 网络样本误写成全体美国企业采用率。

## Requirements

### Requirement: Ramp 数据源必须以官方公开出口发现并记录访问状态
系统 SHALL 以 Ramp AI Index 页面和 FRED 上的 Ramp AI Index 发布作为来源注册依据。FRED 已发布的序列 SHALL 以 FRED 公开 CSV 为唯一采集出口；仅 FRED 未覆盖的增量 SHALL 由受控浏览器读取页面可见的 `Get the data` 控件，也可读取不超过一个探测周期的已落盘官方导出。网页导出的制表符分隔文本和 FRED CSV SHALL 各自作为 source-native payload 保存。若 fixture、控件或剪贴板不可读，网页部分 SHALL 返回 `export_unreadable`，SHALL NOT 以截图、搜索摘要、人工抄录或 Ramp API/MCP 替代。

#### Scenario: 网页导出成功
- **WHEN** 采集器打开 Ramp AI Index 的一个已注册图表，点击可见 `Get the data` 并读取该控件写入剪贴板的文本
- **THEN** 系统 SHALL 保存原始 TSV payload、页面 URL、图表 slug、最新发布标签、获取时间和 payload SHA-256
- **AND** SHALL 将 artifact 的 `export_method` 标记为 `official_clipboard`

#### Scenario: 已有官方 fixture
- **WHEN** 本地已存在对应 scope 的官方 TSV fixture，且其导出时间在一个探测周期内
- **THEN** 系统 SHALL 直接校验、哈希、解析并入库
- **AND** SHALL 不启动浏览器，不访问 Ramp API，不重复下载

#### Scenario: 网页只允许截图而不能读取数据
- **WHEN** 页面渲染成功但 `Get the data` 没有可读取的官方 payload
- **THEN** 对应 slice SHALL 进入 `export_unreadable`
- **AND** 系统 SHALL 不发布从截图 OCR 或视觉估计得到的数值

#### Scenario: FRED 已发布的序列
- **WHEN** 某条 Ramp 序列在 FRED 的 Ramp AI Index 发布中存在
- **THEN** 系统 SHALL 从 FRED 公开 CSV 采集该序列，并保存 FRED series id、序列元数据、最后更新时间和 payload SHA-256
- **AND** SHALL 不需要浏览器即可完成该序列的发现与入库

### Requirement: 采集范围必须按 Ramp 的统计口径分成独立数据集
v2 SHALL 固定支持以下 source-native slices，并以独立 dataset/series identity 保存：`adoption_overall`（总体 adoption）、`adoption_overall_models`（按供应商的 adoption）、`adoption_sector`（NAICS 行业）、`adoption_business_size`（企业规模）、`spend_per_employee_overall`（USD/employee/month 分位数）、`token_spend_share_by_maker` 与 `token_spend_share_by_model`（Token Spend Management 连接企业的周度 token 花费份额）。地理、Token volume、Token prices 与 AI share of business spend 图表可被发现但 SHALL 标记为 out-of-scope。不同 slice SHALL 不共享分母、样本说明或缺失值语义。

#### Scenario: 总体和行业同时采集
- **WHEN** 同一周期内总体与行业 adoption 都有新数据
- **THEN** 系统 SHALL 生成可独立重放的 artifact 和 dataset slice
- **AND** SHALL 不把行业行相加后替代 Overall 原始值

#### Scenario: adoption vendor breakdown
- **WHEN** 供应商 adoption 数据包含 Anthropic、OpenAI 等系列
- **THEN** 系统 SHALL 将各 vendor share 保存为同月独立 series
- **AND** SHALL 标注 vendor shares 可能重叠，禁止检查其必须加总为 100%

#### Scenario: 模型 API 份额
- **WHEN** 消费者查询 v1 的 `model_market_share_overall`（按月 API spend share）
- **THEN** 系统 SHALL 只返回 v1 口径下已入库的历史记录，并标明该 scope 已停止更新
- **AND** SHALL 不将该值解释为全 Ramp 企业的模型采用率，也不与 v2 token 花费份额拼接成一条序列

#### Scenario: 周度 token 花费份额
- **WHEN** 网页 Token spend 图表以 Shares 模式导出按厂商或按模型的周度份额
- **THEN** 系统 SHALL 以 `token_spend_management` technology scope、周度期间和独立 cohort 保存为 `token_spend_share_by_maker` 或 `token_spend_share_by_model`
- **AND** SHALL 不把 Volumes 模式的指数值当作份额入库

#### Scenario: 企业规模来自 FRED
- **WHEN** FRED 发布小型、中型、大型企业三档 Ramp adoption
- **THEN** 系统 SHALL 以 `adoption_business_size` 保存三档独立 series
- **AND** SHALL 不用 Overall 或行业值替代缺失的规模档

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
系统 SHALL 为 Ramp 登记两个相互独立的调度：FRED 部分每周检查一次，网页增量部分每十天探测一次；不得把 `ai_adoption` 其他来源的频率强行改成十天。每次探测 SHALL 以参考期间、方法/schema 指纹和 payload SHA-256 比较库内最新 accepted 数据；报告查询 SHALL 继续只读统一结构化库，不因报告运行重复触发网页采集或 FRED 请求。

#### Scenario: 探测到新月份
- **WHEN** 某个 scope 的最新参考期间晚于库内最新期间，且质量门通过
- **THEN** 系统 SHALL 追加该 scope 的 artifact 与 observation
- **AND** SHALL 保留其他 scope 的异步期间，不以前一期间补齐

#### Scenario: 同期间内容没有变化
- **WHEN** scope 的参考期间和 payload SHA-256 与最近 accepted artifact 相同，且该 payload 是本次探测实际取得的
- **THEN** 系统 SHALL 返回 `no_change`
- **AND** SHALL 不新增 artifact、observation 或 manifest

#### Scenario: 无头调度复用官方导出入站目录
- **WHEN** 浏览器 runner 将官方 TSV 写入受管 `ramp_exports/<scope>.tsv` 目录
- **THEN** Ramp adapter SHALL 读取这些文件完成本轮 discovery
- **AND** SHALL 将同一轮 discovery 的 payload 直接交给 ingest，不得为入库再次打开浏览器
- **AND** SHALL 将文件内容的 artifact、observation 和 payload hash 写入统一结构化库

#### Scenario: 同期间发生修订
- **WHEN** scope 的参考期间相同但 payload SHA-256 变化
- **THEN** 系统 SHALL 追加新的 revision vintage
- **AND** SHALL 保留旧 artifact/observation，`as_of` 查询仍可重放旧值

#### Scenario: 方法或 schema 漂移
- **WHEN** header、方法文本、technology scope 或 methodology fingerprint 改变，且新口径尚未登记批准
- **THEN** 系统 SHALL 返回 `methodology_drift` 并停止该 scope 的自动 platform 发布
- **AND** SHALL 保留诊断和已有数据，不覆盖历史值

#### Scenario: 行业导出缺失一个月份
- **WHEN** Overall 有某月而 Sector 缺少该月
- **THEN** Sector SHALL 报告 period gap
- **AND** 系统 SHALL 不以前一月值填充，也不阻塞 Overall slice

#### Scenario: 官方口径从 50,000 变为 70,000
- **WHEN** API 文档和网页最新方法说明的 sample count 或 methodology text 不一致
- **THEN** 系统 SHALL 保留各自文档指针并标记 methodology drift/warning
- **AND** 跨版本趋势 SHALL 只有在显式批准 regime 后才可计算

#### Scenario: 入站文件过期
- **WHEN** 网页 runner 未能取得新导出，而入站目录中的文件早于一个探测周期
- **THEN** 系统 SHALL 不把旧文件当作本轮上游结果，也 SHALL 不返回 `no_change`
- **AND** 网页部分 SHALL 以 `export_unreadable` 记录失败原因

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

### Requirement: 网页 runner 只采 FRED 未覆盖的增量
网页 runner 取得的导出中，凡 FRED 已发布的同一序列（同 scope、同 segment），系统 SHALL NOT 入库，只用于与 FRED 值交叉核对；其余增量 SHALL 正常进入质量门与发布。FRED 覆盖范围 SHALL 由配置的 FRED 序列清单决定，FRED 新增序列后，网页对应部分自动停止入库。

#### Scenario: 行业部分由两处补齐
- **WHEN** FRED 发布 7 个行业，网页 Sector 导出含 20 个行业
- **THEN** 系统 SHALL 从 FRED 入库这 7 个行业，从网页只入库其余 13 个
- **AND** 同一行业 SHALL 只有一个来源的 v2 序列

#### Scenario: 交叉核对发现差异
- **WHEN** 网页导出与 FRED 对同一序列、同一期间的值相差超过 0.01 个百分点
- **THEN** 系统 SHALL 在网页运行诊断中记录 `fred_page_mismatch` warning，并列出序列与期间
- **AND** SHALL 不改写 FRED 已入库的值

### Requirement: FRED 部分与网页部分的失败相互隔离
FRED 部分与网页部分 SHALL 作为独立来源运行，各自拥有调度、刷新账本记录、重试和新鲜度；一方失败 SHALL NOT 阻止、延迟或回滚另一方的发现与发布。

#### Scenario: runner 被网站拦截
- **WHEN** 网页 runner 因反自动化拦截、超时、浏览器未安装或控件消失而失败
- **THEN** 网页部分 SHALL 记录失败状态和具体原因
- **AND** FRED 部分 SHALL 按其自身调度正常更新总体、供应商、行业与规模 adoption

#### Scenario: 网页部分长期无新数据
- **WHEN** 网页部分最近一次 accepted 数据的采集时间早于 35 天前
- **THEN** 系统 SHALL 报告 `export_stale` 并告警
- **AND** SHALL 不影响 FRED 部分的新鲜度判定

#### Scenario: FRED 不可达
- **WHEN** FRED 请求失败
- **THEN** FRED 部分 SHALL 记录 `source_unreachable` 并按重试策略重试
- **AND** 网页 runner SHALL NOT 因此代采 FRED 已覆盖的序列

### Requirement: Ramp v2 口径独立登记且 v1 历史只读保留
系统 SHALL 将 2026-11 起的 Ramp 页面与 FRED 数据登记为 `ramp_ai_index_v2` methodology regime，并以独立 series 保存。v1 已入库数据 SHALL 原样保留、不删除、不改写；默认查询与 L1 Observer SHALL 只读 v2，v1 仅在显式指定 regime 时返回。v1 与 v2 SHALL NOT 拼接成同一趋势。

#### Scenario: 默认查询总体采用率
- **WHEN** 消费者未指定 regime 查询 `adoption_overall`
- **THEN** 系统 SHALL 只返回 v2 序列，并在结果中标明 `methodology_regime=ramp_ai_index_v2`
- **AND** 结果 SHALL 不包含 v1 期间值

#### Scenario: 审计 v1 历史
- **WHEN** 审计入口显式请求 `ramp_ai_index_v1`
- **THEN** 系统 SHALL 返回 2026-09-11 及以前采集的 v1 记录及其原始 lineage
- **AND** SHALL 标明该 regime 已停止更新
