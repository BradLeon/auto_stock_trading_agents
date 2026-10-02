# FactSet 跨期基线核查

## 2026-09-29 最终语义链路验收（仅隔离环境）

两份官方原件继续使用本文件开头所列 SHA-256，不改写历史资产。两期均使用同一个 `run_semantic` 入口、同一 `factset-semantic-v2` 策略及范围版本 `factset-investment-focus-v1`；当前完整策略 hash 为 `81409077de3eca10581ebbfbc59a03f0bde0c83f102866b80301879977d2c7c1`。生产提取没有读取 golden、按日期切换页码或特设数值。独立验收清单及逐格原文标注仅在 Git 忽略的本地 `tests/fixtures/factset_earnings_insight/acceptance/`，不作为生产批准。

| 日期版 | Index P0 | 科技 P0 | 测试审批后核心 | 全报告 |
|---|---:|---:|---|---|
| 2026-08-28 | 43/43 适用格，与独立原文一致 | 35/35，12/12 分组 | passed | partial |
| 2026-09-18 | 43/43 适用格，与独立原文一致 | 35/35，12/12 分组 | passed | partial |

双报告同逻辑发布/产品断言及分辨率扰动 3 项通过，约 477 秒；全部已发布 P0 值零错误、零缺格、零额外适用格。9 月未披露报道比例被独立登记为 `not_disclosed`，不是零；P1 的消费电子明确原文在两期均未定位，不据科技总体数字推断。P2 非科技行业逐格难点继续 `deferred`，其余行业并未完整发布；历史 8 月 231 格旧 `sector_core` 保留且与当前科技视图分开，770 格全行业源标注保留为回归材料，未伪称全报告 complete。新报告若尚未登记独立清单，系统只存原件并阻断数值核心发布。

非核心延期/缺口清单（8、9 月分别保留，不把缺口记成通过）：

| 范围 | 对应内容 | 当前处置 |
|---|---|---|
| P1 | 原文明示 AI 硬件/半导体文字 | 仅带来源位置的有界事实提及；无子行业数值发布 |
| P1 | 消费电子专门段落 | 两份 FactSet 报告内未定位，记 `not_located`；由其他数据源另行覆盖 |
| P2 | 除 GICS_45 外的十个 GICS 行业：scorecard、surprise、Q2/Q3/CY 增长、margin、Q3/混合财年 guidance、地域收入、forward P/E、评级及目标价 | 原图及既有全行业标注留存；有通用候选的可复用，但疑难单元格不逐一专项修复/批准，本次不发布完整十一行业视图 |
| 后续月份 | 尚未见版式/未登记报告组清单 | 先只收原件，独立登记适用组及原文核验后再审阅；不能继承两份样本的页码/行序 |

误发布防护证据：真实在线抓取无审批时 Index/全部科技组是 shadow，产品读回无该期数值；测试审批后仅精确绑定的 43 个 Index 与 12 个科技组/35 格成为 platform，全行业 `sector_core` 仍 shadow、`report_coverage=partial`。旧十一行业消费者不得把科技通过等同全行业完整；重新审批条件中的 PDF、候选、策略、范围版本任一变化均使旧审批失效。

官方 9 月 PDF 经临时受管队列重新抓取，HTTP 200、SHA-256 `f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08`，采集 task `4897b16e-aad2-552f-b14d-ecfb57a0bcf9` 为 partial：文档/候选完成，未获审阅不发布。仅在 `/private/tmp/factset-managed.hWCRmE/` 隔离库用测试身份审批，再从同一受管 CLI/队列重放本地同 hash 原件，task `2ea540cb-bffa-5543-a489-08e2b20667f1` 为 succeeded；Index release `6c037675693a655d36cf770f`，12 个科技组 release 均为 platform。产品读回 `SP500:2026-09-18:research_article@0c432377ecc3bcd2`，Index 为 platform、科技 12/12、核心 passed、全报告 partial。初次 sandbox 网络拒绝的 retry_wait task 与获准网络重取是两次独立尝试，不冒称同一 task 自动恢复；阶段崩溃恢复另由 3.5 的故障注入验证。

