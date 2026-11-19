# 生产首采核验（2026-11-12，台北晚间）

**写入前备份：** `var/backups/data.sqlite.before-ramp-v2.20261112`（sqlite `.backup`，quick_check ok）。
**运行日志：** `var/logs/ramp-v2-first/`。

## 5.2 FRED 部分（`ramp_ai_index_fred`）

- **命令：** `ats data release-check --source ramp_ai_index_fred --ingest-new --force-check`，run `45d6fddf97f39bb20e6d9fc9`。
- **结果：** 14 条序列全部发现，无失败；644 条 accepted（14 × 46 个月，2023-01 … 2026-10），quarantined 0；全部质量状态为 accepted，没有和 v1 同月值被判为冲突。
- **总体采用率（`RAMPAIALL`）：** 2026-08 55.17、2026-09 55.83、2026-10 56.80，与网页 v2 导出逐位一致。

## 5.3 网页部分（`ramp_ai_index`）

### 第一次运行（失败，已修）
网页部分记 `export_unreadable`，FRED 部分不受影响。暴露了四个问题：

1. 等待“网络空闲”超时：页面一直有后台请求。改为等 Get the data 按钮可见。
2. 点击后没等子按钮（By model maker、Shares）渲染出来就去查找；另有隐藏的重复元素。改为等待并只点可见按钮。
3. 适配器在同一数据集内，一张图失败就把同一个错误抄给所有图。改为按图记录失败。
4. Shares 导出是小数（每周合计 1.0），不是百分比。改为按每周合计识别单位：小数 ×100 存为百分比，原值保留；Volumes 指数仍然拒收。

在临时目录用真实页面试跑时又发现：页面上 Health care、Retail、Technology and media 三个行业，分别就是 FRED 的 Health Care and Social Assistance、Retail Trade、Information，40 个月数值一致（差异小于 1e-10）。已在 `fred_series` 中加 `page_segment` 别名，网页的这 3 个行业改为只做交叉核对。

修复提交：41585ed。

### 第二次运行
- **命令：** `ats data release-check --source ramp_ai_index --ingest-new --force-check`，37 秒；5 张图均由 runner 导出，并写入 `var/data/ramp_exports/`（每张附 json）。
- **`ramp_ai_adoption`：** 1200 条 accepted（Mistral AI、DeepSeek 2 家供应商 + 13 个行业，2023-07 … 2026-10）；908 行属于 FRED 已覆盖的序列，只做交叉核对，`fred_page_mismatch` 为 0。
- **`ramp_ai_spend`：** partial。人均支出 120 条、按厂商的 token 份额 451 条 accepted；按模型的 token 份额被“重复单元格”质量门挡下。
  - 原因：Ramp 导出中有 4 列名称只差零宽空格（U+200B）。
  - 修法（提交 453a74d）：名称含不可见字符的列加稳定后缀 `_ZW<n>`，保持为不同序列，不合并。
  - 随后 `ats data ingest --source ramp_ai_index --dataset ramp_ai_spend` 读取当天的入站文件（未再开浏览器）：5229 条 accepted，571 条 unchanged。
- **核对：** 按模型的最新一周（2026-11-01）Claude Opus 5.5 为 28.72%、GPT-6 Astra 为 16.07%，与页面排行榜（28.7%、16.1%）一致。

### v2 入库汇总（全部 accepted）

| scope | 来源 | 实体数 | 观测数 | 期间 |
|---|---|---|---|---|
| adoption_overall | FRED | 1 | 46 | 2023-01 … 2026-10 |
| adoption_overall_models | FRED / 网页 | 3 / 2 | 138 / 160 | 2023-01 / 2023-07 … 2026-10 |
| adoption_sector | FRED / 网页 | 7 / 13 | 322 / 1040 | 2023-01 / 2023-07 … 2026-10 |
| adoption_business_size | FRED | 3 | 138 | 2023-01 … 2026-10 |
| spend_per_employee_overall | 网页 | 3 | 120 | 2023-07 … 2026-10 |
| token_spend_share_by_maker | 网页 | 8 | 451 | 2025-01-12 … 2026-11-01（周） |
| token_spend_share_by_model | 网页 | 209 | 5229 | 2025-01-12 … 2026-11-01（周） |

## 5.4 L1 观察器

- **命令：** `ats evidence ai-production --sector ai_hardware --layer L1_app`，不调用模型，产物写入 Obsidian `Evidence-ai_hardware-L1_app-2026-11-12*`。
- **Ramp 段落：** 状态 `ok`，可用 scope 7/7，口径 v2。总体付费采用率 56.80%（2026-10），较 2026-09 变化 +0.97pp；企业规模：大型 66.71%、中型 63.21%、小型 51.23%；token 花费份额（2026-11-01 当周）：Anthropic 49.32%、OpenAI 46.09%。7 张图与 7 份数据表齐全。
- **三轴结论不受影响：** 同一时点分别以“Ramp 可用 / Ramp 不可用”各跑一次：`overall_status` 都是 `breadth_without_confirmed_depth`，三条轴的趋势状态完全相同。

## 遗留

1. **release-check 的“部分成功”会把整个上游版本记为已入库。** 失败的那张图要等上游内容变化才会被重新入库（这次是手动 ingest 补上的）。这是通用逻辑，不限于 Ramp，建议另行处理。
2. 第一次失败的队列任务 `948429cb…` 仍处于 `retry_wait`。若被重试，入站文件已是当天的新导出，只会得到 no_change，无害。
3. **磁盘占用：** Chromium 实际约 610 MB（含无头版 219 MB）。
