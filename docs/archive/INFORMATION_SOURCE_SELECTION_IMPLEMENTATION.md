# Information 信息源优化实施记录

日期：2026-10-12。依据开发／测试计划及用户“按照开发与测试计划，开始编码”的指示。本轮代码尚在工作区，未发布生产切换。

## E0 范围与实验约束

- 采集覆盖取 `config/data/unstructured.yaml` 的 IBKR symbols：NVDA、AMD、AVGO、MRVL、TSM、MU、ORCL、MSFT；业务内容验收仍为 MRVL、MSFT、NVDA、TSM。
- 存量输入为 `tmp/information_publisher_review_20261012/inventory.json` 的 22 篇 SemiAnalysis／49 篇 TrendForce。原 as_of、正文和版本保留；第一批只读探测，第二批仅在隔离数据库验证元数据修订，未回写生产数据库。
- 生产 IBKR 正文额度仍为 10。分配规则为每个有直接标题候选的配置标的先获得一次机会，多标的正文不重复请求，然后使用共享池；不足额度明确延期。重试占用同一请求预算。
- 第一批正文调度默认 300 秒。后续已将发现耗时计入启动调用时限；发现阶段结束若已超时，不再启动正文请求。已发出的发现／正文请求仍受 adapter 自身 timeout 控制，不能据此承诺严格墙钟上限，须在 E4 测量。
- 已准入且完整的正文允许在 24 小时内复用；复用不占正文请求，给延期候选腾出机会。到期重新检查来源更新；partial 暂不复用，避免阻止获取完整内容。
- 本批没有启动真实材料 capture。真实模型预算沿用计划起点 8 篇／40,000 字符，但尚未登记总 token／延迟上限及具体内容授权，不能启动新外发。既有 monitor 测试最初未 mock triage 模型，产生一次连接失败；现已补 mock，后续该路径不再尝试真实连接。

## 第一批受影响契约及验证范围（后续进展见第三／四批）

| 契约／实现面 | 本批调整 | 后续要求 |
|---|---|---|
| Article adapter | TrendForce 新增 `fetch_material`，一次请求返回正文及原始页面字段；原 `fetch_body` 保持正文接口 | 补真实页面字段样本／subtitle 定位 |
| Article 采集准入 | 原文不改，按清洗视图有效字符数校验；关联保留字段、注册别名及原文位置 | 第二批已补短实质预览／teaser 政策与冲突核验；生产存量修订尚未执行 |
| IBKR 实体门禁 | 第一批为 title-only；后续显式 source policy 允许 title／body 的注册公司原文证据，require_entity_verified 保持开启 | 已补正文正例、查询血缘／人名反例；真实源取证须重新验证 |
| 文档发布历史 | 新增独立元数据修订；同正文 no_change 可修订关联／标题等，旧 publication 保留 | 第二批已提供受控修订入口和缓存失效测试；生产应用留待相应队列任务 |
| DOC_DATA | 附加可选 quality 元数据；保留正文版本和 publication_id | 出版者探索 scope 尚未扩展，不绕过 bound scope |
| Information | 可见文本清洗；上下文披露 completeness、版本、输入截断；日期按时间解析并排除未知／未来日期 | 第二批已补抽取缓存及 bound-task 读取约束；来源探索、排序与目标简报桥接仍待 E3b |
| Newsletter Article | 附加 source_fields；邮件记录 Subject／Date 来源，页面字段复用已有 HTML；RSS 缺时间不补 now | 第二批支持精确版本／原文片段字段修订，隔离恢复四篇标题；新采集副标题定位仍需补齐 |

上述面涉及 Phase F 受保护的输入／解析／Agent 模板，未来最终取证须重新冻结受影响子范围。本批不会将原 OpenSpec 部署、观察或退休事项标为完成；本文记录实施影响，后续正式验收时还须同步适用 OpenSpec 及资格指纹。

## 已实现的基础行为

1. `material_quality` 提供可追溯清洗视图，去除不可见 padding、独立导航及重复的已知标题，保留原文 hash 和字符映射；保留正文中的订阅边界与引用。
2. 公司名／注册别名可忽略大小写，ticker 按大小写匹配；两字符 ticker 需股票标记或金融上下文。`Poo Mu-ming` 不关联 MU，`NASDAQ: MU` 和 `$MU shares` 可识别。产品主题不自动产生关联。
3. TrendForce 在正文同一次请求中解析 h1／og:title 等原始标题与导语；未取得页面标题时显式保留 slug fallback，不还原其数字含义。
4. 有限正文预算按配置标的分配，并记录请求、延期、缓存命中、候选及用时；缓存复用与到期再检查有回归覆盖。
5. 元数据修订与当前关联索引在同一事务提交；同 known_at 的连续发布按实际写入顺序读取，旧 as_of 保留旧关联与完整性，不新增虚假正文版本。
6. Information 不再将可见预览统称全文，缺发布日期不以 fetched_at 充当新消息。

