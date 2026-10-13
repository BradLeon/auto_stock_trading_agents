# Information 当前详细过滤漏斗与空结果复核

冻结时点：2026-10-12T10:27:57.770290+00:00；七天窗口起点：2026-10-05T10:27:57.770290+00:00。同一快照、同一选中32个目标/文档输入对，只对既有输出离线重审，新增模型调用和生产写入均0。

## 阅读口径

前半段是原文候选筛选；后半段是既有模型输出的处理结果。模型响应失败、无法定位或返回空，都不删除已准入原文。投资采用由下游分析师决定。来源观点、预测和实体关联解释不因语气或无静态图路径而被拒绝。

“合法空”原标签只表示响应结构允许insights=[]，不表示已经证明新闻与ticker无关。这里改称“模型返回空（待相关性复核）”，作为结果分支展示，不能把它放在目标摘要之后当成继续过滤的一步。

## 原文候选漏斗

|阶段|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|目标及产业链通道读取|100|86|116|114|
|SemiAnalysis通道读取|22|22|22|22|
|TrendForce通道读取|49|49|49|49|
|通道记录相加（含重复）|171|157|187|185|
|跨通道按文档版本去重|128|120|136|134|
|发布时间明确|124|116|132|130|
|七天窗口内|38|34|39|38|
|可见内容可作候选|38|34|39|38|
|关联/有限出版者探索候选|38|34|39|38|
|阅读预算选中|8|8|8|8|

通道已经读取Data Platform准入材料，包含历史电话会/SEC等资产，不是供应商全部新闻发现数。前三个通道有重叠；“按版本去重”仅去除同一版本的重复读取，正文相似/更新稿调度在预算选择中处理。预算未选中不等于不相关或内容拒绝。

|退出/延期原因|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|未知发布时间|4|4|4|4|
|窗口外|86|82|93|92|
|完整性不可用|0|0|0|0|
|关联或可见正文不足|0|0|0|0|
|未获本轮阅读预算|30|26|31|30|

本批partial都有足够长的可见正文，故完整性这一层没有新增退出；不代表将来所有partial都通过。仅订阅营销/短预告仍过滤，缺日期也不以采集时间代替。

## 选中8篇后的结果分支

|互斥结果，每列合计8篇|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|有本ticker候选摘要|6|6|5|7|
|只有其他实体摘要|1|2|0|0|
|模型返回空（不是通过率结论）|1|0|1|0|
|响应/JSON失败|0|0|1|1|
|引用片段ID无法定位|0|0|1|0|
|合计|8|8|8|8|

有本ticker候选摘要仍不是投资采纳，也不保证原模型摘要的全部推断正确。上述互斥分支不会把6/6/5/7再过滤成1/0/1/0。

## 两个空结果是否合理

### MRVL：三星退出Exynos Auto并调整System LSI资源

来源：TrendForce；完整性full；原始模型返回insights=[]，程序没有删掉非空条目。正文主要涉及三星、现代/BMW客户、汽车AP、图像传感器、苹果以及Exynos 2700；结尾也提到三星将扩展数据中心芯片设计。没有明确谈MRVL订单、产品、业务合作或观点。

判断：对MRVL本次目标返回无摘要可以接受；作为出版者探索材料保留原文，不强造MRVL影响。若后续分析师发现有用关联，可以消费原文。这不表示三星新闻无投资价值，也不表示所有相关ticker都应返回空。

### NVDA：微软采用NVIDIA RTX Spark的本地AI产品报道

来源：TrendForce；完整性full；原始模型返回insights=[]，程序没有把非空结果过滤掉。正文直接报道微软推出搭载NVIDIA RTX Spark的Surface Laptop Ultra；引述NVIDIA称RTX Spark支持最高128GB统一内存和本地运行最高1200亿参数模型；还报道NVIDIA参与Microsoft Execution Containers。

同一版本在MSFT核读时，模型曾返回一条NVDA洞察，概括微软采用RTX Spark及其能力。

判断：对NVDA空结果不合理，属于明显漏提取/抽取不稳定，而非原文不相关。产品采用与公司自述能力本来可以作为有关联的候选信息，最终投资价值留待分析师。空列表只满足结构，不满足该样本的业务抽取预期。

本例可人工整理的来源摘要：TrendForce转述Reuters称，微软推出搭载NVIDIA RTX Spark的Surface Laptop Ultra；报道同时引述NVIDIA介绍其统一内存及本地模型运行能力。这是对现成原文的人工概括，尚未写入生产Brief，也不视为模型补测成功。

两份响应均来自旧严格提示词。当前提示词已取消来源类别/静态路径硬要求，但没有再次调用模型；离线重审不能凭空补回原本缺失的模型摘要。不能据此断定是哪一条旧提示导致空输出。

## 各标的材料清单与状态

### MRVL

合并后来源分布：{'ibkr_news': 22, 'trendforce_news': 49, 'semianalysis': 22, 'defeatbeta_earnings_transcript': 32, 'sec_edgar_filing_body': 3}；选中来源分布：{'ibkr_news': 4, 'trendforce_news': 3, 'semianalysis': 1}。

