## 1. Catalog、实体与收入语义

- [x] 1.1 注册 `sacra_public_company_profiles`、`tickertrends_public_research`、`frontier_ai_labs_revenue` dataset、四类收入 metrics、保存限制和来源优先级，并明确不接入付费 API/MCP。
- [x] 1.2 在受治理实体表中确认/补齐 OPENAI、ANTHROPIC 及可选产品 identity，增加 company/product scope 校验，禁止页面标题临时生成重复实体。
- [x] 1.3 扩展结构化 observation schema/维度映射，支持 metric identity、observation identity、raw metric label、publisher/origin citation、reference period、published/known time、methodology regime 和 revision。
- [x] 1.4 更新数据字典和 source checklist，写清 reported ARR、annualized run rate、trailing revenue、projection 及 company/media/third-party 身份的区别。

## 2. TickerTrends 2026 年上半年历史回填

- [x] 2.1 为指定 `anthropic-vs-openai-arr-tracking` 文章建立冻结 source artifact/fixture，保存原始页面内容、发布时间、URL、内容 hash 和 parser version。
- [x] 2.2 实现 TickerTrends 历史解析，只接受正文明确披露且 reference period 位于 2026-01-01 至 2026-06-30 的 OpenAI/Anthropic 数值，统一标记为 `third_party_estimate`。
- [x] 2.3 增加缺失月份、7 月以后过滤、图形目测值拒绝、无日期/无口径隔离和重复解析幂等测试。
- [x] 2.4 将解析结果与文章原文逐点人工对账，记录每个 observation 的原句、月份、金额、raw label 和规范化 metric，确认没有插值或补值。

## 3. Sacra 最新数据定期采集

- [x] 3.1 实现 Sacra OpenAI/Anthropic 公开页面发现适配器，一轮 probe 每页只采集一次并保存原始 artifact；后续解析、入库和报告离线复用该 artifact。
- [x] 3.2 解析 Sacra Revenue 段落、参考期间、金额、币种、原文引用和 origin citation，并根据原文区分 `company_reported`、`media_reported`、`third_party_estimate` 与 `projection`。
- [x] 3.3 实现 HTML/页面内容 hash、语义 schema fingerprint、`no_change`、新期间、同期间 revision、source unavailable、parse failure 和 methodology drift 状态。
- [x] 3.4 为静态 HTTP 不含目标内容的情况接入单次受控浏览器 snapshot 回退；验证报告运行和同一 release 的入库不会再次打开网页。
- [x] 3.5 在 `config/data/schedules.yaml` 登记 `frontier_ai_labs_revenue_p7d_probe`，只检查两个 Sacra 公开页面，不触发生产化来源或其他 L1–L8 数据源。
- [x] 3.6 建立当前公开页面 fixtures，并覆盖页面无变化、引用变化、同期间值变化、页面结构变化、空内容和来源不可达测试。

## 4. 统一入库、质量与可比性

- [x] 4.1 将两来源 observations 写入现有统一结构化 SQLite/artifact repository，不创建报告专用库、重复下载目录或独立事实缓存。
- [x] 4.2 实现稳定 observation identity、payload 幂等、同期间 revision vintage、current/as-of 选择和 known-at 可见性测试。
- [x] 4.3 实现正金额、币种、实体、reference period、metric/observation identity、引用链和 lineage 质量门；不合格候选只保留 artifact/诊断，不进入 platform observation。
- [x] 4.4 实现同期间多来源并存、不可比口径分离、可比值差异超过 10% 的 `source_conflict` 和 headline 来源选择，验证不会覆盖低优先级来源。
- [x] 4.5 验证不把公司总收入与产品收入相加、不把 projection 混入 historical actual、不把 ARR 与 run rate 静默融合。

## 5. Revenue DataProduct 与趋势派生

