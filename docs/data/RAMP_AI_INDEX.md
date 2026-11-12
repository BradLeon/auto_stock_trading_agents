# Ramp AI Index 采集与运行清单（v2）

本数据源服务 L1 AI 应用层，回答一个问题：企业是否在真实付费采购 AI，以及钱流向哪家厂商。
2026-11-12 起为 v2 口径（`ramp_ai_index_v2`，OpenSpec change `upgrade-ramp-ai-index-v2`）。

## 两个来源，各管一部分

| 来源 | 采什么 | 怎么采 | 定时任务 |
|---|---|---|---|
| `ramp_ai_index_fred` | FRED 发布的 14 条月度序列：总体、3 家供应商（Anthropic、OpenAI、xAI）、7 个 NAICS 行业、3 档企业规模 | FRED 公开 CSV（`fredgraph.csv`）加序列元数据，纯 HTTP，不需要浏览器 | `ramp_ai_index_fred_weekly`（每周五 08:25 UTC） |
| `ramp_ai_index` | FRED 没有的部分：另外的供应商（Mistral、DeepSeek）、另外 13 个行业、人均 AI 支出、token 花费份额（按厂商、按模型，周度） | 无头浏览器打开页面，点可见的 `Get the data`，截获按钮写入剪贴板的原文 | `ramp_ai_index_p10d_probe`（每 10 天 07:30 UTC） |

- **一个序列只有一个来源。** FRED 序列清单登记在 `config/data/structured.yaml` 的 `ramp_ai_index_fred.fred_series`。网页导出里凡是清单内的（scope, 实体），只用来和 FRED 对数（差值超过 0.01pp 记 `fred_page_mismatch` 警告），不入库。FRED 以后新增序列，加进清单即可，网页对应部分自动停止入库。
- **两部分失败互不影响。** 各自有调度、账本记录、重试与新鲜度。网页部分被拦、超时或没装浏览器时，只记网页部分失败；FRED 部分照常更新。
- **不使用的出口：** Ramp Developer API/MCP（需企业授权）、截图或 OCR、猜测页面背后的数据地址。

## 网页 runner

`src/ats/data/sources/ramp_browser_runner.py`，可选依赖：

```bash
pip install '.[ramp-browser]'
```

```bash
playwright install chromium
```

1. 页面脚本运行前注入一段脚本，包装 `navigator.clipboard.writeText`，记录按钮复制的原文。无头模式下没有系统剪贴板权限也能工作。
2. 依次点击可见按钮切换图表：
   - Adoption → Overall + Models
   - Adoption → Sector
   - AI spend per employee → Overall
   - Token spend → By model maker → Shares
   - Token spend → By model → Shares
3. 每张图点一次 `Get the data`，header 校验后原子写入 `var/data/ramp_exports/<scope>.tsv`，同时写 `<scope>.json`（导出时间、页面地址、点击路径、SHA-256）。同一次探测的第二个数据集任务读这些文件，不再打开浏览器。
4. 超时：总 300 秒，每张图 45 秒；一次运行只开一个浏览器，结束即关闭。
5. 失败一律记 `export_unreadable`，原因码如下。任何一种失败都不回退去读旧文件。

| 原因码 | 含义 |
|---|---|
| `browser_not_installed` | 没装 Playwright 或 Chromium |
| `navigation_blocked` | HTTP 4xx/5xx、挑战页，或页面标题不是 “Ramp AI Index” |
| `control_missing` | 找不到要点的按钮（页面改版）；同时在 `var/data/ramp_exports/diagnostics/` 存一张截图，仅供人工排查 |
| `clipboard_not_captured` | 点了 `Get the data` 但没截获到合法的制表符文本 |
| `timeout` | 超过总时限或单图时限 |

**入站文件的新鲜度：** 只有导出时间在 10 天内的入站文件才算本轮上游结果，否则必须由 runner 重新导出。过期文件永远不会产出 `no_change`。网页部分 35 天没有新的 accepted 数据时，任务新鲜度判为过期，L1 报告显示 `export_stale`。

## 数据字典