## 验证与失败归因

统一入口 `UV_CACHE_DIR=/private/tmp/phase-f-uv-cache ./scripts/run_tests.sh …`，由 uv 同步 all-extras 并执行 pytest；未手动创建或激活 venv。

- 第一轮：63 passed，1 个既有 SyntaxWarning。覆盖新质量测试、IBKR 解析／client、Information、平台文档读取及 provider 故障路径；独立 basetemp 为 `tmp/pytest-basetemp/information-selection-01`。
- 扩大回归初次：118 passed／5 failed。范围为新质量测试、research/replay、newsletter cursor、document assets、unstructured products/source acceptance、chain、SemiAnalysis 和 provider 故障；basetemp 为 `tmp/pytest-basetemp/information-selection-02`。
- 用提交 `5e4379e` 的独立源码快照复跑 `test_research.py`：8 passed／4 failed，确认四项失败在改动前存在。它们依赖兼容 newsletter source 名称／旧库输入能进入 governed extractor，并非本批新增回归；新链路 E3b 集成仍须单独验证，不能凭这些旧路径声称新链路通过。
- 第五项要求缺发布时间时以 fetched_at 补齐，与本计划 T12 冲突；已改为验证缺日期不通过新消息检查，未恢复虚构日期。
- 调整后扩大合并回归：168 passed／4 个已存在失败／1 个既有 SyntaxWarning；basetemp 为 `tmp/pytest-basetemp/information-selection-03`。
- 补充同正文采集集成反例及消费者／Phase F 边界回归：97 passed／1 个既有 SyntaxWarning，324.16 秒；basetemp 为 `tmp/pytest-basetemp/information-selection-04`。覆盖包内无 provider 获取、角色边界、scope 及决策缺材料门禁。
- 最后补入邮件无时区反例后，质量／newsletter cursor／IBKR client／provider 故障回归：42 passed，6.46 秒；basetemp 为 `tmp/pytest-basetemp/information-selection-final`。`git diff --check` 与受影响模块 compileall 通过。
- 既有 monitor 补齐 triage 失败 mock 后，Information 回归最终 22 passed／1 个既有 SyntaxWarning，3.70 秒；basetemp 为 `tmp/pytest-basetemp/information-selection-monitor-final`。该测试同时覆盖模型失败时不发布简报及不写 dossier。

四项已存在失败：`test_research_extracts_filters_and_publishes_briefs`、`test_research_dedups_articles_on_second_run`、`test_research_llm_failure_still_marks_article_seen`、`test_research_uses_stable_document_id_for_pre_metadata_article`。本批不以旧路径失败否定新质量规则，也不把它们隐去或当作通过。

### 71 篇存量的只读规则探测

对原 inventory 应用新清洗／实体规则，输出 `tmp/information_selection_implementation_20261012/neutral_quality_probe.json`，保留每项原文定位；没有修改生产索引、没有模型调用。下表是全历史存量中可定位的中性公司提及篇数，**不是七天窗口篇数、Information 采纳数或重要性评分**。

| 来源 | NVDA | AMD | AVGO | MRVL | TSM | MU | ORCL | MSFT |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SemiAnalysis，22 篇 | 15 | 11 | 2 | 0 | 3 | 0 | 0 | 13 |
| TrendForce，49 篇 | 16 | 6 | 4 | 0 | 12 | 15 | 0 | 4 |

该表为第一批结果。第二批在隔离库恢复了 4 篇标题，4 篇发布日期仍未知；未据 fetched_at 或不完整日期推断发布时间。MU 人名误关联未被新规则保留；MRVL 与 ORCL 的直接提及为空，不能强行补关联或宣称它们已有直接材料。有限产业主题探索的有效性留给 E3b 验证。

## 第二批：E3a／E2b 与 E3b 边界支撑

### 已实现

