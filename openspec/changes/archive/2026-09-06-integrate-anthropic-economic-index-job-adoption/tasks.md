## 1. Catalog、Metric 与 Source Registration

- [x] 1.1 在结构化数据目录注册 `anthropic_economic_index` source 和 `ai_work_adoption` dataset，配置 `current_partial`、`persistent`、`release_event`、`query_slice`、legacy mode、单并发、300 秒单文件超时和明确的最大文件字节数。
- [x] 1.2 注册 SOC major group、SOC occupation、O*NET task 实体类型，以及 source product、classification、hierarchy、methodology、taxonomy 等必需维度。
- [x] 1.3 注册全部首版原始 metrics、单位和 Anthropic provider mappings，确保 `pct` 映射为 Usage Share 而非用户采用率。
- [x] 1.4 增加目录与注册校验测试，验证 source/dataset/metric 引用、约束、验收样本和 runtime 边界均有效。

## 2. 多 Artifact Lineage 与 Entity Relation 基础能力

- [x] 2.1 扩展 structured domain contracts：为 artifact 增加唯一 `artifact_key`，校验 record `slice_key`，并新增 reference entity、entity relation 与逐 slice 结果模型。
- [x] 2.2 添加 `structured_entity_relations` 的向后兼容 schema migration、索引和 repository 写入/读取接口，保存关系版本、known time、artifact 和 metadata。
- [x] 2.3 实现 relation 内容寻址幂等、版本追加、tombstone 语义及按 parent/child/type/source/dataset 的 taxonomy `as_of` 查询。
- [x] 2.4 重构 ingestion pipeline，按 artifact key 建立准确映射，并按 artifacts → entities → relations → observations 顺序准入。
- [x] 2.5 对缺失、重复和未知 slice key、悬空 relation 实施隔离，删除首 artifact fallback，并实现互不依赖 slices 的 partial 状态归并。
- [x] 2.6 添加多 artifact、relation admission、幂等、修订和历史 `as_of` 单元/集成测试，并回归现有单 artifact adapters 行为不变。

## 3. Hugging Face Discovery、下载与 Parser

- [x] 3.1 新建并注册 Anthropic Economic Index structured adapter，定义 repository、release、schema、artifact key 和错误状态常量。
- [x] 3.2 实现公开 Hugging Face metadata discovery：解析 commit/时间/文件清单、枚举完整 release、锁定 commit URL，并用 2026-06-26 fixture 覆盖完整、不完整和无变化场景。
- [x] 3.3 实现有界重试的流式下载器，校验 Content-Length、实际字节、最大文件大小、300 秒超时和完整上游 SHA-256，并分类处理 404、429、5xx、超时和截断。
- [x] 3.4 实现 deterministic Global query-slice writer，保存 canonical JSON Lines、过滤后 hash/bytes/count 以及 commit-pinned pointer、license、文档、parser/schema version 等 artifact metadata。
- [x] 3.5 实现 Claude.ai 与 1P API CSV 流式 parser，仅接受 Global × SOC level 0/1 与 O*NET level 0 白名单，并产生隔离的 source-product records/artifacts。
- [x] 3.6 添加 schema drift、损坏 CSV、未知 header/metric、非 Global 泄漏、单产品失败和双产品 lineage 测试。

## 4. Taxonomy、Monthly Observation 与 Exposure Normalization

- [x] 4.1 实现 `onet_task_statements.csv` 与 `soc_structure.csv` parser，规范化 SOC/Task 稳定 IDs、展示名称、task type 和 source metadata，禁止名称主键连接。
- [x] 4.2 生成 major-group → occupation 的 `contains_occupation` 和 occupation → task 的 `has_task` versioned relations，并报告 unmapped tasks。
- [x] 4.3 将月度原生行规范化为实体、period/start/end、`calendar_month`、source-product series dimensions、published/known/fetched time 和 raw provider payload。
- [x] 4.4 实现全部月度 provider field 到平台 metric 的映射，保证 source product 进入 series identity 且两个产品互不补值。
- [x] 4.5 采集并规范化 `job_exposure.csv` 与 `task_penetration.csv`，保留 ratio_0_1、研究快照日期和 `research_snapshot`，保持 6 位 SOC 与 `.XX` 实体粒度。
- [x] 4.6 添加 Task `7382` → Operations Research Analysts → Computer and Mathematical Occupations 验收测试，以及名称变化、SOC 粒度和 exposure 非月度测试。

## 5. 质量门、幂等与 Vintage

- [x] 5.1 实现每 slice 的范围校验：percent `[0,100]`、autonomy `[1,5]`、ratio `[0,1]`，以及 Automation/ Augmentation 和完整 collaboration patterns 的 ±0.15 个百分点总和容差。
- [x] 5.2 实现 eligible source rows 到 normalized records 的 100% 对账、非 Global 零入库和 Task ID → occupation 99% 健康目标；官方 taxonomy 版本落后时将低覆盖明确告警并保留未映射 task，未经解释的覆盖下降阻止发布。
- [x] 5.3 实现同产品/期间/节点/metric 重复冲突检测，以及 privacy-filtered/未发布 cell 的缺失状态，禁止缺失转零。
- [x] 5.4 扩展 ingestion health/run metadata，报告 last checked、upstream commit、ingested release、available period、总体状态和逐 source-product/slice 状态。
- [x] 5.5 验证相同内容重跑为 `no_change`，上游同期间修订追加 vintage，且修订前 `as_of` 返回旧 observation 和旧 taxonomy relation。

