# 数据层旧路径退役验收（2026-09-01）

变更：`unify-data-layer-architecture`。本报告是 Task 11 的生产切换证据；它不把 Workflow 的 memory 结果误写成数据资产。

## 结论

结构化与非结构化持久数据均已发布至 `var/data.sqlite`。`var/ats.sqlite` 只保留 workflow memory（dossier、事件、评审、决策、交易、报告等）；不再保存结构化 observation、文档、证据、measurement 或数据采集运行记录。

## 退役范围与恢复

- 已删除旧模块：`ats.data_platform`、`ats.structured`、旧 `UnstructuredRepository` 以及 `config/structured_data.yaml`、`config/sources.yaml`、`config/news_sources.yaml`。
- 已删除旧 SQLite 的 `structured_*`、`source_documents`、`document_*`、`evidence_*`、`measurement_*`、`data_sources`、`ingestion_runs`、`newsletter_cursors` 和两个结构化 view。
- 未删除：`data_migrations` 与 workflow memory 表；它们不属于数据输入资产。
- 可恢复副本：`var/data_migration_backups/ats.retirement.937574a53be3d482.sqlite`（实际退役前完整副本）。平台库的 `data_retirement_manifests` 保存源库 hash、对象清单和备份路径。
- 恢复方式：停止服务后，以该已验证副本恢复 `var/ats.sqlite`；不要通过 runtime mode 回退到旧路径。

## 对账与删后验证

退役前逐表验证旧事实数据是新库的子集。文档、候选、版本、实体关联、chunk、处理记录、证据事实/投影，以及结构化 observation、series、artifact、snapshot、ingestion 都无缺失行。结构化 source/dataset/metric/entity 目录由新 canonical catalog 重新规范化，按“新 catalog 覆盖”验收，而非错误要求旧 YAML 字节相同。

删后实际执行：

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data config
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data company NVDA
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data health
PYTHONPATH=src .venv/bin/python -m compileall -q src/ats
```

结果：catalog `validation.valid=true`；NVDA 返回 platform 公司数据；健康检查从 `data_documents` 返回已发布文档来源；编译通过。此前 Task 13–17 的消费者验收记录继续作为 Agent/Workflow E2E 证据；本次只修改其持久化边界，不修改 memory 输出合同。

## 运行限制

本机 pytest 初始化会在 `_pytest.capture` 段错误退出（139），因此本次没有把它伪报为通过。上列 CLI smoke、迁移/退役 manifest 与既有消费者验收是当前可复现证据；恢复 pytest 环境后应重跑全量测试集。
