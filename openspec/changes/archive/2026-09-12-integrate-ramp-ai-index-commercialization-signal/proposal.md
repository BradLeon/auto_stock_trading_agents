## Why

现有 L1 AI 应用层 Observer 已有 BTOS 企业广度、RPS 员工使用和 Anthropic 任务生产化三条互补证据轴，但缺少基于真实企业支付行为的商业化采用信号。Ramp AI Index 使用其企业卡与 Bill Pay 的匿名聚合交易识别“企业在当月为 AI 产品或服务发生正向付款”，能够补充调查自报和单一模型平台遥测无法覆盖的企业购买行为；现在网页已提供无需登录的官方图表导出，适合先以公开、可复放的 source-native artifact 接入。

## What Changes

- 新增 Ramp AI Index 的受治理数据源，首版固定采集 `adoption_overall`、`adoption_overall_models`、`adoption_sector` 三个采用范围；保留网页的历史表、月度变化和年份变化字段。
- 新增 Ramp AI Index 的补充支出信号，固定采集 `spend_per_employee_overall`（中位数、Top 10%、Top 1%）及 `model_market_share_overall`（连接 Token Spend Management 企业的模型归因 API spend），与企业 adoption 使用不同 dataset/scope，不互相补值。
- 以 Ramp 网页每个图表的官方 `Get the data` 控件作为首选公开出口：验证结果为可复制的制表符分隔文本（页面提示 `Copied data to clipboard.`），不得以截图作为事实数据源；若 Ramp 后续提供稳定文件 URL，允许切换为同一 source identity 下的直接下载。
- 首版只使用无需 API key 的公开网页 `Get the data` 导出或已经保存的 source-native fixture；不实现、不调度 API/MCP 回退和主动 API discovery。Ramp API 需要企业授权且当前不可用，不进入本次运行链路；未来若需要另开变更。
- 不把 Ramp 百分比与 BTOS、RPS、Anthropic 百分比合成单一渗透率；在 L1 packet 中作为“付费企业采用/AI 支出”补充轴或独立观察项，显式披露统计主体、分母、覆盖偏差和不同期间。
- 固定第四个 L1 追踪命题：**“AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？”** Ramp 拥有独立 claim id、方法卡、趋势状态和文字结论，不改变前三轴 overall judgement；企业规模在首版五个 scope 中标记为待观测缺口。
- 五个已确认 scope 必须一一对应五张历史图表：`adoption_overall`、`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall`、`model_market_share_overall`；只有最新月时必须明确报告 `insufficient_history`，不得伪造趋势。
- 增加 Ramp-specific discovery、导出解析、哈希、release/vintage、质量门、freshness、lineage、Agent context、中文方法卡和嵌入式图表；失败时只隔离 Ramp，不阻塞其他 L1 来源。
- 将 Ramp 公司/数据产品背景作为来源注释而非 Observer 事实：Ramp 是企业财务运营平台，官方披露 70,000+ 客户、超过 $1B annualized revenue（2026-06-01 口径，未审计的公司自报），收入主要来自卡 interchange、Ramp Plus 订阅及支付服务；这些不作为 AI adoption 观测值。

## Capabilities

### New Capabilities

- `data/ramp-ai-index`: Ramp AI Index 网页导出与可选 API 的发现、采集、规范化、版本、质量、可比性和查询契约。

### Modified Capabilities

- `data/structured-ingestion`: 增加官方图表剪贴板/文件导出作为 source-native artifact、无鉴权回退、导出 payload 哈希和可选 provisioned API 的访问状态；保持多来源精确 lineage 与隔离失败。
- `data/structured-query`: 增加 Ramp adoption/spend 独立 series、source scope/denominator、跨源不可硬融合的 evidence bundle 和 as-of/context 契约。
- `evidence/ai-production-penetration-observer`: 将 Ramp 作为 L1 的补充商业化采用证据，不改写既有三轴定义；报告 Ramp 采用广度、行业截面、AI 支出和模型份额，并明确其与 BTOS/RPS/Anthropic 不同主体和分母。

## Impact

- 数据与配置：新增 `ramp_ai_index` source、`ramp_ai_adoption`/`ramp_ai_spend` datasets、metrics、source group 和 discovery cadence；不修改既有 BTOS、RPS 或 Anthropic observation。
- 采集运行时：优先接收已经落盘的官方 TSV fixture；没有 fixture 时才由受控浏览器读取可见图表和系统剪贴板。首版不调用 Ramp API、不请求公司邮箱注册、不实现 API 限速/重试。
- 报告与查询：新增 Ramp 专属表格、趋势/行业/支出图表、sidecar、方法卡段落和 lineage；所有图表数据来自同一导出 artifact，禁止把网页截图或营销结论当作原始观测。
- 运营与治理：公开网页导出或固定 fixture 可在无 key 环境运行；没有两者时明确返回 `export_unreadable`，不得伪造 `no_change`。Ramp 样本偏向其高增长、技术导向客户，结论只代表 Ramp 网络中的付费企业行为。
- 兼容性：仅影响 `ai_hardware/L1_app` Observer 与其 DataProducts；其他 layer/sector Observer、Chain、PEAD、Chief、组合、风控和交易 workflow 不被调用或修改。
