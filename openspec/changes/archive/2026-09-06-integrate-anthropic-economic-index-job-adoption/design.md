## Context

本设计实现 [proposal.md](./proposal.md) 中定义的能力。当前 structured data 平台已经具备 source/dataset/metric 注册、不可变 artifact、observation vintage、`as_of` 查询、质量状态、DataProducts 和来源发布生命周期，但适配器批次中的全部记录目前会绑定到首个 artifact，且平台没有版本化实体关系。`NativeRecord` 已预留 `slice_key`，但 artifact 尚无对应 key，pipeline 也未使用该映射。

Anthropic Economic Index 的网页 Job Explorer 是展示层，不适合作为稳定采集接口。官方 Hugging Face repository 提供 release 目录、数据文档、两个 source-product 月度 CSV、SOC/O*NET taxonomy，以及 labor-market impact 快照。月度文件较大，源 schema 和研究方法可能随 release 改变，且隐私过滤使可见子节点不能保证向上完整加总。

## Goals / Non-Goals

**Goals:**

- 把官方、commit-pinned、Global-only 数据变成可重放的 query-slice artifacts、observations、entities 和 relations。
- 让 Claude.ai 与 1P API 可以独立采集、校验、发布和查询，并保留精确到输入文件的血缘。
- 复用平台现有 vintage、quality、catalog 和 DataProducts 模型，同时增加通用的多 artifact 与实体关系能力。
- 支持有界内存采集、幂等更新、上游修订、taxonomy `as_of` 和可复算领域派生。
- 让 L1 Observer 的事实陈述保持在 Anthropic 数据实际支持的范围内。

**Non-Goals:**

- 不抓取网页 DOM、图形像素或 Preview table；网页仅用于人工核对。
- 不估算从业者/企业采用率、岗位替代、留存、席位数或 Claude Code 使用。
- 不保存或比较国家、州、省数据，不将 SOC major group 转换为 NAICS/GICS。
- 不将 2026-04 以前抽样周数据伪装成月度连续序列。
- 不在此 change 中给交易 workflow 分配证据权重或迁移其读取路径。

## Decisions

### 1. 以 Hugging Face metadata 和 commit-pinned 文件作为唯一机器采集入口

适配器配置 repository id `Anthropic/EconomicIndex`。Discovery 请求公开 dataset metadata，取得当前 commit SHA、`lastModified` 和 siblings 清单；从文件路径中枚举 `release_YYYY_MM_DD`，按日期排序并检查每个未采集 release 的必需文件：

```text
release_<date>/data_documentation.md
release_<date>/aei_claude_ai_<release>.csv
release_<date>/aei_1p_api_<release>.csv
```

每个实际下载 URL 使用 `/resolve/<commit-sha>/<path>`。若 metadata 只表明目录出现但文件未齐全，记录 `not_yet_published`；若最新 commit 没有新完整 release，记录 `no_change`。Discovery 保存 metadata 响应的最小可复现 artifact，并在下载后把文件 SHA-256 加入：

```text
<repository_commit>:<release_directory>:<filename>:<upstream_sha256>
```

`published_at` 使用该 commit 的提交时间，`known_at` 与 `fetched_at` 使用本次采集时间。若 commit 时间无法从 metadata 可靠取得，对应 release 不进入发布，避免用抓取时间伪造发布时间。

选择这一方案是因为官方文件比 UI 的筛选请求和 DOM 稳定，commit pin 可以重放。备选方案包括抓取 Job Explorer、调用未文档化网页接口或长期依赖 `main`；这些方案都易受前端重构或内容漂移影响，因此不采用。

### 2. 下载阶段验证完整上游文件，长期保存最小 Global query slice

每个文件写入由系统创建的独立临时文件，下载过程增量计算 SHA-256，并同时执行：

- connect/read/total timeout，单文件 total timeout 为 300 秒；
- `Content-Length` 与实际字节数核对；
- 可配置 `max_file_bytes`，初始值按约 200 MB 文件留出明确余量后写入 source constraints；
- 对 404、429、5xx 使用有界退避，尊重 `Retry-After`；
- 先校验 CSV header 和 release schema，再逐行解析。

