## 1. Catalog、Schema 与主动发现基础能力

- [x] 1.1 注册 `us_census_btos/ai_enterprise_adoption_us`、`rps_genai_adoption/ai_worker_adoption_us`、`ons_bics_ai/ai_enterprise_adoption_uk` 的 source、dataset、metric、provider mapping、单位、period basis、质量规则和 retention，并把四个 AI adoption 来源编入独立 source group。
- [x] 1.2 为 `structured_source_checks` 增加兼容迁移、repository 写入/查询和 health 聚合，验证无 ingestion run 时也能持久化 `no_change`、`not_yet_published`、`question_not_fielded` 等检查结果。
- [x] 1.3 增加 `DiscoveryResult`、`ReleaseCandidate`、release identity 和 methodology fingerprint 模型，扩展 `RuntimeSourceSpec` 的 discovery/check cadence/conditional-module 声明，同时保持未迁移 Adapter 的 `fetch()` 兼容。
- [x] 1.4 实现来源级 due-check、候选幂等身份和 `source_id + candidate identity` 互斥，覆盖定时与人工运行并发发现同一版本的测试。
- [x] 1.5 扩展 availability、quality 和 ingestion-history 查询，分别展示 last checked、latest upstream、latest ingested、latest available period、source-native cadence、最近检查/采集状态和错误诊断。

## 2. 统一 Release Discovery 与更新入口

- [x] 2.1 实现注册表驱动的 discovery coordinator，支持按 group/source/dataset 过滤、仅发现和发现后采集，并对每个来源/切片隔离成功、失败及 `partial` 状态。
- [x] 2.2 扩展 `ats data release-check`，支持 `--group ai_adoption`、`--source`、`--ingest-new` 和 `--force-check`，以结构化格式返回 due、候选、最新上游/本地身份和可操作状态。
- [x] 2.3 为 metadata 不可达、未来排期、没有变化、条件问题未出现、方法口径漂移、schema validation failure、新发布和历史修订建立 fixture 与状态机测试。
- [x] 2.4 验证重复内容不产生 artifact/observation 重复、修订内容追加 vintage、一个来源失败不阻止其他来源发布，且 discovery-only 不改变 observation 发布状态。

## 3. Anthropic 主动发现迁移与回归

- [x] 3.1 将 Anthropic Hugging Face metadata/release discovery 拆入统一 `discover()` 协议，继续使用 commit-pinned 文件身份、完整 release 校验和 source-native `no_change` 语义。
- [x] 3.2 让 Anthropic Claude.ai、1P API、taxonomy 和 exposure slice 通过候选身份调用现有采集流程，验证多 artifact lineage、单 slice failure、内容幂等和修订 vintage 不回归。
- [x] 3.3 为无新 release、release 文件未齐、同 commit 重跑、新 commit 同内容和新内容五种情形增加 discovery/ingestion fixtures。
- [x] 3.4 运行现有 `ai_work_adoption`、Job Profile、L1 v1 packet、图表和 manifest 回归测试，确认迁移 discovery 不改变既有 observation 和派生值。

## 4. Census BTOS Core 新口径数据源

- [x] 4.1 实现 Census 官方 periods/questions/answers/data 与历史下载 metadata client，保存请求身份、内容哈希和官方指针，并区分未来排期、已发布期间和历史修订。
- [x] 4.2 建立新 Core 问题的批准 fingerprint 与 2025-11-17 日期门，只准入“任一业务职能”当前/未来 AI 使用问题，测试旧“生产商品或服务”口径始终为 out-of-scope。
- [x] 4.3 流式保存每个合格 period 的 source-native query slice，解析全国、NAICS sector/subsector、employment-size 和 sector-by-size，过滤州、MSA 和其他地理切片。
- [x] 4.4 规范化问题、答案、estimate、standard error、reference window、release/known time、统计单位和 population entity，确保 headline Yes 与完整答案集均可追溯。
- [x] 4.5 实现 BTOS 百分比/SE/答案加总/抑制/重复/schema drift 质量门和源行—observation 100% 对账，未知字段进入 quarantine/pending mapping。
- [x] 4.6 实现四期移动平均、连续 period 百分点变化及当前—未来采用差，返回 derivation version、公式、全部输入 observation IDs，并明确不提供统计显著性结论。
- [x] 4.7 实现 BTOS DataProduct 的全国、行业、规模、sector-by-size、历史、vintage、as-of、quality、freshness、lineage 和 Agent 摘要接口。
- [x] 4.8 在隔离 SQLite/artifact 目录仅回填 2025-11-17 及以后新口径期间，验收最早/最新期间、旧口径零准入、重复回填 no-change 和修订 as-of 重放。

