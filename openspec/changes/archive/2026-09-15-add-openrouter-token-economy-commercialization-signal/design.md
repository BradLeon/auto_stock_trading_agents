## Context

现有 `evidence/ai-commercialization-observer` 已以 Frontier Labs 收入作为首个证据部分，并明确收入增长仍不能验证留存、单位经济或完整商业模式。OpenRouter 新数据的价值在于提供公司披露之外、按日更新的真实路由调用遥测；约束在于它只代表 OpenRouter 公共流量，官方 Rankings Data API 首版只提供每日 Top 50 模型与 `Other` 的 token，不等于请求数、付费金额或全市场规模。

OpenRouter 官方页面说明 Rankings 按 prompt+completion token 排名、日桶使用 UTC、private requests 被排除，且不同上游 provider tokenizer 不完全可比。官方数据端点需要有效 API key，历史自 2025-01-01 起，限制为每 key 每分钟 30 次、每账户每天 500 次，数据按 CC BY 4.0 提供。

## Goals / Non-Goals

**Goals:**

- 构建一次回填、日常增量、离线复用和 as-of 重放的 OpenRouter 官方数据链路。
- 把“路由渠道用量规模”和“厂商竞争结构”作为收入之外的独立商业化证据。
- 模型图同时呈现绝对 token 与逐周排名；作者图保留 token 份额，避免报告图重复表达同一信息。
- 以固定四类图表交付对投资研究有用、可读、可审计的视觉证据。
- 保持故障隔离：OpenRouter 失败不影响 Frontier Labs 收入或生产化 Observer。

**Non-Goals:**

- 不把 OpenRouter 外推为全球模型 API 市场、企业生产流量或 Frontier Labs 总收入。
- 不用 token × 当前标价估算历史 GMV、ARR、收入或毛利。
- 不把 `session-cost` 与日度 token 量相乘；该端点缺少可与 token 行一一对应的 session/token 分母，首版不纳入观测。
- 不抓取截图/OCR、页面 tooltip 或未文档化内部接口作为数值来源。
- 不在首版接入按任务、语言、国家、应用或自有账户 Analytics 的采样数据。
- 不把 OpenRouter、Ramp 或 Frontier Labs 收入合成为单一商业化分数。

## Decisions

### 1. 官方 Rankings Data API 是唯一首版数值入口

使用 `GET https://openrouter.ai/api/v1/datasets/rankings-daily`，通过 `start_date`、`end_date` 和 `period` 获取数据。首次回填采用受限日期窗，稳定运行后每天请求最近 14 个完整 UTC 日，以捕获迟到修订。原始 JSON 先成为 immutable artifact，再由 adapter 解析入库；报告只读结构化 DataProducts。

选择 API 而非浏览器页面，是因为 API 提供明确 schema、`as_of`、历史范围、许可和限流，可稳定测试并保留 lineage。浏览器截图只作为图形设计参考。

### 2. 以日度事实为基础，完整周是正式判断周期

物理 observation 粒度为 `date × model_permaslug × vintage`。周/月由平台从 accepted 日度事实派生，不混用服务端不同 period 返回形成多套事实。正式结论使用完整 UTC 周：最新周未结束时只进入审计表，避免周内数据被误判为暴跌。

规模趋势的固定输出为：最新完整周总 token、4 周移动平均、最近 4 个完整周合计相对此前 4 周的变化、可比周数和 regime。至少 8 个完整周才允许给出扩大/稳定/收缩判断。

### 3. 总量可完整复算，厂商份额必须保留 Other

每日总量由 Top 50 模型与官方 `Other` 相加，可描述为 OpenRouter 公共路由总 token。模型作者从受治理 model-author registry 映射；无法归属或位于 Top 50 之外的流量保留为 `unknown_author`/`unattributed_other`，绝不按历史比例分摊。

因此首版厂商份额名称为“OpenRouter Top-50 可归属作者 token 份额（含 Other）”。命名略长但避免把每日 Top 50 截断后的下界误写成全量厂商份额。图表中作者份额加 `Other` 必须合计 100%。

### 4. Token share 是主契约；request share 只有官方稳定出口才扩展

用户截图中的官网 Market Share 文案指向 text request share，但已确认的公开 Rankings Data API 只返回 token。首版从同一事实派生 author token share，并在标题、轴和方法卡明确标注 token。

若实施时发现 OpenRouter 提供稳定、文档化、可下载、带历史日期和分母的全局 request-count/share 出口，新增独立 `request_count`/`request_share` 序列；否则不发布。自有账户的 Analytics API 不是全局市场数据，不作为替代。

### 5. 免费流量保留，但不单独推断商业收入

模型 identity 保留可识别的 `:free` 或 source-native 免费路由标记。总生态用量包含免费流量；Top 模型榜单用徽标区分 free route。首版不构造“付费 token 规模”，因为仅凭模型名和当前价格不足以重放历史付费状态、折扣和路由定价。

### 6. 固定 DataProducts 与派生版本

数据层提供四个核心产品：

