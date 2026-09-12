# Ramp AI Index 验收记录

本文档对应 OpenSpec 变更 `integrate-ramp-ai-index-commercialization-signal` 的 task 7/8。
验收使用隔离 SQLite、隔离 artifact 目录和固定的 2026-08 官方图表导出 fixture；不向 Ramp 页面发起网络请求，也不保存 API key。

## 固定 fixture

| scope | fixture 事实 |
|---|---|
| `adoption_overall` | 2026-08 Overall = 56.13%，月变化 1.20pp，年变化 8.50pp |
| `adoption_overall_models` | Anthropic 43.8%、OpenAI 39.8%；vendor share 不强制加总 100% |
| `adoption_sector` | Technology and media 61.2%，实体使用 NAICS 前缀 |
| `spend_per_employee_overall` | Median 12.50、Top 10% 675.60、Top 1% 7205.13 USD/employee/month |
| `model_market_share_overall` | OpenAI / GPT-5.6 Sol / API = 17.9%，Token Spend Management cohort |

`business_size` 与 `geographies` 仅作为可发现控件记录，首版不进入 dataset、DataProduct 或 L1 报告。

## 隔离验收链

```text
validate_source_registration
  → release_check（离线无 fixture/browser 时返回 export_unreadable，不伪造 no_change）
  → ingest 两个 Ramp dataset
  → build_quality_report
  → DataProducts adoption/spend/model 查询
  → snapshot manifest
  → replay_snapshot（不访问 Ramp）
```

验收断言包括：每个 scope 独立 artifact；所有 accepted observation 能追溯到 artifact；DataProducts、只读 SQL、Pandas DataFrame 和 renderer 表格保持 observation ID/value/period/source scope 一致；snapshot replay 返回原 observation 与 artifact。

## 失败与隔离

- 页面控件/剪贴板为空、权限拒绝、损坏 TSV：`export_unreadable`，只隔离对应 slice。
- 没有本地官方 fixture 且网页导出不可读：`export_unreadable`；不会尝试 API/MCP，也不会伪造 `no_change`。
- 分位数顺序异常：spend slice 质量失败；adoption slice 不被阻塞。
- 同期间修订：追加 observation vintage；`as_of` 返回当时可见版本；不前向填充。
- 方法说明/sample count 变化：标记 `methodology_drift`，不跨 regime 派生趋势。

## L1 shadow 对账

Ramp 只写入 `supplemental_signals.ramp_paid_adoption` 和中文方法卡；不进入 BTOS/RPS/Anthropic 三轴 `overall_status`、阈值或趋势。Ramp 全部不可用时，Observer 保留三轴结果并增加 `ramp_unavailable` warning。报告图表、CSV/JSON 表格和 sidecar 均携带同一 `rows_hash`、`chart_slug`、artifact/observation IDs、derivation version 与 manifest ID。

## 本次验收运行记录

- 固定 TSV payload SHA-256：`adoption_overall=dfcec87c3854e93196a38cc867aa041b1ee00a78a5910d62881079d2bef06bcb`；`adoption_overall_models=1e1e4927c5561fcf90b46f0e1cf3cf3c7e30c5dc45bf95b9c36d6d5cba8d7fde`；`adoption_sector=0092e17931f097de4f9b07f33ac91848499fe7b671999d7579b6e544a488077f`；`spend_per_employee_overall=26a196be5c45fb58733f82c0cb991668d40e4ca1b448ba1d222f53eaa56cba66`；`model_market_share_overall=3bfbc7170ff4ab80f0b91797cea8f29dfc63565815726ccd26a8b5d387b213a5`。
- 完整采集 fixture 每次生成 3 个 adoption slice artifacts 和 2 个 spend/model slice artifacts；每条 observation 均绑定对应 slice，而不是绑定到第一个 artifact。
- 结构化、Evidence、可视化与 OpenSpec 验证：Ramp/Evidence 定向测试 `25 passed`；API/MCP 测试已随首版范围删除。绘图产生 PNG、CSV/JSON 表格和 sidecar；sidecar 与表格复用同一 `rows_hash`。本地 matplotlib 有中文字体 glyph warning，不影响数据或图表文件生成。
- Agent context 预算由测试断言：compact ≤ 12,000 字符、review ≤ 120,000 字符；Ramp 原始交易 payload 只留在 artifact/lineage，不平铺进 context。

## 执行命令

```bash
.venv/bin/pytest -q tests/test_ramp_ai_index.py
.venv/bin/pytest -q tests/test_evidence_ai_production_workflow.py tests/test_ai_production_penetration.py
openspec validate integrate-ramp-ai-index-commercialization-signal --type change --strict --no-interactive
```

若本地没有官方 TSV fixture，网页真实采集才需在受控浏览器中点击页面可见 `Get the data`，将 clipboard TSV 通过 `RampBrowserAdapter` 传入；首版不接入 API/MCP。

## 周期探测与平台发布验收（task 8）

- 已在 `config/data/schedules.yaml` 登记 `ramp_ai_index_p10d_probe`：外部 scheduler 每 10 天
  只运行 `ramp_ai_index`，不会改变 BTOS/RPS/Anthropic/ONS 的频率。
- 无头执行时，浏览器 runner 将五个官方导出放入 `var/data/ramp_exports/<scope>.tsv`；同一轮
  discovery 的 payload 在内存中直接交给 ingest，避免重复打开浏览器。报告只读取统一库。
- 当前受控探测使用 2026-08 官方五个 TSV：两个 dataset 的质量门均为 `passed`，五个 scope
  均有可用历史数据；第二次相同 payload 探测返回 `ramp_ai_adoption=no_change`、
  `ramp_ai_spend=no_change`，没有新增 observation/artifact。
- release preview 的 registration、repository registration、latest ingestion、两个 dataset
  quality checks 均通过；随后已通过 `/var/structured_data/releases.yaml` 将
  `ramp_ai_index` 显式发布为 `platform`。当前有效 source mode 为 `platform`。
- 发布后的 L1 复核输出为
  [`ramp-l1-platform-review.md`](/private/tmp/ramp-l1-platform-review.md)，与发布前报告的
  三轴值、趋势状态和 Ramp 补充结论一致；平台切换没有把 Ramp 合并进三轴总状态。
- 回滚路径：执行 `ats data rollback --source ramp_ai_index --kind source` 可只切换 overlay，
  不删除已保存的 artifacts、observations 或 vintages。