## 5. RPS/FRED 工作用途数据源

- [x] 5.1 注册并实现五条工作口径白名单 series 的官方 FRED CSV/metadata 发现，固定单位、季度频率、未季调状态、notes、来源机构和每条 series 的 release identity。
- [x] 5.2 为五条 series 分别保存 immutable query-slice artifact 和 observation vintage；允许部分更新，同时输出 series periods 是否齐平，且不引入 all-purpose series 作为替代。
- [x] 5.3 规范化工作采用、上周工作使用、每日工作使用、AI 辅助工时和自报节省工时，固定全国统计主体为美国 18–64 岁 employed adult，并保留自报/非正式部署限制。
- [x] 5.4 实现百分比范围、频率/单位/notes drift、重复冲突及 `daily <= last_week <= adoption` 质量门；异常时停止 persistence 派生并生成可审计 warning/failure。
- [x] 5.5 实现 weekly/daily persistence proxy、季度百分点变化和可用同比，返回总体比例之比的公式、版本、输入 IDs，并禁止命名为 cohort retention。
- [x] 5.6 实现 RPS DataProduct 的 latest、可比历史、五项指标、proxy、series alignment、quality、freshness、lineage 与 compact Agent slice。
- [x] 5.7 用官方历史 fixture 和隔离库验证首次回填、季度无更新 no-change、单 series 修订 vintage、逻辑异常隔离及无需 FRED API key 的默认运行。

## 6. ONS BICS AI 条件模块数据源

- [x] 6.1 实现 ONS 官方 BICS dataset/release 页面、wave workbook 和 questionnaire discovery，保存 wave、URL、文件哈希并以 `wave + workbook URL + SHA-256` 建立 release identity。
- [x] 6.2 实现 questionnaire-first fingerprint：规范化 question text、answer options、routing 和 population universe；精确批准才发布，模糊匹配只创建 schema-drift 候选。
- [x] 6.3 对新 wave 没有受支持 AI 问题的情形写入 `question_not_fielded`，验证不继承上一波值、不创建零 observation，也不触发虚假 stale。
- [x] 6.4 解析并规范化采用广度、平均技术数量、extensive/limited/pilot、员工日常使用 buckets、业务用途、采用方式、培训/技能与岗位影响等白名单指标。
- [x] 6.5 为每个 ONS observation 保存 question regime、全部企业或 conditional-on-AI-user denominator、企业规模、SIC、reference window、response/quality 信息和 UK supplement 标记。
- [x] 6.6 实现范围、抑制、低样本/高误差、schema drift 和 article/chart/workbook reconciliation 质量门；冲突 cell 不进入默认趋势并保留双 artifact lineage。
- [x] 6.7 实现 ONS DataProduct 的 wave/as-of、采用广度、组织深度、行业/规模、coverage、questionnaire 引用、freshness、quality 和 lineage；历史不足时只返回 snapshot。
- [x] 6.8 在隔离库回填官方 workbook 明确可比的 AI waves，验收条件分母、methodology break、单波 `insufficient_history` 和美国/英国不可默认相减。

## 7. 四轴 AI Adoption DataProducts

- [x] 7.1 建立 `AiAdoptionEvidenceBundle`，组合 BTOS enterprise breadth、RPS worker persistence、ONS organizational embedding 和现有 Anthropic task production，保持四套 dataset/series/period 独立。
- [x] 7.2 为每轴定义版本化 headline、趋势最低历史、描述性容差和 `expanding/stable/contracting/mixed/insufficient_history/unavailable` 规则，输出逐步判定与输入 observation IDs。
- [x] 7.3 实现 statistical unit、denominator、geography、technology scope、reference period、frequency 和 methodology regime 可比性矩阵；只有全兼容时才允许数值比较，否则仅输出方向性印证/冲突。
- [x] 7.4 实现异步 `as_of` 选择，返回各来源真实 latest period、published/known time、age 和 source-native freshness；验证不前向填充、不插值、不伪造共同期间。
- [x] 7.5 实现 `broadening_and_deepening`、`breadth_without_confirmed_depth`、`provider_telemetry_only`、`mixed_evidence` 和历史不足的透明整体状态规则，禁止生成综合分数。
- [x] 7.6 生成多来源 snapshot manifest，固定 observation/artifact IDs、release identities、query、period、methodology/derivation/rule versions 和 comparability results，并实现完全离线重放。
- [x] 7.7 为单源缺失、来源不可达后沿用最近成功值、异步期间、方法断点、方向一致和方向冲突建立 DataProduct contract tests。