- `openrouter_token_volume_series`：日/周/月总 token、模型 token、作者绝对 token、Other。
- `openrouter_author_share_series`：作者 token share、绝对 token、Other、Top-N 口径。
- `openrouter_model_leaderboard`：最新完整周或指定窗口模型排行；`openrouter_model_ranking_series`：默认从 2026-01-01 起按累计 token 固定模型集合并返回逐周 token 与 rank，合并同一模型版本的发布日期后缀。
- `openrouter_concentration_series`：作者 Top-3、Top-5 和 HHI。

所有聚合携带 `derivation_version`、observation IDs、rows hash、regime 和 source `as_of`。Provider/source adapter 不直接生成结论或图表。

### 7. 商业化 Observer 使用双证据结构，不做数值融合

商业化 packet 继续以收入证据回答“变现水平和趋势”，新增 OpenRouter 回答“第三方路由使用规模和竞争格局”。两者各有独立状态，顶层只做方向性矩阵：

| 收入证据 | OpenRouter 用量 | 顶层解释 |
| --- | --- | --- |
| expanding | expanding | `revenue_and_routed_demand_expanding` |
| expanding | contracting | `mixed_commercialization_evidence` |
| unavailable | available | `routed_demand_only` |
| available | unavailable | `revenue_evidence_only` |

即便两者同步扩大，仍保留“留存、单位经济、全市场覆盖未验证”。厂商份额只作为竞争结构事实，不直接决定总商业化状态。

### 8. 四类固定可视化面向不同研究问题

1. **OpenRouter 公共路由 Token 周度规模**：完整周柱形总量 + 4 周均线；Y 轴自动使用 B/T，X 轴按季度或每 4–8 周显示，禁止挤满日期。回答“渠道规模是否扩大”。
2. **厂商 Token 份额**：按最近 12 周累计选择 Top 8 作者，其他作者与官方 Other 合并为可区分的长尾层；100% 堆叠柱。回答“竞争格局如何迁移”。
3. **模型排名与 Token 量时序**：按完整历史累计 token 选择稳定模型集合，上方面板为周度 token 堆叠，下方面板为相对全部模型的排名折线。回答“哪些模型增长、上升或跌出头部”。
4. **厂商集中度**：Top-3、Top-5 份额折线，HHI 使用独立面板，注明 Other 造成的下界/归属限制。回答“市场结构趋于集中还是分散”。

每张图均输出 CSV、JSON、sidecar、rows hash、来源 `as_of` 和固定中文字体检查。若字体或渲染失败，正文保留同源表格并标记图表失败。

### 9. 平台发布采用“测试完成即正式、单源可降级”

不设置长期 shadow。实现先使用固定官方 fixture 完成 parser、quality、query、derive、render、packet 和 replay 测试；配置真实 key 后执行一次 live smoke、历史回填和端到端审阅。验收全部通过即写入 platform 配置。任何新 vintage 失败时维持最近 accepted 数据，并在 freshness 超阈值后标记 stale。

## Risks / Trade-offs

- [OpenRouter 是单一渠道且用户结构可能偏开发者] → 报告固定使用渠道限定语，与 Ramp 支出及 Labs 收入只做方向性对照。
- [Top 50 截断使作者份额成为下界] → 保留官方 Other，不逆向分配；展示 Other 比例并在其异常扩大时降低结构结论置信度。
- [不同 tokenizer 令跨厂商 token 不完全可比] → 固定 methodology warning，同时展示同一序列自身的时间变化，不将微小横截面差异作精确经济比较。
- [免费模型、促销和路由迁移扭曲商业意图] → 榜单标记 free route；不把所有 token 当作付费或收入。
- [页面 request-share 与 API token-share 容易混淆] → 数据、图名、单位和 packet 使用不同 metric identity；无官方出口就不采 request share。
- [凭据或限流导致更新失败] → 安全注入 key、指数退避、分窗回填、每日请求预算、最近有效 vintage 和 freshness warning。
- [模型重命名或作者映射漂移] → 保存原始 permaslug，映射表版本化；未知作者不猜测。
- [图表 Top-N 选择导致颜色跳变] → Top-N 集合按固定回看窗选择并记录 sidecar，作者颜色由稳定 registry 控制。

## Migration Plan

1. 增加 OpenRouter source/metric/series/quality/freshness/derivation 配置及密钥环境变量说明，不启用运行时网页抓取。
2. 用官方结构 fixture 建立 artifact、observation、author registry、DataProducts 和隔离质量门。
3. 配置真实 key 后分窗回填 2025-01-01 至最近完整 UTC 日，执行守恒、日期覆盖、Other 和映射审计。
4. 生成五个 DataProducts、四类图表和双证据商业化审阅报告，核对 packet、Markdown、CSV/JSON、sidecar 与 rows hash。
5. 跑既有 Frontier Labs 收入、生产化 Observer、其他 layer 和离线 replay 回归；通过后直接启用 platform 与每日调度。
6. 回滚时停用 OpenRouter source 和商业化报告子段，保留已入库 artifact/observations；收入子段继续使用原配置。

## Open Questions

- OpenRouter 是否会提供与官网 Market Share 图一致、文档化且可历史重放的全局 request-count/share 出口；若没有，首版按既定契约只发布 token 份额，不影响实施和验收。
