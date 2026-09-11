# L1 AI 生产化与应用扩散 v2 端到端验收

验收时间：2026-09-09/10。全部真实源操作在 `/private/tmp` 隔离目录完成，不触发 Chain、PEAD、
Chief、组合或交易工作流。

## 官方发现与入库

统一命令：

```text
ats data release-check --group ai_adoption --ingest-new --force-check \
  --db /private/tmp/ai-adoption-e2e-20260909.sqlite \
  --artifact-root /private/tmp/ai-adoption-e2e-20260909-artifacts
```

|来源|发现状态|真实可用期间|observations|说明|
|---|---|---:|---:|---|
|Anthropic Economic Index|new_release / succeeded|2026-04、2026-05|153,218|release_2026_06_26，commit `2ea58ff...`|
|Census BTOS Core|new_release / succeeded|period 88–107|39,281|仅 2025-11-17 后新问题口径|
|RPS/FRED|new_release / succeeded|2024-Q3–2026-Q2|38|五条 work-only series 对齐|
|ONS BICS AI|question_not_fielded（wave 163）|历史 wave 92–159|5,081|最新波没问 AI；历史 wave 159 明确回填，不继承为 163|

`ai_work_adoption` 质量报告为 `passed`；各来源 quarantined 均为 0。隔离 SQLite 为 768 MiB，
artifact query slices 为 65 MiB，人类审阅表格/图片为 2.2 MiB。

## DataProduct 与 Observer

命令：

```text
ATS_DATA_DB_PATH=/private/tmp/ai-adoption-e2e-20260909.sqlite \
ATS_DATA_ARTIFACT_ROOT=/private/tmp/ai-adoption-e2e-20260909-artifacts \
ats evidence layer --sector ai_hardware --layer L1_app \
  --output /private/tmp/ai-adoption-e2e-review.md \
  --chart-dir /private/tmp/ai-adoption-e2e-charts
```

结果为 `mixed_evidence`：BTOS 企业采用广度和 RPS 上周工作使用的来源原生序列均为 mixed；ONS
组织深度只有一个可比问题 regime 的 snapshot；Anthropic 只有 2026-04/05 两个月，后两轴均为
`insufficient_history`。该结果不表示四个百分比可直接比较，也没有综合分数。

四轴真实最新期间分别为 BTOS 107、RPS 2026-Q2、ONS wave-159、Anthropic 2026-05；系统没有
前向填充到共同日期。Manifest `4681ea47da336d31130da909` 离线重放得到 24,563 条输入 observation、
8 个 artifact 和同一 `mixed_evidence` 判断，无网络请求。

审阅产物顺序为命题判断、四轴总览、印证/冲突、三个调查来源、Anthropic 总体分布与四指标、
TOPN、公式/限制/来源。CSV/JSON、PNG 及每图 JSON sidecar 共用同一 manifest；sidecar 固定标题、
定义、单位、来源、期间、rows hash、observation IDs 和 renderer version。

## 调度、故障与回滚

- `config/data/schedules.yaml` 登记每周 `release-check --group ai_adoption --ingest-new`，单并发和
  60/300/900 秒退避；外部部署平台负责真正创建任务。
- fixture 覆盖 unreachable、404/损坏 payload、schema/question drift、未知 我metric、重复内容、
  revision vintage、单源失败隔离、ONS `question_not_fielded` 和 source age/freshness。
- `claim_id` 保持不变，配置仅把 `ai_hardware/L1_app` 切换到 `claim_definition_version=v2`。
  改回 `v1` 只切换 consumer runner，不删除任何新 observation、artifact 或 snapshot。
- BTOS Supplement 与 Eurostat 明确不接入；RPS 自报和 ONS 条件 wave 限制均写入方法卡。

## 测试边界

本变更聚焦 source/DataProduct/Observer/visualization 共 73 项测试全部通过，OpenSpec strict validation
通过。扩展回归共 155 项中 122 项通过；其余为既有测试硬编码旧 source/migration 数量、已经迁移的
兼容 import 路径、未安装可选 `apscheduler`/`pandas_market_calendars`，以及旧 Workflow memory
fixture 未初始化。这些失败不经过四轴代码路径，已作为基线技术债记录，未用“全仓通过”掩盖。
