# Information 材料准入职责修正与离线重审

依据用户最新裁决：采集/准入检查可读取、来源及版本追溯、重复、时间/完整性与实体关联；关联可包含观点。预测、声明、自述和意见不因语气或类别拒绝。partial仅消费可见实质内容，过短/营销预告可过滤。投资采纳和使用由下游分析师决定。

本次只对原32份输入与旧模型输出离线重审，无新采集、模型调用、生产投影、订单或部署；旧输入、输出及历史拒绝报告未改写。旧提示词生成的影响推断不因此升级为有效因果证据。

## 规则调整

- 删除source_kind与said/according to/forecast关键词一致性的准入门槛；类型仅为模型描述性元数据，展示统一使用“来源信息”，不当作真实发生的认证。
- 删除relation_path必须存在静态边、引用必须重复写目标名及impact_path必须为direct的门槛；关联上下文可带观点，输出实体仍受既有角色scope约束，不授权新增来源或输出范围。
- 不要求Information替后续分析师证明产业链影响成立；新提示词优先忠实来源，不自行扩写投资影响。
- 保留精确文档版本、原文位置、引用可解析及不补写隐藏内容；source_fact与summary仍需非空，相关性判断不是靠静态路径硬证明。
- 修正历史内部人卖出、公司采购芯片被误当系统交易建议的检查；系统自身的交易指令/仓位建议仍受角色边界拦截。
- stored partial没有付费墙标记时，也检查可见预览是否实质足够；仅标题/订阅营销或短预告返回teaser。现有最低有效长度及正文证据规则只是初筛启发式，不保证完整支持每个摘要。

## 当前漏斗

|阶段|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|准入原文通道合并去重|128|120|136|134|
|发布时间明确|124|116|132|130|
|七天窗口内|38|34|39|38|
|可见内容候选|38|34|39|38|
|预算选择|8|8|8|8|
|原模型尝试|8|8|8|8|
|已有摘要结构及定位通过（含空）|8|8|6|7|
|非空摘要材料（含其他实体）|7|8|5|7|
|包含目标专属候选摘要的材料|6|6|5|7|
|模型返回空（旁支统计，不是后续过滤）|1|0|1|0|
|投资采纳／影响使用|留待分析师|留待分析师|留待分析师|留待分析师|

29份通过基础摘要检查，2份为空，27份非空；24份含本次ticker的候选摘要。3份剩余问题：NVDA一次响应JSON失败、一次片段ID不存在；TSM一次工具响应失败。它们的原文仍存在，不等于新闻材料不准入。来源分类、图路径或投资价值不再提前否决材料。

“通过”仅指现有输出字段/引用可解析，不等于摘要每项陈述被单个片段完整支持、观点为真或投资采用。相同文章在不同ticker下的关联和输出分别记录，不算独立来源。原8篇均属选中候选，探索材料缺目标摘要时仍不能声称已建立目标关联。

## 每标的来源、标题、候选摘要与通过依据

### MRVL

#### VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 公司多位高管（包括总裁、董事长、CFO、VP）在2026年4月至10月期间持续通过10b5-1计划及直接交易卖出股票，最新一笔为VP Scarpulla于10/8/26以273.59美元卖出1,100股。这属于历史内幕交易事实，反映内部人持续减持，但多为计划性卖出，对基本面影响有限。

  来源陈述：VP Scarpulla于2026年10月8日以273.59美元卖出1,100股MRVL股票，直接持有降至909股；历史记录显示总裁、董事长、CFO等多位高管在2026年4月至10月期间多次卖出股票。

  来源归属：Dow Jones Newswires / The Washington Service

  原文引用：SOURCE: Form 4    ISSUER: Marvell Technology Inc  SYMBOL: MRVL    FILER: Scarpulla Justin  TITLE: Vice President    DATE TRANSACTION SHARES PRICE VALUE  10/8/26 Sale 1,100 $273.59 $300,949    OWNERSHIP: 909 (Direct)    --------------------------------------- HISTORICAL ACTIVITY --------------------------------------   AVG REMAINING  INSIDER NAME ROLE TRADE

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f878d55-2699cdb0a600:research_article@121a5d7af94a9bb1`；publication：`8b19054464da10a92d125037f0737aea84a9e14fd7743d0ac582a69541fbc8fa`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

模型返回空：该响应未提取洞察，相关性须按原文复核，不证明材料无关。

版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`；publication：`9cd0a00a909835eb3c23803707752eb9892d7ae7cc3dff8c356cfb5d74caaac6`。

#### TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：target_visible_body。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章报道Marvell股价下跌超过1%，主要受债券收益率上升和油价上涨等宏观因素拖累，而非基本面恶化；同时台积电强劲营收表明AI芯片需求持续，作为台积电下游客户的Marvell可能间接受益于AI需求旺盛。

  来源陈述：Advanced Micro Devices, Broadcom and Marvell were down more than 1%, while Intel fell around 2%. U.S. tech stocks were not taking their cue from TSMC or Samsung, though, but from higher bond yields and oil prices.

  来源归属：Barron's / Dow Jones

  原文引用：Advanced Micro Devices, Broadcom and Marvell were down more than 1%, while Intel fell around 2%.     Write to Callum Keown at callum.keown@dowjones.com     This content was created by Barron's, which is operated by Dow Jones & Co. Barron's is published independently from Dow Jones Newswires and The Wall Street Journal.         (END) Dow Jones Newswires

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article@bffae749659ba001`；publication：`73bca2e8949cbbd6b5e408da05aeb449861744731e214d9583079838e031b95c`。

#### Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk

来源：ibkr_news；完整性：full；原选择依据：signal_chain。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章强调TSMC先进节点需求和产能利用率评论是半导体资本开支的指标，作为TSMC下游客户的MRVL，其AI相关需求强度可从TSMC产能信号中间接推断，但属多跳推断。

  来源陈述：TSMC对先进节点需求和产能利用率的评论将作为半导体资本开支计划的指标被密切关注，其财报提供AI投资是否保持完好的早期信号。

  来源归属：James Ooi (Tiger Brokers)

  原文引用：sustained into 2027," he says. Key will be major U.S. banks, which start the 3Q earnings season this week. Earnings of TSMC and ASML, key suppliers in the AI value chain, will also offer an early read on whether AI-related investment remains intact, he says in an email. TSMC's commentary on advanced-node demand and capacity utilization, and ASML's order

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article@584b37cb51b3cc69`；publication：`bf757b2fdf3b9144110422ee213c070e5d81dc7ffabd268e06ff2046b2f2075d`。

#### [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding

来源：trendforce_news；完整性：full；原选择依据：signal_chain。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- AI系统采用chiplet 2.5D封装、interposer面积大幅扩大，反映定制AI芯片（如Marvell的定制ASIC）对先进封装与更大interposer的依赖增强，间接利好其定制计算业务。

  来源陈述：报道指出先进AI系统在interposer上组合多个计算chiplet与HBM堆叠，以在单一封装中集成更多计算与内存。

  来源归属：TrendForce / Chosun Biz

  原文引用：computing requirements of AI workloads. Advanced AI systems are therefore combining multiple compute chiplets and HBM stacks on an interposer, allowing more compute and memory to be integrated into a single package. In response, Samsung is exploring several ways to improve heat dissipation, starting with lower-power designs. According to the report, key

版本：`TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article@4f09ed22d73026ef`；publication：`8ef25d8d3c97a99796623a86f5e316865dcb64bb0961a8435b709563cb5eddc7`。

#### The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：signal_chain。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章强调AI交易和科技资本开支变现能力是市场核心驱动力，MRVL作为AI/数据中心芯片供应商，其需求与AI资本开支周期高度相关，但文章未直接提及MRVL，属间接推断。

  来源陈述：文章引用策略师观点称AI和科技公司变现资本开支的能力以及AI交易的稳定性将决定牛市能否延续。

  来源归属：Keith Lerner (Truist Advisory); Anthony Saglimbene (Ameriprise)

  原文引用：chief market strategist at Truist Advisory.     "The sustainability of that growth, the ability of AI and tech companies to monetize all the capital spending, along with the direction of interest rates and valuations, will be central to determining whether the bull market can extend its advance," he adds.     Tech will lead the charge, as ever, with the

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article@4b65c379c3719d90`；publication：`8e7394526b90dcc8bb53f3e709464c12f580b81934a2b91e1ee0f4fcc6385514`。

#### [News] nvidias nvhbm reshapes memory makers base die strategies as micron samsung turn to external foundries

来源：trendforce_news；完整性：full；原选择依据：signal_chain。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

只有其他实体摘要，没有本ticker专属摘要；仍是候选探索材料，不冒充目标已关联信息。

版本：`TRENDFORCE:managed-trendforce_news-news-nvidias-nvhbm-reshapes-memory-makers-base-die-strat-0319f162c876:research_article@3f32bbe1e1aa7959`；publication：`99ad43da51531efbab957becaf3d0733c3e41bb0b828da740b5a17e8164603f2`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章核心是地缘政治与AI安全政策分析，未涉及MRVL或其供应链的具体业务、产品需求或竞争动态。文章关于中国AI发展速度的宏观描述不足以建立对MRVL的可靠信号链推断。

  来源陈述：文章主要讨论中国AI安全监管体制、前沿实验室发布行为及中美AI竞争，未提及MRVL相关业务。

  来源归属：SemiAnalysis

  原文引用：Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime AI safety is on fire.                                                                                                                                                                                                          Forwarded this email? Subscribe here for more Beijing Will Not

仅使用可见预览，不代表已取得全文；观点与背景应按可见内容范围使用。

版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`；publication：`608d952e5503aaa1acd8363d53f45aa853fc9484ea2b60788d652212c322ddec`。

### MSFT

#### Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章报道美国副总统万斯宣布将微软等公司从允许H-1B签证持有者申请绿卡的项目中暂停，指控微软滥用签证系统。这对微软构成直接监管/运营风险，可能影响其招聘外国高技能人才的能力，但文章同时指出政府表示不想伤害公司，仅想传递信号。

  来源陈述：副总统万斯宣布暂停微软等科技公司参与允许H-1B签证持有者申请绿卡的项目，指控微软滥用签证系统，称微软每裁员一名美国工人就替换为1.5名外国工人。

  来源归属：Dow Jones Newswires

  原文引用：By Natalie Andrews, Philip Wegmann and Michelle Hackman     Vice President JD Vance said Thursday that the administration is suspending several tech firms, including Microsoft, from a program that allows H-1B visa holders to apply for green cards.     The action by Vance's antifraud task force targets Microsoft, Adobe and several tech recruiting companies

