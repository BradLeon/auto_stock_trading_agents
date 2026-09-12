## 1. Catalog、scope 与访问策略

- [x] 1.1 注册 `ramp_ai_index` source、`ramp_ai_adoption`/`ramp_ai_spend` datasets、metrics、`ai_adoption` source group、release cadence、保存约束和 `geographies` 首版 out-of-scope 状态。
- [x] 1.2 增加 Ramp source scope 配置，明确 `adoption_overall`、`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall`、`model_market_share_overall` 的统计主体、分母、technology scope 与独立 series identity；企业规模和地理维度标记为首版 out-of-scope。
- [x] 1.3 实现访问状态模型，区分网页/fixture 成功、`no_change`、`export_unreadable`、`source_unreachable`、`partial` 和 `methodology_drift`；无本地 fixture 且网页不可读时不得伪造无变化。
- [x] 1.4 更新 source checklist、数据字典和运维文档，记录 fixture 优先、浏览器补采和首版不接入 API/MCP 的边界。

## 2. 官方网页导出采集器

- [x] 2.1 实现基于可见页面/aria 控件的 Ramp AI Index browser adapter，仅点击注册的 chart/accordion 和 `Get the data`，禁止猜测隐藏 URL 或使用截图 OCR。
- [x] 2.2 读取 clipboard TSV，保留原始字节和换行，校验目标 header、最小 payload 大小、日期格式和数值列；记录 `official_clipboard` export method。
- [x] 2.3 为每个 scope 建立 `page_url + chart_slug + latest_release_label + payload_sha256` artifact identity，保存控件文本、页面最新发布标签、抓取时间、schema fingerprint 和 parser version。
- [x] 2.4 增加网页 UI contract fixtures，覆盖 Overall、Overall + Models、Sector、Spend per employee、Model market share；Business size 与 Geographies 仅验证可发现但不发布。
- [x] 2.5 实现 clipboard 不可读、控件消失、页面超时、空 payload 和损坏 TSV 的明确诊断与 slice 隔离测试。

## 3. API/MCP 路径（首版删除）

- [x] 3.0 已按用户授权从首版范围删除 API/MCP 回退、API discovery、凭据注入和主动 API 拉取；适配器默认只接受本地官方 fixture 或网页 `Get the data` 导出，未来如需授权 API 另开独立变更。

## 4. 解析、规范化与质量门

- [x] 4.1 解析总体/vendor adoption、NAICS sector 的月度行，规范化为 `paid_business_adoption_share`/`vendor_adoption_share`，保留 monthly/yearly change 原始列；企业规模行首版不发布。
- [x] 4.2 解析 Median/Top10/Top1 AI spend per employee，规范化单位 `usd_per_employee_month` 和 quantile 维度；解析 provider/model/spend_type/API spend share。
- [x] 4.3 为每条 observation 保存 statistical unit、denominator、geography、technology scope、reference period、methodology regime、raw fields、artifact 与 fetched/known time。
- [x] 4.4 实现百分比范围、非负支出、分位数顺序、日期/period gap、重复键、历史表—TSV 逐行对账和未知字段 quarantine 质量门。
- [x] 4.5 实现 vendor share 可重叠（不强制加总 100%）、model share cohort 独立，以及网页方法文本、样本描述或技术范围变化的 warning/regime 规则。
- [x] 4.6 实现内容幂等、同期间修订 vintage、`as_of` 选择、slice 级 partial 发布和不前向填充测试。

## 5. DataProducts、查询与可复现快照

- [x] 5.1 实现 Ramp paid adoption snapshot，支持 `adoption_overall`/`adoption_overall_models`/`adoption_sector`、latest、历史、as-of、quality、freshness、period gap 和 source scope。
- [x] 5.2 实现 spend per employee 与 model market share series，返回 quantile/provider/model/spend type、独立 cohort、原始字段和完整 lineage。
- [x] 5.3 确保 DataProducts、SQL、Pandas 和 CSV/JSON 导出在相同过滤条件下返回相同 observation IDs、值、期间、质量和 source scope。
- [x] 5.4 为跨源请求生成 comparability matrix，只允许方向性/上下文并列；禁止 Ramp 与 BTOS/RPS/Anthropic 数值融合、补值或默认相减。
- [x] 5.5 将 chart slug、rows hash、artifact/observation IDs、derivation version、manifest ID 固定到离线 snapshot，验证重放不访问 Ramp 页面或 API。