CSV 使用 `csv.DictReader` 流式读取。只将下列合格行写入 canonical UTF-8 JSON Lines query slice；字段顺序固定、数值保留源字符串并另存规范化数值，使 artifact hash 稳定：

```text
geo_id       == GLOBAL
geo_level    == global
category_name == soc_occupation and hierarchy_level in {0, 1}
or
category_name == onet and hierarchy_level == 0
```

Artifact store 保存过滤后的 query slice，不长期保存完整约 200 MB 上游文件；metadata 保存 commit-pinned pointer、完整上游 hash/bytes、过滤后 hash/bytes、eligible row count、parser/schema version、过滤表达式、数据文档 URL、source product、license 和下载时间。这样既保留完整性证明，又控制存储量。若未来许可或重现要求变化，可由 commit pointer 重新下载并校验原 hash。

备选方案是整体载入 DataFrame 或直接把完整文件放入 artifact store；前者不满足内存边界，后者对首版 query-slice retention 没有必要。

### 3. 每个逻辑输入是独立、命名且可独立发布的 slice

Artifact keys 采用：

| Artifact key | 内容 |
|---|---|
| `claude_ai:<release>` | Global Claude.ai 月度行 |
| `1p_api:<release>` | Global 1P API 月度行 |
| `onet_tasks:<version>` | 本次使用的 O*NET task taxonomy |
| `soc_structure:<version>` | 本次使用的 SOC hierarchy |
| `observed_exposure:<version>` | 职业 exposure 快照 |
| `task_penetration:<version>` | 任务 penetration 快照 |

`AdapterArtifact` 增加必填 `artifact_key`；已有 `NativeRecord.slice_key` 在返回 records 时变为必填。新增 `ReferenceEntityInput`、`EntityRelationInput`，后者也有 `slice_key`。`AdapterBatch` 增加 `entities`、`relations` 和可选逐 slice 状态。Batch validator 在保存任何数据前验证 artifact keys 唯一、每个 record/relation 的 key 存在，禁止 fallback。

Pipeline 分为四个阶段：

1. 保存并取得每个 artifact key 对应的 artifact id；
2. 准入 reference entities；
3. 准入只引用已知实体的 relations；
4. 对 records 执行 metric/entity/time/quality 准入，并按 slice key 传递准确 artifact id。

每个 slice 在 staging 中完成 schema 与领域质量门后才发布。互不依赖的产品 slice 可以部分成功；taxonomy 是 job/task profile 的显式依赖，失败时不能发布依赖它的层级结果。总体状态由逐 slice 状态归并：全部无变化为 `no_change`，一部分成功一部分失败为 `partial`，全部失败则保留最具体失败状态。

### 4. Entity 保持稳定身份，Relation 记录 taxonomy 的时间版本

Entity IDs 使用：

```text
SOC:<canonical SOC code>
ONET_TASK:<Task ID>
```

示例：`SOC:15-2031.00`、`SOC:15-0000`、`ONET_TASK:7382`。实体当前展示名称继续复用 `structured_entities`；source-specific external IDs、名称和 taxonomy metadata 保存在实体 metadata。名称差异产生 warning，不参与 identity。

新增 append-only `structured_entity_relations`：

| Column | 设计 |
|---|---|
| `relation_id` | source、dataset、parent、child、type、source version 和规范化 metadata 的内容 hash |
| `dataset_id` / `source_id` | 关系所属数据产品与来源 |
| `parent_entity_id` / `child_entity_id` | 已存在的 canonical entity IDs，受外键/应用层一致性校验 |
| `relation_type` | 首版为 `contains_occupation`、`has_task` |
| `source_version` | taxonomy commit/version |
| `known_at` | 系统首次可见时间 |
| `artifact_id` | 产生该关系的准确 taxonomy artifact |
| `metadata_json` | task type、respondent count、taxonomy date、原始代码等 |

建立 parent、child、dataset/source、known_at 索引。相同内容 hash 使用 `INSERT OR IGNORE` 幂等；新版本追加而非结束或覆盖旧行。`as_of` 查询在相同 source/dataset/parent/child/type 的候选中选择 `known_at <= as_of` 的最新已知版本。若新 taxonomy 明确移除关系，adapter 生成 versioned tombstone metadata，查询按该版本排除关系，但历史 `as_of` 仍可重放。