- 文章引用微软发言人回应称公司计划向政府提供更多信息，并称其提交的约6000份H-1B申请中80%是为现有员工提交的。这是微软对监管指控的自我辩护，属于公司自述，不能视为已证实的事实。

  来源陈述：微软发言人表示公司计划向政府发送更多信息，称其美国员工大部分是美国公民，且上一财年提交的约6000份H-1B申请中80%是为现有员工提交的。

  来源归属：Kate Frischmann (Microsoft spokeswoman)

  原文引用：plans to send the administration more information. She said most of its employees in the U.S. are American citizens and that of the approximately 6,000 H-1B visa applications it submitted in the last fiscal year, 80% were for existing Microsoft employees.     "Microsoft only files H-1B petitions for those who meet the rigorous standards of this visa

- 文章报道万斯表示政府不想伤害微软，但将拒绝其申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。这暗示监管行动可能有限度，但持续施压仍可能影响微软的人才获取和运营成本。

  来源陈述：万斯表示政府将继续支持微软并保持良好关系，但将拒绝微软申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。

  来源归属：JD Vance (Vice President)

  原文引用：didn't want to harm the company but send them a message that they ought to hire Americans over foreign workers.     "So we're going to continue to support Microsoft and have a great relationship with Microsoft, but we're also going to deny them the ability to apply for these permanent residencies until they show that they are going to get serious about

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f864502-5b54e9354f7a:research_article@aadce4b4f0da24f3`；publication：`b8dcae59adffcff031415ce769d22ab3091150286e2bb0692e52a9d100d0ff15`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 中国开源模型的价格战正在挤压Anthropic等前沿实验室的经济性，迫使它们加速发布新模型。对微软而言，其投资的OpenAI/Anthropic生态面临来自中国低成本开源模型的竞争压力，可能削弱微软AI产品（如Copilot、Azure OpenAI服务）的定价能力和差异化优势。

  来源陈述：Anthropic was reported to be weighing a new model before its IPO, with cheap open-weight rivals 'especially in China' squeezing the economics, and shipped Claude Opus 5.5, its first release since calling for pacing the frontier, framing pacing as a way of 'remaining competitive with China.'

  来源归属：SemiAnalysis

  原文引用：Claude Opus 5.5 , which Anthropic itself calls “our first release since we called for pacing the frontier.” The launch post now frames pacing as a way of “remaining competitive with China.” Nobody paces alone, not even the man who wrote the essay. What does all this mean for American labs and investors? What did the Trump-Xi Summit say about pacing? What

仅使用可见预览，不代表已取得全文；观点与背景应按可见内容范围使用。

版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`；publication：`608d952e5503aaa1acd8363d53f45aa853fc9484ea2b60788d652212c322ddec`。

#### [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 微软推出搭载NVIDIA RTX Spark的Surface Laptop Ultra，将本地AI能力推向PC，强化Windows作为AI代理平台的地位，对微软自身AI产品线构成直接利好。

  来源陈述：微软在活动中推出了搭载NVIDIA RTX Spark的Surface Laptop Ultra，定价从2599美元起，最高5899美元。

  来源归属：Reuters

  原文引用：the same event, the Windows maker also introduced the NVIDIA RTX Spark-powered Surface Laptop Ultra. Microsoft is taking a similar approach with Copilot, keeping the most demanding tasks in the cloud while allowing local models to handle workloads where cost or privacy matter more, according to Microsoft Copilot chief Jacob Andreou. As AI agents become more

- 微软推出RTX Spark驱动的Surface Laptop Ultra，定价最高达5,899美元，与苹果MacBook Pro形成竞争。

  来源陈述：Reuters notes that Apple's MacBook Pro with a 40-core graphics chip, 128 GB of memory, and 2 TB of storage costs $6,700, although the two machines are not directly comparable.

  来源归属：Reuters

  原文引用：Reuters notes that Apple's MacBook Pro with a 40-core graphics chip, 128 GB of memory, and 2 TB of storage costs $6,700, although the two machines are not directly comparable. Microsoft's entry-level Surface Laptop Ultra is priced above Apple's $1,999 base MacBook Pro, though it offers 24 GB of memory compared with 16 GB on Apple's entry-level model,

版本：`TRENDFORCE:managed-trendforce_news-news-microsoft-expands-local-ai-push-with-nvidia-rtx-spa-269045ffa982:research_article@4a9cc17a39bf92f4`；publication：`6db45e62ef63c09ad991c3edfb7bd5c870cacd26132df21565106798c072b9b1`。

