## 1. 契约、配置与样本

- [x] 1.1 在 source/metric/series 配置中注册 `openrouter_rankings`、公共路由 token、模型/作者 token、token share、Top-N、Top-3/Top-5 和 HHI，并锁定单位、统计范围与 methodology 文本。
- [x] 1.2 在商业化 Observer 配置中注册 `openrouter_routed_usage_and_competition` 子命题及其 DataProduct 依赖，保持 Frontier Labs 收入子命题和生产化 Observer 不变。
- [x] 1.3 增加 `OPENROUTER_API_KEY` 安全运行时配置、缺失凭据状态、官方端点、UTC 日期、14 日重叠窗口、分窗大小、重试和 30/min、500/day 请求预算。
- [x] 1.4 保存脱敏的官方响应 fixture，覆盖多日 Top 50、`Other`、新模型、未知作者、free route、缺日、重复、修订和 schema drift。
- [x] 1.5 核验官网 request-share 是否存在稳定文档化全局数据出口；若不存在，记录首版只发布 token share 的决策并确保没有 DOM/隐藏接口回退。

## 2. 官方 API 采集与原始 Artifact

- [x] 2.1 实现 OpenRouter Rankings Data API adapter，支持 `start_date`、`end_date`、`period=day`、Bearer 鉴权、超时、受限重试和结构化 source 状态。
- [x] 2.2 将原始响应按请求日期窗保存为 immutable artifact，记录 URL、参数、`meta.as_of`、version、获取时间、payload SHA-256、license/citation、parser version 和 credential redaction。
- [x] 2.3 实现首次 2025-01-01 起的分窗历史回填、每日最近 14 个完整 UTC 日增量，以及相同 payload 的 `no_change` 幂等路径。
- [x] 2.4 实现 401/403、429、网络失败、不合法 JSON 和限流预算耗尽的故障隔离，保证不删除或覆盖最近 accepted vintage。
- [x] 2.5 增加采集单元测试，验证请求边界、完整 UTC 日、退避、限流、脱敏、artifact identity、内容哈希和离线 fixture 重放。

## 3. 规范化、实体映射与 Vintage

- [x] 3.1 将 source rows 解析为日度 model observations，保留 reference date、model slug/permalink、display name、author、rank、token、`is_other`、`is_free_route`、quality 和 lineage。
- [x] 3.2 建立版本化 model-author registry 与稳定作者颜色/别名，未知模型保留 source identity 并进入 `unknown_author`，不得猜测或丢弃 token。
- [x] 3.3 将官方长尾保存为 `unattributed_other`，禁止逆向分配给作者；实现 Top 50、unknown 和 Other 的明确 coverage 字段。
- [x] 3.4 建立 source/date/model/payload identity 的幂等键和 revision vintage，同日变化追加版本并支持 current 与历史 `as_of` 查询。
- [x] 3.5 增加 parser 与 persistence 测试，覆盖模型重命名、作者 alias、Other、free suffix、同日修订、重复 payload 和离线 replay。

## 4. 质量门与方法漂移

- [x] 4.1 实现日期可解析、token 非负整数、每日 model identity 唯一、排名合法、Other 唯一、Top-N 数量和日期连续性检查。
- [x] 4.2 实现每日总量守恒与份额合计检查，确保 Top 50 + Other 可复算总 token、作者集合 + unknown + Other 在容差内为 100%。
- [x] 4.3 实现 schema/version、public/private 范围、token 定义和字段指纹的 methodology drift 检测及 regime boundary。
- [x] 4.4 将失败日期或 vintage 隔离为可审计记录，允许其他日期继续发布，并保证趋势和图表不读取 quarantine observations。
- [x] 4.5 增加质量测试，覆盖负值、重复、缺 Other、守恒失败、份额超界、schema drift、未知作者和部分日期隔离。

## 5. DataProducts 与派生指标

- [x] 5.1 实现 `openrouter_token_volume_series`，从 accepted 日度事实派生完整 UTC 周/月的总 token、模型 token、作者绝对 token、unknown 和 Other。
- [x] 5.2 实现 `openrouter_author_share_series`，返回作者绝对量、token share、Other、完整周、coverage、quality、freshness、regime 和 lineage。
- [x] 5.3 实现 `openrouter_model_leaderboard` 与 `openrouter_model_ranking_series`，分别返回最新完整周榜单及按累计 token 选取的模型 token/rank 时序、作者和 free-route 标记。
- [x] 5.4 实现 `openrouter_concentration_series`，输出作者 Top-3、Top-5 与 HHI，并记录 unknown/Other 对归属和浓度解释的限制。
- [x] 5.5 实现最新 4 个完整周相对前 4 周变化、4 周移动平均、至少 8 周历史门和 `expanding/stable/contracting/mixed/insufficient_history` 规则。
- [x] 5.6 为五个 DataProducts 增加 latest、date range、as-of、revision、完整/未完整周、绝对量增长但份额下降和跨-regime 禁算测试。