备选方案是把 task IDs 放进 observation dimensions，无法表达无 observation 的未观察任务，也不能审计 taxonomy 修订，因此采用独立关系表。

### 5. 月度 observation 与研究快照共用 vintage 模型但保持不同 period basis

月度 CSV 原生字段 `date_start`、`date_end` 映射到 `period_start/end`，在覆盖完整自然月时 `period=YYYY-MM`、`period_basis=calendar_month`。每条 series dimensions 至少包含：

```text
source_product
classification
hierarchy_level
node_external_id
node_name
soc_code
soc_major_group_code
task_id
task_name
task_type
release_date
methodology_version
taxonomy_version
```

其中只保存适用于该实体的维度，`source_product` 对月度系列必填并参与 series identity。Observation 继续保存 provider raw row、quality status 和 content hash；同一 series/period 的相同内容去重，不同内容追加 vintage。

`job_exposure.csv` 和 `task_penetration.csv` 使用研究或文件首次官方发布时间作为 `period`，使用源 0–1 比例，`period_basis=research_snapshot`。6 位 SOC exposure 与 O*NET-SOC `.XX` 不合并，通过显式父子关系连接。若 task penetration 文件仅发布任务文本而没有 Task ID，则建立内容哈希标识的 source-native research-task 实体并标注 O*NET 稳定映射不可用，禁止以名称强行连接。通用月度变化函数只接受 `calendar_month`，从结构上阻止 exposure 进入环比。

### 6. 指标注册表保留 Provider 原义

| Provider field | Metric ID | Unit |
|---|---|---|
| `pct` | `ai.work_adoption.usage_share` | `percent` |
| `collaboration_bucket_automation_pct` | `ai.work_adoption.automation_share` | `percent` |
| `collaboration_bucket_augmentation_pct` | `ai.work_adoption.augmentation_share` | `percent` |
| `collaboration_directive_pct` | `ai.work_adoption.collaboration.directive_share` | `percent` |
| `collaboration_feedback_loop_pct` | `ai.work_adoption.collaboration.feedback_loop_share` | `percent` |
| `collaboration_task_iteration_pct` | `ai.work_adoption.collaboration.task_iteration_share` | `percent` |
| `collaboration_validation_pct` | `ai.work_adoption.collaboration.validation_share` | `percent` |
| `collaboration_learning_pct` | `ai.work_adoption.collaboration.learning_share` | `percent` |
| `collaboration_none_pct` | `ai.work_adoption.collaboration.none_share` | `percent` |
| `use_case_work_pct` | `ai.work_adoption.work_use_share` | `percent` |
| `ai_autonomy_mean` | `ai.work_adoption.autonomy_mean` | `scale_1_5` |
| `observed_exposure` | `ai.work_adoption.observed_exposure` | `ratio_0_1` |
| `penetration` | `ai.work_adoption.task_penetration` | `ratio_0_1` |

`pct` 的平台名称有意使用 usage share，不使用 adoption/penetration。未知 metric 保存到 pending mapping/审计报告，不静默丢弃，也不阻断其他已知 metric，除非未知字段表明整个 schema 已不兼容。

### 7. 质量门以 source-product slice 为原子发布单元

通用 admission 之前执行 source-specific preflight，之后执行 normalized reconciliation。门槛为：

- 入库非 Global observation 数为 0；
- eligible source rows 与产生的 accepted/warning observations 加已解释 quarantines 对账 100%，发布集合与合格可映射记录对账 100%；
- percent `[0,100]`、autonomy `[1,5]`、ratio `[0,1]`；
- automation + augmentation 完整时允许 `100 ± 0.15`；
- 六类 collaboration pattern 完整时允许 `100 ± 0.15`；
- Task ID → occupation relation coverage 健康目标至少 99%；官方 taxonomy 版本落后时低覆盖明确 warning 并保留 entity/observation，未经解释的覆盖下降才阻止发布；
- 同产品、期间、节点、metric 出现不同值时，必须由明确 dimension 或 vintage 解释，否则失败；
- schema drift、truncation、hash mismatch 阻止该 slice 发布。

