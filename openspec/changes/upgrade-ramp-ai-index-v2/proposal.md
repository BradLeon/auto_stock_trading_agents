# Proposal

## Why

L1 用 Ramp AI Index 回答“企业是否在真实付费采购 AI、钱流向哪家厂商”，但库里的 Ramp 数据停在 2026-08。原因有三：

1. **定时任务从未真正联网。** `ramp_ai_index_p10d_probe` 只读 `var/data/ramp_exports/` 下 2026-09-11 手工导出的 TSV。规格里负责刷新这些文件的浏览器 runner 从未实现，所以每次都报 `upstream_no_change`。这本身违反了结构化采集规格“不得伪造 no_change”的要求。
2. **Ramp 已改版，口径换代。** 2026-11-12 实测官方 “Get the data”：
   - 采用率与人均支出整段重算。例如 2026-08 总体采用率从 56.13% 改成 55.17%。
   - 行业改用 NAICS 全称。
   - 模型份额改成“按周、每个模型一列”的 Token Spend 数据。
   - 原解析器的 v1 口径不能直接续接。
3. **出现了更稳的官方出口。** FRED 从 2026-10-05 起发布 14 条 Ramp 月度序列：总体、3 家供应商、7 个行业、3 档企业规模。数值与网页导出逐位一致，历史从 2023-01 开始，无需浏览器。

## What Changes

- **FRED 优先：** 新增来源 `ramp_ai_index_fred`，从 FRED 公开 CSV 采集 14 条 `RAMPAI*` 序列，写入 `ramp_ai_adoption`。每周检查一次，不依赖浏览器。
- **网页 runner 只补增量：** 新增 Playwright 无头浏览器 runner。它打开 Ramp 页面，逐个点击可见的 “Get the data”，截获按钮写入剪贴板的原文。只采 FRED 没有的部分：
  - 另外两家供应商（Mistral、DeepSeek）
  - 另外 13 个行业
  - 人均 AI 支出
  - Token 花费份额，按厂商和按模型各一份，取周度 Shares 模式
  
  FRED 已有的序列不从网页入库，只用来交叉核对。
- **失败隔离：** runner 被拦、超时或控件消失时，网页部分明确报失败（`export_unreadable`），FRED 部分照常更新。过期的入站文件不再产出 `no_change`；网页部分超过 35 天没有新的 accepted 数据时报 `export_stale`。
- **v2 口径：** 新数据标记为 `ramp_ai_index_v2`，作为独立序列；读取方默认只读 v2。v1 历史只读保留，可显式按 regime 查询审计，不参与 v2 趋势。
- **BREAKING：** scope `model_market_share_overall`（按月 API 花费份额）停止更新，由 `token_spend_share_by_maker` 和 `token_spend_share_by_model`（按周 token 花费份额）取代。
- **新增 scope：** `adoption_business_size`（来自 FRED 的企业规模）进入发布范围。
- **不纳入：** 网页新增的 Token volume、Token prices、AI share of business spend、Geographies 只做发现，不入库。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `data/ramp-ai-index`：
  - 官方出口从“仅网页导出”改为“FRED 优先，网页 runner 补增量”。
  - 采集范围改为 v2 scope 集合。
  - 新增 runner 失败隔离与过期报警。
  - 新增 v2 口径登记与 v1 只读保留。
  - 调度改为“FRED 周检 + runner 十天一次”。
- `evidence/ai-production-penetration-observer`：
  - Ramp 补充段落与可视化 scope 从首版五个改为 v2 集合，企业规模不再标为待观测缺口。
  - 模型份额改为 token 花费份额。
  - 只读 v2 口径。

## Impact

- **代码：**
  - `src/ats/data/sources/ramp_ai_index.py`：v2 解析器、runner 接入、增量过滤。
  - 新增 FRED 适配器（复用 `rps_genai_adoption` 的 FRED 读取方式）。
  - `src/ats/data/adapters/structured/registry.py`。
  - `src/ats/data/products/ramp_ai_index.py`：按 regime 过滤、新 scope。
  - `src/ats/agents/evidence/work_adoption.py`、`ramp_visualization.py`：新 scope 与图表。
- **配置：**
  - `config/data/structured.yaml`：新来源、scope 与指标映射、v2 regime 批准。
  - `config/data/schedules.yaml`：新增 FRED 周任务；runner 沿用 `ramp_ai_index_p10d_probe`。
- **依赖：** Python 包 `playwright` 与 Chromium 浏览器，约 150 MB 下载，安装前需用户确认。作为可选依赖，未安装时网页部分报 `export_unreadable`，不影响 FRED 部分。
- **数据：** 生产库新增 v2 序列。v1 序列（2026-09-11 及以前采集）保留不删。
- **文档：** `docs/data/RAMP_AI_INDEX.md`、`docs/data/L1_OBSERVER_DATA_SOURCES.md`、`docs/validation/RAMP_AI_INDEX_ACCEPTANCE.md`。
- **不在范围：** 刷新控制器每小时重复记录过期窗口 misfire 的日志噪声，另行处理。