| scope | 来源 | 统计主体/分母 | entity 形状 | 核心 metric | 期间 |
|---|---|---|---|---|---|
| `adoption_overall` | FRED | Ramp 相关美国企业中当月有正向 AI 付款的企业 / 相关企业 cohort | `RAMP_OVERALL` | `ai.ramp.paid_business_adoption_share` | 月 |
| `adoption_overall_models` | FRED（3 家）+ 网页（其余） | 同一 cohort，按供应商；企业可同时支付多家 | `RAMP_VENDOR:<VENDOR>` | `ai.ramp.vendor_adoption_share` | 月 |
| `adoption_sector` | FRED（7 个）+ 网页（其余） | 同一 cohort，按 NAICS 行业 | `NAICS:<行业全称>` | `ai.ramp.paid_business_adoption_share` | 月 |
| `adoption_business_size` | FRED | 同一 cohort，按小型、中型、大型企业 | `RAMP_SIZE:SMALL/MEDIUM/LARGE` | `ai.ramp.paid_business_adoption_share` | 月 |
| `spend_per_employee_overall` | 网页 | AI 付款 cohort 的美元月支出 / 员工数，按分位数 | `QUANTILE:MEDIAN/TOP10/TOP1` | `ai.ramp.ai_spend_per_employee` | 月 |
| `token_spend_share_by_maker` | 网页 | Token Spend Management 连接企业当周 token 总花费中各厂商的占比 | `TOKEN_MAKER:<厂商>` | `ai.ramp.token_spend_share_by_maker` | 周 |
| `token_spend_share_by_model` | 网页 | 同一 cohort 当周 token 总花费中各模型的占比 | `TOKEN_MODEL:<厂商>:<模型>` | `ai.ramp.token_spend_share_by_model` | 周 |

- **token 份额只收 Shares 模式。** 页面默认显示 Volumes（指数），那不是份额。解析时若某周各列合计明显超过 100，判为 Volumes 并拒绝入库（`methodology_drift`）。合计偏离 100±1 只记警告，因为可能有四舍五入或长尾。
- **只发现、不入库：** Geographies、Token volume、Token prices、AI share of business spend。
- **其他字段：**
  - `Monthly change (pp)`、`Yearly change (pp)` 作为原始发布字段保存。v2 行业导出没有同比列，就不生成同比，不补零。
  - 所有 observation 都保留原始列、参考期间、`methodology_regime`、artifact、payload hash、获取时间和质量状态。

## v1 与 v2

- **为什么换口径：** 2026-11 页面改版后，Ramp 对采用率与人均支出整段重算（例如 2026-08 总体采用率从 56.13% 改为 55.17%），行业改用 NAICS 全称，模型份额改为周度 token 花费。
- **v2 单独成序列：** 写 `methodology_regime: ramp_ai_index_v2`，与 v1 不是同一序列的不同版本。入库比对跨源冲突时只比同一口径，v2 值不会因为和 v1 不同而被标为冲突。
- **读取规则：** DataProducts 的 Ramp 查询默认只读 v2；显式传 `methodology_regime="ramp_ai_index_v1"` 可审计 v1。`model_market_share_overall` 只存在于 v1，v2 下查询返回 `scope_discontinued`。v1 与 v2 不拼成一条趋势。
- **第一次 v2 入库：** 走配置里显式批准的口径换代（`approved_methodology_regimes: [ramp_ai_index_v2]`），发现结果记 `approved_regime_transition`。同一口径内的 header 或指纹变化仍然判为 `methodology_drift` 并阻断。

## L1 报告展示规则

- 七个 scope 各一张趋势图。月度图横轴只标起始期、每年一月和最新期；token 份额按周绘制，不与月度采用率共轴。
- `spend_per_employee_overall` 只画 Median 与 Top 10%，用对数纵轴，以免 Top 1% 压扁中位数；数据表保留 Top 1%。
- token 份额画 100% 堆叠面积，保留历史累计与最新一周靠前的序列（最多 8 个），其余合并为 `Other`；底层表保留每个厂商或模型的完整行。
- 网页部分不可用时，总体、供应商、行业、规模采用率照常展示；人均支出与 token 份额标出不可用原因，不把旧数据当作最新。

## 运行检查

```bash
ats data validate-source ramp_ai_index_fred
```

```bash
ats data validate-source ramp_ai_index
```

```bash
ats data release-check --source ramp_ai_index_fred --ingest-new --force-check
```

```bash
ats data release-check --source ramp_ai_index --ingest-new --force-check
```

之后通过 DataProducts 或 L1 Observer 查询，不直接读物理表。

相同 payload 幂等；同期间新 payload 追加 vintage，`as_of` 可重放旧值。未发布不转为零，不前向填充缺失期间。