缺失 cell 不是 record，查询层通过 taxonomy denominator 生成 `not_published_or_privacy_filtered`，从不补零。质量报告同时显示 source eligible rows、filtered rows、normalized rows、warnings、quarantines、unmapped tasks 和 privacy-filtered proxy count。

### 8. 派生指标只在 DataProducts 查询时计算

新增 derivation registry namespace `ai.work_adoption`。每个结果返回 derivation id/version、公式参数、输入 observation IDs 和 relation IDs。首版公式：

| Derived field | Formula / rule |
|---|---|
| `usage_rank` | 同产品、同月、详细职业按 Usage Share 降序；缺失不排名，tie 使用稳定 competition rank 后按 entity id 展示 |
| `usage_rank_change` | 当月 rank − 上月 rank，仅连续自然月 |
| `usage_share_change_pp` | 当月 − 上月，百分点 |
| `automation_share_change_pp` | 当月 − 上月，同产品、同 methodology |
| `collaboration_*_change_pp` | 各模式当月 − 上月，不跨 methodology regime |
| `automated_usage_share` | `usage_share × automation_share / 100` |
| `job_share_of_major_group` | job usage / Provider level-1 major-group usage；显示不完全加总提示 |
| `observed_task_coverage` | 有合格 AEI usage cell 的关联 tasks / taxonomy tasks |
| `mostly_automated_task_share` | automation > augmentation 的 tasks / 同时有两值的 tasks |
| `mostly_augmented_task_share` | augmentation > automation 的 tasks / 同时有两值的 tasks |
| `balanced_task_share` | automation = augmentation 的 tasks / 同时有两值的 tasks |
| `unobserved_task_share` | 无公开 AEI cell 的 taxonomy tasks / taxonomy tasks |

`industry_usage_share` 和 `industry_automation_share` 直接选择 Provider SOC level 1 observation，不是派生求和/平均。默认同比函数返回 `insufficient_history`，直到具有同口径去年同期月度数据。

### 9. DataProducts 定义面向消费者的稳定返回结构

新增：

```python
DataProducts.ai_work_adoption_snapshot(
    source_product: Literal["claude_ai", "1p_api"],
    period: str,
    as_of: datetime,
) -> AIWorkAdoptionSnapshot

DataProducts.ai_job_profile(
    occupation: str,
    source_product: Literal["claude_ai", "1p_api"],
    period: str,
    as_of: datetime,
) -> AIJobProfile
```

Snapshot 返回 request context、major groups、jobs、rank/change/automation contribution、task coverage buckets、coverage/quality summary 和 lineage manifest。Job profile 返回 occupation、parent major group、job observations/changes、全部 taxonomy tasks、每个 task 的 usage/automation/augmentation/collaboration observations、缺失状态、Observed Exposure 快照及 observation/relation lineage。

两者默认只使用 accepted/warning 数据。`source_product` 不提供默认值，防止产品混合；跨产品对照使用显式的两个独立调用或通用 cross-section 的显式 compare mode。底层 repository 查询先解析 taxonomy `as_of`，再选择同一 `as_of` 可见的 observation vintages，最后执行 derivations。

Snapshot manifest 扩展现有 `DataSnapshot` metadata，固定 query、产品、period、as_of、observation IDs、relation IDs、artifact IDs、derivation versions 和 quality summary。重放按 IDs 读取，不重新访问外部来源。

### 10. Catalog、运行与 CLI 复用统一生命周期

Catalog 注册：

```text
source_id      anthropic_economic_index
dataset_id     ai_work_adoption
catalog_status current_partial
persistence    persistent
cadence        release_event
retention      query_slice
source mode    legacy（初始）
concurrency    1
file timeout   300 seconds
max bytes      显式配置
```

外部 cron、launchd 或部署平台每周调用统一 `ats data ingest`；仓库不实现常驻 scheduler。Health 在 source 总体状态外记录 `last_checked_at`、`latest_upstream_commit`、`latest_ingested_release`、`latest_available_period`，并在 run metadata 保存逐 source-product 状态。

现有运维命令覆盖 validate-source、ingest、history、quality、availability、lineage、release-check、publish、rollback；增加只读便捷查询：