#### The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：target_visible_body。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章指出微软是过去两个月贡献标普500约90%涨幅的少数几只股票之一，且科技/AI板块盈利增长强劲（Q2标普盈利增长35.1%），但文章同时强调AI资本开支能否变现、利率和估值方向是牛市能否延续的关键，对微软构成中性偏正面的背景信息。

  来源陈述：文章指出微软是过去两个月贡献标普500约90%涨幅的少数几只股票之一（与Meta、Alphabet、Apple、Nvidia并列），并强调AI和科技公司能否变现资本开支是牛市能否延续的核心。

  来源归属：Barron's / Martin Baccardax

  原文引用：around 90% of the S&P 500's gains over the past two months.     Goldman Sachs data also suggests that the S&P 500's top 10 stocks, all of which are tied to the tech sector, will deliver around 37% of the benchmark's overall earnings this quarter, the highest share in more than two decades.     But while the market's tech-led resilience remains firmly in

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article@4b65c379c3719d90`；publication：`8e7394526b90dcc8bb53f3e709464c12f580b81934a2b91e1ee0f4fcc6385514`。

#### [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead

来源：trendforce_news；完整性：full；原选择依据：target_visible_body。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- AMD扩大AI服务器产能并锁定供应链，Foxconn为AMD主要AI服务器供应商且为微软等云服务商组装服务器，AMD产能扩张可能增加对微软的AI服务器供给，利好微软AI基础设施部署。

  来源陈述：Foxconn是AMD主要AI服务器供应商之一，其业务涵盖基板、散热模块到为包括微软和Oracle在内的云服务商组装完整AI服务器。

  来源归属：Economic Daily News

  原文引用：AMD's major AI server suppliers, with its involvement spanning substrates and thermal modules to complete AI server assembly for cloud service providers including Microsoft and Oracle. The group also supplies high-speed switches and optical transmission equipment alongside AMD-based servers. AMD is also working closely with memory chipmakers to secure

版本：`TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article@c610ff16bd99e252`；publication：`7b4b364cfe2d93b0cb16213216f866072a5c89619a7bb45237e58d661624edb4`。

#### Four Years In, $40 Trillion Up: Why This AI-Fueled Bull Market Has Room to Run -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：target_visible_body。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章指出微软对OpenAI的投资（OpenAI估值超1.4万亿美元并计划明年上市）是这轮AI牛市早期阶段的催化剂，这直接利好微软作为OpenAI主要投资者的资产价值与AI战略地位。

  来源陈述：微软对OpenAI的投资——基于OpenAI计划明年上市，其估值已超过1.4万亿美元——点燃了牛市初期的涨势。

  来源归属：Barron's / Martin Baccardax

  原文引用：publically-traded company next year -- sparked the early stage of the bull-market rally, but Nvidia's first-quarter earnings report in spring 2024 added the real rocket fuel.     The chip maker said that during the period, data-center revenue topped its gaming sales for the first time ever and its top line rose 83% year over year to $3.75 billion, and it

- 文章指出微软对OpenAI的投资是这轮AI牛市早期阶段的催化剂，OpenAI估值超1.4万亿美元并计划明年上市，微软作为主要投资者将受益于OpenAI估值上升带来的资产增值。

  来源陈述：微软对OpenAI的投资——基于OpenAI计划明年上市，其估值已超过1.4万亿美元——点燃了牛市初期的涨势。

  来源归属：Barron's / Martin Baccardax

  原文引用：publically-traded company next year -- sparked the early stage of the bull-market rally, but Nvidia's first-quarter earnings report in spring 2024 added the real rocket fuel.     The chip maker said that during the period, data-center revenue topped its gaming sales for the first time ever and its top line rose 83% year over year to $3.75 billion, and it

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f885259-3484c501d2e9:research_article@feb63d60b34870e6`；publication：`987b24f0e984808485f12eb1cccc57bceb6aa3f05499a50bd7ee45bc6f8f320f`。

#### [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding

来源：trendforce_news；完整性：full；原选择依据：signal_chain。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

只有其他实体摘要，没有本ticker专属摘要；仍是候选探索材料，不冒充目标已关联信息。

版本：`TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article@4f09ed22d73026ef`；publication：`8ef25d8d3c97a99796623a86f5e316865dcb64bb0961a8435b709563cb5eddc7`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

只有其他实体摘要，没有本ticker专属摘要；仍是候选探索材料，不冒充目标已关联信息。

版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`；publication：`9cd0a00a909835eb3c23803707752eb9892d7ae7cc3dff8c356cfb5d74caaac6`。

### NVDA

#### Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD

来源：ibkr_news；完整性：full；原选择依据：target_title。

已有摘要未通过：failed_no_retry；原文保留，模型输出失败不等于材料不可读取。

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f883520-7db846a53c29:research_article@357638c4867df6c9`；publication：`ea4f0d95d79725c3e1d4ed3f5354481e4cc7409f8e88e04503717bb632f1c6b1`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 三星计划扩展数据中心芯片设计业务，可能在未来增加数据中心AI芯片领域的竞争，对英伟达构成潜在竞争压力。

  来源陈述：三星计划扩展包括显示驱动IC、电源管理IC以及数据中心芯片设计在内的业务。

  来源归属：Munhwa Ilbo

  原文引用：suppliers in the first half of 2026, according to the report. Beyond these areas, Samsung plans to expand businesses including display driver ICs (DDIs), power management ICs (PMICs), and chip design for data centers, Munhwa Ilbo notes.