受控周调度测试证明语义入口需 `ATS_FACTSET_SCHEDULE_SEMANTIC=1` 才选用、同周同策略去重、取消该变量回到旧入口；未等待或冒称自然周六运行。版本 pin/unpin、来源与 Macro 消费者开关回退在隔离库验证，两版 release 不删除，交易链未调用。日常操作、未知下一期的清单核验与事故回退见 [运行手册](FACTSET_SEMANTIC_RUNBOOK.md)。已将本专项证据明确映射到父 change 的 FactSet 数据源及消费者读回验收，但没有自动勾选父 change 的复合任务、推送 Git 或默认切换生产调度。

Change：generalize-factset-report-ingestion。日期：2026-09-26。
本文件仅记录原件和实施前差异，不是通用解析器通过证明。

## 原件与历史资产

| 报告 | PDF SHA-256 | 页数 | 已核实资产 |
|---|---|---|---|
| 2026-08-28 | ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381 | 37 | artifact 89b7f3809223462f63e47dd5 |
| 2026-09-18 | f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08 | 33 | artifact 9a4bad771e53bd36187e4db4 |

两份原件均重新计算hash并核对数据库，未改写。8月原件首次入库时间为2026-09-03T09:14:18.193371+00:00；历史行业正式release为 ed1e8fae378f3f24892c566d，document version为 SP500:2026-08-28:research_article@111e4bf657fee2b9，release known_at为2026-09-04T00:26:53.707880+00:00，包含231个observation引用。原文首次入库与后续发布不是同一时刻，不改写历史时间。

9月原件历史官方最终URL为 `https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_091826.pdf`，首次采集2026-09-25T13:39:18.714950+00:00；本轮仅核实本地原件，未声称重新下载。

## 复用清单

- sources/factset_earnings_insight.py：复用 fetch_report、inspect_pdf、原始PDF及逐页文本/图像提取。
- sources/factset_earnings_text.py：复用数值候选、期间、证据契约及既有正文提取，补跨期回归。
- sources/factset_earnings_charts.py：复用GICS归一、ChartCell/ChartTable及校验契约；日期专用坐标、行业顺序和像素数值推断不能作为新生产策略。
- pipelines/factset_earnings_insight.py：复用唯一受管编排及文档/观测/血缘存储，后续改分组发布与审阅。
- products/earnings_insight.py、结构化/文档stores：保留历史身份和vintage，后续增量改善组状态及历史可用性。

## 原件审阅发现的验收范围冲突

采用pypdf抽出未经修改的嵌入图像，直接视觉核查（本机未安装Poppler，未以OCR输出作为独立答案）。本地辅助脚本为Git忽略的 tests/factset_visual_inventory_local.py；图像位于临时review目录，不提交授权原文。

1. 8月PDF第18页第三张嵌入图像标题为行业Revenue Surprise，提供11个GICS行业及SP500总体。旧082826.yaml却把revenue_surprise列标为not_applicable。
2. 8月PDF第21页第三张嵌入图像标题为Revenue Growth (Y/Y): Q2 2026，包含11个行业、SP500、Today和30-Jun列。旧基线把revenue_growth标为not_applicable。
3. 因此仅这两项就至少遗漏22个行业当前期数值；231是历史已验子集，不是原文八组完整覆盖。未据此宣布253就是最终完整数量，尚需全面审阅其他页和期间。
4. 两份原文还包含后续季度/年度的增长与指引图表。需要明确本专项是现有八组当前主目标期间加遗漏列，还是全部披露期间，避免将“所有适用单元格”与“231格历史基线”混为一谈。
5. 直接比较8月17页与9月13页Scorecard可见行业排序与数值均变化，不能靠页码偏移保留固定行业位置。

任务1.1已核实；1.3/1.4及后续完整验收未通过。用户随后确认方案1：八组覆盖全部披露目标期间，231为历史子集；范围暂停已解除。不能读取错误golden指导生产提取，不冒充操作者批准，不静默减少原文覆盖。

## 9月官方原件受管重取

Task `0a5e45e5-0751-5f3c-baf5-0d9d606043f3` 使用新增受限query scope指定上述官方日期版URL，HTTP 200；hash与已有原件完全一致，文档为no_change，保留首次known_at和版本。CLI范围校验14项通过，拒绝非官方地址、非法日期、查询参数及未知选项。任务1.2完成。