- SemiAnalysis 明示允许实质 partial，最低有效正文 400 字符，同时要求多句实质内容及公司／数值证据；长营销文案、teaser、未解决的弱访问边界不能作为事实正文。其他来源的完整正文门禁不随之降低。
- stored full 遇到本文付费边界重新判定 partial／teaser；显式引用、他文提示保留原判断，弱提示输出 unknown。已有 partial 不因没有标记就自动升级 full。当前为保守规则，尚需 E4 对自然样本检查误判。
- 研究输入在 SemiAnalysis 自身强付费边界前结束，排除其后 CTA／推荐文章／页脚参与公司关联和模型抽取。原文、hash 和 body version 完整保留，结论片段仍映射回原始字符位置；他文引用不触发截断。
- 新增 `scripts/reconcile_article_metadata.py`，默认 dry-run，限定注册来源、数量、文档 ID；字段修复必须提供 exact version 与可解析原文 span。生产写入仍受原 source 队列 lease 约束，不能用脚本绕过 owner。
- 元数据修订独立发布，旧正文不重新生成。补上 legacy-only 文档在第一次修订前的原读取视图保留，并明确记录 legacy provenance，不将其宣称为过去已发布的新元数据。
- 抽取复用指纹包括正文版本、标题／发布时间、完整性、关联／字段证据、清洗策略、模型／prompt、输入字符上限与 universe。元数据变化使旧 full 结果失效；失败可重试，成功复用；无模型探测不占成功缓存。通知改为显式 opt-in。
- bound-task 抽取使用授权身份的 DOC_DATA scope，禁止转为全库扫描或跨角色读取。输出保留原始文档版本、publication、证据 span、三时钟与置信度，partial 明示隐藏内容未取得。此处只完成读取／抽取边界，尚未接通文档投影到标的简报。
- 公司别名证据与相同时点文档顺序均固定排序，避免跨进程散列顺序导致伪修订和缓存失效。

### 隔离存量验证

输入仍为原 22 篇 SemiAnalysis 与 49 篇 TrendForce；没有采集新文章、没有调用模型，也没有修改生产数据库。

- 四篇 SemiAnalysis 标题从同一 immutable body 内经人工审阅的重复标题片段恢复；4 篇日期保持未知，不能通过新鲜度门禁。
- 22 篇 SemiAnalysis 在现有付费边界证据下均为 partial；49 篇 TrendForce 保持 full。此结果是本批存量的规则核验，不代表供应商全部内容形态。
- 初次隔离探测发现 23 篇 legacy-only 文档在旧 as_of 消失，已修复并加入回归。后来发现相同时点排序与别名集合顺序不稳定，亦已修复。失败记录保留，不能把第一次探测当成通过。
- 修复后原 as_of 的 **366 条读取结果逐条相等**；没有新增正文版本，重复修订为 no_change。页脚排除后再次验证仍保持这些性质。该时点统计受公司注册别名影响；本轮后来补齐 ORCL 注册信息后的复验见下文，原 ORCL 零值不能作为来源无相关内容的结论。

本机证据目录：`tmp/information_selection_implementation_20261012/round2/`。`field_evidence.json`／`document_ids.json` 固定修订范围；`first_probe_failure.json` 保留首次失败；`reconciliation_results.json` 记录字段／完整性修订，`visible_prefix_results.json` 记录页脚排除后的最终核验；`verify_visible_prefix.py` 为该隔离验证入口。数据库为 `isolated_data_v2.sqlite`，生产写入、模型调用和新增正文版本均为 0。这些临时文件按既有规则不提交 Git。

### 第二批回归

统一使用 uv 的 `./scripts/run_tests.sh`，分别采用独立 basetemp。

- 中间回归 73 passed、71 passed，均有 1 个既有 SyntaxWarning。
- 恢复中断后补付费边界后页脚反例、真实 store 抽取缓存集成和旧 as_of 固定排序反例：**78 passed／1 个既有 SyntaxWarning，76.54 秒**；范围为质量、可见性修订、Information、newsletter cursor、document assets、provider 故障；basetemp 为 `tmp/pytest-basetemp/information-selection-round2-recovered`。
- 补充 Data Products／来源准入回归：**17 passed，14.14 秒**；basetemp 为 `tmp/pytest-basetemp/information-selection-round2-products-corrected`。一次误用不存在的测试文件名导致未执行测试，改为实际文件后重跑；未执行的那次不计入通过数。最终 `git diff --check` 与受影响模块 compileall 通过。

四项第一批确认的旧 research 路径失败仍是独立遗留项；上述针对新链路的通过数不代表整个测试套件全绿，也不代表 Phase F 最终资格通过。

## 第三批：E3b 新入口接线