版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`；publication：`9cd0a00a909835eb3c23803707752eb9892d7ae7cc3dff8c356cfb5d74caaac6`。

#### [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

模型返回空：该响应未提取洞察，相关性须按原文复核，不证明材料无关。

版本：`TRENDFORCE:managed-trendforce_news-news-microsoft-expands-local-ai-push-with-nvidia-rtx-spa-269045ffa982:research_article@4a9cc17a39bf92f4`；publication：`6db45e62ef63c09ad991c3edfb7bd5c870cacd26132df21565106798c072b9b1`。

#### Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD

来源：ibkr_news；完整性：full；原选择依据：target_title。

已有摘要未通过：Information has unlocated evidence span: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88687f-310c9002144e:research_article；原文保留，模型输出失败不等于材料不可读取。

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88687f-310c9002144e:research_article@3119269c40355908`；publication：`49e6764c4446c81e6a32be45e88d1bde3a5a9683d0a3da9c1f39f59142582801`。

#### [News] huawei says ascend market share has surpassed nvidia in china backs npo over cpo for optical interconnects

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 华为称Ascend在中国市场已超越NVIDIA，且中国2026年国产AI芯片将占据高端市场近90%份额，对NVIDIA在中国的高端AI芯片业务构成直接竞争压力。

  来源陈述：华为轮值董事长徐直军称基于华为可追踪的数据，Ascend在中国已可能超越NVIDIA；TrendForce预计2026年国产AI芯片将占据中国高端AI芯片市场近90%份额。

  来源归属：Eric Xu (华为轮值董事长) / TrendForce

  原文引用：customers. Meanwhile, Xu said Ascend has likely surpassed NVIDIA in China based on data Huawei can track, though NVIDIA's exact market share is difficult to determine. He expects the global supply-demand balance for AI computing chips to largely recover around 2029, with China potentially taking longer. The broader shift toward domestic AI chips is also

- 华为Ascend 950PR已限量供货、950DT SuperPoD预计2026年底至2027年初放量，且华为产能全部售罄，显示国产AI芯片供给持续扩张，对NVIDIA在中国市场份额构成持续侵蚀。

  来源陈述：Ascend 950PR已限量供货，950DT SuperPoD仍在测试，预计2026年底或2027年初放量；华为表示产能全部售罄并希望供应链加速扩张。

  来源归属：TrendForce / ijiwei

  原文引用：architecture and UnifiedBus (UB) interconnect. As the report points out, Xu said the Ascend 950PR, designed mainly for inference, is already available in limited volumes. The 950DT-based SuperPoD, aimed primarily at training, remains in testing, with volume supply expected around the end of 2026 or early 2027. Supply remains the bigger constraint. Xu said

- 华为选择NPO而非CPO路线并宣称成本降低近40%，其光学互连技术路线选择反映国产AI系统在互连层面形成差异化竞争，但该信息对NVIDIA的直接竞争影响有限且属技术路线层面。

  来源陈述：华为称早期决定采用NPO而非CPO，认为NPO在工程实现、成本、维护和良率上更优，可降低成本近40%。

  来源归属：Eric Xu (华为轮值董事长)

  原文引用：CPO, arguing that NPO should remain the better option for a considerable period in terms of engineering implementation, cost, maintenance, and yield. While NPO requires about 5 centimeters of copper connection, versus roughly 5 millimeters for CPO, he said it cuts costs by nearly 40%. If the optical engine fails, it can also be replaced without discarding

版本：`TRENDFORCE:managed-trendforce_news-news-huawei-says-ascend-market-share-has-surpassed-nvidi-aaac1643c5f2:research_article@e78b4821c48d9f4a`；publication：`63b72ab58165c8075721151d47e25a073b187a3873ebc22be038bfdf7470f87e`。