## 8. L1 Observer、Agent Context 与方法卡

- [x] 8.1 保持 `claim_id=ai_core_production_workflow_penetration`，新增 `claim_definition_version=v2` 和扩展命题文本，更新 `ai_hardware/L1_app` 配置而不改变其他 layer/sector。
- [x] 8.2 改造 L1 runner 只消费受治理的四轴 bundle，禁止直接访问 Provider 或物理表，并让 packet、compact context、Markdown、表格和图表共享同一 manifest。
- [x] 8.3 定义并实现 `compact`/`review` context schema 与确定性字符/事实预算；确保命题、逐轴状态、分母、期间、矛盾、质量/缺失警告、来源和 manifest 不被裁剪。
- [x] 8.4 扩展本 Observer 专属中文方法卡，按来源说明统计主体、分母、地区、技术范围、期间、样本/可见覆盖、问题/taxonomy regime、派生、质量、不可推断事项和 lineage。
- [x] 8.5 按“命题判断—四轴总览—印证/冲突—BTOS—RPS—ONS—Anthropic 分布与四指标—TOPN—公式/限制/来源”生成中文审阅报告。
- [x] 8.6 验证历史不足、只有广度改善、只有 Provider 遥测改善、三轴同步扩大和轴间冲突的 claim judgement 与事实措辞，禁止越过来源可支持的推断。
- [x] 8.7 回归 `ats evidence layer --sector ai_hardware --layer L1_app` 的单层运行与产物路径，并证明其他 layer Observer、Chain、PEAD、Chief 和交易 workflow 均未被调用或修改。

## 9. 结构化表格与人类可视化

- [x] 9.1 从 review model 生成稳定排序的 CSV/JSON table rows 与 visualization descriptors，统一中文指标名、单位、来源、期间、observation IDs 和 rows hash。
- [x] 9.2 实现 BTOS 全国趋势/四期均值及行业规模截面、RPS 使用持续性 small multiples/工时面板、ONS 同 universe 深度 snapshot/趋势、Anthropic 现有覆盖与 TOPN 图表。
- [x] 9.3 实现四轴状态/期间矩阵图，禁止把不同统计主体的百分比放入共同数值轴或绘制统一渗透指数。
- [x] 9.4 为每张 PNG 或等价静态图保存包含标题、指标定义、单位、来源、期间、rows hash、observation IDs、manifest ID 和 renderer version 的 JSON sidecar。
- [x] 9.5 实现 renderer 失败降级：保留 packet、Agent context、Markdown 和表格，返回 `visualization_warning`，且不改变数据结论。
- [x] 9.6 运行可视化快照/数据对账和人工审阅，确认正文、表格、图片标题与 sidecar 数值一致、中文可读、TOPN 位于总体分布之后。

## 10. 端到端验收、调度与发布

- [x] 10.1 在隔离 SQLite/artifact/output 目录完成四来源 `release-check → ingest → quality → availability → bundle → L1 layer → lineage → manifest replay` 全链路验收。
- [x] 10.2 使用当前官方端点执行一次真实 discovery，逐来源核对 latest upstream/available/ingested、问题或 series identity、来源期间和 `no_change/new_release/question_not_fielded` 语义，并保存验收记录。
- [x] 10.3 配置外部部署环境每周调用 `ats data release-check --group ai_adoption --ingest-new`，验证失败隔离、重试/退避、单并发、超时、字节上限和 last-checked 健康监控。
- [x] 10.4 对 BTOS、RPS、ONS 和 Anthropic 的重复运行、历史修订、网络超时、404、限流、损坏文件、schema/question drift、未知 metric 和 source-age warning 做故障注入验收。
- [x] 10.5 比较 L1 v1 与 v2 shadow 输出，核对稳定 claim ID、definition version、四轴判断、数据期间、context budget、图表和 manifest；审阅通过后仅切换 `ai_hardware/L1_app` 默认版本。
- [x] 10.6 更新运维手册、数据字典、方法卡说明、source checklist 和命令示例，明确 BTOS Supplement/Eurostat 排除、BTOS 日期边界、RPS 自报限制、ONS 条件波次及跨源不可融合原则。
- [x] 10.7 运行结构化数据、Evidence、CLI 和可视化完整测试集以及 OpenSpec validation，记录性能、artifact 大小、context 字符预算和全部验收证据。
- [x] 10.8 验证配置回滚到 v1 不删除新 observations/artifacts、暂停单一来源不会污染其他轴，并完成 platform cutover 与发布后首轮健康检查。

