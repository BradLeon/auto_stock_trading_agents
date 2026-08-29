# PEAD 官方披露完整性与准确性验收（2026-08-29）

## 结论

本次仅覆盖 `config/pead.yaml` 的 11 个 PEAD `targets`：GOOG、NVDA、SKHY、TSM、ASML、COHR、LRCX、LITE、AVGO、MRVL、MSFT。每家公司最新已披露业绩事件均形成三件套：earnings release、监管披露及 earnings-call transcript。最终为 **33/33 个角色 accepted，11/11 个完整 package**。

这不是按文件名或下载成功计数。每个 accepted 候选均已核验：canonical entity 与 claimed entity 相同、target fiscal period 与 claimed period 相同、来源/角色正确、正文达到角色长度与语义门槛，且无 quarantine reason code。监管文件的 form/accession 亦保留在验收记录中。

## 每家公司结果

| 标的 | 最新事件 | Release | Regulatory filing | Transcript | 最终结果 |
|---|---|---|---|---|---|
| GOOG | Q2 FY2026 · 2026-07-22 | 8-K `0001652044-26-000066` | 10-Q `0001652044-26-000071` | DefeatBeta · 2026-07-22 | accepted |
| NVDA | Q2 FY2027 · 2026-08-26 | 8-K `0001045810-26-000073` | 10-Q `0001045810-26-000075` | DefeatBeta · 2026-08-26 | accepted |
| SKHY | Q2 FY2026 · 2026-07-28 | 6-K `0001193125-26-321989` | 6-K `0001193125-26-321989` | DefeatBeta · 2026-07-29 | accepted |
| TSM | Q2 FY2026 · 2026-07-16 | 6-K `0001046179-26-000451` | 6-K `0001046179-26-000536` | DefeatBeta · 2026-07-16 | accepted |
| ASML | Q2 FY2026 · 2026-07-13 | 6-K `0001628280-26-048235` | 6-K `0001628280-26-048235` | DefeatBeta · 2026-07-15 | accepted |
| COHR | Q4 FY2026 · 2026-08-12 | 8-K `0001193125-26-346860` | 10-K `0000820318-26-000020` | DefeatBeta · 2026-08-12 | accepted |
| LRCX | Q4 FY2026 · 2026-07-29 | 8-K `0000707549-26-000033` | 10-K `0000707549-26-000037` | DefeatBeta · 2026-07-29 | accepted |
| LITE | Q4 FY2026 · 2026-08-11 | 8-K `0001628280-26-055726` | 10-K `0001628280-26-057358` | DefeatBeta · 2026-08-11 | accepted |
| AVGO | Q2 FY2026 · 2026-06-03 | 8-K `0001730168-26-000051` | 10-Q `0001730168-26-000054` | DefeatBeta · 2026-06-03 | accepted |
| MRVL | Q2 FY2027 · 2026-08-27 | 8-K `0001835632-26-000022` | 10-Q `0001835632-26-000025` | DefeatBeta · 2026-08-27 | accepted |
| MSFT | Q4 FY2026 · 2026-07-29 | 8-K `0001193125-26-323632` | 10-K `0001193125-26-323660` | DefeatBeta · 2026-07-29 | accepted |

外国发行人不会被强行套用 10-Q/10-K：SKHY、TSM、ASML 的合规监管角色由相应 6-K 提供。一个 6-K 可以同时承载 earnings release 和 regulatory filing，但它们仍以各自角色独立记录和校验。

## 实际运行与人工复核位置

完整首次隔离运行的 Markdown 报告在：

`/private/tmp/pead-official-disclosures-20260829-live/PEAD_OFFICIAL_DISCLOSURE_ACCEPTANCE.md`

其下载文档按 `标的/财年季度-角色.md` 放在：

`/private/tmp/pead-official-disclosures-20260829-live/`

首次运行中 COHR filing 和 LRCX release 先被严格拒绝；针对这两个角色修复后，在新的隔离库重跑并通过，资产与候选审计记录在：

`/private/tmp/pead-official-disclosures-20260829-remediation/`

`/private/tmp/pead-official-disclosures-20260829-remediation.sqlite`

首次隔离库的 31 个 accepted 候选，加上 remediation 库的 2 个 accepted 候选，共 33 个；所有记录的 `expected_entity = claimed_entity`、`target_period = claimed_period`，`reason_codes = []`。正文字符数范围为 4,055–589,345（release/filing）与 38,956–59,728（transcript），不存在空正文或 teaser 被视为完整资产的情形。

## 修复记录

- **COHR 10-K**：有效文件在 Inline-XBRL 开头大量使用历史名称 `II-VI`。实体检查现在会读取配置的实体别名，并保留 40k 字符的开头窗口，避免在仍然受限的 opening-document 范围内漏掉真实发行主体；不放宽 ticker/名称的边界匹配。
- **LRCX release**：正文披露的是 “quarter ended June 28, 2026”，未写 `Q4 FY2026`。日期型期间仅当其年份同目标财年一致、且期末日至已解析业绩事件的间隔为 0–100 天时才通过；同财年但更早的 March quarter 回归用例仍会拒绝。

## 非业务副作用

运行通过独立 `--db` 和 `--artifact-root` 执行。JSON 明确记录：LLM、PEAD scoring、Chief、broker orders 与 trades 均为 `0`；未写生产文档库，未触发任何交易。
