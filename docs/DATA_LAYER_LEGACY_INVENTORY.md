# 数据层 Legacy Inventory 与迁移准入

日期：2026-08-26  
状态：阶段二进行中；所有下列对象均未获准删除。

## 目标

此清单把“旧实现”拆成可独立迁移、对账、切换和退役的对象。它不是一次性删除清单：每个对象都必须先满足数据迁移、消费者切换、回滚演练和稳定观察条件。

机器可读的唯一来源是：

- [`config/data/legacy_inventory.yaml`](../config/data/legacy_inventory.yaml)
- [`config/data/migration.yaml`](../config/data/migration.yaml)

只读检查：

```bash
ats_cli data migration-plan
```

输出包含旧对象、消费者、迁移域、备份要求和验证结果。命令不打开生产数据库、不复制数据、不修改 release mode。

## Legacy 对象与目标所有权

| 旧对象 | 目标 owner | 退役前的回退 |
|---|---|---|
| `ats.data_platform` | `ats.data.products` | import compatibility facade |
| `ats.structured` catalog/repository/pipeline/products/rollout | 对应 `ats.data.catalog`、`stores`、`pipelines`、`products`、`rollout` | source/consumer mode `legacy` 或 release rollback |
| flat `ats.data` Provider 与文档模块 | `ats.data.adapters`、`pipelines`、`products` | 每 source/consumer 独立回退 |
| `memory.store` 中的数据层表 | `ats.data.stores` | 保留源表及已验证 backup |
| `structured_data.yaml`、`sources.yaml`、`news_sources.yaml` | `config/data/*` | catalog legacy overlay |
| structured artifact 与文档根目录 | data stores | 保留原始文件与 manifest |

交易、决策、持仓、绩效、dossier 和 Agent/Workflow run memory 仍属于 `ats.memory`，不在数据层迁移/删除范围内。

## 迁移域

| 域 | 源资产 | 目标 owner | 必需对账 |
|---|---|---|---|
| `structured-legacy-measurements` | `measurement_series`、`measurement_points`、legacy ingestion 元数据 | `ats.data.stores.structured` | 行数、稳定 ID、期间/vintage、质量、血缘、查询结果 |
| `unstructured-documents` | 文档、版本、实体关系、alias、chunks、处理 ledger | `ats.data.stores.unstructured` | 行数、稳定 ID、内容 hash、血缘、查询结果 |
| `unstructured-evidence` | observation/facts/projections/claims | `ats.data.stores.unstructured` | 行数、稳定 ID、内容 hash（适用时）、血缘、查询结果 |

每个运行都会生成 versioned manifest。中断恢复必须从 manifest 中已确认的批次继续，或幂等重放该批次；不得覆盖原始文档版本、artifact 或历史 observation。

## 消费者切换顺序

1. PEAD 与公司基本面；
2. Sector 与宏观/区域指标；
3. Evidence、Chain、research 与文档读取；
4. Chief 和 scheduler 入口。

每个消费者必须经历 `legacy → shadow → platform`，并保留单独回滚到 `legacy` 或 `fallback` 的能力。一个消费者通过不代表其他消费者自动切换。

## 删除门槛

删除任一 legacy 对象前，必须同时具备：

1. 对应迁移域完成、无未解释差异；
2. 所有调用方已切换并通过端到端回归；
3. source/consumer rollback 已演练；
4. 已验证 backup、manifest 与恢复步骤存在；
5. 达到约定稳定观察期。

在这些条件全部满足前，OpenSpec 变更保持 active，禁止归档。
