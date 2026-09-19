## 1. 前置：让退役 diff 干净

- [x] 1.1 处理 `codex/new_data_source` 分支上的未提交改动（`config/data/structured.yaml`、`config/data/schedules.yaml`、4 份 docs、1 份 src、3 份 tests、未跟踪的 `docs/L1_OBSERVER_DATA_SOURCES.md`）：提交或另行暂存，使 `git status --short` 在开始退役前为空。验证：`git status --short` 无输出。

## 2. 墓碑登记与配置退役

- [x] 2.1 在 `config/data/structured.yaml` 新增顶层 `retired_sources` 段落，登记 `ons_bics_ai` 的 `retired_at`、`reason`、`prior_catalog_status: legacy`、`disposition: purged`、`disposition_at`、`successor: ""`、`spec_removed: true`。验证：`python -c "import yaml;d=yaml.safe_load(open('config/data/structured.yaml'));print(d['retired_sources']['ons_bics_ai'])"` 输出完整墓碑。
- [x] 2.2 同一批删除 `ons_bics_ai` 的活条目：`feature_flags.sources.ons_bics_ai`、`sources.ons_bics_ai`、`datasets.ai_enterprise_adoption_uk`、7 个 `ai.uk_enterprise_adoption.*` 指标定义、`provider_mappings.ons_bics_ai`。验证：`grep -n "ons_bics\|ai_enterprise_adoption_uk\|ai\.uk_enterprise_adoption" config/data/structured.yaml` 仅命中 `retired_sources` 与 `reason` 文本。
- [x] 2.3 同步 `docs/STRUCTURED_DATA_OPERATIONS.md`：从 §10.1 source 表删除 `ons_bics_ai` 行与 `ai_enterprise_adoption_uk` dataset 行，从 §10.2 删除 `ons_bics_ai` 行，并更新 §3 范围边界段落中去掉 ONS 表述。验证：`pytest tests/test_structured_docs_consistency.py` 全绿。

## 3. 注册校验 fail-closed 与墓碑访问器

- [x] 3.1 在 `StructuredCatalog` 暴露 `retired_sources()` 与 `retired_source(source_id)` 访问器，并在 `load()` 中对「同一 `source_id` 同时出现在 `sources` 与 `retired_sources`」抛配置错误。验证：新增测试 `test_catalog_rejects_source_present_in_both_registries` 通过。
- [x] 3.2 在 `validate_source_registration()` 中命中墓碑时返回 `checks: [source_retired: False]` 与 `reason_codes: ["source_retired"]`，并附 `retired_at` / `reason` / `disposition`。验证：新增测试断言 `validate_source_registration("ons_bics_ai")["valid"] is False` 且 `reason_codes == ["source_retired"]`。
- [x] 3.3 锁定「退役来源不进入默认发现与目录」：`release_check` 不带过滤时结果不含 `ons_bics_ai`；`DataProducts.sources()` / `datasets()` 不含退役来源。验证：新增测试 `test_retired_source_is_absent_from_default_discovery_and_catalog_views` 通过。

## 4. purge 入口

- [x] 4.1 在结构化仓储实现 `purge_source(source_id, *, confirm=False)`：未确认时只读返回逐表待删行数、artifact 数、blob 数、字节数；已确认时先断言墓碑存在，再单事务删除 `structured_observations` → `structured_series` → `structured_artifacts` → `structured_source_checks`，并计算仅被该来源引用的 `blob_id` 集合。验证：新增测试在临时库上构造两个来源的数据，断言未确认调用后行数不变、确认后仅目标来源行消失。
- [x] 4.2 blob 文件删除放在事务提交之后，删除条件为「该 `blob_id` 不被任何其他 `structured_artifacts` 行引用」。验证：新增测试构造「其他来源复用同一 blob」的场景，断言共享 blob 文件与 blob 行保留。
- [x] 4.3 未登记墓碑的来源即使传入确认参数也必须拒绝执行。验证：新增测试 `test_purge_refuses_source_without_retirement_tombstone` 通过，且目标来源行数不变。
- [x] 4.4 写入可查询的清除记录（`source_id`、时间、操作者、各表删除行数、释放字节数、artifact 数、是否导出留存）。验证：新增测试断言清除记录字段完整且可通过只读接口查回。
- [x] 4.5 在 `ats data` 注册 `purge-source` action（复用 `--source` / `VALUE` 与 `--db` / `--artifact-root` 隔离路径，新增确认参数），并确认它不被 ingest / publish / rollback / release-check 路径隐式调用。验证：`ats data purge-source --source ons_bics_ai` 返回 dry-run JSON；`ats data --help` 列出新 action。

## 5. 删除 ONS 源码与适配器注册

