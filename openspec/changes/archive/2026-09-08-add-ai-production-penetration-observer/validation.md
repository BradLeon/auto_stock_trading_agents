# 实施验收证据

## 受治理数据对账（2026-09-08）

从 `DataProducts.ai_production_penetration(periods=["2026-04", "2026-05"])` 读取的 1P API / GLOBAL 结果：

| period | grain | visible production rate | production traffic share | visible / qualified |
|---|---|---:|---:|---:|
| 2026-04 | occupation | 44.95412844% | 68.77000000% | 654 / 294 |
| 2026-04 | task | 32.07831325% | 59.40000000% | 1,992 / 639 |
| 2026-05 | occupation | 46.66666667% | 75.36000000% | 690 / 322 |
| 2026-05 | task | 31.72113290% | 68.66000000% | 2,295 / 728 |

与 OpenSpec 基准一致。当前 packet 为 `overall_status=insufficient_history`；唯一的月度比较是
2026-04→2026-05，广度 `mixed`、深度 `increased`。这不是持续趋势断言。

最新职业覆盖 CCDF 使用 taxonomy `2ea58ff75e4247d26810c37f10c179edc2466cac:taxonomy`，eligible
occupations 为 974：至少 10%/25%/50%/75%/100% 覆盖的职业数为 91/24/3/0/0。

## 重放、CLI 与图表

- 合成 fixture 引入更晚 vintage 后，以旧 `as_of` 重放，`ordered_record_hash` 保持不变；当前版本 hash 改变。
- `ats data ai-production --periods 2026-04 --periods 2026-05 --limit 10 --format markdown --snapshot --chart-dir ...`
  输出方法卡、四序列、TOP10、职业覆盖和 CCDF，并生成 `monthly_comparison` 与
  `occupation_coverage_ccdf` PNG/JSON sidecar。
- 真实运行的专属方法卡含 release `2026-06-26`、published_at
  `2026-06-26T23:21:00+00:00`、taxonomy 诊断和 snapshot manifest。旧 work-adoption Observer
  不返回 `methodology_card`。
- 两次 shadow/report-only 运行对同一输入得到一致的表格、命题状态和图表 data hash；两张 PNG
  的 SHA-256 也逐字节一致。月度比较图 data hash 为
  `5643c1f53d842c7a0c91d4c7f63535570613fca6ba3892b7cd5c34148904f5ca`，CCDF 为
  `d9e207231570b0a6e9ec3cd0417019cd1cb6ab4be15f205c04ba0dc5a6e89ee5`。未显式传 packet 时，
  Chain report 不显示该章节。

## 正式 L1 Evidence workflow 审阅（2026-09-08）

正式命令：

```text
ats evidence layer --sector ai_hardware --layer L1_app \
  --periods 2026-04 --periods 2026-05 \
  --output /private/tmp/ai-production-layer-final/review.md
```

- 命令生成 scope 为 `AI硬件 / L1 AI应用层（Token经济）` 的 packet、持久化 Markdown、两张 PNG 和 JSON sidecar；
  manifest ID 为 `9085c1f6fb032202924a95c3`。命令打印文档与图表目录；snapshot purpose 含 claim、sector、layer 和期间，便于按范围重放。
- 输出顺序为方法卡、四项核心指标、职业任务组合已确认生产化覆盖的分布、分布尾部完整披露、职业/任务高流量 TOP10、文末公式和语义限制。
- 974 个有任务组合的职业中，达到至少 10%/25%/50%/75%/100% 已确认生产化覆盖的职业数为 91/24/3/0/0。50% 以上的 3 个职业是分布右尾的完整披露，不是典型职业样本，也不表示员工、工时或完整工作流覆盖率：Data Entry Keyers 为 5/9（55.56%）、`SOC:15-1131.00` 为 8/15（53.33%）、`SOC:15-1199.08` 为 9/17（52.94%）。当前 Anthropic taxonomy artifact 没有为后两个旧 O*NET-SOC 代码提供标题，因此报告保留稳定代码并显式提示，未擅自补写外部名称。
- 月度比较图和 CCDF 分别使用“职业/任务可见单元生产化率”“职业/任务生产化流量份额”“职业任务组合的已确认生产化覆盖”中文标题/坐标；图片已人工查看，中文字体正常渲染。
- 已生成审阅 Markdown、两张 PNG 与两个 JSON sidecar 于
  `/private/tmp/ai-production-layer-final/`，供本机复核与 manifest 重放；正式默认路径则由各 sector 的 `output_dir` 决定。
- `ats evidence layer --sector ai_hardware --layer L2_compute` 返回 `no_registered_observers`；只运行 L1 claim 的快捷入口在该范围返回 `claim_not_registered_for_scope`。两者均不会运行 L1 或其他层的命题来替代。既有 `observe`、`report` 等 Evidence action 未改动。
- `config/sectors/ai_hardware.yaml` 的 `L1_app.evidence_observers` 声明
  `ai_core_production_workflow_penetration / ai_production_penetration`，并与该层的 Chain
  `claims` 分离。真实配置驱动命令已生成
  `/private/tmp/ai-production-layer-config-driven/review.md`；配置加载、disabled/unknown/duplicate
  声明和 Chain 隔离均有回归覆盖。

## 自动化质量门

- `ruff format`：本变更文件已格式化。
- `openspec validate add-ai-production-penetration-observer --strict`：通过。
- 目标回归：`tests/test_ai_production_penetration.py`、`tests/test_evidence_ai_production_workflow.py`、
  `tests/test_evidence_work_adoption.py`、`tests/test_anthropic_economic_index.py` 与 Chain 附录限定测试通过。
- 全量 `pytest -q` 在约 5% 即显示仓库既有的大量基础测试失败（并非本变更目标测试）；在归档前需
  单独定位/确认这些历史失败，不能把它们视为本变更通过。
- 不受本变更影响的 Layer/PEAD/structured-report 目标回归为 41 通过、1 个既有失败；失败为
  `tests/test_structured_reporting.py::test_cli_structured_quality_and_inventory` 导入不存在的
  `ats.data.products.products`。本变更使用的是现有 `ats.data.products.base` 公开入口，未删除该模块。

## 回滚

停止调用 `observe_ai_production_penetration`，并且不向 `chain.report.render(...,
ai_production_packet=...)` 传 packet，即可撤回 consumer 与只读附录。无需迁移或删除任何
observations、artifacts、taxonomy relations 或 snapshot manifests。阈值变更、持续工作流信号或交易
集成均须另开 OpenSpec change。
