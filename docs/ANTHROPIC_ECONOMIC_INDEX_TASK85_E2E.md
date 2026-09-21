# Anthropic Economic Index Task 8.5 端到端验收

验收日期：2026-09-06

## 隔离边界

- SQLite：`/private/tmp/ats-aei-task85-20260906.sqlite`
- Artifact root：`/private/tmp/ats-aei-task85-20260906-artifacts`
- Release：`release_2026_06_26`
- Commit：`2ea58ff75e4247d26810c37f10c179edc2466cac`
- Period：`2026-04`、`2026-05`
- 本次只执行 release preview，没有应用 platform 发布，也没有写入平台数据库或 artifact 目录。

月度大文件通过 transport-only 本地覆盖读取；artifact 中仍保存官方 commit-pinned URL、官方文件名和完整上游哈希：

| Product | 本地输入 | SHA-256 |
|---|---|---|
| Claude.ai | `/Users/liuchao/Downloads/aei_claude_ai_2026-06-26.csv` | `f974b358bce0e5a8417510c61da4342234cd0de9d9d0b62acf4c6dbcf8ec7b68` |
| 1P API | `/Users/liuchao/Downloads/aei_1p_api_2026-06-26.csv` | `62197f003e001945ad130c2f26f5e07f3fda45ff41644df91444b04fd524a19f` |

## 命令链与结果

按 Task 8.5 规定顺序完成：

```text
validate-source → ingest --force → quality → availability → ai-job → lineage → release-check
```

| 步骤 | 结果 | 核心证据 |
|---|---|---|
| `validate-source` | PASS | 12/12 registration、catalog、budget、adapter 和 unified-ingestion 检查通过 |
| `ingest --force` | PASS | run `e56d438f21229a92aeb3cb29`；discovered 193,857；accepted 153,218；quarantined 0；unchanged 6 |
| `quality` | PASS | overall `passed`；open conflicts 0；artifact lineage 153,218/153,218；13 个规范化指标齐全 |
| `availability` | PASS | dataset `queryable`；153,218 accepted observations；period `2026-04` 至 exposure snapshot `2026-06-26` |
| `ai-job` | PASS | `SOC:15-2031.00` / `1p_api` / `2026-05` 返回职业指标、14 个 O*NET tasks、缺失语义和派生血缘 |
| `lineage` | PASS | observation `341b0fe1da5b1ec095d796eb` 精确指向 1P API artifact `41ed832531efc5fb7b19507b`、固定 commit 和完整上游哈希 |
| `release-check` | PASS | `ready=true`；registration、repository registration、latest ingestion、dataset quality 全部通过；`applied=false` |

## 消费者样例核对

`SOC:15-2031.00`（Operations Research Analysts）的 1P API 2026-05 profile 返回：

- Usage Share：1.01%
- Automation Share：97.08%
- Augmentation Share：2.92%
- 关联任务：14 个，其中 10 个 observed、4 个 `not_published_or_privacy_filtered`
- Observed Task Coverage：0.7142857
- 所有 job/task 派生值均携带 observation IDs、relation IDs 和 `ai_work_adoption/v1`

未发布任务没有被转换为零。职业 Usage Share 的语义边界仍为 Claude 使用量份额，不是从业者采用率。

## 已知 warning

真实 2026-06-26 月度文件包含一批晚于公开 O*NET task taxonomy 的 Task ID，因此 Task ID → occupation 覆盖率为：

- Claude.ai：78.85%
- 1P API：78.45%

该差异已产生明确 warning；未映射 task 仍作为实体和 observation 保留，没有按名称强行关联或静默丢弃。质量报告整体通过，因为这是已识别的官方 taxonomy 版本滞后，而不是解析丢数；后续 release discovery 应继续监控覆盖率和 taxonomy 更新。

## 结论

Task 8.5 的完整命令链在隔离环境通过。数据可被统一 DataProducts/CLI 查询，职业画像、缺失语义、多 artifact 精确血缘和发布前检查均符合当前 OpenSpec 契约。
