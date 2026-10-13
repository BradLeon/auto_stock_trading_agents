# Information 原2篇抽样过滤漏斗（历史）

**当前准入职责已按用户裁决修正，最新结果见 [材料准入重审](INFORMATION_SOURCE_SELECTION_MATERIAL_POLICY_REVIEW.md)。下文保留旧契约历史，不作为当前可用信息计数。**

**当前每标的8篇均已核验，最新漏斗与采纳结果见 [完整8篇核验报告](INFORMATION_SOURCE_SELECTION_FULL_SELECTED_RESULTS.md)。以下保留补测前历史。**

冻结时点：2026-10-12T10:27:57.770290+00:00；窗口起点：2026-10-05T10:27:57.770290+00:00。使用同一已准入快照，不重新采集或调用模型。

采纳口径：可见内容须足以完整支持结论，目标关联有依据，无交易建议且具有投资研究材料性；partial标签不构成补写隐藏内容的理由。以下保留现行严格拒绝结果，不放宽检查。现有程序不保证所有语义正确，最终内容价值仍须审阅。

**最终采纳四标的均为0篇。每标的选中8篇，但新版只核验2篇，另外6篇尚未测试，不能称为被拒绝。原64次旧契约诊断通过项不自动升级为新版最终采纳。**

|过滤阶段（篇/文档版本）|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|目标＋产业链通道原始读取|100|86|116|114|
|SemiAnalysis通道原始读取|22|22|22|22|
|TrendForce通道原始读取|49|49|49|49|
|合并去除同版本重叠|128|120|136|134|
|发布时间明确|124|116|132|130|
|七天窗口内|38|34|39|38|
|可用完整性／可见范围|38|34|39|38|
|目标／产业链关联或有限publisher探索候选|38|34|39|38|
|去重与覆盖调度后预算选中|8|8|8|8|
|实际模型尝试（诊断抽样）|2|2|2|2|
|schema通过|2|2|1|1|
|来源分类、引用路径、禁建议等内容契约通过|1|0|0|0|
|上述通过且非空|0|0|0|0|
|投资价值／材料性审阅|未到达|未到达|未到达|未到达|
|最终采纳|0|0|0|0|

通道之间有重叠，前三行不能直接相加。原始读取已经是Data Platform准入后的文档，不是供应商全量新闻发现数；上游采集属于另一轮，不能混成同一批漏斗。窗口外排除与未知日期分别统计。预算调度同时处理重复与覆盖，未选中不是内容拒绝。

|退出／未处理原因|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|日期未知|4|4|4|4|
|窗口外|86|82|93|92|
|完整性不可用|0|0|0|0|
|关联／可见正文不满足候选条件|0|0|0|0|
|候选预算未选中|30|26|31|30|
|选中但新版尚未测试|6|6|6|6|
|内容契约通过但为空|1|0|0|0|

## MRVL

来源分布（合并去重后）：{'ibkr_news': 22, 'trendforce_news': 49, 'semianalysis': 22, 'defeatbeta_earnings_transcript': 32, 'sec_edgar_filing_body': 3}；预算选中来源：{'ibkr_news': 4, 'trendforce_news': 3, 'semianalysis': 1}。

**最终采纳：无。数据来源、标题、摘要、通过原因均无最终采纳记录。**

### 已测试材料及未采纳原因

- 来源：ibkr_news；标题：VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL。

  拒绝原因：信息简报不得包含交易建议：命中中文建议词 '卖出'。

  未采纳模型摘要：公司多位高管（包括总裁、董事长、CFO、VP）在2026年4月至10月期间持续通过10b5-1计划及直接交易卖出股票，最新一笔为VP Scarpulla于10/8/26以273.59美元卖出1,100股。这属于历史内幕交易事实，反映内部人持续减持，但多为计划性卖出，对基本面影响有限。