该次仍使用旧行业解码器，因此虽然旧管道总体返回succeeded，sector_decoder实际failed，仅识别2张局部表；不能据此认定通用解析或行业发布完成。新分组状态修复仍属3.4任务。

## 通用解析实施记录（未签收）

已实现但尚未接入生产默认入口的模块：

- `factset_report_layout.py`：从实际OCR词框拼接标题，发现八组图表，保留动态页码、图像hash及bbox；区分季度、季度范围、CY、FY和混合财年。无法确定期间保持待审。
- `factset_chart_grid.py`：从原图印刷网格测量单元格边界，不预设页码、行业顺序或表格坐标；网格缺损不猜测。仅读取印刷文本，不用柱高/颜色计算值。
- `factset_chart_values.py`：由注册表别名识别行业表头和行业次序，保留比较行、原始token及标签/数值区域；横向surprise按同一视觉行匹配行业与百分比，支持负数；所有结果仍为待审候选。

策略配置位于 `structured.yaml` 的 `sp500_earnings_insight.extraction_policy`，包括八组标题、单位/列、行业别名、预算和禁止单一OCR置信度放行的规则。通用候选尚未完成指标语义映射、估计状态判定、审阅及分组发布；不能把底层读取模块存在当作任务2.1—2.9全部完成。

本地两份PDF实跑发现：原文Q2/Q3、CY 2026/CY 2027增长图均可按同一策略发现。FY指引含不同发行人的FY 2026/2027合并截面，不能拆成两套独立年度数值。已有图表发现证据为本地忽略文件，不含生产发布批准。

### 独立原图对照与OCR问题

独立查看未修改的原始嵌入图像，手工记录15张增长图的165个当前行业数值，另记录8月营收surprise的11个值；该局部标注不构成全部八组golden。文件为Git忽略的 `tests/fixtures/factset_earnings_insight/acceptance/cross-report-visual-partial.yaml`，明确标记partial且非操作者生产批准。生产模块不读取该文件。

RGB局部放大4倍、PSM 6对上述165格的初次对照为161格一致、4格不一致：

| 报告 / 页 / 图 | 行业 / 当前增长列 | 原文 | OCR结果 |
|---|---|---|---|
| 2026-08-28 / 26 / 3 | Financials / revenue | 7.4% | 74% |
| 2026-09-18 / 22 / 3 | Financials / revenue | 7.4% | 74% |
| 2026-09-18 / 24 / 2 | Consumer Staples / EPS | 7.4% | 74% |
| 2026-09-18 / 25 / 2 | Health Care / EPS | 22.1% | `22. 1%`（严格数值解析拒绝） |

已测试旧数值裁剪器、PSM 6/7、不同放大/插值与灰度阈值。某些策略能修复这4格，却在其他单元格新增字符误读（如8→5、7→1）；未采用按日期/坐标/已知正确答案选择策略的方式。保留RGB策略与原始错误，不做小数点补写、金标准回填或按数值范围猜测纠正。原文可读不等于现有OCR能稳定自动读取。

已询问用户是否授权OpenRouter视觉辅助解析（发送必要图表裁剪及可能产生API费用）；尚未进行该外发，也未配置其为默认依赖。无论选择何种解析方式，独立原文核验及分组发布门槛不降低。

本次没有切换生产图表解码器、没有批准或发布新候选、没有推送Git，也没有签收父change。1.3/1.4、所有解析/分组发布及双报告全量验收仍未完成。

代码回归：使用uv运行原有FactSet测试与新增入口、发现、网格、候选测试，共88项通过；新增模块Ruff E9/F、OpenSpec strict、git diff --check通过。本地测试和golden已核实被Git忽略。这些检查证明受测行为，不替代真实PDF全量数值验收。

## 2026-09-27 授权视觉辅助与增长组盲测

用户已明确允许OpenRouter视觉辅助解析，解除此前外发授权阻塞。新增`factset_vision.py`，读取.env中的`OPENROUTER_API_KEY`；仅在OpenAI base URL明确为OpenRouter时兼容其key。模型、授权记录和预算配置在既有structured.yaml，不改变其他Agent路由。

