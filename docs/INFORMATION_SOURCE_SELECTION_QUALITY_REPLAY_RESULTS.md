# Information 新版输出质量：8份真实模型复验

日期：2026-10-13。结论：**本轮未通过新版输出内容验收**。8次授权全部用于原清单，没有重试或新增正文外发。6份schema通过；仅1份通过内容契约且为空，0份非空材料可进入下一层内容验收。不能据此说四个标的没有有效消息。

模型：`deepseek/deepseek-v4-flash-0731`；目的地：OpenRouter；manifest：`f2783067012aaef3505fbf1943cb127e0d717e027694d793938a68ad2ad7d73c`。

7次有usage，已知合计 {'prompt_tokens': 26436, 'completion_tokens': 6990, 'total_tokens': 33426}；供应商返回费用已知小计 $0.004585280。1次没有usage/费用记录，小计不是全轮费用或账户账单。保留$0.50规划预留，单价限制与零重试不变。没有生产Brief发布、订单或部署。

|标的|请求|结构通过|内容契约通过|通过且非空|主要问题|
|---|---:|---:|---:|---:|---|
|MRVL|2|2|1|0|内部人交易来源陈述仍被卖出词误伤；另一篇合法空|
|MSFT|2|2|0|0|报道事实/自述分类；跨片段陈述；未声明路径与英文来源陈述|
|NVDA|2|1|0|0|一份非法JSON；另一份路径不符合静态卡|
|TSM|2|1|0|0|同一片段混合预测与观点导致整篇拒绝；另一份工具响应失败|

## 实际收获与剩余问题

- 原文片段ID减少了模型抄写，但不能证明摘要被选中的单个片段完整支持。本轮出现正文中确实有信息、但选中片段只支持一部分的情况。分段按字符边界也会截断句子。下一步需优先处理句子边界与多片段证据；不能仅提高quote通过率。
- MRVL的来源陈述第一句有VP、历史日期和股份，但后半句“多次卖出股票”被按分句检查误伤；这属于检查器缺陷。不过陈述还包含当前引用片段之外的多名高管历史，不能修复误伤后直接宣称整条采纳。
- 来源分类检查对整个片段的says/forecast用词判定过粗；一个片段可能同时包含已发生报道、发言人与预测。应按所抽取的来源陈述及支持句判定，保留预测/指控归属，不能简单删除规则。
- 部分摘要把“监管审查”沿供应链泛化到NVDA/AVGO，或把中国模型竞争外推成GPU/ASIC需求增加；静态边不是业务影响证据。MSFT newsletter还输出英文source_fact、零置信度以及未声明的Anthropic路径，需要收敛为有依据的有限结论。
- 工具响应失败须区分非法JSON和预期工具响应缺失；已有失败不自动补全JSON、不重试。响应结构失败时usage若尚未保存会丢失，本轮最后1次因此费用未知，后续执行器应先保存usage与响应形态。
- 原71篇元数据修订、去重/覆盖离线结果和135项业务回归仍有效；本轮没有验证新的正式Brief发布/缓存、Routine链路，也没有解除邮箱TLS阻塞。

## 分标的原始输出与审阅

下列摘要全部是**未采纳模型输出**或合法空结果，供核签和诊断，不能作为生产事实。引用来自批准的可见输入；即使存在该片段，也不证明摘要的全部内容被支持。

### MRVL

#### VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL

来源：ibkr_news；完整性：full。检查：content_contract_failed。

拒绝原因：信息简报不得包含交易建议：命中中文建议词 '卖出'

- 未采纳摘要：公司多位高管（包括总裁、董事长、CFO、VP）在2026年4月至10月期间持续通过10b5-1计划及直接交易卖出股票，最新一笔为VP Scarpulla于10/8/26以273.59美元卖出1,100股。这属于历史内幕交易事实，反映内部人持续减持，但多为计划性卖出，对基本面影响有限。

  来源陈述：VP Scarpulla于2026年10月8日以273.59美元卖出1,100股MRVL股票，直接持有降至909股；历史记录显示总裁、董事长、CFO等多位高管在2026年4月至10月期间多次卖出股票。

  分类：reported_fact；归属：Dow Jones Newswires / The Washington Service；路径：直接；材料性：0.3；置信度：0.8。

  选中片段 `ev:0:359`：