## 6. DataProducts、派生计算与 CLI

- [x] 6.1 注册并实现版本化派生：usage rank/rank change、Usage/Automation/collaboration 百分点变化、automated usage share 和 job share of major group，并拒绝非连续月份或跨 methodology/source product 计算。
- [x] 6.2 实现 observed task coverage、mostly automated/augmented/balanced/unobserved task shares，使用 `as_of` taxonomy denominator 并保留 privacy-filtered 缺失语义。
- [x] 6.3 实现 SOC level 1 原始指标选择、层级 entity 查询和按 source product 的 metric series/cross section repository 服务。
- [x] 6.4 实现 `ai_work_adoption_snapshot` 返回类型和 DataProducts 方法，包含截面、变化、任务结构、质量、口径和 lineage。
- [x] 6.5 实现 `ai_job_profile` 返回类型和 DataProducts 方法，包含职业层级、job/task 指标、未观察任务、exposure 快照和 observation/relation lineage。
- [x] 6.6 增加 `data ai-adoption` 与 `data ai-job` 只读 CLI，强制 product/period 参数并支持 `as_of`。
- [x] 6.7 添加派生公式复算、跨产品禁止默认比较、同比历史不足、level-1 不由 jobs 聚合及 SQL/DataFrame/DataProducts 一致性测试。

## 7. 2026-06-26 Release 隔离接入与截面验收

- [x] 7.1 建立 commit-pinned 2026-06-26 metadata、Claude.ai/1P API 月度、taxonomy 和 exposure fixtures；确认该 release 包含 2026-04、2026-05 两个自然月，并记录 fixture 来源 hash、裁剪规则和原始 period 清单。
- [x] 7.2 在隔离 SQLite 和 artifact 目录执行 source validation 与强制 ingestion；仅接入 `release_2026_06_26`，不接入 2026-01-15、2026-03-24 抽样周数据；分别入库 2026-04、2026-05 的 Claude.ai 与 1P API。
- [x] 7.3 对隔离结果执行 Global-only、源行对账、指标范围、taxonomy coverage、逐产品 artifact lineage 和 exposure period-basis 验收；确认默认数据产品返回 2026-05 最新截面，2026-04 作为历史 observation 保留。真实 release 因最新公开 task taxonomy 落后于月度文件，Task ID 映射覆盖率为 Claude.ai 78.85%、1P API 78.45%；缺口明确告警并保留 observation，不使用名称强行映射。
- [x] 7.4 重复采集同一 release，并注入同期间修订 fixture，验证 `no_change`、append vintage、旧值 `as_of` 和两个 source products 的独立性；4→5 月变化仅用于验证计算正确性，在新的同口径 release 出现前不得输出趋势性研究结论。

## 8. Consumer Snapshot 与 Lineage 验收

- [x] 8.1 扩展 snapshot manifest，固定 query、source product、period、as_of、observation/relation/artifact IDs、derivation versions 和质量摘要，并实现离线重放。
- [x] 8.2 将 L1 Evidence Observer 接到 DataProducts snapshot/profile 契约，加入事实陈述 guardrails，禁止直接读取 Hugging Face 或物理表。
- [x] 8.3 添加消费者测试，确保 Usage Share 不被表述为员工采用率、SOC major group 不被表述为公司行业、Observed Exposure 不进入月度趋势。
- [x] 8.4 验证 PEAD、Chain、Macro、Sector、Chief 和交易决策 workflow 没有新增直接依赖或默认证据权重。
- [x] 8.5 在隔离环境完成 `validate-source → ingest --force → quality → availability → ai-job → lineage → release-check` 端到端验收并保存结果。验收记录见 `docs/ANTHROPIC_ECONOMIC_INDEX_TASK85_E2E.md`。

## 9. Platform 发布、运维文档与 Source Checklist

> 后续运维触发项（不属于当前 change 的完成条件）：Anthropic 下一次公开 release 出现后，运行真实官方 metadata/release discovery 与首次更新验收，核对新 commit、release、period、schema drift 和网络异常状态。
- [x] 9.2 更新 structured data 开发者文档，说明多 artifact contracts、relation schema、Anthropic adapter、测试 fixture 和扩展规则。
- [x] 9.3 更新运维与使用者文档及 source checklist，说明公开无鉴权来源、每周外部调度、限制参数、状态机、质量门、命令、语义限制和消费者示例。
- [x] 9.4 通过 release preflight 后显式将来源切换到 platform mode，只开放研究查询与 L1 Observer，并记录发布审计事件。发布验收见 `docs/ANTHROPIC_ECONOMIC_INDEX_PLATFORM_RELEASE.md`。