## 11. 审阅返工：统一存储、可解释趋势与报告闭环

- [x] 11.1 统一结构化采集 CLI、主动发现、DataProducts 与 Evidence 的正式 repository 选择，以 `ATS_DATA_DB_PATH`/`ATS_DATA_ARTIFACT_ROOT` 为单一发布入口；兼容变量只允许显式测试使用，禁止再以跨库复制完成发布，并增加路由回归测试。
- [x] 11.2 收窄 L1 snapshot 与 Agent context：主结论只固定各轴 headline/trend 的必要输入，明细表和图表使用独立可复算 lineage/rows hash；context 返回计数和 lineage pointer，不平铺无关的数万条 observation IDs。
- [x] 11.3 重写趋势与判断解释层：按来源原生时间顺序排序；用端点变化、线性趋势、方向一致率和最小实质变化共同识别中期趋势，单次小回撤不机械判为 `mixed`；BTOS 在可用时披露 standard error，所有轴输出变量、期间、变化轨迹、规则和中文判断原因。
- [x] 11.4 修复 BTOS 波次按字符串排序导致的 `107 → 88` 顺序错误，图表横轴显示可理解的调查参考期并辅以波次号；为波次 88–107 建立排序和图表回归测试。
- [x] 11.5 将 visualization descriptors 渲染为 Markdown 内嵌图片及 CSV/JSON/sidecar 链接，确保四轴图、来源面板和 Anthropic 分布/TOPN 按正文顺序出现；完成数据—sidecar—图片—正文相关测试集对账、全仓测试现状审计和 OpenSpec strict validation。

## 12. 第二轮审阅返工：三轴收敛与方法披露

- [x] 12.1 将 ONS BICS 从 L1 bundle、Observer、manifest/context、报告、图表及 `ai_adoption` 主动更新组剔除，保留既有历史数据和独立 DataProduct 供审计；命题与整体规则改为 BTOS、RPS、Anthropic 三轴。
- [x] 12.2 删除状态/期间矩阵图及其 table/descriptor/正文引用，验证 renderer 不再生成 `four_axis_status_period_matrix.*`。
- [x] 12.3 将 BTOS 图横轴改为带四位年份的调查参考期日期，不显示 W 波次号，同时保留内部按数值 wave 排序与 sidecar period 血缘。
- [x] 12.4 从人类正文移除逐步判断轨迹，保留结构化 packet 中的规则和 lineage；补充每轴指标定义、公式、发布来源、统计主体/分母、参与维度和数量。
- [x] 12.5 为 Anthropic 披露 occupation/task 的可见数、达标数、Usage Share 分母、阈值和四项公式；TOP occupation 过滤无法映射名录名称的 code-only 实体并重新稳定排名。
- [x] 12.6 重新生成正式 L1 报告，核对三轴结论、图表、CSV/JSON/sidecar、精简 manifest/context，运行相关回归测试和 OpenSpec strict validation。

## 13. 第三轮审阅返工：恢复 Anthropic 信息密度

- [x] 13.1 恢复 Anthropic 职业/任务两种粒度的可见单元生产化率与生产化流量份额双面板月度图，并保存与四项 period rows 一致的 sidecar。
- [x] 13.2 恢复职业内已确认生产化任务覆盖的 CCDF 分布图，使用中文指标名和可解释坐标轴，并在分布统计之后、TOPN 之前嵌入报告。
- [x] 13.3 在 Anthropic 正文增加最近两个可比月的四指标数值与百分点变化表，确保表格、图、CSV/JSON 和 packet 使用同一派生结果。
- [x] 13.4 重新生成正式 L1 报告，完成图表/sidecar/正文对账、相关回归测试和 OpenSpec strict validation。
