# 官方财务与 defeatbeta `stock_statement` 专项验收（2026-08-25）

## 决策

两条真实源均成功采集，但本轮结论为 **保持 `legacy`，不启用 fundamentals shadow/platform**。这是质量门的正常作用，不是采集失败：

- AMZN、MSFT、KLAC 的 SEC Company Facts 已覆盖到 2026-06-30，TSM 的 SEC Company Facts 只有 2024-12-31 年报；TSM 的新季度披露需要后续 `company_disclosures` 适配器补齐。
- 按配置的 240 小时 freshness 门，四个实体的官方最新 filing age 分别约为 606、654、463、11,887 小时，均未通过。该门是否应按“事件型季度数据”重新定义，应通过后续 OpenSpec 修订，不能在本验收中临时放宽。
- 官方最新期间的原始核心指标覆盖率为 AMZN 80%、MSFT 100%、KLAC 80%、TSM 80%，达到最低 80%。
- 发现 5 个真实跨源 EPS 冲突，主要集中在 KLAC 与 TSM；平台保留并列值、默认官方优先，没有覆盖或平均。

因此，本阶段完成底座、适配、真实验证和明确回滚决策，但不改变现有 `fundamentals` 默认读取路径。

## 数据来源与方法

### SEC Company Facts

- 官方入口：`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- ticker → CIK 来自 SEC 官方 `company_tickers.json`。
- 覆盖 `us-gaap` 与 `ifrs-full` 的已注册核心概念。
- 只接受 10-Q、10-K、20-F、40-F、6-K 中可确定期间、单位和 filing date 的 fact。
- 10-Q 单季、YTD 和 instant 分开保存；非标准 12 个月 comparative duration 不冒充季度值。
- 同一 concept/期间重述选择当前源快照内最新 filing，同时后续重新抓取仍会追加 observation vintage。
- SEC 返回未提供可用 ETag/Last-Modified 时，`source_version` 保持空值；artifact content hash 仍能唯一标识本次完整响应。

### defeatbeta `stock_statement`

- 托管文件：HuggingFace `defeatbeta/yahoo-finance-data/data/stock_statement.parquet`。
- 上游声明：Yahoo Finance structured statement data；HuggingFace 只作为托管/传输渠道。
- DuckDB 查询只读取目标 symbol、报告日期和所需列，使用 predicate pushdown；不下载 115 MB 全量文件到平台。
- 排除 `TTM` 和非日期 report_date，只接受 annual/quarterly 事实。
- 命中切片以 JSON artifact 保存，包含托管快照时间和上游 provenance。
- 未确认语义的行项目进入 pending mapping，不形成另一套 statement 表，也不静默丢弃。

## 真实结果

| 实体 | SEC 最新期 | SEC 原始核心覆盖 | mirror 最新期 | mirror 最新期覆盖 | 发布/可见性结论 |
|---|---|---:|---|---:|---|
| AMZN | 2026-06-30 | 80% | 2026-06-30 | 10% | SEC filed 2026-07-31；mirror 只有本次 known_at |
| MSFT | 2026-06-30 | 100% | 2026-06-30 | 100% | SEC filed 2026-07-29；mirror 只有本次 known_at |
| KLAC | 2026-06-30 | 80% | 2026-06-30 | 100% | SEC filed 2026-08-06；mirror 只有本次 known_at |
| TSM | 2024-12-31 | 80% | 2026-06-30 | 100% | SEC annual fact filed 2025-04-17；需公司披露补季度 |
| MIRROR_MISSING_ENTITY | — | — | — | 0% | 明确 no coverage，不生成伪零值 |

采集与治理统计：

- accepted observations：1,064。
- quarantined candidates：7,670，全部保留原因；主要是非核心、尚未映射的 mirror 行项目。
- pending mapping：228 个不同字段；无字段被静默丢弃。
- cross-source conflict records：7；质量层按完全可比口径确认 5 个数值冲突。
- `as_of` future leakage：0；首次抓取之前的历史时点不能看到本次回填值。
- 资产 = 负债 + 权益：检查 21 组，没有恒等式错误。
- quarter/YTD：检查 75 组，没有把累计值当单季。
- 单位突变：检查 881 对，没有 1,000 倍缩放突变。
- 连续性：在识别 SEC 不单列 Q4 的合法年度跨越后，只剩 2 个 KLAC cash-flow/capex 覆盖缺口。

## 派生指标

Free Cash Flow、Gross Margin、Operating Margin 不作为来源原始 fact 永久写入：

- `free_cash_flow_v1 = cash_from_operations - capex_outflow`
- `gross_margin_v1 = gross_profit / revenue`
- `operating_margin_v1 = operating_income / revenue`

三个公式均为查询时、版本化派生，保留两侧 observation id；缺任一输入返回 `insufficient_inputs`，不会按零计算。

## 在哪里检查

- 机器报告：`var/structured_financial_validation_20260825_v4/validation.json`
- 隔离数据库：`var/structured_financial_validation_20260825_v4/financial-smoke.sqlite`
- artifact 根目录：`var/structured_financial_validation_20260825_v4/artifacts/`
- defeatbeta 命中切片：`artifacts/2d/2dd5436491e34b348eed80c98e491871df4089a4579cf23eef628cb303734bed.json`
- SEC AMZN：`artifacts/76/76786a69103fd0437520bd789ecdf2633d7e6679a578989133dec7466e27a361.json`
- SEC MSFT：`artifacts/75/75bebe2eebf09fd5c1383646a761563922c8eb0f2cdb107a13e526572a5140c3.json`
- SEC KLAC：`artifacts/bd/bd46b713934fc76721512a1ec4080741f76aa8a4edbe1d588f572152303202f4.json`
- SEC TSM：`artifacts/d3/d39826bf49737592b6e460aff55b4fc52b3974d1d2862a3686b69e6d65b12b42.json`

以上均位于 `var/`（Git 忽略）且与生产数据隔离。旧的 v1–v3 验证目录保留了问题发现过程，最终检查应以 `_v4` 为准。
