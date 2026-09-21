## Why

`ons_bics_ai`（英国 ONS Business Insights and Conditions Survey 的 AI 条件模块）自 2026-09-11 引入起就**不是任何 L1 Observer 的有效输入**：它不在 `ai_adoption` 发现组，不进 AI adoption evidence bundle，不进主动更新组，也不进任何报告或图表，其消费者数为 0。同时它的两个固有缺陷使其无法承担「美国之外的企业采用深度」这一角色：

- **信息量不足**：AI 题只在 BICS 部分 wave 出现，全库只有 12 个 wave、5,081 个 cell、21 个 `BUSINESS_POP:UK:*` 实体，且**深度题（extensive/limited/pilot）只在 wave 159 出现过一次**。没有可重复的同口径序列，产品层只能永久返回 `insufficient_history`。
- **时效性不足**：最新可比参考期是 wave 159（2026-06-15 至 06-28），而最近一次探测（wave 163）结果是 `question_not_fielded`——上游已停止提问，来源事实上已断流。

维持它的成本却是持续的：条件模块探测、workbook schema drift 解析、问卷 regime 归因、UK 补充口径与「不与 BTOS 相减/合并」的说明，以及一个从诞生起就挂着「审计专用、无人消费」注释的注册条目。用户判定其信息量与时效性均不达标，决定正式退役，而不是继续以「监听后接入」的形态长期挂账。

## What Changes

- **BREAKING**：`ons_bics_ai` 来源、数据集 `ai_enterprise_adoption_uk`、7 个 `ai.uk_enterprise_adoption.*` 指标定义、`provider_mappings.ons_bics_ai` 映射与 `feature_flags.sources.ons_bics_ai` 条目标记为退役并从机器配置中**硬删除**。删除后 `structured.yaml` 不再能加载、采集或发布任何 UK 企业采用数据。
- **新增 `retired_sources` 墓碑登记**：在 `config/data/structured.yaml` 中登记 `source_id`、退役时间、退役原因、原定级、数据处置结果（`purged` / `retained_orphan`）与替代来源建议。墓碑是机器可读的，用于三件事：（1）禁止该 `source_id` 被重新注册——注册校验在发现墓碑时 fail-closed 并提示走新的 change；（2）向运维解释库内可能存在的孤儿行；（3）保留「这个来源曾存在、为何退出」的审计线索，而不是让 id 静默消失后被后人静默复用。
- **BREAKING**：**purge 库内既有数据**——`structured_observations` 5,081 行、`structured_series` 5,081 行、`structured_artifacts` 3 行及其 7,629,839 字节 blob，以及 2 条 `structured_source_checks`。purge **必须**是显式运维动作：新增独立 purge 入口，默认干跑并打印将删除的行数/字节数/artifact id，只有显式二次确认参数才真正删除；不接受在采集或生命周期路径中隐式清理，也不接受 `DELETE` 无审计记录。本次决策为**不导出**：wave 159 的深度题孤本随 purge 永久丢失，属用户已知并接受的不可逆损失。
- **BREAKING**：`openspec/specs/data/ons-bics-ai` 整个 capability 退役，6 条 requirement 全部 REMOVED（发现区分 wave 与 AI 模块、question regime、广度与深度不合成单值、UK 仅作地域补充、质量门、深度快照查询）。
- 删除源码：`src/ats/data/sources/ons_bics_ai.py`（490 行）、`src/ats/data/products/ons_bics_ai.py`（79 行）、`products/base.py::ons_bics_ai_snapshot`、`adapters/structured/registry.py` 的 `_ons_bics_ai` 工厂与 `_RUNTIMES["ons_bics_ai"]` 键。
- 抽取退役中被证明仍然通用的能力，避免把有价值的行为一起删掉：`DiscoveryStatus.QUESTION_NOT_FIELDED` 与其「条件模块未发布不是负面证据、必须与 `no_change`/`not_yet_published` 区分」的状态语义**保留**在 `data/structured-ingestion` 原样不动，因为该 requirement 正文本身与来源无关，且它是 `question_not_fielded` 目前在规范层唯一的场景示例。
- 清理三个主 spec 里因 ONS 存在而写下的例外句与来源枚举：`data/structured-ingestion`（「来源注册驱动主动发现与增量更新」的来源清单、「统一 release check 可独立运行和按来源过滤」的 `ai_adoption` group 场景）、`data/structured-query`（「ONS BICS SHALL NOT 进入本 bundle，但既有历史数据 MAY 保留供独立审计」）、`evidence/ai-production-penetration-observer`（「ONS BICS SHALL NOT 进入该 Observer」）。退役后这些例外不再有指称对象。
- 文档同步：`STRUCTURED_DATA_OPERATIONS.md` §10.1/§10.2 移除两行镜像（该表被 `tests/test_structured_docs_consistency.py` 逐行机器校验，必须与 config 同批改）、`L1_OBSERVER_DATA_SOURCES.md` 移除「容易误读的边界」段与两行表格、`AI_MODEL_EVIDENCE_SOURCES.md` 移除来源行与双周 cadence 行、`AI_PRODUCTION_ADOPTION_CANDIDATE_SOURCES_RESEARCH.md` 的 ONS 章节改为已退役结论、`docs/validation/` 的两份验收记录标注退役（保留不删，审计链需要）。
- 测试：删除 ONS 解析与 DataProduct 测试，把依赖 `ons_bics_ai` 作为「任意来源」载体的通用测试（如发现状态机持久化）改绑到仍在役来源，并新增墓碑登记与 purge 干跑/确认的回归测试。