- [x] 5.1 实现 Frontier Labs revenue query/DataProduct，支持 company、product、metric identity、observation identity、source、period、as-of、quality 和 latest/history 查询。
- [x] 5.2 返回 value/currency、真实参考期、披露日、period gaps、source citation、comparability、artifact/observation IDs、revision、methodology regime 和完整 lineage。
- [x] 5.3 实现同一 trend cell 的 latest、净变化额、净变化率、真实天数斜率和方向状态；少于 3 点或不足 60 天返回 `insufficient_history`，不得插值或前向填充。
- [x] 5.4 实现公司级与 section 级状态聚合：两家公司同向才确认共同方向，一方历史不足返回 partial，方向冲突返回 mixed，不计算跨公司平均增速。
- [x] 5.5 对 SQL/DataProduct、Pandas、CSV/JSON 导出进行对账，确保相同过滤条件下 observation IDs、值、期间、来源身份和 derivation version 一致。

## 6. L1 商业化 Observer、Agent context 与正式报告

- [x] 6.1 新增 `ai_commercialization` runner 和 `ai_frontier_labs_commercialization/v1` 固定命题，packet 中分别记录 revenue、retention、unit economics 和 business-model durability 的 coverage/status。
- [x] 6.2 在 `config/sectors/ai_hardware.yaml` 的 `L1_app.evidence_observers` 增加独立商业化 Observer；验证与现有生产化 Observer 分开运行、分开失败且不改变后者 claim/version/status。
- [x] 6.3 实现收入部分规则化结论，并在完整商业化判断中固定表达“收入兑现方向已观察、留存与单位经济尚未验证”，禁止仅凭收入增长宣称商业模式可持续。
- [x] 6.4 生成紧凑 Agent context，包含 claim/version、coverage、公司 latest、趋势摘要、facts、warnings、observation IDs、manifest 和 lineage pointer，不平铺网页全文。
- [x] 6.5 生成中文正式 Markdown：命题判断、最新收入、历史变化、收入曲线、历史观察表、来源/可比性、指标公式、限制及待补商业化维度，并明确 Labs 不能代表完整 L1 应用层。
- [x] 6.6 生成 OpenAI/Anthropic 收入双面板或等价清晰图表；只连接同一可比 cell 的离散披露点，使用真实月份、USD 十亿美元、来源/身份标记和 period-gap 注释。
- [x] 6.7 确保 Markdown、PNG、CSV/JSON、sidecar、packet 和 Agent context 共享同一 rows hash；执行中文字体 glyph 与渲染可读性检查。

## 7. 端到端验收与直接平台发布

- [x] 7.1 在隔离 SQLite/artifact/output 目录运行 `catalog → history seed → Sacra discovery → parse → quality → ingest → query → trend → Observer → report → lineage → manifest replay`。
- [x] 7.2 验证 manifest 离线重放不访问 Sacra/TickerTrends，重复 probe 返回 `no_change`，同期间修订保留旧 vintage，失败候选不破坏最近有效数据。
- [x] 7.3 运行 `ats evidence layer --sector ai_hardware --layer L1_app`，确认同时生成生产化和商业化两个独立正式输出路径，且不运行其他 layer/sector。
- [x] 7.4 回归验证 BTOS、RPS、Ramp、Anthropic Economic Index 的生产化报告、图表、Agent context 和整体判断未发生非预期变化；确认 Chain、评分、组合、风控与交易 workflow 未被接入。
- [x] 7.5 审阅实际商业化报告中的数据点、来源、结论、图表和方法注释，并运行结构化数据、Evidence、CLI、可视化及 OpenSpec strict validation 测试。
- [x] 7.6 全部测试通过后，将收入 source/dataset 配置直接设为 `platform` 并默认启用 L1 商业化 Observer；不得遗留 shadow/experimental promotion 步骤。
- [x] 7.7 更新运维文档，记录 P7D 探测、`no_change`、revision、methodology drift、最近有效数据保留和停用回滚流程。
