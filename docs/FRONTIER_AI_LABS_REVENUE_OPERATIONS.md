# Frontier AI Labs 收入数据 — 运维手册

> 读者：发布负责人、故障处理者、运行 L1 Evidence Observer 的值班
> 适用范围：`sacra_public_company_profiles`、`tickertrends_public_research` 两个 Source，
> `frontier_ai_labs_revenue` Dataset，以及 `ai_frontier_labs_commercialization/v1` Observer
> 发布日期：2026-09-13

## 1. 数据源契约

| Source | Adapter | 采集触发 | 内容 | 修改后回退路径 |
|---|---|---|---|---|
| `sacra_public_company_profiles` | `SacraPublicCompanyProfilesAdapter` | `frontier_ai_labs_revenue_p7d_probe`（每 7 天一次） | `https://sacra.com/c/openai/`、`https://sacra.com/c/anthropic/` 两个公开公司页 | 重复运行返回 `no_change`；semantic fingerprint 变化触发 `methodology_drift` 警报 |
| `tickertrends_public_research` | `TickerTrendsPublicResearchAdapter` | 不参与周期发现；运行 E2E 时从 `tests/fixtures/frontier_ai_labs_revenue/tickertrends_anthropic_vs_openai_arr_tracking.json` 读取 | `https://blog.tickertrends.io/p/anthropic-vs-openai-arr-tracking` 公开 Substack 帖 | 冻结的 2026H1 seed；只有同 sha256 的 fixture 可重放 |

正式平台运行通过 `feature_flags.sources` 切换为 `platform` 启用。

## 2. 运行入口

```bash
# P7D 探测（cron/launchd 调用）
ats_cli data ingest --source sacra_public_company_profiles \
                    --dataset frontier_ai_labs_revenue \
                    --db /var/lib/ats/structured.sqlite \
                    --artifact-root /var/lib/ats/artifacts

# L1 商业化 Observer（与生产化 Observer 同时跑）
ats_cli evidence layer --sector ai_hardware --layer L1_app \
                      --output var/evidence/L1_app.md \
                      --chart-dir var/evidence/charts

# 仅跑商业化 Observer
ats_cli evidence ai-commercialization --sector ai_hardware --layer L1_app \
                                    --output var/evidence/commercialization.md \
                                    --chart-dir var/evidence/commercialization_charts
```

每次运行会写入：

- 合并 Markdown：`var/evidence/L1_app.md`
- 商业化独立报告：`var/evidence/L1_app-ai_frontier_labs_commercialization.md`
- 生产化独立报告：`var/evidence/L1_app-ai_core_production_workflow_penetration.md`
- 图表 + sidecar：`var/evidence/charts/ai_frontier_labs_commercialization/...`

## 3. `no_change` / 修订 / methodology drift / 最近有效数据

| 现象 | 触发条件 | 处置 |
|---|---|---|
| `no_change` | Sacra 页面内容 hash 与 `discovered_payloads` 一致 | 正常情况；下一次探测 |
| Revision vintage | 同一 (entity, metric, observation_identity, period) 的值变化 | 旧版本保留，`latest_only=False` 可查；趋势 cell 仍使用最新观察 |
| `methodology_drift` | Sacra 页面 semantic fingerprint 变化（heading/table 列/付费墙标记） | 暂停 Observer；先做 fixture 验证再恢复 |
| Quality gate quarantined | 引用链/币种/单位缺失 | 写入 `rejected_candidates` 与 `failures`，**不**覆盖最近有效数据 |
| Sacra 静态 HTML 缺 Revenue 段 | 单次受控 `browser.snapshot()` 回退，每页每运行最多一次 | 仍写入 artifact；如浏览器连续失败则降级为 `no_admissible_revenue` failure |
| TickerTrends 7 月以后点 | `outside_accepted_reference_window` | 写入诊断；不会污染 2026H1 序列 |
| 同期间跨来源差异 > 10% | `source_conflict` 警报 | headline 仍按 identity→source→known_at 优先级；候选不删除 |

## 4. 最近有效数据保留与降级

- 受质量门拒绝的 candidate 只进入 `rejected_candidates` 与 `failures`，**不会覆盖**最近平台 observation。
- Revision 全部保留在 `observations`（`latest_only=True` 关闭时可见）。
- Manifest snapshot 通过 `products.replay_frontier_labs_revenue_bundle(snapshot_id)` 离线重放；不重新打开网页。
- Sacra 接入失败时，Observation 不写入，但 `no_change` 状态下历史 manifest 仍可重放。

## 5. 停用与回滚

把 `feature_flags.sources.sacra_public_company_profiles` 与
`feature_flags.sources.tickertrends_public_research` 通过
`var/structured_data/releases.yaml` 回退到 `legacy` 或 `shadow`：

```yaml
overrides:
  sources:
    sacra_public_company_profiles: legacy
    tickertrends_public_research: legacy
```

下次 E2E 重新接受前不要删除 catalog / dataset / adapter 注册：

- `config/data/structured.yaml` 的 sources / datasets 段
- `src/ats/data/adapters/structured/registry.py` 的 runtime 注册
- `config/sectors/ai_hardware.yaml` 的 `L1_app.evidence_observers`

## 6. 端到端验收产物

Task 7.1–7.5 留下的可复现物料位于 `tmp/e2e-acceptance/`：

- `structured.sqlite`：受治理 SQLite 仓库
- `artifacts/`：原始 HTML、JSON artifact
- `outputs/L1_app.md`、`outputs/L1_app-ai_frontier_labs_commercialization.md`
- `outputs/charts/`：PNG、sidecar、CSV/JSON

如果未来 OpenSpec change 需要重新验证，请以 `tmp/e2e-acceptance/` 为基线
重跑 `tests/test_frontier_ai_labs_revenue_sources.py`、
`tests/test_frontier_ai_labs_revenue_product.py`、
`tests/test_evidence_ai_commercialization.py`，并执行 `openspec validate <change> --strict`。
