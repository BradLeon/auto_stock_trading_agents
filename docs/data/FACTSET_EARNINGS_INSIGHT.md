# FactSet Earnings Insight 数据产品

最后更新：2026-09-04。本文是 FactSet Earnings Insight 的来源清单、指标字典、运行手册和消费者边界；配置的最终机器可读定义仍在 `config/data/structured.yaml`。

## 1. 当前状态与边界

| 项目 | 当前状态 |
|---|---|
| 数据集 | `sp500_earnings_insight`，周度、持久化、内部研究用途 |
| 原始来源 | `https://www.factset.com/earningsinsight` 的稳定入口；实际日期版 PDF 与 HTTP 元数据一并保存 |
| 文档/指数分区 | `factset_earnings_insight_index=platform`；`macro_factset=platform` |
| 行业分区 | `factset_earnings_insight_sector=platform`；`sector_factset=platform` |
| 当前上线验收 | 2026-08-28（`082826`）：8 张表、11 个 GICS 行业、231/231 单元格与独立原图 decoder 一致 |
| 唯一导入路径 | `factset_weekly_ingest` 或受控 `factset-import`；Macro/Sector 不下载、解析 PDF，也不读取 Obsidian 文件夹 |

一份授权 PDF 形成两个独立分区：指数正文指标是 `index_core`，GICS 图表指标是 `sector_core`。一个分区失败不会覆盖另一个已发布分区；每个观测都保留报告版本、`known_at`、证据页码/区域和质量状态。

## 2. 版权与内部使用

- 原始 PDF、整页文本、图表和裁剪图是 FactSet 授权的内部研究资产，不提交到 Git，也不作为默认对外报告内容。
- 正式分析优先引用已发布的结构化观测；需要核对时，用 observation 的血缘定位内部 PDF 页面或图像区域。
- 对外或可分享的报告只使用系统基于已发布观测重新组织的数字、结论和来源说明；不得默认复制 FactSet 原图、长段原文或带认证参数的 URL。
- 失败日志与运行报告不得输出 PDF 正文或带签名/查询参数的 URL。部署时应保护 artifact 存储和数据库访问权限。

## 3. 指标字典

除非来源明确为 `not_applicable`，比率以小数保存并在报告中显示为百分比；计数为整数，P/E 为倍数。每条观测还携带实体、目标期间或快照日、`estimated|blended|actual|not_applicable`、报告日期和 `known_at`。

| 主题 | metric id |
|---|---|
| 披露进度 | `earnings.reporting.coverage` |
| EPS / 营收超预期分布 | `earnings.eps.above_estimate_share`、`earnings.eps.inline_estimate_share`、`earnings.eps.below_estimate_share`、`earnings.revenue.above_estimate_share`、`earnings.revenue.inline_estimate_share`、`earnings.revenue.below_estimate_share` |
| EPS / 营收超预期幅度 | `earnings.eps.surprise_pct`、`earnings.revenue.surprise_pct` |
| EPS / 营收增长 | `earnings.eps.yoy_growth`、`earnings.revenue.yoy_growth` |
| 利润率 | `earnings.net_profit_margin`、`earnings.margin.increase_share`、`earnings.margin.unchanged_share`、`earnings.margin.decrease_share` |
| 公司指引 | `earnings.guidance.positive_count`、`earnings.guidance.negative_count` |
| 盈利预测修正广度 | `earnings.revision.improved_sector_count`；额外维度：`comparison_date`、`revision_direction`、`sector_total` |
| Bottom-up EPS | `earnings.bottom_up_eps` |
| 估值 | `valuation.forward_pe`、`valuation.trailing_pe`、`valuation.forward_pe.average_5y`、`valuation.forward_pe.average_10y`、`valuation.trailing_pe.average_5y`、`valuation.trailing_pe.average_10y` |
| 收入地域 | `revenue.geographic.us_share`、`revenue.geographic.international_share` |
| 分析师预期 | `consensus.rating.buy_share`、`consensus.rating.hold_share`、`consensus.rating.sell_share`、`consensus.target.upside` |

实体为 `SP500` 和 11 个 `GICS_*` 行业。`Today` 列的行业 EPS 增长是正式值；如图表同时显示较早日期，该日期仅作为修正比较证据，不能被伪造为第二条正式观测。

## 4. 消费者边界

| 消费者 | 可读内容 | 使用规则 |
|---|---|---|
| Macro | 全部已发布的 SP500 指数材料、程序诊断、最多六段带页码正文 | 解释增长质量、集中度、利润率/指引、估值和预期；仍需结合利率、通胀、就业、信用和市场价格 |
| Sector | 11 个 GICS 行业矩阵，以及最新正式 Macro 结论 | 只在八个产业链层结论完成后作最后对照；不得改写任何层、个股判断或公司证据链 |
| Chief、Risk、PEAD、Technical | 不直接读取 | 仅间接消费已持久化的 Macro/Sector 结论，避免同一来源重复加权 |

`--offline` 仅禁止现场网络获取：本地已经发布的 FactSet snapshot 和已持久化 Macro memory 仍必须可读。无正式 release 时，报告要写明 `unavailable`；超过 10 天时标记 `stale` 并给出原报告日期，绝不能填零。

## 5. 每周运行手册

默认调度在周六 08:10（`Asia/Shanghai`）运行 `factset_weekly_ingest`，随后 08:50 的周度评审按 Macro → Sector 顺序读取同一份本地发布快照。

```bash
# 查看来源健康、报告版本/哈希、两个分区的质量与最近失败（URL 已脱敏）
ats data factset-status

# 查询当前或历史决策时点能看到的快照
ats data earnings-insight
ats data earnings-insight --as-of 2026-08-29T00:00:00+00:00
ats data earnings-insight --vintages

# 受控导入一份已获授权的本地 PDF；实际导入时间即 known_at
ats data factset-import --report-path /absolute/path/EarningsInsight_082826.pdf

# 用命名 extractor 重处理已有 PDF；不得修改原始 artifact 或伪造历史 known_at
ats data factset-reprocess factset-chart-v1 --report-path /absolute/path/EarningsInsight_082826.pdf
```

运行检查顺序：先看 `factset-status` 的 source health、报告 hash 和 `index_core` / `sector_core` manifest；再运行 Macro；最后运行 Sector。若本周抓取失败但已有旧 release，允许评审继续，但必须保留 `stale` 状态。若没有任何 release，则只省略 FactSet 结论，不得让整个周评失败。

回滚只改变 `macro_factset` 或 `sector_factset` 的读取路由；不得删除 PDF artifact、document version、candidate、evidence link、observation 或 vintage。当前没有 Macro 旧下载路径可回退，事故处理应使用 release mode、质量状态和受控重处理，而不是重新引入本地文件夹读取。

## 6. 验收与排障

- 当前行业上线门禁是 231/231 单元格一致、完整 11 行标签和零未解决单元格；缺失、额外、重复、值/单位/期间/状态/页码/区域不一致均阻止行业分区发布。
- OCR 不可用时，文档和指数文本仍可处理；行业候选保持不可用或 shadow，不能用模型猜值补齐。
- `unreachable`、`unauthorized`、`not_pdf`、`parse_failed` 和 `validation_failed` 是不同状态。先保留 artifact/失败记录、检查 source health 和模板，再决定是否受控重处理。
- 证据与上线记录位于 `openspec/changes/integrate-factset-earnings-insight/evidence/`；授权 PDF 本身不在版本库。