|来源|标题|候选选择依据|当前结果|目标摘要条数|
|---|---|---|---|---:|
|ibkr_news|VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL|target_title|目标候选摘要|1|
|trendforce_news|[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround|publisher_exploration|模型返回空|0|
|ibkr_news|TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com|target_visible_body|目标候选摘要|1|
|ibkr_news|Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk|signal_chain|目标候选摘要|1|
|trendforce_news|[News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding|signal_chain|目标候选摘要|1|
|ibkr_news|The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com|signal_chain|目标候选摘要|1|
|trendforce_news|[News] nvidias nvhbm reshapes memory makers base die strategies as micron samsung turn to external foundries|signal_chain|其他实体摘要|0|
|semianalysis|Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime|publisher_exploration|目标候选摘要|1|

target_title/target_visible_body表示标题/正文目标匹配；signal_chain表示产业链候选；publisher_exploration表示有限出版者探索。探索命中不是最终目标关联证明。

### MSFT

合并后来源分布：{'ibkr_news': 18, 'trendforce_news': 49, 'semianalysis': 22, 'defeatbeta_earnings_transcript': 28, 'sec_edgar_filing_body': 3}；选中来源分布：{'ibkr_news': 3, 'trendforce_news': 4, 'semianalysis': 1}。

|来源|标题|候选选择依据|当前结果|目标摘要条数|
|---|---|---|---|---:|
|ibkr_news|Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update|target_title|目标候选摘要|3|
|semianalysis|Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime|publisher_exploration|目标候选摘要|1|
|trendforce_news|[News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise|target_title|目标候选摘要|2|
|ibkr_news|The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com|target_visible_body|目标候选摘要|1|
|trendforce_news|[News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead|target_visible_body|目标候选摘要|1|
|ibkr_news|Four Years In, $40 Trillion Up: Why This AI-Fueled Bull Market Has Room to Run -- Barrons.com|target_visible_body|目标候选摘要|2|
|trendforce_news|[News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding|signal_chain|其他实体摘要|0|
|trendforce_news|[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround|publisher_exploration|其他实体摘要|0|

target_title/target_visible_body表示标题/正文目标匹配；signal_chain表示产业链候选；publisher_exploration表示有限出版者探索。探索命中不是最终目标关联证明。

### NVDA

合并后来源分布：{'ibkr_news': 23, 'trendforce_news': 49, 'defeatbeta_earnings_transcript': 39, 'semianalysis': 22, 'sec_edgar_filing_body': 3}；选中来源分布：{'ibkr_news': 4, 'trendforce_news': 3, 'semianalysis': 1}。

|来源|标题|候选选择依据|当前结果|目标摘要条数|
|---|---|---|---|---:|
|ibkr_news|Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD|target_title|响应/结构失败|0|
|trendforce_news|[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround|publisher_exploration|目标候选摘要|1|
|trendforce_news|[News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise|target_title|模型返回空|0|
|ibkr_news|Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD|target_title|片段定位失败|0|
|trendforce_news|[News] huawei says ascend market share has surpassed nvidia in china backs npo over cpo for optical interconnects|target_title|目标候选摘要|3|
|ibkr_news|Nvidia to Invest $1B in U.S. Science Over Next 5 Years|target_title|目标候选摘要|1|
|ibkr_news|SpaceX Stock Falls as Musk Doubles Down on Nvidia. Why It's a Big Deal. -- Barrons.com|target_title|目标候选摘要|2|
|semianalysis|Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime|publisher_exploration|目标候选摘要|2|

target_title/target_visible_body表示标题/正文目标匹配；signal_chain表示产业链候选；publisher_exploration表示有限出版者探索。探索命中不是最终目标关联证明。

### TSM

合并后来源分布：{'ibkr_news': 22, 'trendforce_news': 49, 'defeatbeta_earnings_transcript': 35, 'semianalysis': 22, 'sec_edgar_filing_body': 6}；选中来源分布：{'ibkr_news': 3, 'trendforce_news': 4, 'semianalysis': 1}。

|来源|标题|候选选择依据|当前结果|目标摘要条数|
|---|---|---|---|---:|
|ibkr_news|TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk|target_title|目标候选摘要|2|
|semianalysis|Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime|publisher_exploration|响应/结构失败|0|
|trendforce_news|[News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64|target_title|目标候选摘要|3|
|ibkr_news|TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com|target_title|目标候选摘要|1|
|trendforce_news|[News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows|target_title|目标候选摘要|2|
|trendforce_news|[News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead|target_title|目标候选摘要|3|
|ibkr_news|Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk|target_visible_body|目标候选摘要|1|
|trendforce_news|[News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround|publisher_exploration|目标候选摘要|1|

target_title/target_visible_body表示标题/正文目标匹配；signal_chain表示产业链候选；publisher_exploration表示有限出版者探索。探索命中不是最终目标关联证明。

## 摘要、依据与证据入口

[每篇候选摘要、来源归属、引用及基础通过依据](INFORMATION_SOURCE_SELECTION_MATERIAL_POLICY_REVIEW.md)；[本次离线重审JSON](../tmp/information_quality_closeout_20261013/material_policy_review_final/combined_review.json)；[冻结原文候选计数](../tmp/information_quality_closeout_20261013/review_v2_final/full_funnel.json)。

建议后续对直接目标材料的空输出标记为“待复核”，不把“模型为空”自动等同于“没有相关信息”。这不是自动重试授权；本次没有重试、新外发或生产发布。