```text
ats data ai-adoption --product claude_ai --period 2026-05 [--as-of ...]
ats data ai-job 15-2031.00 --product 1p_api --period 2026-05 [--as-of ...]
```

所有消费者经 DataProducts 或受控 SQL/DataFrame 读取。L1 Observer 保存 manifest；PEAD、Chain、Macro、Sector、Chief 和交易决策 workflow 保持不变。

### 11. 错误分类和重试不会改变已发布事实

状态映射：

```text
metadata timeout/limit/unreachable -> unreachable
same commit/files/content          -> no_change
release directory incomplete       -> not_yet_published
schema/hash/quality violation       -> validation_failed
some independent slices pass       -> partial
new valid content                   -> succeeded
```

404 对必需文件在新目录尚未齐全时归为 `not_yet_published`；已记录完整 release 的文件后来 404 则归为 `unreachable`/source regression 并告警。429 和临时 5xx 可重试，解析/质量错误不可盲目重试。任何失败都不覆盖或删除最近已发布 vintage。

## Risks / Trade-offs

- **[上游 schema 或 methodology 无预告变化]** → 以 release schema allowlist 和 methodology version 阻断跨 regime 派生，保存文档 URL与失败 artifact 供适配器升级。
- **[Usage Share 被消费者误当成采用率]** → metric 命名、返回说明、文档和 Observer assertion tests 四层约束；禁止使用 `user_penetration` 别名。
- **[隐私过滤导致 jobs/tasks 不完全加总]** → 大类使用官方 level 1 值，任务缺失表达为 unknown/privacy-filtered proxy，不补零。
- **[taxonomy 映射覆盖不足]** → 稳定 ID join、99% 门槛、保留 unmapped entity/observation warning；禁止名称模糊匹配。
- **[大文件占用网络、磁盘和运行时间]** → 单并发、流式临时文件、显式大小/超时、下载中 hash、长期只存 query slice。
- **[部分成功使用户误判 release 完整]** → 总体 `partial` 加逐产品/切片状态；snapshot 明确产品与可用期间。
- **[新 relation 表增加迁移复杂度]** → 只做 additive migration；旧消费者不引用该表，回滚只关闭 source/consumer flag，不删除数据。
- **[官方 release cadence 不固定]** → 每周 discovery 与 `release_event` 业务 cadence 分离；无新数据为正常 `no_change`。
- **[Observed Exposure 与月度 Usage 统计总体不同]** → 不在原始层拼接，period basis 和 DataProducts 分区返回；只允许带方法说明的并列观察。

## Migration Plan

1. 添加向后兼容 schema migration、domain contracts 和 repository relation APIs；先用现有 adapters 回归多 artifact 变更不会改变单 artifact 行为。
2. 注册 source、dataset、entities、metrics、provider mappings 和限制参数；source 保持 `legacy/current_partial`，不进入 platform 默认采集。
3. 用固定 metadata、月度、taxonomy、exposure fixtures 完成 parser、质量和错误状态测试。
4. 在隔离 SQLite 与 artifact 目录运行 2026-06-26 release，分别回填 2026-04/05 Claude.ai 和 1P API，并执行 `validate-source → ingest --force → quality → availability → ai-job → lineage → release-check`。
5. 验证 task 7382 层级、逐 artifact lineage、幂等重跑、修订 vintage、privacy-filtered 缺失、派生复算和多入口对账。
6. 进入 shadow 模式执行一次真实 metadata discovery；核对最新 commit/release/period 与网页展示，但不以网页作为数据来源。
7. 发布 source 到 platform，仅开放 DataProducts、研究查询和 L1 Observer；Observer 首次运行保存 manifest。
8. 更新开发者、运维者、使用者文档和 source checklist，配置外部每周调度。

Rollback 仅把 source 恢复为上一安全运行模式并关闭 L1 consumer flag；保留全部 artifacts、observations、relations、run history 和 manifests。Relation 表和新增 nullable contract fields 是向后兼容结构，不在业务回滚中删除。若 schema migration 本身必须撤回，先确认没有新 relation 写入后走单独数据库迁移，不由常规 `data rollback` 执行。