> SOURCE: Form 4    ISSUER: Marvell Technology Inc  SYMBOL: MRVL    FILER: Scarpulla Justin  TITLE: Vice President    DATE TRANSACTION SHARES PRICE VALUE  10/8/26 Sale 1,100 $273.59 $300,949    OWNERSHIP: 909 (Direct)

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full。检查：evidence_valid_pending_human_content_review。

合法空结果：本份没有抽取到静态范围内洞察，不代表目标在市场上没有消息。

### MSFT

#### Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update

来源：ibkr_news；完整性：full。检查：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f864502-5b54e9354f7a:research_article

- 未采纳摘要：文章报道美国副总统万斯宣布将微软等公司从允许H-1B签证持有者申请绿卡的项目中暂停，指控微软滥用签证系统。这对微软构成直接监管/运营风险，可能影响其招聘外国高技能人才的能力，但文章同时指出政府表示不想伤害公司，仅想传递信号。

  来源陈述：副总统万斯宣布暂停微软等科技公司参与允许H-1B签证持有者申请绿卡的项目，指控微软滥用签证系统，称微软每裁员一名美国工人就替换为1.5名外国工人。

  分类：reported_fact；归属：Dow Jones Newswires；路径：直接；材料性：0.7；置信度：0.85。

  选中片段 `ev:0:358`：

> By Natalie Andrews, Philip Wegmann and Michelle Hackman     Vice President JD Vance said Thursday that the administration is suspending several tech firms, including Microsoft, from a program that allows H-1B visa holders to apply for green cards.     The action by Vance's antifraud task force targets Microsoft, Adobe and several tech recruiting companies

- 未采纳摘要：文章引用微软发言人回应称公司计划向政府提供更多信息，并称其提交的约6000份H-1B申请中80%是为现有员工提交的。这是微软对监管指控的自我辩护，属于公司自述，不能视为已证实的事实。

  来源陈述：微软发言人表示公司计划向政府发送更多信息，称其美国员工大部分是美国公民，且上一财年提交的约6000份H-1B申请中80%是为现有员工提交的。

  分类：claim；归属：Kate Frischmann (Microsoft spokeswoman)；路径：直接；材料性：0.4；置信度：0.5。

  选中片段 `ev:1070:1422`：

> plans to send the administration more information. She said most of its employees in the U.S. are American citizens and that of the approximately 6,000 H-1B visa applications it submitted in the last fiscal year, 80% were for existing Microsoft employees.     "Microsoft only files H-1B petitions for those who meet the rigorous standards of this visa

- 未采纳摘要：文章报道万斯表示政府不想伤害微软，但将拒绝其申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。这暗示监管行动可能有限度，但持续施压仍可能影响微软的人才获取和运营成本。

  来源陈述：万斯表示政府将继续支持微软并保持良好关系，但将拒绝微软申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。

  分类：claim；归属：JD Vance (Vice President)；路径：直接；材料性：0.5；置信度：0.6。

  选中片段 `ev:4992:5347`：

> didn't want to harm the company but send them a message that they ought to hire Americans over foreign workers.     "So we're going to continue to support Microsoft and have a great relationship with Microsoft, but we're also going to deny them the ability to apply for these permanent residencies until they show that they are going to get serious about

同时返回其他实体：META, NVDA, AVGO。它们共享本份来源，不是独立信息源；详见完整审阅JSON。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial。检查：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article

- 未采纳摘要：中国开源模型的价格战正在挤压Anthropic等前沿实验室的经济性，迫使它们加速发布新模型。对微软而言，其投资的OpenAI/Anthropic生态面临来自中国低成本开源模型的竞争压力，可能削弱微软AI产品（如Copilot、Azure OpenAI服务）的定价能力和差异化优势。

  来源陈述：Anthropic was reported to be weighing a new model before its IPO, with cheap open-weight rivals 'especially in China' squeezing the economics, and shipped Claude Opus 5.5, its first release since calling for pacing the frontier, framing pacing as a way of 'remaining competitive with China.'

  分类：reported_fact；归属：SemiAnalysis；路径：Anthropic → MSFT；材料性：0.7；置信度：0.0。

  选中片段 `ev:29591:29948`：