本轮使用`openai/gpt-5.6-terra`，调用前通过OpenRouter模型目录核实image输入支持。固定HTTPS端点、禁止重定向，请求`data_collection=deny`及ZDR；没有降级隐私策略。协议参考[官方图像输入文档](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)及[官方路由策略](https://github.com/OpenRouterTeam/docs/blob/main/guides/routing/provider-selection.mdx)。这些是请求约束，不是对第三方服务的独立隐私审计。

- 首轮4个误读单元格：视觉转录均与此前独立原文标注一致，费用$0.00142362；OCR原值仍保留并标冲突，没有静默替换发布。
- 扩大盲测：从两份报告16张增长图的动态网格生成352个必要裁剪（176个当前值、176个行业标签），分30次调用。请求不包含OCR答案、golden或账户信息；golden只在全部调用之后由独立比较脚本读取。
- 结果：176个数值、176个标签全部对齐，覆盖Q2/Q3、CY 2026/CY 2027、EPS/营收、正负数和不同行业排序。两份报告策略hash均为`1aeeedd772c1685e6ab16b23de54ec5dc238e91ccc3f65f56471ab20f91eda5e`，扩大盲测费用$0.13596660；连同首轮共$0.13739022。
- 本地证据：`tests/fixtures/factset_earnings_insight/vision-growth/20260926T222034/`，包含逐批原始响应、来源crop hash/bbox、模型、usage/cost和`comparison.json`。目录使用UTC时间，台湾当地日期为9月27日。原始报告、裁剪、测试与标注继续被Git忽略。
- 测试：相关uv定向测试共106项通过，包括未知/重复/缺失ID、输出截断、预算耗尽、并发预算、隐私策略、未授权禁用、密钥不进入失败日志及证据绑定。OpenSpec strict通过。

本轮只验证增长组当前数值及行业标签转录。它不构成全部八组golden，不证明期间/估计状态全量映射、比较列语义、生产发布链或重启恢复已完成。所有视觉结果仍是`pending_review`、`approved=false`；没有接入默认生产解码器或发布新行业观测。完整任务进度仍为2/29，后续按原计划继续，不再以视觉调用未获授权为阻塞。

## 2026-09-27 续作：契约、审阅、发布隔离及独立标注

进度更新为3/29，完成2.1契约与集中规则。其余复合任务保持未完成，不以局部模块或mock回归替代真实双报告验收。

- 新增`factset_contracts.py`：完整组身份区分期间基础/估计状态，候选hash绑定原始证据和版本；检验11行业完整性、列单位、原始标签、数值token及合计关系。人工修正保留原候选并绑定修正证据。
- 新增`FactSetReviews`及`ats data factset-review-packages`、`factset-review`、`factset-review-correct`。操作需显式确认、操作者和证据引用；审批只写审计，不直接发布。新组发布只接受精确绑定的review ID；旧默认编排的布尔审批仍待退役。
- 新增`FactSetGroupPipeline`，在现有artifact/candidate/observation/evidence/release上按组发布。先写观测再提交manifest时若崩溃，通用结构化查询不会暴露未提交的组；重试复用观测ID，不将后来manifest回填为较早可见时间。此故障点已测试，但全阶段恢复仍未签收。
- 新增`DataProducts.earnings_insight_groups`，要求明确报告版本、日期及期望组清单。当前报告与历史值分开返回，空清单不冒充完整；snapshot历史保留自身日期。shadow或已撤回审批的组不进入产品数值，历史as-of保留当时状态。旧sector_core历史适配尚未接入。
- 独立读取原始嵌入图像，新增估值、评级/目标价、地域收入、scorecard、margin、surprise和指引标注。加上先前增长及8月营收surprise，共每期385格、双报告770格。表中出现99%/101%合计时按原文保留，不修改单值凑整。标注完整性测试核验11行业和列数；单元格坐标、期间状态复核及自动解析对账仍待完成，`production_review_approval=false`。
- 新增增长当前列/比较列、其他表格行语义映射，未知行不按旧位置猜测。真实图中的部分OCR图例和缩写比较日期仍需解析/视觉证据整合，因此不宣称任务2.4—2.9完成。

本地验证：uv定向139项通过，包含审批绑定/过期/驳回/修正/重启、CLI确认、发布前崩溃与重试、当前/历史隔离、as-of和原有FactSet回归；OpenSpec strict通过。新增标注位于Git忽略的`tests/fixtures/factset_earnings_insight/acceptance/`。没有新增外部模型调用费用、生产发布、生产默认切换或Git推送。

## 2026-09-27 两态规则及科技增长核心链路

用户确认仅actual/estimated：新组在对应原文明示实际业绩时为actual，否则estimated（含blended/未标明）。原始状态措辞作证据保留，actual证据进入候选hash。旧不可变包仍可读，旧hash不改写；新策略不能复用旧审批。Index旧文本契约及默认周调度尚未迁移，不宣称整个来源已全面切换两态。

- 注册表策略版本`factset-semantic-v2`，本次完整回放策略hash：`d7bf66aeffa43e5742944418279a7c422191abe254d64df5d79e4efe521b369c`。
- 先缓存回放，随后重新从两份真实PDF读取并发现全部82/78个嵌入图像；同一本地OCR、标签/网格、增长组装代码处理两份报告，无golden输入、日期/页码分支或新视觉调用。
- 鲜跑发现并修正了公司Top Contributors榜单误分类问题；登记语义排除规则，保留原文，不将公司榜单当行业横截面。科技范围不再继承其他行业缺格错误，全行业声明仍须11行业，共享图例歧义仍阻断。
- 两报告分别4个期间组（Q2/Q3/CY2026/CY2027），各含EPS与营收，共16格与独立原图golden全部一致；原始单元格标签/token/bbox及比较图例保留。短比较日期使用显式注册的最近不晚于报告日规则，保留原文，并测试跨年/非法日期；不影响图表目标期间。
- 隔离SQLite链路：待审无可发布观测；仅本地测试审批后8组/16格可从产品读回；重复执行no_change且观测ID不变。测试审批不是用户生产批准。生产库、调度和交易路径未变。
- 鲜跑证据：`/private/tmp/factset-core-growth-2rucqtkz/2026-08-28.json`、`2026-09-18.json`、`verification-summary.json`；隔离库`/private/tmp/factset-core-chain-w4ln3bny/isolated.sqlite`。初次发现分类失败的证据另保留于`/private/tmp/factset-core-growth-gsfool8u/`，没有覆盖。
- 复现入口（均为Git忽略的本地测试）：`tests/factset_core_growth_fresh_local.py`先生成源候选，再以`tests/factset_core_growth_verify_local.py <evidence_directory>`独立比较并测试发布链。单元/缓存回归164项通过，新增代码Ruff E9/F、OpenSpec严格校验、diff空白检查通过。

完成任务2.4，当前5/30。未完成其他七组、Index范围基线、全报告聚合、默认入口切换及受管队列端到端验收；未将本轮隔离增长链路扩大宣称为整个change完成。测试、golden及授权原文仍不提交Git。
# 2026-09-28 Index 语义入口续验（未批准发布）

两份官方原件新增独立的 Index 适用性清单，保存在本地忽略目录
`tests/fixtures/factset_earnings_insight/acceptance/index-core-inventory.yaml`。
它先于完整图表抽值冻结期间与来源，不把解析成功的子集作为分母；
当时还不是完整数值 golden 或生产审阅批准。8 月 Q3 bottom-up EPS 趋势图无可靠的
精确印刷端点值；后续独立原图复核发现同报告季度 EPS 柱图明确标出 $89.68，
因此撤销此前 unreadable 判断。9 月对应柱图明确给出 Q3 bottom-up EPS $90.03，
新文本入口从该句提取，并将报告标题的 Q3 2026 作为唯一期间锚点。

新 Index 文本入口扫描整份文档、以 Key Metrics/Earnings Scorecard 锚点识别报告，
不依赖前 10/16 页。两份 PDF 将前 20 页移到末尾后，候选指标保持一致。
图表聚合读取使用印刷的 S&P 500 标签、行标题和数值词框；两期各发现 22 个
scorecard、增长、margin、guidance share 候选，并能独立读出两期地域暴露的
59%/41% 印刷标签，以及 9 月 Q2 EPS/revenue surprise 的 25.9%/3.1%。
行业图表的百分数在 Index 口径规范为比例，原印刷 token 与单位仍保留。
bottom-up CY 年度图的端点虽然印有数字，OCR 与年份关联仍未可靠验收，禁止按
曲线高低或固定版式猜测。此阶段所有新 Index release 强制 shadow，旧文本门禁
通过也不能自动发布；完整 golden、组审阅、核心门禁和调度切流仍未签收。

## 2026-09-28 语义阶段故障隔离续验

`run_semantic` 对同一 PDF 只获取并投影一次，保存图表发现 manifest、Index
候选/release，以及按声明实体范围分开的行业组包/release。科技单行业和 11 行业
完整横截面可以同时声明，范围身份及完整性规则各自独立；未批准组仍为 shadow。
发现阶段故障以独立失败 manifest 保留，修复后重试不会覆盖失败记录；Index
解析故障与某一行业范围解析故障分别留痕，不清空其他阶段。9 月原件隔离 SQLite
回放已验证缺组、科技/全行业并行声明、发现故障—恢复、Index/行业故障留痕。
这些故障注入测试不表示核心 Index 已通过独立 golden，也不是生产审批。

本轮完成 OpenSpec Task 3.1/3.2，进度 14/30；本地范围、证据、审阅和
发布定向测试 39 项通过，严格 OpenSpec 校验通过。当前新语义入口未切到
生产默认周调度，`core_acceptance` 保持 blocked，
`report_coverage` 为 partial。Index 已登记适用字段的逐格独立对照、审阅、
范围发布兼容摘要和消费者读回仍在 Task 1.3/1.4、2.10、3.2–5.6 范围内。
旧日期专用解码路径仅保留历史回放，不在新入口被调用。
语义 Index release 身份现包含策略 hash；同一 PDF 在策略修订后生成独立
release，不覆盖上一版。相同输入的文档、发现、Index 和科技组 release ID
重放一致；发现故障先保留失败 manifest，恢复成功后两版均可查。
这只覆盖已测故障点，Task 3.5 的所有节点崩溃、as-of 与审阅组合仍未签收。

P1 原文检查：8 月正文第 12–13 页、9 月第 9–10 页明确讨论半导体及科技硬件
行业的盈利/营收增长贡献；9 月第 3 页讨论财报电话会中 AI 的提及。仅保留这些
带页码的原文事实，不以 GICS_45 汇总推算半导体、AI 硬件或消费电子单独指标。
这两份 PDF 的正文搜索未找到 consumer electronics 明示段落；记为该主题在此
来源未定位，不等于其他来源无消费电子数据。

另外独立读取两份原件第一页 Key Metrics，建立忽略目录中的
`index-key-metrics-partial.yaml`：8 月 10 个、9 月 7 个原文数值与原句锚点，
并将 9 月仅披露 3 家公司报告业绩而未给覆盖比例登记为
`not_disclosed_as_ratio`。17 个局部值的来源原句与新文本候选逐项对账通过。
此标注仅覆盖第一页关键指标，不能替代 Index 全部适用指标及图表期间的
独立 golden；Task 1.3/1.4/2.10 继续保持未完成。

## 2026-09-28 P0 双报告独立基线冻结

沿用两份已验 SHA-256 的原始 PDF，独立复核印刷图表数字、标题、期间、实体
和正文数值。忽略目录 `tests/fixtures/factset_earnings_insight/acceptance/`
中的 `index-core-golden.yaml` 记录了 8 月、9 月各 43 个适用的 S&P 500
指标×期间数值，以及页码、图像序号/原文锚点；`index-core-inventory.yaml`
记录适用性，`dual-report-fixtures.yaml` 将两份 PDF hash、Index/科技行业
原文标注和基线绑定。两期合计 86 格，8 月覆盖 33 个登记指标，9 月覆盖
32 个；9 月报道公司数为 3，但未给对应报道比例，故
`earnings.reporting.coverage` 明确为 `not_disclosed`，不是 0 或解析失败。
目前没有 P0 `unreadable`；P1/P2 按已确认的停止规则记录，不影响 P0 清单。

纠正此前 8 月 Q3 bottom-up EPS `unreadable` 的判断：趋势线确无精确端点
标签，但同报告第 32 页季度 EPS 柱图明确标为 $89.68；9 月第 28 页对应
柱图标为 $90.03。两者均只从印刷数字读取。FY 2026/2027 guidance
计数图只印出 11 个行业，没有 S&P 500 合计计数；因此 Index 合计计数
明确记为 `not_disclosed_index_aggregate`，不通过人工求和制造报告未印出的
Index 数值。对应百分比图有 S&P 500 印刷行，仍属于核心验收。
季度与混合财年口径不合并。印刷整数百分比
受四舍五入影响，允许组成合计为 99% 或 101%，不擅自归一为 100%。

本节是可版本化脱敏摘要，不含授权原文 PDF 或视觉裁剪；本地 golden
不是生产审阅或发布批准，也不得作为解析器输入。后续 Task 2.10、3.3—3.5
必须用此独立基线回归并另行完成候选审阅、发布与恢复验证。

## 2026-09-28 Index 全期间回归（Task 2.10）

两份 PDF 经相同的语义文本及图表入口提取，分别与 43 格独立 P0 Index
基线对照：86 格全部数值、指标和期间一致，零缺失、零多余；历史日期专用
图表解码器未参与。新图表适配从季度/年度 bottom-up EPS 柱图的印刷期间
刻度与数字标签配对，不按页码、报告日期 hash 或柱高估值。年度 bottom-up
EPS 在注册表中补充 `calendar_year` 合法期间。报告未印出的 FY S&P 500
指引总计计数不伪造，Index 百分比仍验收。

语义 Index 质量摘要保留有界原文主题证据（AI、半导体、消费电子、硬件），
每条有页码和字符范围，状态仅为 `source_mention_not_inference`。
两份报告的半导体原文分别位于第 12/9 页；9 月 AI 电话会讨论在第 3 页，
未找到消费电子明示段落时不生成相关证据。科技行业总量不被换算为
AI 硬件或消费电子数据。该证据不构成生产发布审批。

## 2026-09-29 核心审阅、发布与恢复规则

Index 审阅包由独立声明的适用格、未披露格及来源引用组成；候选集合
hash、PDF/document version、策略与提取器版本、注册指标清单、指标组及
实体范围/版本一并绑定。仅有解析器候选或裸布尔标注不能批准。修改上述
任一身份、出现未登记格或漏报注册指标时，原批准不再适用；同一审阅包
后续驳回也会使旧批准在相应 as-of 时点后失效。CLI 提供审阅包登记、
批准/驳回及查询入口，但测试中的批准仅限隔离数据库，不是生产人工签核。

行业组沿用可追溯的人工纠正：纠正格要求原文证据、操作者与说明，
新候选状态为 `manual_reviewed`，生成新包 hash，并须重新审核；不得
把人工修正冒充自动解析成功。Index 当前不支持直接修改已发布候选值；
若发现 Index 原文/解析不符，应驳回审阅包、修订提取器并重放，再以
新候选集合重新审阅。

发布摘要把 `core_acceptance` 与 `report_coverage` 分开：Index 或必需
科技组缺失、失败、未审批均阻断核心；Index 与科技组均通过而其他
行业未发布时，核心可为 `passed`，全报告仍为 `partial`。旧
`sector_core` 兼容分区此时保持 shadow/partial，不能代表全行业完整。
全行业完成还要求已发现的核心图表期间均被明确声明。Index release
修订身份包含候选与审核状态；相同输入重放沿用已有 observation，
新策略/提取器或审阅结论生成新 manifest，历史不覆盖。消费者按
`known_at`/as-of 选择版本并在读取时复核现行批准。

本轮使用 `uv run --offline --no-sync` 在隔离 SQLite 上复核：
双 PDF Index 金样本、未审批/批准/驳回影子与发布切换、Index 加 12 个
科技分组的完整核心发布，3 项原件慢速测试全部通过（806.92 秒）；
审阅与行业组产品 14 项、行业组管道 4 项、Index 审核/崩溃重放/
提取器修订/候选 ID 稳定 4 项均通过，另有注册指标漏报反例通过。
集成回放中的第二次 Index 写入为 0 个新 observation，已存候选被判为
unchanged。`ruff --select F` 对本次 FactSet 触及文件通过，
`openspec validate generalize-factset-report-ingestion --strict` 通过。
这完成 Task 3.3—3.5 的隔离验收，不等于 Task 4 消费者全面兼容、
Task 5 受控真实入口/调度切流或生产数据发布授权。