依据原目标文档 §9.1，复用 Information 现有抽取与材料性职责，没有新增 Agent 或 workflow。

### 读取契约与冻结影响

- `config/pead.yaml#research.publisher_entities` 明示 `SEMIANALYSIS`／`TRENDFORCE`，只扩展 Information 的 entity 读取身份；Fundamental 等其他角色不继承。目标输出 scope 仍为目标 ticker。
- `target_dataflow_coverage.yaml#consumers.information.document_candidate_scope` 记录 publisher 候选契约。新模块／测试纳入 qualification 必需指纹文件，配置也仍由业务计划冻结。
- 新身份包括目标＋已有 signal_chain＋这两个 publisher。旧 tape／scope／部署许可不能自动承接新范围；缺少 publisher 的旧身份会在读取前拒绝。必须对受影响范围重新冻结与取证，未执行生产切换。
- 直接材料和 publisher 材料均经 `consumer_api.read_input` 的 DOC_DATA 读取，分别限定目标／peer、SemiAnalysis、TrendForce 三个通道，每通道 120 条，再按发布时间七天窗口过滤；直接通道保留各类已准入文档，包括公告／申报／电话会，不只取新闻。产品通道界限命中单列，不能声称全覆盖。

### 选择、抽取和简报

- 新 target 入口改用 `selection.py`／`target_research.py`；兼容入口保留。启用 publisher 后，独立 pass 不再额外运行一次历史全库抽取，以免双重消费。
- 对目标的标题、副标题／导语、可见正文证据依次优先，其次为静态 signal_chain。没有明确公司提及的两个 publisher 材料可进入有限探索；产品名不写入公司关联索引，publisher 身份不构成重要性判断。
- 每个 target invocation 最多选 8 篇，其中最多 2 篇为无实体提及的 publisher 探索，两来源轮换。未处理解释优先于已缓存解释，版本重复只处理一次。读窗口仍为 monitor 七天，独立历史抽取的回补配置没有被用来伪装本期新消息。
- 调用既有 `research_extract`，在同次抽取中明确返回材料性，独立于置信度；不读取 Fundamental thesis。引用必须能在实际可见输入中定位，否则本次抽取失败，不能将坏引用变为成功的空结果。
- Workflow Memory 以同一原文版本＋解释指纹＋target universe＋模型／prompt／输入策略缓存抽取结果，包括空结果。每次先重新通过 DOC_DATA 准入／scope／时间检查，再读本角色缓存；不会读别的分析师观点。未来时点缓存不供过去运行使用。
- 汇总为目标 scope 的 InformationBrief，逐结论保留原文 span、publication、抽取投影、三时钟和置信度；相同 quote／实体／结论去重，但不按文章数提高置信度或宣称独立佐证。partial、未核验事件时点和延期候选均明确披露。
- 真正空结果输出限定于已选、已核读可见材料的核读记录，不虚构公司变化、不宣称整个市场无消息。模型失败不发布简报，并允许重试；无模型选择探测不占成功缓存。

### 预算口径

配置登记每个 target invocation 的 8 篇／40,000 字符每篇／320,000 正文字符总额／180 秒启动调用时限。两篇探索计入 8 篇而非额外调用；缓存复用不消耗模型正文额度。超限前不 claim、不调用模型，状态及延期数可查。

这些是当前代码的**逻辑抽取调用与正文字符**上限，不是总 API 请求、token、成本或严格墙钟保证。网关既有 structured-output 重试以及正在进行的调用仍按其原策略；universe、模板等额外 prompt 字符不在正文额度中。多个 target 的总预算也须在 E4 实验清单登记。真实 capture 前仍须登记总 token／延迟／成本和具体内容，不能以本配置声称性能验收完成。

### 验证状态

- 首轮质量／新接线／Information 回归：55 passed，1 个既有 SyntaxWarning，22.25 秒。
- 增补空结果、失败重试、缓存时点、角色边界与 scope 冻结：94 passed，1 个既有 SyntaxWarning，94.52 秒；`tmp/pytest-basetemp/information-selection-e3b-03`。
- 空结果正式投影、缓存和 Information 回归：60 passed／1 个既有 SyntaxWarning，47.90 秒；`information-selection-e3b-final`。
- 资格闭包／scoped intake／Phase E planning 回归：60 passed，249.44 秒；`information-selection-e3b-closure`。
- 分来源候选上限与原生 DOC_DATA publisher 集成最终回归：62 passed／1 个既有 SyntaxWarning，25.22 秒；`information-selection-e3b-channel-final`。SemiAnalysis／TrendForce 各自独立候选上限，防止一个来源的新文章挤掉另一个来源；原始返回条数与限额命中也分别记录。
- 纯排序规则提取为离线复演共用函数后，新接线／原生产品集成复跑 23 passed，14.71 秒；`information-selection-e3b-rehearsal`。

