## Why

现有 L1“商业化能力”Observer 首版以 Frontier AI Labs 的收入水平与趋势回答收入兑现，但仍缺少独立于公司披露的真实调用需求与厂商竞争格局证据。OpenRouter 的公共路由数据可以补充观察第三方聚合渠道中的 token 用量扩张、厂商份额迁移和模型采用结构，但其覆盖范围、token 可比性和“用量不等于收入”的边界必须进入正式契约，避免把单一渠道误写成全市场 Token 经济或商业收入。

## What Changes

- 新增 OpenRouter Rankings 受治理数据源，使用官方公开 Rankings Data API 获取自 2025-01-01 起的日/周/月模型 token 用量，并保存原始响应、查询窗口、`as_of`、版本、内容哈希和完整 lineage。
- 规范化 OpenRouter 公共流量的总 token、模型 token、模型作者/厂商 token 绝对量与份额、Top-N/Others、免费路由标记及浓度派生指标；禁止将 token、请求数、用户数、收入和全市场规模互相替代。
- 明确排除 `session-cost`：其 median session cost 无法与 rankings-daily 的 model×date token 事实建立可验证的一一对应关系，因此不用于收入估算或正式观测。
- 将 OpenRouter 作为 L1“商业化能力”Observer 的第二个独立证据部分，命题为：“第三方模型路由渠道的真实调用规模是否持续扩大，需求是否在模型厂商之间形成可持续且可解释的竞争格局？”
- 提供可重放的趋势判断和可视化：公共路由总 token 周趋势与 4 周均线、厂商 token 份额 100% 堆叠、模型 token 量与逐周排名变化，以及 Top-3/Top-5/HHI 浓度趋势。
- 正式报告明确披露：只覆盖 OpenRouter、排除 private requests、上游 tokenizer 不同、每日 Top 50 之外只能归入 `Other`、免费模型及促销流量可能扭曲商业意图；OpenRouter 证据只对 Labs 收入结论做方向性印证或冲突提示，不参与收入金额计算。
- 对官网“Market Share”若其稳定公开数据出口能够提供全局 request count/share，则以独立指标、独立分母和独立 lineage 接入；否则首版仅发布官方 Rankings Data API 可复算的 token 份额，不抓取未文档化隐藏接口，也不从截图/OCR估值。
- 完成 fixture、解析、质量、增量、查询、派生、报告、可视化、lineage 与离线重放测试后直接发布到 `platform` 正式商业化报告；未通过质量门的新 revision 隔离并保留最近有效版本。

## Capabilities

### New Capabilities

- `data/openrouter-rankings`: OpenRouter 公共 Rankings 数据的官方 API 采集、规范化、增量版本、质量门、查询、派生及可重放契约。

### Modified Capabilities

- `evidence/ai-commercialization-observer`: 在现有 Frontier Labs 收入证据之后加入 OpenRouter 路由用量与竞争格局证据、独立状态、文字结论、可视化和方法限制，不改变收入证据自身的口径。

## Impact

- 数据层：新增 OpenRouter source adapter、原始 artifact、模型/厂商实体映射、observation series、DataProducts、调度与 provenance/manifest 支持。
- Evidence 层：扩展 `ai_commercialization` claim packet、compact Agent context、中文 Markdown、CSV/JSON、PNG 与 sidecar；保持既有 `frontier_labs_revenue_scale_and_trend` 子结论可独立运行。
- 配置：更新 `ai_hardware/L1_app` 的商业化 Observer 数据依赖和正式报告段落，但不修改生产化与应用扩散 Observer，也不进入 Chain、评分、组合、风控或交易 workflow。
- 外部依赖：官方接口 `GET https://openrouter.ai/api/v1/datasets/rankings-daily` 需要有效 OpenRouter API key；凭据只通过安全运行时配置注入。采集器遵守官方 30 请求/分钟、500 请求/日的限制和 CC BY 4.0 署名要求。
- 运维：数据从 2025-01-01 起体量较小，首次分窗回填，之后每日重叠窗口增量；报告仅从统一结构化库读取，不在报告运行时访问 OpenRouter。
- 兼容性：无破坏性接口变更；OpenRouter 不可用时收入报告继续生成并标记 `openrouter_unavailable`。