> Claude Opus 5.5 , which Anthropic itself calls “our first release since we called for pacing the frontier.” The launch post now frames pacing as a way of “remaining competitive with China.” Nobody paces alone, not even the man who wrote the essay. What does all this mean for American labs and investors? What did the Trump-Xi Summit say about pacing? What

同时返回其他实体：AMZN, GOOG, META, NVDA, AVGO。它们共享本份来源，不是独立信息源；详见完整审阅JSON。

### NVDA

#### Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD

来源：ibkr_news；完整性：full。检查：failed_no_retry。

调用/输出失败：ValidationError；schema错误：[{"location": [], "type": "json_invalid"}]。不重试。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full。检查：content_contract_failed。

拒绝原因：Information inference escapes declared relation path: TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article

- 未采纳摘要：三星计划扩展数据中心芯片设计业务，可能在未来增加数据中心AI芯片领域的竞争，对英伟达构成潜在竞争压力。

  来源陈述：三星计划扩展包括显示驱动IC、电源管理IC以及数据中心芯片设计在内的业务。

  分类：reported_fact；归属：Munhwa Ilbo；路径：Samsung → NVDA；材料性：0.2；置信度：0.25。

  选中片段 `ev:2844:3080`：

> suppliers in the first half of 2026, according to the report. Beyond these areas, Samsung plans to expand businesses including display driver ICs (DDIs), power management ICs (PMICs), and chip design for data centers, Munhwa Ilbo notes.

同时返回其他实体：TSM。它们共享本份来源，不是独立信息源；详见完整审阅JSON。

### TSM

#### TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk

来源：ibkr_news；完整性：full。检查：content_contract_failed。

拒绝原因：Information prediction requires forecast attribution: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f855c03-cef32fdea955:research_article

- 未采纳摘要：伯恩斯坦预测台积电今年美元营收将增长41%，且2027-2028年30%的年增长率存在上行风险，3季度营收比市场共识高3%，对台积电为利好信号。

  来源陈述：伯恩斯坦预计台积电今年美元营收将增长41%，并认为其2027-2028年30%的年营收增长预测存在上行风险，3季度营收比市场共识高3%。

  分类：forecast；归属：Bernstein；路径：直接；材料性：0.8；置信度：0.75。

  选中片段 `ev:359:719`：

> in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

- 未采纳摘要：文章提到投资者将关注台积电2纳米产量提升及美国进一步扩张，这些是台积电未来增长的关键驱动因素，属直接相关事实。

  来源陈述：分析师称投资者将关注台积电2纳米产量提升、潜在的美国进一步扩张以及2027年展望。

  分类：claim；归属：Bernstein；路径：直接；材料性：0.6；置信度：0.7。

  选中片段 `ev:359:719`：

> in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

同时返回其他实体：ASML, AMAT, NVDA, AMD。它们共享本份来源，不是独立信息源；详见完整审阅JSON。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial。检查：failed_no_retry。

调用/输出失败：ValueError；schema错误：[]。不重试。

## 证据与下一步

[完整逐条输出审阅](../tmp/information_quality_closeout_20261013/review_v2_final/content_review.md)，[JSON](../tmp/information_quality_closeout_20261013/review_v2_final/content_review.json)，[执行账本](../tmp/information_quality_closeout_20261013/capture_v2_remaining/capture.json)。前5次原账本也保留，续跑仅发送剩余3份，合并计数8。

先修上述引用支持范围、分句误伤、分类粒度及工具诊断，做离线失败样本回归，再安排有限真实模型复验。当前8次额度已耗尽；不得自动增加调用。通过新版内容验收后，才能推进正式Brief发布/缓存及Routine验证。