## 采集收尾与同时间选择复演

- IBKR 仍为 10 次正文预算，显式登记最多 2 篇未匹配实体／主题的正文探索，计入总额度。候选保留延期状态，已缓存完整正文不占新探索额度；已补“不永久遮蔽后续未处理候选”的测试。
- IBKR source policy 显式允许 `entity_evidence_fields: [title, body]`，仍要求验证关联。正文中的注册公司、别名或合法 ticker 必须有可解析原文 span；查询过 MRVL／MU 并不让泛新闻或 Poo Mu-ming 人名通过。正式 ingestion 与 source acceptance 探测均遵守该政策。
- 采集调度从发现前开始计时，按精确发布时间及稳定身份排序；逐配置标的记录直接标题候选、请求、重试、缓存、取得正文、失败、延期及状态。此表描述 acquisition 阶段，`fetched_pending_admission` 不等于最终准入成功，后者仍看 admission run／候选记录。
- 页面解析补上显式 subtitle 标记及来源，不把任意 h2 当副标题。缺标题时依然保存已取得导语／日期字段；字段来源和解析版本进入解释指纹。公司关联定位可以引用 immutable publication 的 title／subtitle／lede 字段或正文 span，二者不混用。未保留 HTML carrier 的历史页面不能声称拥有原始 HTML 位置证据。
- 采集／新接线／质量合并回归：80 passed，31.77 秒；`information-selection-final-integrated`。IBKR 明示正文政策及 source acceptance／client 回归：62 passed，18.48 秒；`information-selection-body-policy-probe-corrected`。补探索续取后的最终回归：86 passed，27.75 秒；`information-selection-acquisition-continuation`。一次测试命令误用文件名导致未执行，已改为实际文件名重跑，未计入通过数。
- `git diff --check` 与受影响模块／脚本 compileall 通过；以上均为 uv 统一测试入口。

### 四标的离线清单

新增只读脚本 `scripts/review_information_selection.py`，通过公开 Data Products 读取两个数据库快照，统一 as_of 为 `2026-10-12T09:25:00+00:00`，七天 cutoff 为 `2026-10-05T09:25:00+00:00`。after 使用第二批隔离修订库；before 使用生产库旧元数据。没有模型、采集或生产写入，也没有绕过真实业务 qualification gate。

| 标的 | 旧规则选中 | 新规则候选 | 新规则选中 | 延期 |
|---|---:|---:|---:|---:|
| MRVL | 3 | 20 | 8 | 12 |
| MSFT | 3 | 20 | 8 | 12 |
| NVDA | 3 | 20 | 8 | 12 |
| TSM | 4 | 21 | 8 | 13 |

报告在 `tmp/information_selection_implementation_20261012/round3/selection_review.md`／`selection_review.json`，含逐标的标题、来源、选择理由、完整性、原文开头及版本。开头是原文摘录，不冒充中文模型摘要或 Information 最终采纳结果。

结果说明 publisher 通道恢复了更多可发现材料，MSFT／NVDA／TSM 的直接标题／正文材料已经不同；MRVL 仍主要为 signal_chain 材料，不能宣称已有 MRVL 直接报道。共同探索的 TrendForce Exynos 文章与 SemiAnalysis 中国 AI 安全制度文章可能无关，须允许抽取返回空结果，并靠缓存续取其他候选，不因 publisher 名称认定重要。每标的均保留 4 项未知日期缺口，不能将其回填成新消息。

## 剩余工作与继续顺序

本轮完成 E3a／E2b 的核心实现和隔离验证，并接通 E3b 核心新路径，尚不宣称整个 E0～E4 或 Phase F 验收完成。