#### Nvidia to Invest $1B in U.S. Science Over Next 5 Years

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 英伟达宣布未来五年投入10亿美元支持美国科研机构及云服务商，属于公司自身战略投资公告，对NVDA本身无直接财务或需求层面的实质影响，更多是品牌与生态建设举措。

  来源陈述：英伟达宣布未来五年投入10亿美元，向高等教育研究机构提供计算和超级智能基础设施资源，并支持云服务商强化美国政府需求。

  来源归属：Dow Jones Newswires

  原文引用：By Kelly Cloonan         Nvidia said it is committing $1 billion over the next five years to help the U.S. gain an edge in key scientific fields.     The chipmaker said Thursday it will offer compute and super-intelligence infrastructure resources to higher-education research institutions, as well as support for cloud service providers bolstering U.S.

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f8430e9-f485ed2ab427:research_article@fb05e832634539f1`；publication：`67cb03eb9d7e86395f8fb0d136c1dec1ed046e35f3df9c931b12434a01d85cd5`。

#### SpaceX Stock Falls as Musk Doubles Down on Nvidia. Why It's a Big Deal. -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- SpaceX计划融资400亿美元购买Nvidia芯片，且xAI业务计划年底前在孟菲斯Colossus数据中心部署多达144万颗Nvidia芯片，直接利好Nvidia的AI芯片需求。

  来源陈述：SpaceX is aiming to raise $40 billion in funding to buy Nvidia chips, and Musk set out plans for SpaceX's xAI business to have as many as 1.44 million Nvidia chips at its Memphis Colossus data-center complex before the end of the year.

  来源归属：Financial Times / Barron's (Adam Clark)

  原文引用：By Adam Clark     SpaceX and Nvidia are already strong allies. Now the two companies are set to tighten their links even more as Elon Musk's space-and-artificial intelligence company prepares to spend tens of billions on Nvidia's hardware.     SpaceX is aiming to raise $40 billion in funding to buy Nvidia chips, the Financial Times reported late Tuesday,

- SpaceX的AI业务预计2027年营收达600亿美元（高于7月估计的380亿），2031年AI相关营收预计达5300亿美元，表明大规模AI算力采购将持续，利好Nvidia。

  来源陈述：SpaceX's AI business is now expected to generate $60 billion in 2027 revenue, up from a July estimate of $38 billion. By 2031, Wall Street projects $530 billion in AI-related revenue.

  来源归属：Wall Street analysts (via Barron's)

  原文引用：said SpaceX would exclusively use Nvidia AI chips.     As Barron's recently noted, SpaceX's AI business -- set to be renamed "super intelligence" in line with President Donald Trump's preferred nomenclature -- is now expected to generate $60 billion in 2027 revenue, up from a July estimate of $38 billion. By 2031, Wall Street projects $530 billion in

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f818180-1fbf42af2288:research_article@b632f8f32d2c4aa5`；publication：`9d07cccd728dd3bfa2a063087d745d7529871685c6506220b429a5a4d1290b74`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章指出中国AI前沿实验室处于发布和价格战状态，加速开发而非减速，且中国监管层优先发展而非安全。中国持续快速部署AI（AI+行动计划2027年70%渗透率）意味着对AI算力需求持续增长，这对NVDA的出口受限市场之外的全球需求信号是间接的。但文章同时强调美国出口管制是中国的供应侧风险，中国加速开发可能更多依赖国产替代而非NVDA芯片，因此对NVDA的净影响偏中性。

  来源陈述：中国前沿实验室处于发布和价格战状态，无人要求减速；中国AI+行动计划目标2027年70%渗透率、2030年90%，追求AI在各行业快速部署。

  来源归属：SemiAnalysis

  原文引用：Council’s AI+ Action Plan targets 70% penetration of agents and intelligent terminals by 2027 and 90% by 2030, aiming for a rapid deployment of AI across all sectors and industries. We think the Chinese government takes an open position on frontier models but tightens the applications and regulates outputs. Since 2025, China has issued a number of mandates,

- 文章指出中国将出口管制视为供应链安全风险，认为美国通过技术垄断和出口管制'恶意切断全球AI供应链'。中国加速自研替代的动机增强，长期可能削弱NVDA对华出口市场，但这是间接推断。

  来源陈述：Framework 3.0将出口管制列为供应链安全风险，警告'某些国家利用技术垄断和单边强制措施如出口管制设置发展壁垒并恶意切断全球AI供应链'。

  来源归属：SemiAnalysis

  原文引用：that borrows the frontier vocabulary also lists export controls as a supply-chain security risk. It warns that “certain countries use technological monopoly and unilateral coercive measures such as export controls to erect development barriers and maliciously sever the global AI supply chain.” The Framework’s appendix of trustworthy-AI principles places

仅使用可见预览，不代表已取得全文；观点与背景应按可见内容范围使用。

版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`；publication：`608d952e5503aaa1acd8363d53f45aa853fc9484ea2b60788d652212c322ddec`。

### TSM

#### TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 伯恩斯坦预测台积电今年美元营收将增长41%，且2027-2028年30%的年增长率存在上行风险，3季度营收比市场共识高3%，对台积电为利好信号。

  来源陈述：伯恩斯坦预计台积电今年美元营收将增长41%，并认为其2027-2028年30%的年营收增长预测存在上行风险，3季度营收比市场共识高3%。

  来源归属：Bernstein

  原文引用：in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

- 文章提到投资者将关注台积电2纳米产量提升及美国进一步扩张，这些是台积电未来增长的关键驱动因素，属直接相关事实。

  来源陈述：分析师称投资者将关注台积电2纳米产量提升、潜在的美国进一步扩张以及2027年展望。

  来源归属：Bernstein

  原文引用：in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f855c03-cef32fdea955:research_article@11e02b38f83b3925`；publication：`d1f866d2dbda6c286050e8bebc3adf81d0f787740b1c4fd8e54c1dae8e2c1d76`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；完整性：partial；原选择依据：publisher_exploration。

已有摘要未通过：failed_no_retry；原文保留，模型输出失败不等于材料不可读取。

仅使用可见预览，不代表已取得全文；观点与背景应按可见内容范围使用。