- [x] 5.1 删除 `src/ats/data/sources/ons_bics_ai.py` 与 `src/ats/data/products/ons_bics_ai.py`。验证：`python -c "import ats.data.sources.ons_bics_ai"` 报 `ModuleNotFoundError`。
- [x] 5.2 删除 `src/ats/data/products/base.py::ons_bics_ai_snapshot` 与 `src/ats/data/adapters/structured/registry.py` 中的 `_ons_bics_ai` 工厂、`_RUNTIMES["ons_bics_ai"]` 条目及其注释。验证：`grep -rn "ons_bics\|ONSBICS" src/` 零命中（忽略 `__pycache__`）。
- [x] 5.3 复查全仓引用面收敛：`grep -rn "ons_bics\|ai_enterprise_adoption_uk\|BUSINESS_POP:UK" src/ config/ tests/` 只应命中墓碑登记、退役相关测试与新 spec 文本。验证：逐条核对输出并在实施记录中列出。

## 6. 文档同步

- [x] 6.1 `docs/L1_OBSERVER_DATA_SOURCES.md`：删除「容易误读的边界」段与来源表、规模表中的 ONS 行，修正规模合计（232,016/92.4% → 226,935/90.4%，九来源），并顺带修正 `tickertrends_public_research` 行仍写 `frozen_seed，不参与周期发现` 的既有漂移。验证：`grep -nE "ons_bics|ONS BICS" docs/L1_OBSERVER_DATA_SOURCES.md` 零命中（注意：裸 `ONS` 会被 `..._OPERATIONS.md` 里的 `OPERATI**ONS**` 误命中，不可用作探针）。
- [x] 6.2 `docs/AI_MODEL_EVIDENCE_SOURCES.md`：删除 ONS 来源行与「双周 | Census BTOS、ONS BICS」中的 ONS。验证：`grep -n "ons_bics\|ONS BICS" docs/AI_MODEL_EVIDENCE_SOURCES.md` 零命中。
- [x] 6.3 `docs/AI_PRODUCTION_ADOPTION_CANDIDATE_SOURCES_RESEARCH.md`：在「六、英国 ONS BICS AI」章节与来源矩阵、结论清单处标注「已于 2026-09-19 正式退役（`retire-ons-bics-ai`），不导出历史数据」，保留研究正文作为历史定级依据。验证：该章节含退役标注且正文其余部分未被删改。
- [x] 6.4 `docs/validation/ONS_BICS_AI_TASK6_ACCEPTANCE.md` 与 `docs/validation/AI_ADOPTION_V2_END_TO_END_ACCEPTANCE.md`：顶部加退役标注并注明 5,081 obs 已 purge、验收结论仅作历史记录；保留文件不删。验证：两份文件均含退役标注。
- [x] 6.5 复查 `docs/DATA_SOURCES.md`、`docs/AI_PRODUCTION_PENETRATION_OBSERVER.md`、`docs/STRUCTURED_DATA_OPERATIONS.md`、`docs/DATA_ARCHITECTURE.md` 无残留 ONS 来源表述。验证：`grep -rn "ons_bics\|ONS BICS\|ai_enterprise_adoption_uk" docs/` 只命中 6.3/6.4 的退役标注。附带修正 `RAMP_AI_INDEX.md`/`RAMP_AI_INDEX_ACCEPTANCE.md` 中把 ONS 列为在役节奏来源的过期枚举。

## 7. 测试调整

- [x] 7.1 删除 `tests/test_ai_adoption_source_adapters.py` 中真正测 ONS 的用例（工作簿解析、ONS DataProduct 快照）及 ONS import。验证：`grep -n "ons_bics\|parse_ons_rows\|ons_snapshot" tests/test_ai_adoption_source_adapters.py` 中仅剩刻意保留的部分（若有）。
- [x] 7.2 把仅借用 ONS 当「任意来源」载体的通用测试改绑 `rps_genai_adoption` / `us_census_btos`（发现状态机持久化、`source_checks` 无观测等）。验证：`pytest tests/test_ai_adoption_source_adapters.py` 全绿且用例数只减少被删的 ONS 专属用例。
- [x] 7.3 删除 `tests/test_ai_adoption_bundle.py` 中 `ons_bics_ai_snapshot` 的「必须不被调用」守卫替身，改为断言 bundle 的输入只包含在役来源。验证：`pytest tests/test_ai_adoption_bundle.py` 全绿。
- [x] 7.4 新增墓碑与 purge 的回归测试组（覆盖 3.1–3.3、4.1–4.4 的场景），文件建议 `tests/test_source_retirement.py`。验证：新测试文件全绿，且每条新 spec scenario 至少有一个对应用例。
- [x] 7.5 跑完整结构化与 L1 相关测试面确认无回归：`pytest tests/ -k "structured or ai_adoption or frontier or evidence"`，并记录既有失败（`test_structured_consumer_migration.py::test_source_and_consumer_rollout_flags_are_independent`、`test_structured_docs_consistency.py` 中 `release-assessment` 相关断言）是否仍为退役前状态，不得新增失败。验证：失败集合与退役前一致。