1. E1a／E1b 实测收尾：新解析、探索、续取、明示正文准入、发现计时和 acquisition 状态已实现；实际来源 refresh 已执行，结果见下文；TrendForce 连接失败、SemiAnalysis 邮箱通道 partial，真实字段与严格墙钟验收仍有缺口。原始 HTML carrier 缺失的历史字段不伪造来源定位；生产 IBKR 正文预算仍为 10。
2. E3b／E4 收尾：新候选／抽取／目标简报接线已实现；同 as_of 无模型选择对照已产出；下一步登记多 target 的总 token／API 重试／延迟／成本预算，验证真实输出的材料性和产业链解释。纯代码测试不代表业务内容验收。
3. E4：同 as_of 离线前后选择报告、实际采集及有限真实模型内容验收；四篇缺日期继续单列缺口，缺失直接提及不强行补关联；注册遗漏与真正无材料分开记录。生产修订与新 capture 分别遵守队列所有权和具体外发授权。
4. 回到原 OpenSpec：重冻受影响的 Information 输入／解析／模板／scope，再推进相应资格、人工内容签核及部署事项；不修改原任务完成状态来代替真实证据。

目前没有需要用户裁决才能继续编码的阻塞点。真实模型尚未调用，订单与部署 owner 未变更。


## 第四批：实际采集复验及最新检查点

三个来源均经已有持久化采集队列执行。IBKR 在本机只读访问 TWS；本批确实写入生产 Data Platform 的准入文档，不能把前述隔离实验的“生产写入为零”推广到本批。模型／Agent／workflow／订单／成交副作用均为零，未执行生产路线切换。

| 来源 | 实际结果 | 解释与限制 |
|---|---|---|
| IBKR | 348 个候选；新准入 10；326 个预算延期；质量隔离 0；正文请求 10、缓存 12；125.27 秒 | 八配置标的均有直接标题候选；请求、缓存与准入计数口径不同，不能求和当作逐标的准入量 |
| TrendForce | unreachable；5 页均 TLS UNEXPECTED_EOF；新候选／准入 0 | 是连接失败，不能解释成无新闻；真实新页面解析仍未取证 |
| SemiAnalysis | partial；RSS succeeded，但无新候选；IMAP partial | 没有证据证明邮箱完整读取；不能解释成全文齐备或没有新邮件 |

记录分别为 `tmp/information_selection_implementation_20261012/round4/ibkr_ingestion_after_fixes.json`、`trendforce_ingestion.json`、`semianalysis_ingestion.json`。SemiAnalysis 的首次自动审批审查超时，按工具允许的同范围重试后运行完成；没有最终审批拒绝阻塞。

### 实测揭示并修复的缺陷

- ORCL 虽在采集配置中，但此前未注册 Oracle 公司名称与别名，导致直接候选和存量关联漏识别。已补注册并排除 Oracle of Omaha／Delphi 等人名语境；复验发现 11 个直接标题候选，取得一次新正文并复用一次缓存。之前 ORCL 零值不能证明供应商无 Oracle 材料。
- 原采集将正文预算延期错误计为 body_below_minimum／quarantined。已在质量校验前单列 deferred，整体运行标为 partial。首轮 `ibkr_ingestion.json` 的 334 项隔离是历史错误分类，保留用于归因，不作为真实质量失败统计。
- 71 篇隔离库复验后，SemiAnalysis 9 篇、TrendForce 3 篇具有可定位 Oracle 提及；MRVL 仍为零。原 as_of 的 366 行读取不变、无新增正文版本、重复修订 no_change。该结果没有回写生产存量元数据。
- 嵌套同标签副标题保留内部容器之后的文本，避免截断。邮箱重试耗尽以后报告 failed／fetch_retry_exhausted，与 RSS 成功但为空分开；这项新增诊断不能倒推旧运行具体错误。

### 刷新后四标的无模型对照

只读备份刷新后的生产库，再仅在隔离 after 库修订同一 71 篇，使用共同 as_of `2026-10-12T10:27:57.770290+00:00`。这是该时点的旧／新选择规则对照，并非刷新前后因果实验，不能将自然新增材料都归功于排序优化。

| 标的 | 旧规则选中 | 新规则候选 | 新规则选中 | 延期 |
|---|---:|---:|---:|---:|
| MRVL | 21 | 38 | 8 | 30 |
| MSFT | 17 | 34 | 8 | 26 |
| NVDA | 22 | 39 | 8 | 31 |
| TSM | 21 | 38 | 8 | 30 |

逐标的标题与原文开头见 `tmp/information_selection_implementation_20261012/round5/selection_review.md`。MRVL 已可选择 Marvell 高管卖股及正文提及报道，MSFT 有签证政策／本地 AI 材料，TSM 有营收／产能报道。**选中不等于材料性通过**：NVDA 仍有多篇相近市场综述，标题相关与发布时间优先不能单独保证信息价值；后续真实抽取须检查无重要增量、重复事实及简报去重，不能因数量增加核签。

