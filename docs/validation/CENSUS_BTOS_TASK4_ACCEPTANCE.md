# Census BTOS Core 新口径接入验收（OpenSpec Tasks 4）
验收日期：2026-09-09。验收数据库与 artifact 目录位于 `/private/tmp`，不进入生产库。

## 范围与口径

- 来源：美国 Census Bureau Business Trends and Outlook Survey 官方 API。
- 问题口径：过去两周是否在任一业务职能中使用 AI，以及未来六个月是否预计使用 AI。
- 日期门：只准入 collection start 不早于 2025-11-17 且当前/未来两个问题均通过批准指纹的 period。
- 统计主体：调查覆盖范围内的美国 employer businesses；不是员工、席位或任务采用率。
- `Yes` headline 是各回答集合中的一个响应比例；完整的 Yes / No / Do not know 回答同时保留。
- 州和 MSA 数据被过滤；保留全国、NAICS2、NAICS3、企业规模及行业×规模切片。
- `/answers` 无参数调用目前不返回可用结构化记录，答案字典因此从 period data 的回答行提取，并在 artifact metadata 中说明该 fallback。

## 官方回填结果

执行命令：

```text
ats data release-check --source us_census_btos --ingest-new --force-check \
  --db /private/tmp/btos-task4-20260909.sqlite \
  --artifact-root /private/tmp/btos-task4-20260909-artifacts
```

结果：

| 验收项 | 结果 |
|---|---:|
| 最早准入 period | 88（2025-11-17 至 2025-11-30） |
| 最新准入 period | 107（2026-08-10 至 2026-08-23） |
| 新口径 periods | 20 |
| 独立 query-slice artifacts | 20 |
| observations | 39,281 |
| quarantined | 0 |
| period < 88 的旧口径 observations | 0 |

重复执行 discovery 返回 `no_change`，collection identity 为
`BTOS_COLLECTION:fd64aeee38ddc3bf051668172354e34e167ac2124dee1a4f75d941f1e03859dc`，未再次采集。

## 最新 DataProduct 抽查

正式 `DataProducts.census_btos_ai_snapshot()` 在不指定 period 时按 BTOS 数字 period 排序，选择 period 107：

- 当前使用 AI 的企业比例：22.4%。
- 未来六个月预计使用 AI 的企业比例：25.9%。
- 当前减未来：-3.5 个百分点。
- 相邻 period 变化：+0.6 个百分点（描述性变化，不代表统计显著）。
- 最近连续四期当前采用率移动平均：21.85%。
- 最新期返回全国、行业、企业规模和行业×规模四类切片，并携带 observation/artifact lineage。

## 自动化测试证据

`tests/test_ai_adoption_source_adapters.py` 覆盖：

- 新旧问题口径与日期门。
- 地理过滤、NAICS/规模实体和 source-native slice。
- 完整响应、headline Yes、standard error、抑制缺失语义。
- 百分比范围、负 standard error、回答加总、重复 cell、schema drift 和源行对账。
- 流式下载、完整上游响应哈希和最大字节限制。
- 四期均值、连续 period 变化、当前—未来差、公式与输入 IDs。
- DataProduct strata、freshness、quality、lineage、vintage 和 Agent summary。
- 重复写入 `no_change`、同期间修订追加 vintage、`as_of` 重放旧值。

专项测试结果：16 passed。