- 来源：trendforce_news；标题：[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround。

  结果：合法空输出；没有目标洞察，因此不构成采纳。

### 预算选中但尚未测试

- ibkr_news：TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com
- ibkr_news：Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk
- trendforce_news：[News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding
- ibkr_news：The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com
- trendforce_news：[News] nvidias nvhbm reshapes memory makers base die strategies as micron samsung turn to external foundries
- semianalysis：Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

这些材料只有原文预览与选择依据，没有新版合格摘要，不编造采纳原因。


## MSFT

来源分布（合并去重后）：{'ibkr_news': 18, 'trendforce_news': 49, 'semianalysis': 22, 'defeatbeta_earnings_transcript': 28, 'sec_edgar_filing_body': 3}；预算选中来源：{'ibkr_news': 3, 'trendforce_news': 4, 'semianalysis': 1}。

**最终采纳：无。数据来源、标题、摘要、通过原因均无最终采纳记录。**

### 已测试材料及未采纳原因

- 来源：ibkr_news；标题：Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update。

  拒绝原因：Information attributed statement cannot be promoted to reported fact: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f864502-5b54e9354f7a:research_article。

  未采纳模型摘要：文章报道美国副总统万斯宣布将微软等公司从允许H-1B签证持有者申请绿卡的项目中暂停，指控微软滥用签证系统。这对微软构成直接监管/运营风险，可能影响其招聘外国高技能人才的能力，但文章同时指出政府表示不想伤害公司，仅想传递信号。

  未采纳模型摘要：文章引用微软发言人回应称公司计划向政府提供更多信息，并称其提交的约6000份H-1B申请中80%是为现有员工提交的。这是微软对监管指控的自我辩护，属于公司自述，不能视为已证实的事实。

  未采纳模型摘要：文章报道万斯表示政府不想伤害微软，但将拒绝其申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。这暗示监管行动可能有限度，但持续施压仍可能影响微软的人才获取和运营成本。

- 来源：semianalysis；标题：Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime。

  拒绝原因：Information attributed statement cannot be promoted to reported fact: SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article。

  未采纳模型摘要：中国开源模型的价格战正在挤压Anthropic等前沿实验室的经济性，迫使它们加速发布新模型。对微软而言，其投资的OpenAI/Anthropic生态面临来自中国低成本开源模型的竞争压力，可能削弱微软AI产品（如Copilot、Azure OpenAI服务）的定价能力和差异化优势。

### 预算选中但尚未测试

- trendforce_news：[News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise
- ibkr_news：The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com
- trendforce_news：[News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead
- ibkr_news：Four Years In, $40 Trillion Up: Why This AI-Fueled Bull Market Has Room to Run -- Barrons.com
- trendforce_news：[News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding
- trendforce_news：[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

这些材料只有原文预览与选择依据，没有新版合格摘要，不编造采纳原因。


## NVDA

来源分布（合并去重后）：{'ibkr_news': 23, 'trendforce_news': 49, 'defeatbeta_earnings_transcript': 39, 'semianalysis': 22, 'sec_edgar_filing_body': 3}；预算选中来源：{'ibkr_news': 4, 'trendforce_news': 3, 'semianalysis': 1}。

**最终采纳：无。数据来源、标题、摘要、通过原因均无最终采纳记录。**

### 已测试材料及未采纳原因

- 来源：ibkr_news；标题：Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD。

  结果：输出失败（ValidationError），不重试、不采纳。

- 来源：trendforce_news；标题：[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround。

  拒绝原因：Information inference escapes declared relation path: TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article。

  未采纳模型摘要：三星计划扩展数据中心芯片设计业务，可能在未来增加数据中心AI芯片领域的竞争，对英伟达构成潜在竞争压力。

### 预算选中但尚未测试

- trendforce_news：[News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise
- ibkr_news：Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD
- trendforce_news：[News] huawei says ascend market share has surpassed nvidia in china backs npo over cpo for optical interconnects
- ibkr_news：Nvidia to Invest $1B in U.S. Science Over Next 5 Years
- ibkr_news：SpaceX Stock Falls as Musk Doubles Down on Nvidia. Why It's a Big Deal. -- Barrons.com
- semianalysis：Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

这些材料只有原文预览与选择依据，没有新版合格摘要，不编造采纳原因。


## TSM

来源分布（合并去重后）：{'ibkr_news': 22, 'trendforce_news': 49, 'defeatbeta_earnings_transcript': 35, 'semianalysis': 22, 'sec_edgar_filing_body': 6}；预算选中来源：{'ibkr_news': 3, 'trendforce_news': 4, 'semianalysis': 1}。

**最终采纳：无。数据来源、标题、摘要、通过原因均无最终采纳记录。**

### 已测试材料及未采纳原因

- 来源：ibkr_news；标题：TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk。

  拒绝原因：Information prediction requires forecast attribution: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f855c03-cef32fdea955:research_article。

  未采纳模型摘要：伯恩斯坦预测台积电今年美元营收将增长41%，且2027-2028年30%的年增长率存在上行风险，3季度营收比市场共识高3%，对台积电为利好信号。

  未采纳模型摘要：文章提到投资者将关注台积电2纳米产量提升及美国进一步扩张，这些是台积电未来增长的关键驱动因素，属直接相关事实。

- 来源：semianalysis；标题：Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime。

  结果：输出失败（ValueError），不重试、不采纳。

### 预算选中但尚未测试

- trendforce_news：[News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64
- ibkr_news：TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com
- trendforce_news：[News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows
- trendforce_news：[News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead
- ibkr_news：Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk
- trendforce_news：[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

这些材料只有原文预览与选择依据，没有新版合格摘要，不编造采纳原因。

## 证据

[完整漏斗JSON](../tmp/information_quality_closeout_20261013/review_v2_final/full_funnel.json)；[逐条原始输出与原文证据](INFORMATION_SOURCE_SELECTION_QUALITY_REPLAY_RESULTS.md)。本报告无新增模型调用、无生产Brief发布；未宣称完成每标的8篇的正式例行运行。