`round5/capture_preparation/review.md` 与 `manifest.json` 已固定 8 份候选模型 payload（四目标各 2 份），交替覆盖两个 publisher，包含 SemiAnalysis partial 预览。内容是精确可见正文、标题／版本／完整性、静态 signal_chain 和既有抽取模板／schema；拟目的地 openrouter.ai、当前 research_extract 模型 deepseek/deepseek-v4-flash-0731。尚未发送。总 token／成本／严格 API 尝试及墙钟上限仍需完成登记；原网关配置下 8 次逻辑调用的理论请求上限为 128，不能以 8 次冒充真实请求封顶。完成预算后还须取得这批具体新增内容的外发授权；旧两模型批准不自动覆盖。

### 本轮最终检查

- ORCL／延期分类实测修复相关回归：67 passed，21.23 秒。
- 新字段解析、目标选择、可见性修订、邮箱 cursor、provider 故障：76 passed，16.23 秒；`information-selection-final-recovery`。
- 新增邮箱失败诊断与副标题回归：35 passed，5.43 秒；`information-selection-transport-final`。
- 上述通过数为不同范围，不累加成唯一测试数。统一使用 uv 测试入口；`git diff --check` 与受影响模块／脚本 compileall 通过。

下一检查点：先完成真实 capture 的总预算控制与具体内容授权，再核验四目标材料性、预览披露和简报／下游依赖；来源连接缺口保持单列。原 Phase F 任务、部署及业务资格没有据此勾选完成。本轮代码与文档尚未提交／推送。


## 第五批：整体验收准备与分层报告

最新状态见 [整体验收报告](INFORMATION_SOURCE_SELECTION_ACCEPTANCE.md)。TrendForce 网络恢复后实际新准入 5 篇，五份取得原始 h1 标题与 og:description 导语；SemiAnalysis 再次刷新确认邮箱 fetch_retry_exhausted、RSS 成功但为空。核心合并 102 passed、下游依赖 108 passed、证据校验 50 passed、重复与预算 29 passed，范围有交集不累加。

新增隔离预算 runner 默认 dry-run，8 请求、零重试、60 秒单请求、480 秒实验剩余时限、输出与 prompt／provider 单价限制；不改生产模型配置。固定外发输入已经可审阅，但尚未获具体新增内容授权，真实模型未执行。新增 reviewer 与正式抽取共用引用定位校验，输出分标的 Markdown，不把 schema 通过冒充人工内容核签。原 OpenSpec tasks、生产路线与部署状态未修改。


## 已批准首轮 capture 的最新状态

用户已经批准原八份固定内容外发，累计 8 次请求额度已用完：6 份 schema 可解析、1 份路由失败、1 份 schema ValidationError；内容复查仅 2 份通过引用与非空摘要检查，另外 4 份因改写引用／空摘要失败。通过定位的结果仍有业务推断问题，整体验收不通过。细节与中文分标的审阅见 [首轮真实内容审阅](INFORMATION_SOURCE_SELECTION_CAPTURE_REVIEW.md)。

已补路由参数、必填非空摘要、逐字连续引用和事实／推断区分契约。新八份复验输入与预算已准备，原文内容授权不需重复请求，但追加模型调用额度尚待批准；没有自动追加调用、生产发布或 Phase F 勾选。原文内“尚未授权／未执行”均为首轮之前的历史状态，以本节为当前状态。


## 2026-10-13 输出、选择与元数据收尾

用户授权的三项修复已实施：新target契约分离来源事实/归属与静态路径推断；禁建议词误伤修复；读取前完全重复归并、高相似更新延期及同关联层级来源轮转。8篇预算保持不变，同冻结候选集NVDA的IBD相似综述由6篇减至2篇。71篇生产元数据经来源队列修订，389条修订前时点记录不变、正文版本不变，重复全部no_change。旧节中“生产修订未执行”等状态是历史，以本节及[收尾报告](INFORMATION_SOURCE_SELECTION_QUALITY_CLOSEOUT.md)为准。

核心回归99 passed；新契约真实模型复验尚未执行。实际邮箱服务器imap.qq.com在本机代理、直连和TLS兼容性对照中均连接失败SSLEOFError，正式刷新为IMAP失败/RSS成功/partial，未将失败记作空邮箱或推进cursor。保留4篇未知日期与partial范围限制。新版8份复验载荷已准备，先前64次额度已用完，本轮没有自动扩大调用或签核资格。生产InformationBrief、受影响Routine及Phase F最终取证仍需后续执行。


