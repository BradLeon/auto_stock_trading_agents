# Ramp AI Index 采集与运行清单

本数据源服务 L1 AI 应用层，仅保存五个公开图表 scope：`adoption_overall`、
`adoption_overall_models`、`adoption_sector`、`spend_per_employee_overall` 和
`model_market_share_overall`。Business size 与 Geographies 只做发现，不进入发布。

## 访问与发现

- 默认入口是 [Ramp AI Index](https://ramp.com/data/ai-index#adoption#overall) 的可见
  `Get the data` 按钮。按钮复制官方 TSV 后才进入解析；不使用截图、OCR 或猜测隐藏 URL。
- Ramp 单独由外部 scheduler 每 10 天执行一次 `data release-check --source ramp_ai_index
  --ingest-new --force-check`；不把 BTOS/RPS/Anthropic/ONS 的发现频率改成 10 天。页面导出可用时无需账户。
- 一次探测应在同一受控采集会话中处理五个白名单 scope；审阅报告只读结构化库，不因报告运行重复打开浏览器。
- 无头调度时，桌面浏览器 runner 将五个导出按固定文件名写入
  `var/data/ramp_exports/<scope>.tsv`（也可用 `ATS_RAMP_OFFICIAL_EXPORT_DIR` 指定受管目录），再执行
  `release-check`。适配器会先读取这个入站快照；同一轮 discovery 的 payload 会直接交给 ingest，
  不会再次打开浏览器。入站文件只作为下一次 probe 的官方导出快照，最终事实仍写入统一 SQLite/artifact store。
- 首版不接入 Developer API/MCP，也不要求公司邮箱注册。适配器不会读取
  `RAMP_DATA_API_KEY`、不会等待 API 重试；未来若获得企业授权需另开 OpenSpec 变更。
- 已有官方 TSV fixture 时直接入库；没有 fixture 时才需要受控浏览器点击 `Get the data`。
- 每个 scope 独立失败和发布。剪贴板拒绝、控件消失、空/损坏 TSV 使用
  `export_unreadable`；网络不可达使用 `source_unreachable`；新定义或样本规则使用
  `methodology_drift`。

## 数据字典

| scope | 统计主体/分母 | entity 形状 | 核心 metric |
|---|---|---|---|
| `adoption_overall` | Ramp 相关美国企业中当月有正向 AI 付款的企业 / 相关企业 cohort | `RAMP_OVERALL` | `ai.ramp.paid_business_adoption_share` |
| `adoption_overall_models` | 同一 Ramp cohort，按 vendor；企业可同时支付多个 vendor | `RAMP_VENDOR:<VENDOR>` | `ai.ramp.vendor_adoption_share` |
| `adoption_sector` | 同一 Ramp cohort，按 Ramp 内部 NAICS 分组 | `NAICS:<GROUP>` | `ai.ramp.paid_business_adoption_share` |
| `spend_per_employee_overall` | AI 付款 cohort 的美元支出 / 员工数，按分位数 | `QUANTILE:MEDIAN/TOP10/TOP1` | `ai.ramp.ai_spend_per_employee` |
| `model_market_share_overall` | Token Spend Management 连接企业的模型归因 API spend | `MODEL:<PROVIDER>:<MODEL>:<TYPE>` | `ai.ramp.api_spend_share` |

`Monthly change (pp)` 与 `Yearly change (pp)` 作为 `provider_monthly_change_pp` 和
`provider_yearly_change_pp` 原始发布字段保存。vendor share 不要求合计 100%；model share
不是全 Ramp 企业采用率。所有 observation 都保留原始列、reference month、methodology regime、
artifact、payload hash、known/fetched time 和 quality 状态。

## L1 报告展示规则

- 五个 scope 各自保留一张趋势图；Overall、vendor 和 NAICS 行业使用全部月度点，但横轴只标注起始期、年度一月和最新期，避免月份标签重叠。
- `spend_per_employee_overall` 的报告图只展示 Median 与 Top 10% 并使用对数纵轴，以免 Top 1% 极端值压扁中位数趋势。Ramp 当前公开导出没有 Top 30% 字段，因此不插值、不把 Top 1% 改名为 Top 30%；数据表仍保留源端 Top 1%。
- `model_market_share_overall` 的报告图按最新月份份额最高的 8 个模型绘制 100% 堆叠面积，其余模型合并为 `Other`。底层表仍保留每个 provider/model 的完整 source-native 行，图表不改变观测值。

## 运行检查

1. `data validate-source ramp_ai_index`
2. `data release-check --group ai_adoption --force-check`
3. 使用隔离数据库执行 `data ingest --source ramp_ai_index --dataset ramp_ai_adoption`
   和 `--dataset ramp_ai_spend`。
4. `data quality --dataset ramp_ai_adoption`、`data availability --dataset ramp_ai_spend`。
5. 通过 DataProducts 或 L1 Observer 查询，不直接读取物理表。

相同 `page_url + chart_slug + latest_release_label + payload_sha256` 幂等；同期间新
payload 追加 vintage，`as_of` 可重放旧值。未发布或隐私过滤不转为零，不前向填充缺失月份。

## 周期探测与发布规则

- `reference_period` 是图表数据所属月份，例如 `2026-08`；`fetched_at` 只是本次探测时间，
  两者不得混用。
- 参考期间和 payload hash 都未变化时返回 `no_change`，不产生新 observation。
- 参考期间变新时追加月份；同期间 hash 变化时追加 revision vintage，旧值仍可按 `as_of` 查询。
- header、方法文本、technology scope 或 methodology fingerprint 变化时标记
  `methodology_drift`，停止自动 platform 发布并保留诊断。
- 首次受控 probe 的五个 scope 均通过质量门后，source 才能通过 release overlay 显式切换为
  `platform`；后续新月份沿用相同质量门，revision/methodology drift 不静默自动升级。