## Capabilities

### New Capabilities

无。本次退役不引入新的行为能力；`retired_sources` 是既有来源注册契约的扩展，归入 `data/structured-ingestion`。

### Modified Capabilities

- `data/structured-ingestion`: 在「所有结构化来源遵守统一的注册与运行契约」与「运维者可以通过统一生命周期接入、验收、发布和回滚来源」之外，新增两条要求——退役来源以机器可读墓碑登记且不可被静默复活、来源数据清除必须显式确认且不隐式触发；在「来源注册驱动主动发现与增量更新」「统一 release check 可独立运行和按来源过滤」中删除 ONS 命名与 ONS 专属 scenario，改为与来源无关的活跃来源表述。
- `data/structured-query`: 「AI adoption evidence bundle 保持三条证据轴独立」删除已失效的 ONS 例外句。
- `evidence/ai-production-penetration-observer`: 「Observer 使用固定且隔离的生产化命题」删除已失效的 ONS 例外句。

### Removed Capabilities

- `data/ons-bics-ai`: 整个 capability 退役，6 条 requirement 全部 REMOVED，主 spec 文件在 archive 时删除。

## Impact

- **配置**：`config/data/structured.yaml`（`feature_flags.sources`、`sources`、`datasets`、`metric_definitions`、`provider_mappings` 各减条目，新增 `retired_sources`）；`config/data/schedules.yaml` 无 ONS job 需删，仅其 `semantics` 中泛指的 `question_not_fielded` 表述保留不改；`config/data/catalog.yaml`、`sources.yaml`、`news_sources.yaml`、`unstructured.yaml` 经核验零命中，不改。
- **代码**：删除 2 个模块、1 个 DataProducts 方法、1 个运行期适配器工厂与其注册键；新增 `retired_sources` 读取与注册校验、purge 干跑/确认入口。
- **数据**：`var/data.sqlite` 删除 5,081 obs + 5,081 series + 3 artifacts（7.63 MB blob）+ 2 source checks；`structured_sources` / `structured_datasets` 各减 1 行注册镜像。这是本次唯一不可逆的改动，且 wave 159 的深度题样本是全库唯一的非美国企业采用切片。
- **规范**：1 个 capability 删除、3 个 capability 修改。
- **文档**：6 份文档需改，其中 `STRUCTURED_DATA_OPERATIONS.md` §10.1/§10.2 受机器校验约束。2 份验收记录与 1 份候选源调研保留并标注退役。
- **不受影响**：L1 三个 Observer 的输入、命题、报告、图表与 manifest 全部不变，本次退役不产生任何 L1 输出差异；Chain、PEAD、Chief、Trader、Portfolio、Risk 路径从未读取该来源。
- **协调注意**：当前工作区在 `codex/new_data_source` 分支上已有未提交改动（`config/data/structured.yaml`、`config/data/schedules.yaml`、4 份 docs、1 份 src、3 份 tests），与本次退役改动的文件高度重叠。实施前需先处理该批未提交改动，否则两次改动会混成一份 diff。
- **顺带发现（不在本次范围）**：`docs/L1_OBSERVER_DATA_SOURCES.md` 的 `tickertrends_public_research` 行仍写着 `frozen_seed，不参与周期发现`，与该来源已改为 7 天周期的事实不符；退役改到该文件时可一并修正，也可另开一次文档修正。