## 8. 数据清除与验收（不可逆，最后执行）

- [x] 8.1 对正式库执行 `ats data purge-source --source ons_bics_ai` 干跑，核对报告的观测数 5,081、序列数 5,081、artifact 数 3、blob 字节数 7,629,839、source check 数 2 与实测一致；不一致则停下排查。验证：干跑 JSON 数字与 `sqlite3 "file:var/data.sqlite?mode=ro"` 独立查询一致。（实测：observations/series 各 5,081、artifacts 3、source_checks 2、registered_sources/datasets 各 1、blobs 3（且三个均判定为独占 blob）、**bytes_freed 7,626,839**——计划文本的 7,629,839 高估 3,000 字节，以实测为准；另 `structured_ingestion_runs: 2` 为计划未覆盖的残留，保留不删。）
- [x] 8.2 执行带确认参数的 purge。验证：复查 `structured_observations` / `structured_series` / `structured_artifacts` / `structured_source_checks` 中该来源行为 0，`structured_sources` / `structured_datasets` 各减 1 行，且其他来源行数完全不变。（用户 2026-09-19 决策：**直接 purge、不留备份、不导出**。实测 purge_id `85f24117df0c73e9b13f0247`，`purged_at 2026-09-19T12:01:20Z`，actor `cli`，exported false：观测/序列/artifact/source_check 全部归零，两个注册镜像行各减 1 且无其他来源受影响；3 个 blob 行删除，因磁盘上本就不存在对应文件，故 `blob_files_removed: 0`（已按子串全库复核确认无残留文件）；审计行已写入 `structured_source_purges`。）
- [x] 8.3 验收 `ats data sources`、`ats data datasets`、`ats data health`、`ats data coverage` 输出中无 `ons_bics_ai` / `ai_enterprise_adoption_uk`。验证：`grep` 四项输出零命中。（四条命令均 exit 0，ONS 命中数均为 0；`ai_enterprise_adoption_us` 属 BTOS 美国数据集，正确保留。）
- [x] 8.4 验收 L1 无差异：重跑 L1 三个 Observer（`ai_production_penetration`、`ai_commercialization`、`ai_raw_capability`），比对命题、三轴状态判定、manifest、`rows_hash` 与退役前一致。验证：新旧 packet 的 claim version、状态与 rows hash 逐项相同。（以 purge 前后各跑 `ats evidence layer --sector ai_hardware --layer L1_app --format json` 并抽取指纹比对：`claim_definition_version`、`claim_id`、`status`、`overall_status`、`rows_hash`、`input_observation_ids_hash`、`ordered_record_hash` **逐项相同**；仅 `snapshot_ids` 与 `bundle_content_hash` 不同，且已用「purge 后连跑两次同样不同」证明二者是**每次运行固有易变**字段而非 purge 影响。三条命题分别为 v3 `breadth_without_confirmed_depth`、v1 `revenue_monetization_expanding_but_economics_unverified`、v1 无 overall。）
- [x] 8.5 记录并登记残余产物：检索 `var/` 下 ONS 相关隔离库、报告 assets 中的 UK 图表；若存在，按残留项处理并在实施记录中说明，不在本次强制删除。验证：检索结论写入实施记录。（结论：`var/` 内除目标库 `var/data.sqlite` 外，尚有 **9 个** `var/reports/frontier_ai_*` 隔离评审快照库含 ONS 注册行与观测——它们是整库时点副本，属历史评审产物，本次不删并在此登记；无 ONS 命名文件、无文本引用、无 ONS fixture；另已清理被删模块遗留的 2 个陈旧 `.pyc`。）

## 9. 收口

- [x] 9.1 运行 `openspec validate retire-ons-bics-ai` 与 `openspec status --change retire-ons-bics-ai`，确认全部构件 done。验证：validate 输出 `is valid`。
- [x] 9.2 更新 `.workbuddy/memory/`：当日日志追加退役执行记录，`MEMORY.md` 更新 `ons_bics_ai` 条目为「已于 2026-09-19 正式退役、数据已 purge」并写入 `retired_sources` 墓碑约定。验证：两份记忆文件含退役结论。
- [ ] 9.3 按 Why / How / What 三段中文结构提交并推送 `codex/new_data_source`。验证：`git log -1` 显示三段式 commit message 且 `git status` 干净。