## 6. L1 Observer、Agent context 与报告

- [x] 6.1 在 `ai_hardware/L1_app` 的现有三轴 packet 中增加 `supplemental_signals.ramp_paid_adoption`，保持既有 claim、阈值和整体状态不变。
- [x] 6.2 增加 Ramp 专属中文方法卡段落，披露 Ramp 网络企业 cohort、positive paid transaction 定义、NAICS 分组、Token Spend Management 独立 cohort、免费工具/个人账户漏计和客户选择偏差，并明确企业规模与地理维度未纳入首版。
- [x] 6.3 更新 compact/review Agent context，包含 Ramp 最新期间、scope、值、变化、质量/access warning、分母和 lineage pointer，但不平铺原始交易或无关 artifact。
- [x] 6.4 生成 Ramp adoption 行业截面、spend per employee 和 model market share 图表；正文、表格、PNG、CSV/JSON、sidecar 共用同一 rows hash 和 manifest。
- [x] 6.5 报告 Ramp 与 BTOS/RPS/Anthropic 同向时仅写“方向性印证”，样本/口径冲突时说明原因，不把 Ramp 值写成全美企业或员工采用率。
- [x] 6.6 固定 Ramp 第四个 L1 追踪命题 `ai_paid_business_adoption_diffusion`，在报告中给出独立状态、文字结论和企业规模 `not_published` 缺口；五个白名单 scope 各自产生历史图表、表格和 sidecar。

## 7. 测试、隔离验收与运行发布

- [x] 7.1 用当前公开页面 fixture 验收 2026-08 Overall=56.13%、vendor、Sector、Spend 和 Model share TSV 的 header、行数/字段与单位，并确认 Business size 与 Geographies 未进入首版发布范围。
- [x] 7.2 在隔离 SQLite/artifact/output 目录完成 `validate-source → release-check → ingest → quality → availability → DataProducts → evidence layer → lineage → manifest replay`。
- [x] 7.3 注入网页控件改版、剪贴板权限拒绝、空/损坏 TSV、API 未授权、429/504、重复 payload、历史修订、methodology drift 和单 slice 失败，核对状态机与失败隔离。
- [x] 7.4 做 L1 v2 shadow 对账：未提供 Ramp 或 Ramp slice 失败时三轴结论、BTOS/RPS/Anthropic observation 与报告保持不变；验证其他 layer/sector、Chain、PEAD、Chief 和交易 workflow 未被调用或修改。
- [x] 7.5 运行结构化数据、Evidence、CLI、浏览器采集、可视化和 OpenSpec strict validation；记录 payload hash、artifact 数量、context 字符预算、图表 sidecar 和性能。
- [x] 7.6 通过显式 release-check/publish 后再启用 Ramp supplement；停用或回滚 Ramp 时保留其 artifacts/observations/vintages，三轴 Observer 继续可用。

## 8. 周期探测、增量入库与平台发布

- [x] 8.1 在 `config/data/schedules.yaml` 登记 Ramp 专属 `P10D` 探测任务，命令只选择 `ramp_ai_index`，不改变 BTOS/RPS/Anthropic/ONS 的现有频率；同步更新运维文档。
- [x] 8.2 让 Ramp 探测按参考期间、methodology/schema 指纹和 payload hash 判断 `no_change`、新期间、同期间 revision 和 methodology drift；报告路径不得触发网页采集；同一轮 discovery 的官方 payload 直接交给 ingest，避免重复打开浏览器。
- [x] 8.3 增加周期探测与增量入库测试：重复 TSV 幂等、新月份追加、同期间变更保留 vintage、单 scope 失败隔离、方法漂移阻断自动发布；补充受管 TSV 入站目录与单轮 payload handoff 测试。
- [x] 8.4 使用当前五个官方 TSV 做一次受控 probe/quality/availability 验收，确认 5/5 scope 可用且重复运行返回 `no_change`；两数据集质量门均通过、adoption 数据集可查询。
- [x] 8.5 验收通过后，通过结构化 release overlay 将 `ramp_ai_index` source 显式发布为 `platform`；验证 L1 三轴结论和 Ramp 补充报告均保持不变，并记录可回滚路径。