版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`；publication：`608d952e5503aaa1acd8363d53f45aa853fc9484ea2b60788d652212c322ddec`。

#### [News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 台积电9月及Q3营收超预期创纪录，AI需求与智能手机备货推动增长，Q3净利预计同比大增64%，Q4有望继续受NVIDIA Vera Rubin平台及2nm制程需求推动。

  来源陈述：台积电9月营收5118.6亿新台币，连续第二个月突破5000亿，Q3营收创纪录达1.49万亿新台币（467.1亿美元），同比增50%，超市场预期；LSEG分析师预计Q3净利同比增64%至7408亿新台币。

  来源归属：Reuters/LSEG

  原文引用：Ahead of its October 15 earnings call, TSMC reported September revenue of NT$511.86 billion, topping NT$500 billion for the second straight month. The strong finish propelled third-quarter revenue to a record NT$1.49 trillion (US$46.71 billion), up 50% year over year and beating market expectations, according to Reuters . The figure surpassed the LSEG

- 台积电Q3营收超过自身7月电话会议给出的指引区间上限，毛利率与营业利润率指引低于Q2实际值，但营收动能强劲。

  来源陈述：台积电7月电话会议给出Q3营收指引为446亿至458亿美元，毛利率65%-67%，营业利润率56%-58%，均低于Q2的67.7%和60.3%。

  来源归属：Anue

  原文引用：July earnings call. As highlighted by Anue , the foundry giant had projected third-quarter revenue of US$44.6 billion to US$45.8 billion, implying 12% sequential growth and a 37% year-over-year increase at the midpoint. Based on an assumed exchange rate of NT$32 per U.S. dollar, that reportedly translated into revenue of roughly NT$1.427 trillion to

- 分析师预计Q4营收将进一步攀升，受NVIDIA Vera Rubin平台及台积电2nm制程需求推动，显示先进制程需求持续强劲。

  来源陈述：中央社引述分析师预计Q4营收将进一步攀升，受NVIDIA Vera Rubin平台和台积电2nm制程技术需求推动。

  来源归属：Central News Agency引述分析师

  原文引用：in the fourth quarter, fueled by demand for NVIDIA's Vera Rubin platform and TSMC's 2nm process technology.

版本：`TRENDFORCE:managed-trendforce_news-news-tsmc-q3-revenue-beats-forecasts-at-record-nt1-49-tr-5387da44b1d8:research_article@5178d7d63d9abf53`；publication：`0a0072de80d4dc70c644765472cf93c20c5fbbd1494b5480f98f4f08d4fcbe83`。

#### TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com

来源：ibkr_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 台积电9月营收同比增长55%，第三季度总营收同比增长51%且超出分析师预期，显示AI芯片需求强劲，对台积电构成直接利好。

  来源陈述：台积电9月营收同比增长55%至5119亿新台币，第三季度总营收同比增长51%至1.49万亿新台币，高于分析师预期的1.46万亿新台币。

  来源归属：Barron's / Callum Keown

  原文引用：bumper revenue shouldn't be ignored -- and neither should memory-chip giant Samsung's ninefold increase in profit.     Demand for AI chips is relentless.     TSMC reported a 55% revenue jump to 511.9 billion New Taiwan dollars ($16 billion) in September. That takes its total third-quarter revenue to NT$1.49 trillion ($46.7 billion) -- a 51% increase year

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article@bffae749659ba001`；publication：`73bca2e8949cbbd6b5e408da05aeb449861744731e214d9583079838e031b95c`。

#### [News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章报道马斯克正与台积电讨论Terafab合作，若达成将深化制造合作，对台积电构成潜在需求利好，但尚未确定，属推测性。

  来源陈述：马斯克本周早些时候表示正与台积电讨论在Terafab上的合作，而英特尔自4月起担任该项目的开发合作伙伴。

  来源归属：Investing.com/Reuters/Bloomberg

  原文引用：notes that Tan's comments come as speculation over a potential partnership between Tesla and TSMC has raised concerns among investors. Musk said earlier this week that he was in discussions with TSMC about collaborating on Terafab, while Intel has served as a development partner for the project since April. Terafab, a joint effort involving Tesla, SpaceX,

- 文章指出马斯克尚未承诺单一制造合作伙伴，英特尔和台积电都可能参与Terafab，且部分分析师认为提及台积电是谈判筹码，因此对台积电的实际订单影响不确定。

  来源陈述：CTech指出，事态发展表明马斯克尚未承诺Terafab的单一制造合作伙伴，英特尔和台积电都可能发挥作用；Seoul Economic Daily称部分分析师认为马斯克提及台积电是谈判策略。

  来源归属：CTech/Seoul Economic Daily

  原文引用：manufacturing partner for Terafab, with both Intel and TSMC potentially playing roles. The project is expected to involve much deeper manufacturing cooperation than a traditional customer-supplier relationship. The stakes are particularly high for Intel. When the chipmaker joined Terafab as a development partner in April, the project offered CEO Lip-Bu Tan

版本：`TRENDFORCE:managed-trendforce_news-news-intel-ceo-lip-bu-tan-says-it-remains-in-musks-teraf-3133e66b25bb:research_article@840b505d5c696133`；publication：`544aeacd7a864d77a949ae6c741a25286b4f4c8a43587f9d1afb12b6cd05d784`。

#### [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead

来源：trendforce_news；完整性：full；原选择依据：target_title。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- AMD锁定台积电3-5年产能，MI450系列采用台积电2nm与CoWoS先进封装，2026下半年开始量产，2027年放量，直接利好台积电先进制程与封装产能利用率。

  来源陈述：AMD的MI450系列AI平台于2026下半年采用台积电2nm节点和CoWoS开始生产，2026年第三季度进入台积电量产，2027年放量。

  来源归属：Economic Daily News

  原文引用：its next-generation AI platforms. As noted by Economic Daily News , AMD began production of its MI450-series AI platform in the second half of 2026, using TSMC's 2nm node and CoWoS. The MI450 series entered production at TSMC in the third quarter, with capacity expected to ramp gradually by the end of 2026 before higher-volume shipments begin in 2027.