## 6. 商业化 Observer 与 Agent Context

- [x] 6.1 扩展商业化 claim packet，使收入与 OpenRouter 子命题分别携带状态、期间、分母、facts、warnings、observations 和 lineage，且不改变收入原始结论。
- [x] 6.2 实现收入 × 路由用量方向性矩阵，覆盖同步扩大、方向冲突、仅收入、仅路由和均不可用；禁止金额-token 换算和不透明综合分数。
- [x] 6.3 生成 OpenRouter 文字化摘要，分别解释总量、4 周变化、头部作者绝对量与份额、Top 模型、集中度、Other 和方法限制。
- [x] 6.4 扩展 compact Agent context，保留结论所需的最新完整周、4 周变化、Top 作者/模型、浓度、Other、manifest 和 lineage pointer，不平铺逐日明细。
- [x] 6.5 增加 Observer 测试，验证 `openrouter_unavailable/stale` 不影响收入段、OpenRouter 不进入生产化结论、跨证据只做方向性综合。

## 7. 四类可视化与正式报告

- [x] 7.1 绘制“OpenRouter 公共路由 Token 周度规模”完整周柱形 + 4 周均线，使用 B/T 单位、稀疏日期刻度和未完成周排除注释。
- [x] 7.2 绘制最近 12 周 Top 8 作者 + 其他作者 + 官方 Other 的绝对 token 堆叠图，并稳定作者颜色和 Top-N 选择 sidecar。
- [x] 7.3 使用与绝对量图相同作者集合、颜色和期间绘制 100% token 份额堆叠图，确保各期在容差内合计 100%。
- [x] 7.4 绘制 2026-01-01 起的模型 token 量与排名时序图：按该窗口累计 token 选择稳定模型集合，合并发布日期后缀，堆叠完整周 token，并显示逐周相对全部模型的排名变化。
- [x] 7.5 绘制 Top-3、Top-5 与 HHI 集中度趋势，明确 Other/unknown 对 concentration 的限制。
- [x] 7.6 为四类图生成 CSV、JSON、PNG 和 sidecar，核对 observation set、rows hash、claim version、manifest、来源 `as_of` 与官方 CC BY 4.0 citation。
- [x] 7.7 将报告结构调整为总命题、收入证据、OpenRouter 证据、跨证据综合、方法/公式/限制，且每张图前有结论性文字、末尾有指标注解。
- [x] 7.8 增加中文字体发现、glyph 覆盖和图片渲染 QA；乱码或图表失败时降级为同源表格，不发布缺字图片。

## 8. 调度、平台发布与回归验收

- [x] 8.1 注册 OpenRouter 独立每日调度和 freshness 阈值，验证采集只发生在 source job、一次入库后报告和 Agent context 全部离线复用。
- [x] 8.2 使用 fixture 执行采集、解析、质量、persistence、query、derive、Observer、四图、lineage、manifest replay 和故障隔离端到端测试。
- [x] 8.3 配置真实 API key 后执行 live smoke、2025-01-01 起历史回填和源站对账，记录日期覆盖、总量守恒、Other 比例、未知作者和 API `as_of`。
- [x] 8.4 生成可供人工审阅的 L1 商业化正式报告，检查数据价值表述不将 OpenRouter 写成全市场、收入或 request share。
- [x] 8.5 运行 Frontier Labs 收入、Ramp/BTOS/RPS/Anthropic 生产化 Observer、其他 layer、CLI、离线 replay 和确定性 rows-hash 回归测试；变更相关回归通过，记录与本 change 无关的环境缺依赖/既有失败。
- [x] 8.6 全部变更相关验收通过后将 OpenRouter source 和报告子段直接切换为 `platform`，确认默认配置无需 shadow promotion。
- [x] 8.7 验证回滚流程可停用 OpenRouter 子段而保留历史 artifact/observations、最近收入报告和生产化 Observer，并记录运维说明。
