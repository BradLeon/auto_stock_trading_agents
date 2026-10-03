# Target Dataflow 初始差距矩阵（2026-09-24）

**后续验收（2026-10-03）**：Tasks 3.1–3.8 已按零网络缓存重放完成，当前结果与边界以 [专项验收报告](TARGET_DATAFLOW_TASK3_ACCEPTANCE.md) 和 [版本化结果](TARGET_DATAFLOW_TASK3_REPLAY.json) 为准。下列 blocked/needs_refactor 是当时基线，不应继续作为新 Data API 未完成的判断；5880 条旧缺血缘 facts 仅分类登记，不修旧路径。Tasks 4 的回滚与最终 qualification 仍未由 Tasks 3 代替。

此表是 `config/data/target_dataflow_coverage.yaml` 的首次取证记录，不是 Phase F 切流资格。覆盖清单的每个节点/连线/消费者引用一个 `verify` 键；下表逐键给出状态。因此代码存在、历史 change 归档和人工文档声明均不自动变成 `verified`。`blocked` 表示当前证据不足或外部条件缺失，当前读路由不因此改变。

**当前状态更新（2026-10-02）**：后续真实 launchd 验收已完成，逐源事实以 [UNSTRUCTURED_REFRESH_INVENTORY.md 的本机 launchd 逐源验收](UNSTRUCTURED_REFRESH_INVENTORY.md#本机-launchd-逐源验收2026-10-02覆盖-229) 为准：17 个 `com.ats.data-refresh` jobs 已逐项定性，三项月度结构化来源通过真实触发及受管队列运行；可通过来源启用，IBKR/TWS 失败及未授权的 Frontier/SEC 正文仍关闭并明确未通过/非阻塞例外。FactSet 月报由单独 `com.ats.schedule` owner 运行，见 FactSet 月报手册。OpenSpec 2.2.11 已按用户确认完成；财务数据 22 个 `no_coverage` 是预期返回状态，不构成其验收缺口。下文原始矩阵与 9 月运行描述保留为历史快照，不能覆盖本更新，也不代表 Phase F 完成。

只读取证入口：

```sh
UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline python -c 'from ats.data.catalog.loader import DataCatalog; v=DataCatalog.load().validate(); print(v.valid, len(v.checks), v.reason_codes)'
UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline python -c 'import sqlite3; c=sqlite3.connect("file:var/data.sqlite?mode=ro",uri=True); print(c.execute("select count(*),sum(case when document_version_id is null or document_version_id=\"\" then 1 else 0 end) from data_evidence_facts").fetchone())'
UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline ats data --help
```

此次 catalog 校验为 `valid=True`、362 项检查、0 项失败；共有 23 个结构化来源、10 个非结构化来源、15 个结构化数据集。真实 Data DB 中可读取 `structured_ingestion_runs`、`structured_artifacts`、`structured_observations`、文档 candidate/version、事实和非结构化运行表。只读样本观察到结构化 observation→artifact→source、document version→document、部分 fact→document version 的血缘；也观察到 27 个 quarantined document candidates（例如 `period_mismatch`）与结构化的 `parse_failed`/`no_change` 运行状态。`data_evidence_facts` 共 6573 条，其中 5880 条的 `document_version_id` 为空；这可能包含旧/非文档来源，必须按来源分类，不能直接伪造文档版本。当前 `var/data.sqlite` 没有 `schedule_events` 表，不能用 Phase E 代码存在证明当前数据库已刷新日历。

| verify 键 | 初始状态 | 证据、缺口与受影响对象 |
|---|---|---|
| `catalog_validation` | `verified` | `DataCatalog.validate()` 362/362 通过；仅证明配置合同，不证明数据新鲜。 |
| `source_adapter_resolution` | `blocked` | 结构化 runtime adapter 被 catalog 校验；非结构化 adapter 及真实调用绑定尚未逐源重放。影响全部持久化消费者。 |
| `source_identity_and_policy` | `blocked` | source registries 存在；权限、保留和预算尚未逐源与实际调用核对。影响结构化/非结构化域。 |
| `refresh_binding_and_run` | `missing` | `schedules.yaml` 自述不创建 job；仓库未发现 cron/launchd/service 部署绑定。影响所有声称自动刷新的数据集。 |
| `structured_ingest` | `blocked` | DB 有成功、无变化、解析失败等真实运行；尚未逐目标域重放。影响公司、宏观、行业。 |
| `unstructured_ingest` | `blocked` | DB 有成功/缺失/尚未发布真实运行；尚未逐文档类别重放。影响 Information、Fundamental 等。 |
| `raw_lineage_readback` | `blocked` | 单条 observation→artifact→`sec_companyfacts` 可读；其余域及失败原文未覆盖。 |
| `document_version_readback` | `blocked` | 单条 transcript version→document 可读；正文与修订回放未覆盖。 |
| `structured_admission_and_rejection` | `blocked` | `structured_ingestion_runs` 存在 `parse_failed`、`zero_match`；原始资产及拒绝 reason 尚未逐域闭环。 |
| `document_admission_and_rejection` | `blocked` | candidate 表有 40 accepted、27 quarantined；期间不符 reason 可读，尚缺逐文档类别验证。 |
| `rejection_raw_and_reason_readback` | `blocked` | 非结构化拒绝 reason 可读；结构化拒绝原文/资产读回未验证。 |
| `observation_vintage_asof` | `blocked` | accepted observation 含 artifact 与 known-at；修订和历史 as-of 尚未重放。 |
| `immutable_document_versions` | `blocked` | version ID/hash 存在；覆盖和历史查询尚未重放。 |
| `neutral_fact_lineage` | `needs_refactor` | 6573 个事实中 5880 个 `document_version_id` 为空；需按来源区分合法非文档事实与缺失血缘。影响事实产品与消费者。 |
| `curated_version_and_readback` | `blocked` | 行业知识文件/产品存在；版本及数据产品边界尚未验证。影响 Layer/Sector。 |
| `product_to_consumer_contract` | `blocked` | products 模块存在；逐消费者 as-of、完整性及旧新对账未完成。 |
| `runtime_timestamp_failure_no_persistence` | `needs_refactor` | 本次已将 Technical 批量价格读取统一到 `data.runtime.market_data.fetch_close_history_many`，返回逐标的 status、query time 和最后交易日；假 Provider 隔离验证成功/缺标的/不可用，且实现路径没有持久化写入。券商与期权路径、全 runtime 查询持久层零写入和真实时间戳仍待验证。 |
| `broker_query_and_execution_boundary` | `blocked` | 券商边界需要只读账户/订单及授权写入的隔离演练；当前未取证。 |
| `calendar_version_and_trigger_boundary` | `blocked` | Calendar store/refresh 代码存在，但当前 Data DB 无 `schedule_events`；刷新/触发尚未取证。 |
| `internal_asof_completeness` | `blocked` | `execution/state_api.py` 定义 as-of/completeness；需要真实 Clerk 账本与缺口重放。 |
| `ledger_readback` | `blocked` | Clerk 实现存在；部分/迟到/人工订单读回需重放。 |
| `deterministic_rebuild` | `blocked` | rebuild 模块存在；从事实重建绩效及 hash 对账需重放。 |
| `opinion_isolation` | `blocked` | ownership 表区分数据与 Workflow 表；需验证全 Agent 写调用链。 |
| `opinion_write_rejection` | `blocked` | 静态所有权表不是运行时拒绝证明；需负例测试。 |
| `consumer_read_boundary` | `blocked` | `products/workflows.py` 有边界声明；逐 Agent 直连 Provider 检查未完成。 |
| `authorized_order_boundary` | `blocked` | Trader 审批链属于既有 Phase B/C，需以真实 revision/authorization 样本核验边界，不在本专项重做。 |
| `layer_read_contract` | `blocked` | 2026-10-02 已完成 Layer 输入盘点与只读读回（10/10 知识语料、6 个命题层共 26 条 assessment，另有 L1 独立 Observer；区域/月度产品）；按用户确认的美股范围排除 `005930.KS` 后，company_financials 仅覆盖 7/29 Layer 实体。逐层命题、静默证人和财务账本标的清单见 `UNSTRUCTURED_REFRESH_INVENTORY.md`。Layer→Sector 投影输入 vintage 与回滚演练仍未满足 3.7/4.4，不授予 consumer qualification。 |
| `information_read_contract` | `blocked` | `information/assemble.py` 通过 store bridge 读 admitted 材料；尚未取证每种材料的版本/完整性。 |
| `sector_read_contract` | `blocked` | Sector 使用 `UnstructuredReadRouter`；其 `mode` 当前不参与平台读方法路由，需核对已退役旧事实读路径与实际回退。 |
| `fundamental_read_contract` | `blocked` | 双模式入口存在；公司研究包及文档/预期基线逐项对账未完成。 |
| `macro_read_contract` | `blocked` | `macro/assemble.py` 混合 runtime 与受管 FactSet/区域数据，需分别核对 as-of 和失败。 |
| `technical_read_contract` | `blocked` | 技术面读取 runtime；行情/期权返回时间和未落库尚未取证。 |
| `chief_read_contract` | `blocked` | Internal State 与研究投影读取需绑定完整性/研究快照，未进行对账。 |
| `risk_read_contract` | `blocked` | Internal State 的风险消费与缺口 fail-closed 尚未重放。 |
| `clerk_read_contract` | `blocked` | 券商读接口和账本归因需在隔离账户/快照重放。 |
| `source_quality_cost_lineage` | `blocked` | catalog 有质量/预算字段；逐源权限、成本和完整血缘未验证。 |

初始影响与最小修复：

每个非 `verified` 项的唯一 owner 取覆盖清单中同 `verify` 键所关联节点/连线/消费者的 `owner`；若同键跨多个 owner，Data Platform 负责协调并以边的 owner 为修复责任人。受影响消费者按以下域传播，不把一个域的通过外推到其他域：

| 路径域 | 直接受影响消费者 | 稳定处理 |
|---|---|---|
| 结构化财务 / 预期 | Fundamental、Sector | 保持当前逐消费者路由，不进行 Phase F 读切流 |
| 结构化宏观 / 行业 | Macro、Sector、Layer | 保持当前逐消费者路由，不进行 Phase F 读切流 |
| 非结构化文档 / 中性事实 | Information、Fundamental、Layer、Sector、Macro | 保持已发布产品或显式缺口，不能直连 Provider 补齐 |
| runtime 市场 / 券商 | Technical、Chief、Risk、Clerk | 不写入持久化研究事实，缺失时间戳/完整性即不发资格 |
| 内部账本 / Memory | Chief、Risk、Clerk | 只读 Internal State/Memory 边界，缺口标降级 |
| 跨域治理 / 刷新 / 产品 | 上述所有依赖该来源/产品的消费者 | 逐 `domain_id + consumer_id` 阻断资格，不全局切换 |

上表是影响传播规则；单个消费者实际依赖的产品、开关和旧入口以覆盖清单 `consumers` 行为准。上表中的 `blocked` 行以第三列缺口为下一步验收动作，`missing` 和 `needs_refactor` 行以下述明确修复为先。若旧路径已经退役且无稳定回退，应保留 `ineligible`，不是恢复旧表。

- `refresh_binding_and_run`：owner 为 Data Platform；补可执行 Refresh Controller/部署绑定和运行记录。自动刷新消费者在此之前不能获得资格。
- `neutral_fact_lineage`：owner 为 Data Platform evidence store；先分类 5880 条无文档版本事实的来源，不对合法结构化事实硬塞文档引用；确有缺失原始来源者补真实 lineage 或保持 blocked。事实产品消费者在此之前不能获得资格。
- `sector_read_contract`：owner 为 Data Products；核对 platform-only 路由与已退役 Workflow Memory 事实表，确认是否存在真实稳定回退。无回退演练前保持 ineligible，不能为通过测试恢复旧事实表。
- 其余 `blocked` 项的 owner 为覆盖清单对应 `owner`；所需工作是逐域/消费者重放与证据，不是证明代码文件存在。所有旧入口暂记待退，不物理删除。

## 刷新部署绑定核验

`config/data/schedules.yaml` 有 9 个结构化 job 意图、0 个非结构化 job；其中 FactSet job 没有可执行 command。`config/data/structured.yaml` 明言 cadence 不是 cron。当前用户 crontab 为空；`com.ats.schedule` LaunchAgent 运行的是 Workflow `schedule`，不是 `ats data ingest`；仓库未发现数据刷新 service/cron/launchd 部署文件。可能存在不可见的外部部署平台，因此下表按“未提供可核验证据即 `unbound`”处理，而不是断言世界上绝无作业。

| 数据集 / 来源 | 意图 | 当前绑定分类 | 影响 |
|---|---|---|---|
| `frontier_ai_labs_revenue` | 每 7 天 group release-check | `unbound` | 公司研究/行业数据刷新不可宣称自动 |
| `ai_work_adoption` | group release-check | `unbound` | AI 采用率数据刷新不可宣称自动 |
| `ai_enterprise_adoption_us` | group release-check | `unbound` | 企业采用率数据刷新不可宣称自动 |
| `ai_worker_adoption_us` | group release-check | `unbound` | 工作者采用率数据刷新不可宣称自动 |
| `ramp_ai_adoption` / `ramp_ai_spend` | 每 10 天 source probe | `unbound` | Ramp 数据刷新不可宣称自动 |
| `openrouter_rankings_daily` | 每 7 天 ingest | `unbound` | 排名数据刷新不可宣称自动 |
| `frontier_ai_capability_benchmarks` | 每 7 天双来源探测 | `unbound` | benchmark 数据刷新不可宣称自动 |
| `sp500_earnings_insight` | 每周 FactSet job，缺 command | `unbound` | Macro/Sector 盈利背景 |
| `regional_tw_exports` / `regional_kr_exports` | 有月度 cadence，无有效 job | `manual-only`，自动能力待补 | Macro/Layer/Sector |
| `industry_dram_contract_price` | 有月度 cadence，无有效 job | `manual-only`，自动能力待补 | Layer/Sector |
| `company_financials` | 来源链有更新意图，无有效 job | `manual-only`，自动能力待补 | Fundamental/Sector |
| `market_consensus` | 有来源，无有效 job | `manual-only`，自动能力待补 | Fundamental/Sector |
| `private_company_events` | deferred | `manual-only` | 当前不作为切流候选 |
| 非结构化 10 个注册来源 | daily/weekly/monthly 或未声明 cadence | `manual-only`，有 cadence 者自动能力待补 | Information/Fundamental/Macro 等 |

首期不把以上 `unbound` 项标为自动刷新已完成；仓库内控制器与实际调度部署需分别取证。事件日历的刷新与 Workflow 触发属于 Phase E，不作为这些数据集的刷新绑定。

### 2026-09-24 本机部署增量（不改写上方初始快照）

用户选择本机 macOS `launchd` 作为数据刷新 owner。`com.ats.data-refresh` 已安装于当前用户的 `~/Library/LaunchAgents` 并经 `launchctl bootstrap`、`kickstart` 运行；`plutil -lint` 通过，控制器 `status` 显示 `launchd_loaded=true` 且最近 tick 新鲜。仓库中的部署模板为 `deploy/launchd/com.ats.data-refresh.plist`，控制器为 `src/ats/data/refresh.py`。首次部署增量补齐了 9 个结构化 job 的命令/来源/数据集/预算前置校验和 SLO；后续同日复核移除了可能与现有 `FactSetWeeklyPipeline` 重复的结构化 FactSet job，目前保留 8 个结构化 job。当前仅 `ramp_ai_index_p10d_probe` 启用；其余 job 仍是 **已配置但未启用**，不可据此宣称来源自动刷新。

Ramp 实际探针在 `2026-09-24T14:17:27Z` 触发，刷新账本键 `22041e05cc52cf3431b52e4e82ee1fd5`，退出码 0、结果 `no_change/upstream_no_change`，未产生 ingestion run、raw、准入或发布 ID；同一窗口再次触发返回 `already_claimed`。Data DB 有对应时间的来源检查记录，但本次账本版本尚未关联其 check ID，故不能把这次试跑作为完整血缘验收。控制器随后已增加 `source_check_ids_json` 字段，须在下一次真实运行验证关联。非结构化来源仍为 manual-only：现有 `ats data ingest` 面向结构化数据，不能直接把 10 个非结构化来源填入同一控制器并称已自动化。

逐源分类、现有入口和验收门槛见 [UNSTRUCTURED_REFRESH_INVENTORY.md](UNSTRUCTURED_REFRESH_INVENTORY.md)。该清单特别标出 FactSet 已由运行中的旧 Scheduler 拥有，不能与新控制器双重调度；也标出 3 个被兼容 registry 归作“非结构化”的数值序列，应沿现有结构化入口刷新。

#### 同日实现增量：文章入口与扩展调度

后续实现将刷新计划扩展为 13 个可解析 job：7 个一般结构化 job、3 个结构化月度 job、2 个非结构化周更 job、1 个非结构化日更 job。原先的 AI-adoption group schedule 被移除：现有 consumer-boundary 守卫禁止该工作流出现在 structured catalog 以外，disabled job 也会建立默认路由。新增任务仍默认关闭（除既有 Ramp）；FactSet PDF 保持 `com.ats.schedule` 中现有 `FactSetWeeklyPipeline` 唯一拥有，不另建重复 job。`ats data refresh validate` 通过，dry-run 明确显示新增 job 均为 `disabled`。三个旧 registry 数值别名现在归类为 `structured` 并映射到唯一规范 source/dataset；ECOS 显式预算为 1 request/run。此次配置验证不等于真实来源验证或启用。

Technical 批量价格读取随后统一到 Runtime Data Gateway：`sector_snapshot` 不再自行调用 Provider，`sector_inputs.sector_price_history` 返回逐 symbol 的状态、最后交易日和查询时间；Technical 报告记录价格最后交易日。兼容 `sector_prices` 仅投影出旧字典形状，不持久化运行数据。隔离假 Provider 验证覆盖多标的、symbol 缺失、Provider 不可用及日期；未调用真实行情服务。该段早期记录中将共识 cache-miss managed queue 列为缺口；该缺口已在本文后续的 2026-09-25 实施增量中修复，剩余队列入口守卫仍未全部完成。

新增 `ats data article-ingest` data-only 路径复用现有文章 adapter，写共享不可变平台文档、candidate、quarantine/raw 与 ingestion run；不触发 LLM、Agent、Workflow 或交易。为文章身份新增独立 `document_key`，新闻/研报写入 `period=''`，不再把 ID 冒充 fiscal period。隔离临时 SQLite/文档目录验证了：成功发布后读回 version ID；完全重复返回 `no_change`；同批精确 hash 只留一份文档并保存 alias；短正文进入 quarantine；IBKR 失败切片只向对应 NVDA 范围触发 Yahoo，候选保持 `pending_human_review`、raw 可读且没有 published version。刷新账本隔离重放验证相同窗口 claim 幂等、失败遵守 backoff/attempt 递增，以及 ingestion run→candidate/raw→version 引用可读。验证均未访问真实 provider 或修改生产数据。

当前仍有明确阻塞：Semianalysis 的运行中 `com.ats.schedule` Workflow 仍调用旧 `research.ingest_configured`，需先设计单一 owner 交接与消费者读路径；FactSet PDF 继续由该 Scheduler 中的 `FactSetWeeklyPipeline` 唯一拥有，本 change 没有添加第二个 PDF job。TrendForce、IBKR 与三个数值 job也保持禁用，须逐源验证真实来源/权限、launchd 最近运行和消费者读回后再启用。本文隔离重放只验证代码契约，不可替代任务 2.2.9 的真实数据证据或 Phase F 资格。

#### 2026-09-25 部署归因复核

`launchctl print gui/501/com.ats.data-refresh` 显示服务已加载、空闲（`active count=0`、`state=not running`）且上次退出码为 0；Workflow 的 `com.ats.schedule` 仍在运行。为避免把手动调用误认为 launchd heartbeat，刷新账本现在区分 `launchd` 与 `manual` invocation，升级前的 tick 标为 `legacy_unknown`。本次自然定时 tick 于 `2026-09-25T04:55:42Z` 被新归因逻辑记录，`ats.data.refresh status` 随后返回 `binding=active`。该 tick 对此前已 claim 的 Ramp 窗口做了幂等跳过，未触发新的来源请求或队列任务，因此只证明 launchd→controller heartbeat，不证明本轮采集/准入/发布/消费者读回。没有手动 kickstart，因为已启用的 Ramp job 会触发真实来源检查/潜在持久化。之前历史中关于 2026-09-24 的记录保留为历史快照，不作为当前数据集刷新链健康证明。

#### 本地回归记录

基线建立时的历史记录曾通过 `.venv` 运行；项目现统一使用 `uv`。本轮使用 `UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline` 复验，针对性队列、日历、runtime 和 FactSet 测试结果记录于本轮进度。此前完整测试的历史结果为 2011 passed、7 failed：1 项是本 change 新增的 disabled AI-adoption refresh job 越过已有 consumer-boundary 守卫；已删除该调度项，复跑 `tests/test_anthropic_consumer_boundaries.py` 为 2 passed，refresh plan 随后为 13 jobs 且配置有效。其余 6 项复跑仍失败，分别为 4 个架构守卫临时目录 `/private` 与仓库 `/Users` symlink 路径不一致、1 个已有 Chief order-path 测试对 `grep` 输出路径分隔符的断言、1 个 Chief vintage stale-snapshot 预期；均未由当时 change 文件引起，未在此范围内修改。Dataflow/Technical/非结构化相关定向测试分别为 51、56、50 passed；`compileall`、`git diff --check` 和 `openspec validate complete-target-dataflow --strict` 通过。测试文件仅为本地被忽略的验证资产，没有加入仓库交付。

### 2026-09-25 实施增量（尚未完成本专项）

覆盖清单已明确列出 Layer、Information、Sector、Fundamental、Macro、Technical、Chief、Risk、Trader、Clerk 十个 consumer contract；Information 的材料读取 API 已改为 `ats.data.products.unstructured.admitted_documents`，不再从 `Workflow Memory` bridge 读取外部文档。新增 manifest guard 验证角色产品、禁止的读 API 与仅有的 Layer→Sector / Information→Fundamental 分析投影边。

已增加 SQLite 持久队列：`ats.data.refresh` 将计划任务入队后再交给 worker；普通 `ats data ingest`、`article-ingest`、带 `--ingest-new` 的 `release-check` 以及 FactSet 导入/重处理命令经队列入口。结构化统一 Ingestion Pipeline 和非结构化文章写入口要求有效 worker lease；有显式临时 DB、artifact root 和 `--force` 的隔离回放仍可直接执行。队列记录稳定幂等键、优先级、lease/heartbeat、attempt/backoff、取消状态和运行事件，刷新账本与 queue task 双向关联并保留 raw/admission/publication 引用。

本次发现共识读路径此前仍在 Agent 请求期间直接运行结构化适配器，现改为仅读已发布快照；缺失、陈旧或未来时间戳的快照按 `structured.yaml` 中显式策略提交 24 小时幂等 cache-miss 任务，并立即返回已发布旧快照或带 `_data_status`/`_data_as_of` 的缺口，不启动 worker、不等待 provider。runtime-only/未登记数据集没有 cache-miss policy，不能入队。使用 `UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline` 验证 72 项相关回归通过；本地忽略测试 `tests/test_managed_cache_miss_local.py` 的两项检查覆盖允许数据集的排队/去重和 runtime 数据集拒绝入队。

使用 `uv run --offline` 完成定向回归：Information、非结构化 Data Product/存储/准入及读取切流相关测试 45 passed；`ats.data.contract_validation` 通过；目标清单 YAML 可解析。测试使用本地隔离 fixture，不证明生产来源、授权、部署或消费者切流已验收。当前仍未完成两个权威 registry 收敛（文章采集实现仍有 legacy registry 读取）、全部旧结构化/事件写入口的架构守卫和负例、Data-Agent 全链路禁边扫描、真实逐源 launchd 运行、FactSet/SemiAnalysis 安全 owner 交接、回退演练及完整旧新对账；这些仍是待办，不授予 Phase F 资格。后续所有 Python 验证均以项目 `uv` 环境运行。

#### 2026-09-25 队列边界后续

FactSet schedule callback 已改为周度稳定任务入队；FactSet 总管线、文档管线与独立指标/行业阶段在生产 Data DB 上均要求有效 worker lease，防止直接调用先抓取或写入。事件日历的手动入口和现有 scheduler 也改为按 source enqueue；其来源 adapter 及覆盖层/材料对账均在受管 worker 中执行。Runtime Gateway 验证覆盖逐标的行情状态与 last-bar/query 时间戳、期权和 IBKR portfolio 查询不写入 ingestion queue；允许的 consensus cache miss 只入队并返回既有版本或显式缺口。worker 正常完成测试发现并修复了 heartbeat-stop 与 lease-lost 共用事件的误判。代码级定向回归 75 passed，包含隔离 queue、calendar、runtime、FactSet、结构化生命周期测试；`contract_validation`、compileall、OpenSpec strict validation 和 `git diff --check` 通过。

更广的 103 项选择性测试中 98 passed、5 failed。4 项是架构守卫测试把 macOS 临时目录 `/private/...` 传给硬编码仓库根 `/Users/...` 后产生的相对路径错误；1 项是旧 PEAD `Evidence Observer` 运行路径仍会直接获取 transcript/搜索材料。按用户明确范围，这些 legacy 问题仅记录，不修复，也不作为新受管路径的验收门槛；Observer 对照记录见 `UNSTRUCTURED_REFRESH_INVENTORY.md`。OpenSpec Task 2.6 仅验收新目标入口并已完成；Evidence Observer 映射/退役仍是 Task 3.9 后续事项。LaunchAgent 新回调是否已由当前进程加载未验证，Task 2.2.6/2.2.9 仍未完成。

#### 2026-09-25 Target Registry 边界增量

`catalog.yaml` 收敛为仅装配 `structured.yaml` 与 `unstructured.yaml`；`DataCatalog.validate()` 检查 target domain 集合及结构化/非结构化 source ID 冲突。`target_unstructured_sources()` 返回唯一 target registry 项，Refresh Plan 的配置验证与运行前 source gate 改用该集合；原 `unstructured_sources()` 仍保留 legacy compatibility view，供历史报告与迁移盘点使用，不能授权新 job。新文章入口五个受管 source 与 target registry 一致；所选 Catalog/refresh/article/FactSet/adapter 回归为 99 passed，配置 lint 414 项、0 失败。2.2.10 保持未完成：固定 SEC/电话会、RSS alias、curated knowledge corpus 等持久化来源尚未全部登记，且本次只验证代码/config 契约，不证明外部来源和生产授权。

#### Task 3 数据域验收增量（2026-09-25）

按 Task 3 拆分运行，不将一个域的测试外推到全量数据流：

| 子集 | 结果 | 判读 |
|---|---:|---|
| 结构化 ingestion/lifecycle/company/regional/query/products/macro/calendar | 117 passed / 1 failed | 唯一失败 `test_chain_regional_feature_flag_switch_and_rollback` 从旧 Chain 入口直调持久化采集，缺少 queue lease；属于 legacy caller，本 change 只记录，不放宽守卫。其余通过项是隔离测试，不代表真实 provider 新鲜度或每域 revision/as-of 全覆盖。 |
| 文档 admission/assets/official SEC/DefeatBeta/FactSet/product | 102 passed / 22 failed | 22 个失败均为旧 `admission`、`document_assets`、`documents.gather`、DefeatBeta adapter helper 直接写 platform store 而未持 queue lease；这些调用链仅记录为 legacy gap，不改旧 writer/测试旁路。目标 data-only `article-ingest`、managed queue、新 registry 路径由其独立 99 项定向回归验证通过。 |
| Runtime market + Internal State + store ownership/data architecture/consumer routing/Memory+LLM boundaries | 55 passed | 证明所选契约/隔离测试通过；Clerk 完整订单账本、跨消费者旧新对账及真实回滚仍未覆盖。 |

因此 Task 3.1–3.4 仍保持未完成：还需逐结构化域补齐 source→raw→gate/quarantine→vintage→product→consumer 的异常/as-of 重放；对各已登记和固定文档源完成独立样本；对 Internal State/Clerk、所有目标角色逐边读取、旧新对账和回退单独验收。上述 23 个旧直调失败只登记，不是新目标路径验收失败，也不通过修改旧路径来消除。

#### Task 2.2 后续真实来源与旧测试差异（2026-09-25）

权威 `unstructured.yaml` 新增 SEC 固定索引、SEC 官方正文、DefeatBeta transcript、repository-curated 知识语料及 5 个双向 dataset 引用；`catalog.yaml` 仍只装配两个领域 registry。`DataCatalog.validate()` 当前 509 checks、0 失败，`ats.data.refresh validate` 解析 17 个 job；SemiAnalysis IMAP/RSS transport 已不经旧 `news_sources.yaml`。`com.ats.data-refresh` 的 LaunchAgent 模板和本机服务改以 `uv` 启动，新受管文章文档根位于仓库 `var/data/documents`，固定资料位于 `var/data/fixed_documents`，不把新材料写进旧 Obsidian 路径。旧路径不变。

真实运行摘要：策展语料 10/10 发布、10/10 Layer 产品读回，launchd 日检 `no_change`；TrendForce 16/16 发布、16/16 Information 产品读回；SemiAnalysis 13/13 发布并明确 `partial`、13/13 Information 产品读回；韩国 ECOS 2026-08 观测发布并由区域产品读回。上述外部源除策展日检外仍待各自自然周期的 launchd 来源运行。DefeatBeta NVDA 固定索引 4 行（只作 metadata）、电话会四财季 4 个版本读回；快照 2026-09-22 更新，在本次运行时滞后约 76.8h，新的 freshness gate 报 `partial/snapshot_stale`。SEC 官方正文对 accession 官方 URL 的真实请求失败为 `ConnectError`，0 发布；IBKR TWS 7496 拒绝连接、Yahoo 候选全部隔离或待人工审阅，0 发布；台湾 MOF `parse_failed`；DRAM 虽写入三行但最新观测 2026-07-16，超过 45d SLO。这些来源保持关闭。逐源任务 ID、revision/hash 和状态见 [UNSTRUCTURED_REFRESH_INVENTORY.md](UNSTRUCTURED_REFRESH_INVENTORY.md)。

`uv run --offline pytest` 的 49 项目标 registry、受管队列、SemiAnalysis、固定源、来源失联及诊断测试通过；`openspec validate complete-target-dataflow --strict`、`git diff --check` 通过。额外旧 Chain/IBKR/calendar 测试为 19 passed、13 failed，失败全部在旧 `chain/articles.collect_articles` 的无 queue lease 调用及已移除的旧周评入口断言；按用户要求仅记录，不修改旧路径，也不把旧测试失败解释成新目标路径失败。FactSet `com.ats.schedule` 是仍在运行的长驻旧进程，未重启/未完成 live owner 交接。

补充验证：目标全集当前为 30 标的；原固定来源 `max_rows_per_run=80`、`max_rows_per_symbol=4` 可能按报告日期截断后面的标的。新路径现将 SEC 索引与 transcript 的行预算改为 160，并在全集规模乘单标的行限超过预算时于访问 Provider 前报 `fixed_universe_exceeds_row_budget`，不再静默宣称覆盖。新增负例后目标定向测试为 **50 passed**，刷新计划 17 jobs 可解析、Catalog 509 checks 全通过、OpenSpec strict 和 `git diff --check` 通过。一次全集 SEC 索引真实受管任务 `f0c421d8-24ba-55b5-90b4-85ae088337c8` 遇 `ConnectError` 进入重试，未取得全集覆盖证据；四个 NVDA 索引行仍是此前单标的结果。修正后的 IBKR→Yahoo 真实受管任务 `a7b3779b-1456-5ec9-80e6-7bd2908614ef` 只查询登记的八标的，66 候选、62 隔离、4 待人工审阅、0 发布。以上不改变 2.2.9/2.2.12 未完成状态。

FactSet live owner 交接后续：经用户授权，在 `com.ats.schedule` 无 job 执行且下一计划窗口未到时，将旧 `/Users/liuchao/Library/LaunchAgents/com.ats.schedule.plist` 备份为 `/private/tmp/com.ats.schedule.pre-uv.plist`，以仓库 `deploy/launchd/com.ats.schedule.plist` 安装并重启。新 `launchctl` 进程 PID 85818 使用 `uv run --offline --no-sync python -m ats.runtime.cli schedule --live`，启动日志确认 FactSet 周更 job 已注册。受控调用同周回调生成唯一 `factset-weekly:2026-W39` 队列任务 `c5ed535a-221c-5100-afd3-99cc1a639a72`，事件为 `enqueued → leased → finished`，13:39:13–13:39:43 UTC，`succeeded`/attempt=1；lease 中再次调用回调仍只保留同一任务。原 plist 和新 plist 均通过 `plutil -lint`，未发现正在执行的 Workflow job 被中断。此证据完成 Task 2.2.6 的 live 入队交接；自然周六任务、PDF 与结构化指标准入质量/产品读取尚未签收，不作为 Task 2.2.9/3.2 通过。

受控运行的下游读回补充（2026-09-25 历史快照，Layer 输入签收后续于 2026-10-02 更新）：FactSet 文档版本 `SP500:2026-09-18:research_article@0c432377ecc3bcd2` 链接 33 页、1,062,946 bytes 的 PDF 原件，磁盘 SHA-256 与 `source_pdf` 关联 hash `f679661e1aaf…` 一致；Macro Data Product 返回 15 个观测，Sector 分区则为 `registered_no_data`，不得合并宣称已发布完整行业材料。固定来源 US 全集真实受管重试中，SEC 索引得到 116 行、29/30 个 Layer 标的；transcript 111 行、109 新文档、2 no-change，连同此前 NVDA 发布共 113 个完整文档由 `admitted_documents` 读回、29 实体、无空正文/缺版本。当前数据集卡片只列 `data/US/`，韩股 `005930.KS` 明确未覆盖。新路径现将缺失标的列入 `coverage_missing_entities` 并使运行保持 `partial`，而非把 29/30 当作全覆盖；两文件快照约 80.8h 超过 3d SLO。SEC 官方正文重试仍 `ConnectError`，0 发布。隔离重放另覆盖 transcript 与官方正文同一身份下的 append-only 修订与重复 no-change；目标定向集合现为 **53 passed**，Catalog 509 checks、刷新计划 17 jobs、OpenSpec strict、`git diff --check` 均通过。该段仅记载当时结果，不代表当前 2.2.9/2.2.12；2.2.11 的 2026-10-02 结果见本文件及 `UNSTRUCTURED_REFRESH_INVENTORY.md`。

#### Task 3.1 结构化数据链增量（2026-10-03，未完成）

本次结构化/区域/共识/宏观/日历定向回归为 **186 passed**。3 个旧 Chain regional 测试仍因无受管 queue lease 被拒绝；它们是旧调用路径，只登记、不修改。政府事件日历此前只在 source-run 中记录来源 URL 和计数，没有保存抓取原文；现已将 FOMC/BLS/BEA 文本原件写入共享内容寻址 ArtifactStore，source-run provenance 保存 hash/path/fetch 时间，并新增 source-run→candidate→raw artifact observation 关系。解析失败会保留已抓取 raw，但不生成已发布事件。日历 CLI 局部 `import os` 引发的 `UnboundLocalError` 也已移除并有入口回归测试。

早先的隔离受管队列真实运行中，FOMC 40 个候选与 BEA 6 项发布；BLS 的普通 urllib 请求返回 `HTTP 403`。这是历史运行记录，不代表当前结论。经授权外网只读重试已成功，见下节；每个结构化域完整的异常/as-of 到 consumer 链尚未全部签收，因此 Task 3.1 仍不勾选。

#### Tasks 3 新路径修复与实跑（2026-10-03 08:05 UTC）

可版本化入口：`UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync python scripts/verify_target_calendar.py`。该脚本使用新建临时 DB/queue/artifacts，经真实 worker lease 执行，仅读取政府公开日历，不写生产数据库、不修改 launchd 或交易。此次隔离根为 `/var/folders/n1/5cnt70z50cg4_pk9fcd707mm0000gn/T/ats-target-calendar-ojpt7_8l`。

| 来源 | Queue task | Source run | 发布 | Raw SHA-256 |
|---|---|---|---:|---|
| BEA | `3e4ad198-d46b-5279-9c99-b89b6778a88b` | `a3d680ce7e8890c013a19b5ee08ea9d1` | 6 | `68ee1f10218c2690c6d38f5e32fd01317186f633470e9a041b43122c8745bfc7` |
| BLS | `1199e44e-50f9-5545-8d46-c7a1d94b8ed5` | `434ee70865789a25e99a52cae0c1712e` | 48 | `92a350111ace106deaab5584e4084366bd367b594a0d0bad116008d82d63e501` |
| FOMC | `3e001b8e-fced-5d78-b1ee-d6504c2996ee` | `f4a464fe25239182f6e4ee80eb45e76e` | 38 | `5173ff1a156105ab5b0651c265c730dcfbfab8feb0629255929b137284dc22f2` |

三任务均 `succeeded`、source run 均 `complete`，0 conflict/0 quarantine；队列分别保存 6/48/38 条准入与发布引用，每源一个 raw 和 run；产品读回 92 项，质量 `ok`。BLS 原件与用户浏览器下载的 80,672 字节文件 hash 一致。浏览器控制工具启动失败，未声称成功操作浏览器；公开地址使用项目既有 curl_cffi 浏览器兼容 TLS、无 cookies/登录会话即可获取。真实文件的 `US-Eastern` 及无统计月份标题现已适配，未提供 reference period 时明确未知、用官方 UID 定义身份，不猜测月份。

真实 FOMC 页面暴露下一年度 footer Note 被误归入当前年度的问题；现限制解析至会议正文，2027 段末的 2028 Note 不发布为 2027 修订。同批同身份矛盾会在发布前拒绝。此前有错误候选的隔离 DB 未覆盖或删除，本次从新 DB 验收。另修复未来 source-run/conflict 影响历史 as-of、历史失败永久导致 degraded、单源刚更新掩盖另源过期，以及未来 source link 泄漏至历史 lineage 的问题。人工裁决后的历史 conflict 状态完全重放仍待专项核验，不据此宣称 Task 3.1 全完成。

Observer 退役：覆盖清单按 dataset 登记 Layer HIER/DOC 和一般共享产品去向；`agent.evidence_observer` 墓碑与 consumer alias 已建立。新快照以 Layer 保存并携带旧身份/策略版本，source、observation、原版本不改写；现有三个 Layer evidence helper 归属 Layer，旧中性提取工具及模型 key 仅为兼容组件，不新增可调度 Observer。合同检查拒绝重复角色和无效 dataset。Task 3.9 已完成，不等于其余角色数据边已验收。

本轮 `uv run --offline --no-sync pytest -q` 验证 consumer identity、architecture guards、calendar refresh/product/store、Internal State、Clerk e2e、Runtime boundary 共 **59 passed**；三个 warning 均为既有 sector 可视化字符串转义，未修改该旧路径。`openspec validate complete-target-dataflow --strict` 通过。架构守卫本身的临时路径识别及相对 import 解析已修复，不修改旧数据 caller。消费者清单中的四个不存在/不适合的 API 名称已纠正，但完整逐边数据包契约和对账仍属 Task 3.7，不能仅凭声明通过即勾选。
