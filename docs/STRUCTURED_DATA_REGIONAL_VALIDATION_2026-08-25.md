# 台湾/韩国官方出口序列专项验收（2026-08-25）

> 后续状态：本报告正文记录专项当时尚未改变生产默认值；完成消费者重放与完整回归后，阶段 12 已将 `chain_regional` 默认切至 `platform`。以消费者验收和机器目录为当前状态准绳。

## 结论

台湾财政部与韩国银行 ECOS 两条官方月度序列均已通过隔离真实源验收：每个来源取得 20 个连续月份，覆盖 2024-12 至 2026-07；中央准入各接受 20 条、隔离 0 条；同比/环比与 Chain 兼容输出零差异。测试没有读取或覆盖生产数据库。

首次真实测试暴露了一个真实缺口：本机未配置 `KR_ECOS_API_KEY`，ECOS `sample` key 单次只返回 10 行，原实现因此只到 2025-09，无法计算同比。修复后按不超过 10 个月拆分请求，仍使用官方接口和官方示例 key；第二次真实测试取得完整 20 个月并通过质量门。

## 可复现命令

```bash
PYTHONPATH=src .venv/bin/python scripts/smoke_structured_regional.py \
  --root var/structured_regional_validation_20260825
```

## 验收结果

| 检查项 | 台湾财政部 | 韩国 ECOS |
|---|---:|---:|
| 采集终态 | succeeded | succeeded |
| 原始水平记录 | 20 | 20 |
| accepted / quarantined | 20 / 0 | 20 / 0 |
| 覆盖 | 2024-12～2026-07 | 2024-12～2026-07 |
| 月份连续 | 是 | 是 |
| 可计算 MoM | 19 | 19 |
| 可计算 YoY | 8 | 8 |
| 与旧 Chain 输出差异 | 0 | 0 |
| 原始 artifact | 1 个，70,012 bytes | 1 个，6,094 bytes |

## 数据在哪里检查

- 隔离数据库：`var/structured_regional_validation_20260825/regional-smoke.sqlite`
- 台湾财政部原始 CSV：`var/structured_regional_validation_20260825/artifacts/36/360e9ea99fc39de0f228196a3e0737df6273775eee42bf04d18d67f4787de979.bin`
- 韩国 ECOS 原始分页 JSON：`var/structured_regional_validation_20260825/artifacts/a1/a1abba04bf0a82fa0ae934bb1a46ee49d9ab640ae9861febf628dd8e02ada2ec.json`

`.bin` 是因为 artifact store 对非 JSON 媒体使用通用二进制后缀；内容是带 BOM 的 UTF-8 CSV，可用文本编辑器或 `iconv`/Python 直接查看。数据库与 artifact 目录位于 `.gitignore` 中，不会提交到 GitHub。

## 时间语义说明

两个当前接口都没有在每条月度记录上给出可验证的精确发布日期。因此平台没有用抓取时间伪造 `published_at`，而是明确记录：

- `published_at = unknown`；
- `known_at = 本次系统实际抓取时间`；
- coverage metadata 中分别标记 `not_supplied_by_dataset_endpoint` 和 `not_supplied_by_statistic_search`。

这保证未来 `as_of` 重放不会声称系统在首次抓取之前已经知道这些值。若后续接入官方发布日历，应以新的、有证据的 vintage 补充，不回写或猜测旧发布时间。

## Feature flag 与回滚

Chain 区域序列提供 `legacy`、`shadow`、`platform`、`fallback` 四种读取模式，默认保持 `legacy`：

```bash
export ATS_STRUCTURED_CHAIN_REGIONAL_MODE=platform
export ATS_STRUCTURED_CHAIN_REGIONAL_MODE=legacy   # 回滚
```

自动化回滚演练已验证：切换为 `platform` 使用新平台结果；仅恢复同一环境变量为 `legacy` 即回到原有 `SeriesPoint` 契约，无需删表、改数据或影响其他消费者。生产默认值本次未切为 platform，后续仍须经过完整更新周期观察。
