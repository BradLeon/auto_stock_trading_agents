# Dataflow 验证证据与 Phase F 读取资格

本 runbook 对应 `complete-target-dataflow` 任务 4.1–4.5。资格记录与当前 source/consumer 开关分离：查询不会切换 `read_mode`，也不会触发采集或交易。所有现有消费者在证据不齐时均 fail closed。

## 证据登记

先从 `config/data/target_dataflow_coverage.yaml` 读取 consumer 的 `domain`、`contract_version` 和 `required_evidence`。每种必需证据都必须使用同一精确 scope，并至少提供一个仓库内代码或配置路径作为 fingerprint。`as-of` 必须带时区；摘要只写脱敏结果和命令概述，不写原文、token、密码、券商账户或环境变量值。

示例（只登记一个 Fundamental 的结构化数据写入证据，不足以取得资格）：

```sh
ats data assurance record \
  --consumer fundamental --domain company --contract-version target-dataflow-v1 \
  --evidence-type write --outcome passed \
  --scope-json '{"entity":"NVDA","dataset":"company_financials"}' \
  --as-of 2026-09-24T00:00:00Z \
  --command-summary 'isolated governed-ingest replay; no provider secrets recorded' \
  --summary 'one accepted vintage and source artifact read back' \
  --environment-name ATS_DATA_DB_PATH \
  --fingerprint-path src/ats/data/pipelines/structured/ingest.py \
  --fingerprint-path config/data/structured.yaml
```

每个事件 append-only 写入 Data DB 的 `dataflow_assurance_events`，包含 manifest hash/version、依赖文件 hash、scope、前置事件、as-of、环境变量名称、结果和时间。撤销通过追加 `revoke` 事件，不覆盖旧证据。读取资格要求 consumer 配置列出的每个证据类型均有最新、未撤销、未过期且指纹匹配的 passed 事件；对账范围不一致、依赖漂移、缺少回退/rollback 证据或任一证据失败都会返回 `ineligible`。

需要纳入变更审阅时，可导出脱敏事件视图并保存为版本化报告：

```sh
ats data assurance history --consumer fundamental --domain company \
  --scope-json '{"entity":"NVDA","dataset":"company_financials"}'
```

## 资格查询与撤销

```sh
ats data assurance query \
  --consumer fundamental --domain company --contract-version target-dataflow-v1 \
  --scope-json '{"entity":"NVDA","dataset":"company_financials"}'

ats data assurance revoke --event-id EVENT_ID --reason 'source policy changed'
```

`scope-json` 必须与登记证据完全一致。若实现、catalog/配置或覆盖清单发生变化，重新计算的 SHA-256 不匹配即拒绝资格；重新验证后按相同 scope 写入新证据。查询是只读操作。

目前没有为任何 consumer 填写完整真实证据集，故预期结果是 `ineligible`。隔离 SQLite 的代码路径检查只验证账本机制，不能被登记为真实来源、消费者对账或回滚演练的生产通过证据。该接口供 Phase F 调用；本 change 不改变当前 consumer routing、Schedule 或 live Trader。

## 持久化采集队列（实施中）

本 change 新增/目标化的持久化写入入口使用本机 SQLite ledger `var/persistent_ingestion.sqlite`。定时 `ats data refresh`、受管人工 `ats data ingest` / `article-ingest`、FactSet schedule、事件日历各来源刷新、calendar finalize 与允许的 cache miss 先登记 queue task；worker 在有效 lease 下执行 allowlisted `ats data` 命令。结构化 Ingestion Pipeline、文章 data-only 入口、FactSet 子管线和日历 adapter 在目标生产库路径校验 worker lease。明确带独立 `--db`、`--artifact-root` 与 `--force` 的隔离验收仍可直接运行，但不得用它作为生产刷新证据。

```sh
# 查看任务状态（命令及结果只保存脱敏摘要/哈希）
uv run python -m ats.data.persistent_queue status --limit 50

# 由受管 worker 处理当前可运行任务；launchd 的 data-refresh tick 也会处理其刚入队任务
uv run python -m ats.data.persistent_queue worker --max-tasks 25

# 人工触发应优先使用正常入口，CLI 会创建 managed task 并交给 worker
uv run ats data ingest --source <registered-source-id> --dataset <registered-dataset-id>
uv run ats data article-ingest --source <registered-unstructured-source-id>
```

队列使用稳定 `source + scope + trigger + trigger_ref + policy_fingerprint` 幂等；维护 lease、heartbeat、attempt/backoff、取消与追加事件。Refresh Controller 的账本回指 queue task，queue item 再关联 ingestion/check、raw、admission/quarantine 和 publication 引用。Runtime 行情/期权/券商查询不进入该队列。

新目标路径代码级验收通过，不代表旧 scheduler/Agent/Workflow caller 已迁移，也不代表所有来源和 launchd 部署已获真实数据验收。按用户确认的范围，legacy caller 的问题只记录，不在本 change 修复。来源 A/B/C 与旧入口见 [UNSTRUCTURED_REFRESH_INVENTORY.md](UNSTRUCTURED_REFRESH_INVENTORY.md)；其中旧 PEAD Evidence Observer transcript/search 路径待固定来源和 Layer 迁移，仅作差距记录。新文章 data-only adapter 的五个受管来源配置读取 `config/data/unstructured.yaml`；新 Refresh Plan 也只接受这份 target registry 中显式登记的非结构化来源，legacy compatibility view 不构成刷新授权。`catalog.yaml` 现仅装配 `structured.yaml` 和 `unstructured.yaml`，loader 校验两领域 source identity 不重复。SEC 官方 filing 正文、固定季度 transcript、RSS aliases 与静态知识语料的完整登记仍未完成，因此 task 2.2.10 继续开放。FactSet 的 LaunchAgent live handoff、SemiAnalysis 生产凭据与真实来源授权、逐来源 freshness/消费者读回也未通过；不得因此启用新 job 或取得 Phase F 资格。十角色产品/API/投影边契约见 [target_dataflow_coverage.yaml](../../config/data/target_dataflow_coverage.yaml)。隔离重放只证明代码契约和故障语义，不证明生产授权、来源 freshness 或真实消费者读回。