- AMD产能规划从1-2年拉长至3-5年，晶圆制造、封装、基板需同步就绪，CoWoS需求随AI芯片出货上升，台积电作为主要代工与CoWoS供应商将受益于更长的订单能见度。

  来源陈述：Su表示产能规划从一到两年延伸至三、四甚至五年，晶圆制造、封装和基板需同时就绪，AI芯片出货增加推升CoWoS需求。

  来源归属：Lisa Su (AMD CEO)

  原文引用：rapid growth is also changing how far ahead AMD plans with its partners. Su said capacity planning that once looked one to two years ahead now extends three, four, or even five years, with wafer fabrication, packaging, and substrates all needing to be ready at the same time. Advanced packaging is a key part of those plans. Su said rising AI chip shipments

- AMD确认台积电亚利桑那新厂进展良好，并期待全球产能扩张以满足需求，显示台积电海外扩产获得客户认可。

  来源陈述：Su表示台积电亚利桑那新厂进展非常好，AMD期待看到更多全球产能扩张以满足强劲市场需求。

  来源归属：Lisa Su (AMD CEO)

  原文引用：is progressing very well and that AMD looks forward to seeing more capacity expand globally to meet strong market demand. The ramp-up is also expected to drive greater demand across AMD's Taiwan AI server supply chain. Foxconn is among the Taiwanese suppliers positioned to benefit from AMD's expansion. Economic Daily News notes that the company is one of

版本：`TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article@c610ff16bd99e252`；publication：`7b4b364cfe2d93b0cb16213216f866072a5c89619a7bb45237e58d661624edb4`。

#### Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk

来源：ibkr_news；完整性：full；原选择依据：target_visible_body。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 文章指出TSMC作为AI价值链关键供应商，其先进节点需求和产能利用率评论将作为半导体资本开支计划的早期指标，市场将密切关注其3Q财报指引。

  来源陈述：TSMC's commentary on advanced-node demand and capacity utilization will be closely watched as indicators of semiconductor capex plans.

  来源归属：James Ooi at Tiger Brokers

  原文引用：outlook, will be closely watched as indicators of semiconductor capex plans, he adds. (monica.gupta@wsj.com)         (END) Dow Jones Newswires     October 11, 2026 22:44 ET (02:44 GMT)     Copyright (c) 2026 Dow Jones & Company, Inc.      The statements in this document shall not be considered as an objective or independent explanation of the matters.

版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article@584b37cb51b3cc69`；publication：`bf757b2fdf3b9144110422ee213c070e5d81dc7ffabd268e06ff2046b2f2075d`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；完整性：full；原选择依据：publisher_exploration。

基础检查通过依据：在冻结快照中存在准入版本；publication与载荷一致；可见输入相符；返回的引用能解析到原文位置；实体输出在现有scope内。发布类别和关联观点不作为拒绝原因。

以下是旧模型候选摘要，保留供分析师阅读，未重新生成或人工核签：

- 三星退出汽车AP业务并聚焦移动AP和图像传感器，其Exynos 2700将在三星自家2nm工艺量产，可能减少对台积电等外部代工的依赖，但三星图像传感器在德州奥斯汀自家晶圆厂量产，对台积电影响有限。

  来源陈述：三星计划退出汽车AP业务，将System LSI资源转向图像传感器和下一代移动AP，Exynos 2700将在三星Foundry第二代2nm工艺上量产。

  来源归属：Munhwa Ilbo / Seoul Economic Daily

  原文引用：expand its adoption in the Galaxy S27 series due in 2027. Seoul Economic Daily notes that the chip will be produced on Samsung Foundry's second-generation 2nm process. Greater use of in-house APs could help improve foundry utilization while reducing reliance on Qualcomm and MediaTek chips. Samsung spent KRW 7.44 trillion on mobile APs from external

版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`；publication：`9cd0a00a909835eb3c23803707752eb9892d7ae7cc3dff8c356cfb5d74caaac6`。

## 证据与限制

[重审JSON](../../tmp/information_quality_closeout_20261013/material_policy_review_final/combined_review.json)，旧报告[完整32份严格契约结果](INFORMATION_SOURCE_SELECTION_FULL_SELECTED_RESULTS.md)保留为历史。此结果是新规则对旧输出的复核，不是新提示词的真实模型验证或正式Brief/Routine验收。

任务仍在当前开发工作区，不修改OpenSpec完成状态。生产元数据71篇和389条历史一致性证据保持原结果；未再次写生产库。


验证记录：uv入口核心范围首轮94项通过；扩展范围133项通过、1项元数据重复修订失败。原因是修订器硬编码旧完整性策略版本，已改为保留受支持策略的原始baseline。修复后visibility_revisions与output_quality共41项全部通过（102秒），覆盖原失败、短partial过滤、来源类别/关联元数据、采购与交易指令边界。编译和diff检查通过。测试组有交集，不累加为独立用例总数。未再次执行生产元数据修订。


漏斗展示补充：“模型返回空”是互斥结果分支，不是把目标摘要进一步过滤。MRVL三星探索新闻无目标摘要可以接受；NVDA的微软RTX Spark报道正文直接涉及NVIDIA，空输出属于漏提取。旧响应尚未重新生成；详见 [详细漏斗与空结果实例](INFORMATION_SOURCE_SELECTION_DETAILED_FUNNEL.md)。
