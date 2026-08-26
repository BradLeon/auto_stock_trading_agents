# 结构化数据层五维质量验收（2026-08-25）

> 范围：阶段二各专项隔离数据库的统一只读汇总；未写入生产数据库。
> 口径：状态来自机器目录和实际 ingestion/observation/candidate 表，不用缺失值补 0。

## 结论

本报告用于确认统一质量入口能够跨专项展示 Coverage、Accuracy / Reconciliation、Freshness、Completeness 和 Availability。`warning` 或 `failed` 是实际切换门的输入，不代表通过格式检查后自动上线。

| Dataset | Overall | Coverage | Accuracy | Freshness | Completeness | Availability | Accepted observations |
|---|---|---|---|---|---|---|---:|
| `company_financials` | `failed` | `failed` | `failed` | `passed` | `warning` | `warning` | 1064 |
| `industry_dram_contract_price` | `warning` | `no_coverage` | `not_evaluated` | `no_data` | `no_data` | `no_run` | 0 |
| `market_consensus` | `passed` | `passed` | `passed` | `passed` | `passed` | `passed` | 168 |
| `private_company_events` | `warning` | `passed` | `passed` | `not_configured` | `passed` | `no_run` | 5 |
| `regional_kr_exports` | `passed` | `passed` | `passed` | `passed` | `passed` | `passed` | 20 |
| `regional_tw_exports` | `passed` | `passed` | `passed` | `passed` | `passed` | `passed` | 20 |

## 数量对账

| Dataset | Query observations | Run discovered | Run accepted | Run quarantined | Candidate statuses | Reconciled |
|---|---:|---:|---:|---:|---|---|
| `company_financials` | 1064 | 8734 | 1064 | 7670 | accepted=1064, quarantined=7670 | `true` |
| `industry_dram_contract_price` | 0 | 0 | 0 | 0 | — | `true` |
| `market_consensus` | 168 | 210 | 210 | 0 | accepted=210 | `true` |
| `private_company_events` | 5 | 0 | 0 | 0 | accepted=7, rejected=2 | `true` |
| `regional_kr_exports` | 20 | 20 | 20 | 0 | accepted=20 | `true` |
| `regional_tw_exports` | 20 | 20 | 20 | 0 | accepted=20 | `true` |

## Artifact 用量

- 跨隔离库 logical artifacts：17
- 各库 unique blobs 合计：14
- physical bytes 合计：17398894
- 库内去重引用合计：3
- 各专项使用独立 artifact 根目录，因此不把跨库相同哈希虚报为全局去重。

## 来源矩阵

| Source | Catalog status | Persistence | Latest validation status |
|---|---|---|---|
| `accepted_document_evidence` | `current_partial` | `persistent` | `no_run` |
| `company_disclosures` | `planned` | `persistent` | `no_run` |
| `defeatbeta_stock_statement` | `current_partial` | `persistent` | `partial` |
| `ibkr_market` | `runtime_excluded` | `runtime` | `no_run` |
| `kr_ecos_exports` | `current_partial` | `persistent` | `succeeded` |
| `sec_companyfacts` | `current_partial` | `persistent` | `succeeded` |
| `thetadata_options` | `runtime_excluded` | `runtime` | `no_run` |
| `trendforce_dram` | `current_partial` | `persistent` | `no_run` |
| `tw_mof_exports` | `current_partial` | `persistent` | `succeeded` |
| `yfinance_consensus` | `current_partial` | `persistent` | `succeeded` |
| `yfinance_market` | `runtime_excluded` | `runtime` | `no_run` |
| `yfinance_options` | `runtime_excluded` | `runtime` | `no_run` |

## 解释边界

- `company_disclosures` 仍为 planned；不能因 SEC Company Facts 已实现而写成已接入。
- `industry_dram_contract_price` 在本轮聚合库中没有独立真实专项，报告保留 `no_coverage/no_run`，不伪造通过。
- 证据型数据由候选核验后去重发布，一个 observation 可由多条 accepted evidence 支撑，因此 accepted candidates 不要求等于 observations。
- ticker 行情与期权四个来源只显示 `runtime_excluded`，不应出现 ingestion、artifact 或 dataset。