2026-10-13 最终补充：新版输入用原文片段ID定位引用，模型无需重新抄写正文；来源陈述与推断分开。最新专项53项、相关业务链路135项回归通过（测试组有交集）。当前精确8份载荷及有界预算见 [收尾报告](INFORMATION_SOURCE_SELECTION_QUALITY_CLOSEOUT.md)，尚未调用模型；旧准备稿已替代。真实模型质量、正式Brief发布/缓存及受影响Routine尚待验证，邮箱TLS连接阻塞仍未解除。


## 2026-10-13 已批准的8份真实模型复验结果

本轮8次已执行，零重试。6份schema通过，1份内容契约通过且为空，0份非空材料通过，**新版内容验收未通过**。一个非法JSON、一个预期工具响应失败；其余失败涉及内部人交易误伤、来源分类粒度、未声明静态路径及预测归属。存在摘要超出所选片段支持范围的问题，片段ID不能替代内容审阅。7次有usage，供应商费用已知小计$0.004585280，1次费用未知，不将小计当全轮账单。没有生产Brief发布或订单。

分标的来源、标题、中文摘要、原文片段及下一步修复顺序见 [新版真实模型复验报告](INFORMATION_SOURCE_SELECTION_QUALITY_REPLAY_RESULTS.md)。本轮载荷仍保持原manifest；前5次账本和续跑的合并8次账本均保留，失败不重试。先前“8份尚未调用”的表述是准备阶段历史，以本节为准。需先完成引用支持范围、分句误伤、来源分类与执行器诊断回归，再安排新的有限模型复验；本轮8次额度已经用完，不自动扩大。正式Brief发布/缓存、Routine及邮箱TLS复验仍未完成。


## 补齐每标的8篇：当前最终状态

用户批准只放开核验抽样，保持选择预算8。原8次保持结果，新增24次均完成、零重试；新增24份schema全部通过，总计32份中30份schema通过。MRVL/MSFT/NVDA/TSM内容契约通过（含空）分别1/0/1/0，通过且非空全部0。每标的8篇均已尝试，不再有“选中但未测”的6篇。最终仍无采纳材料，**内容验收未通过**。

新增24次供应商返回费用$0.013073900，约299.54秒；含旧8次已知总费用$0.017659180，旧轮1次费用未知。未改变上游预算、选择预算、严格契约或生产Brief；未增加采集、订单或部署。完整漏斗、32篇来源/标题/未采纳摘要和逐篇拒绝原因见 [完整8篇核验结果](INFORMATION_SOURCE_SELECTION_FULL_SELECTED_RESULTS.md)。先前每标的“只测2篇”等表述为历史，以此状态为准。增加核验覆盖未带来有效采纳，当前不支持直接扩大选择预算。


## 当前裁决：材料准入与下游投资采纳分开

用户明确：可读取、来源/版本可追溯、去重、时间/完整性准确、实体关联（允许观点）；partial不足以支持完整信息可过滤。公告、预测、自述、透露或意见不因source_kind/said/according to被拒绝；不要求采集阶段证明静态产业链因果路径。下游分析师决定采用和使用。

当前实现删除来源关键词类别硬拒绝、强制静态路径和impact_path=direct的准入门槛；保留原文位置/版本、角色scope、可见范围与非空输出。观点关联作为上下文，不冒充已经证实的因果链。partial仅为短标题/营销预告时拒绝（含无付费墙标记的stored partial）。没有增加模型调用或生产写入。

对原32份旧模型输出离线重审：MRVL/MSFT/NVDA/TSM基础结构及定位通过（含空）为8/8/6/7；包含本ticker候选摘要的材料为6/6/5/7。这不是投资核签或新提示词真实验证，旧输出的投资推断没有因此证明成立。剩余两次响应失败和一次无效片段ID不代表原文不可用。分标的来源、标题、候选摘要、通过依据见 [材料准入重审](INFORMATION_SOURCE_SELECTION_MATERIAL_POLICY_REVIEW.md)。旧“0篇采纳”严格契约结果为历史诊断，不能继续作为当前材料可用性结论。


验证记录：uv入口核心范围首轮94项通过；扩展范围133项通过、1项元数据重复修订失败。原因是修订器硬编码旧完整性策略版本，已改为保留受支持策略的原始baseline。修复后visibility_revisions与output_quality共41项全部通过（102秒），覆盖原失败、短partial过滤、来源类别/关联元数据、采购与交易指令边界。编译和diff检查通过。测试组有交集，不累加为独立用例总数。未再次执行生产元数据修订。
