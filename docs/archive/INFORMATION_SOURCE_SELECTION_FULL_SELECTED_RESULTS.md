# Information：补齐每标的8篇后的过滤与采纳结果

**当前准入职责已按用户裁决修正，最新结果见 [材料准入重审](INFORMATION_SOURCE_SELECTION_MATERIAL_POLICY_REVIEW.md)。下文保留旧契约历史，不作为当前可用信息计数。**

冻结时点：2026-10-12T10:27:57.770290+00:00；七天窗口从2026-10-05T10:27:57.770290+00:00起。选择预算保持8，严格拒绝标准与同一抽取契约保持不变。

原8次结果保留，仅新增24次未尝试输入；总计32个目标/文档输入对。零重试，没有新数据采集、生产Brief发布或订单。

|阶段|MRVL|MSFT|NVDA|TSM|
|---|---:|---:|---:|---:|
|已准入材料跨通道合并去重|128|120|136|134|
|发布时间明确|124|116|132|130|
|七天窗口内|38|34|39|38|
|可见内容满足候选条件|38|34|39|38|
|关联或有限publisher探索候选|38|34|39|38|
|去重与覆盖调度后预算选中|8|8|8|8|
|实际模型尝试|8|8|8|8|
|输出结构通过|8|8|7|7|
|内容契约通过（含合法空）|1|0|1|0|
|内容契约通过且非空|0|0|0|0|
|通过且有目标专属洞察|0|0|0|0|
|最终可采纳材料（未发布）|0|0|0|0|

候选预算未选中分别为30/26/31/30篇，不是内容拒绝。通道读取有重叠，已合并去重。本轮补测后每标的选中8篇均有一次尝试，失败没有补发。旧64次诊断不升级为本契约采纳结果。

## 增加核验覆盖的效果

|标的|原2篇：契约通过/非空|完整8篇：契约通过/目标非空|
|---|---|---|
|MRVL|1/0|1/0|
|MSFT|0/0|0/0|
|NVDA|0/0|1/0|
|TSM|0/0|0/0|

## 结论与首个拒绝原因

补齐24份后，四标的最终仍为0篇。已排除“只抽样2篇导致看不到剩余6篇”这一覆盖缺口；在当前抽取与整篇拒绝契约下，增加核验覆盖没有带来有效采纳。不能据此证明原文没有投资价值，因为内容多在证据/分类等前置检查退出，未到达最终材料性审阅。

30份结构有效输出中，2份合法空，28份内容拒绝。首个拒绝原因如下，同一篇其他潜在问题不重复计数：

- 禁建议检查：1篇。
- 来源分类不符：13篇。
- 关联路径不符合静态声明：4篇。
- 预测归属不符：2篇。
- 直接事实缺目标证据：7篇。
- 原文片段ID不存在：1篇。

目前不支持直接扩大选择预算8→16。应先决定如何使抽取按现有严格契约稳定产出有据结论，而非降低采纳标准或继续扩大数量。此次没有修改源分类/引用/路径/整篇拒绝规则。执行器仅提前保存usage及finish_reason/tool_call_count，响应失败仍拒绝；8项预算/失败/续跑回归通过。

## 最终可采纳来源、标题、摘要及通过原因

### MRVL

无最终采纳材料。

### MSFT

无最终采纳材料。

### NVDA

无最终采纳材料。

### TSM

无最终采纳材料。

## 逐篇检查结果与未采纳输出

拒绝项摘要仅用于诊断；不构成采纳内容。文档因任一洞察违反当前契约而整篇拒绝的，仍按现行严格规则处理，不在此轮挑出其中好条目改写计数。

### MRVL

#### VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：信息简报不得包含交易建议：命中中文建议词 '卖出'

- 模型摘要：公司多位高管（包括总裁、董事长、CFO、VP）在2026年4月至10月期间持续通过10b5-1计划及直接交易卖出股票，最新一笔为VP Scarpulla于10/8/26以273.59美元卖出1,100股。这属于历史内幕交易事实，反映内部人持续减持，但多为计划性卖出，对基本面影响有限。

  来源陈述：VP Scarpulla于2026年10月8日以273.59美元卖出1,100股MRVL股票，直接持有降至909股；历史记录显示总裁、董事长、CFO等多位高管在2026年4月至10月期间多次卖出股票。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f878d55-2699cdb0a600:research_article@121a5d7af94a9bb1`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；full；检查状态：evidence_valid_pending_human_content_review。

合法空结果，无可采纳洞察。

文档版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`。

#### TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information inference escapes declared relation path: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article

- 模型摘要：文章报道Marvell股价下跌超过1%，主要受债券收益率上升和油价上涨等宏观因素拖累，而非基本面恶化；同时台积电强劲营收表明AI芯片需求持续，作为台积电下游客户的Marvell可能间接受益于AI需求旺盛。

  来源陈述：Advanced Micro Devices, Broadcom and Marvell were down more than 1%, while Intel fell around 2%. U.S. tech stocks were not taking their cue from TSMC or Samsung, though, but from higher bond yields and oil prices.

其他实体洞察：{'TSM': 2, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article@bffae749659ba001`。

#### Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article

- 模型摘要：文章强调TSMC先进节点需求和产能利用率评论是半导体资本开支的指标，作为TSMC下游客户的MRVL，其AI相关需求强度可从TSMC产能信号中间接推断，但属多跳推断。

  来源陈述：TSMC对先进节点需求和产能利用率的评论将作为半导体资本开支计划的指标被密切关注，其财报提供AI投资是否保持完好的早期信号。

其他实体洞察：{'TSM': 1, 'NVDA': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article@584b37cb51b3cc69`。

#### [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article

- 模型摘要：AI系统采用chiplet 2.5D封装、interposer面积大幅扩大，反映定制AI芯片（如Marvell的定制ASIC）对先进封装与更大interposer的依赖增强，间接利好其定制计算业务。

  来源陈述：报道指出先进AI系统在interposer上组合多个计算chiplet与HBM堆叠，以在单一封装中集成更多计算与内存。

其他实体洞察：{'TSM': 1, 'NVDA': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article@4f09ed22d73026ef`。

#### The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article

- 模型摘要：文章强调AI交易和科技资本开支变现能力是市场核心驱动力，MRVL作为AI/数据中心芯片供应商，其需求与AI资本开支周期高度相关，但文章未直接提及MRVL，属间接推断。

  来源陈述：文章引用策略师观点称AI和科技公司变现资本开支的能力以及AI交易的稳定性将决定牛市能否延续。

其他实体洞察：{'NVDA': 2, 'AMZN': 1, 'MSFT': 1, 'TSM': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article@4b65c379c3719d90`。

#### [News] nvidias nvhbm reshapes memory makers base die strategies as micron samsung turn to external foundries

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-nvidias-nvhbm-reshapes-memory-makers-base-die-strat-0319f162c876:research_article

没有目标专属洞察。

其他实体洞察：{'TSM': 3}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-nvidias-nvhbm-reshapes-memory-makers-base-die-strat-0319f162c876:research_article@3f32bbe1e1aa7959`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；partial；检查状态：content_contract_failed。

拒绝原因：Information inference escapes declared relation path: SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article

- 模型摘要：文章核心是地缘政治与AI安全政策分析，未涉及MRVL或其供应链的具体业务、产品需求或竞争动态。文章关于中国AI发展速度的宏观描述不足以建立对MRVL的可靠信号链推断。

  来源陈述：文章主要讨论中国AI安全监管体制、前沿实验室发布行为及中美AI竞争，未提及MRVL相关业务。

其他实体洞察：{'NVDA': 1, 'TSM': 1}；共享本篇来源，不算独立来源。

文档版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`。

### MSFT

#### Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f864502-5b54e9354f7a:research_article

- 模型摘要：文章报道美国副总统万斯宣布将微软等公司从允许H-1B签证持有者申请绿卡的项目中暂停，指控微软滥用签证系统。这对微软构成直接监管/运营风险，可能影响其招聘外国高技能人才的能力，但文章同时指出政府表示不想伤害公司，仅想传递信号。

  来源陈述：副总统万斯宣布暂停微软等科技公司参与允许H-1B签证持有者申请绿卡的项目，指控微软滥用签证系统，称微软每裁员一名美国工人就替换为1.5名外国工人。

- 模型摘要：文章引用微软发言人回应称公司计划向政府提供更多信息，并称其提交的约6000份H-1B申请中80%是为现有员工提交的。这是微软对监管指控的自我辩护，属于公司自述，不能视为已证实的事实。

  来源陈述：微软发言人表示公司计划向政府发送更多信息，称其美国员工大部分是美国公民，且上一财年提交的约6000份H-1B申请中80%是为现有员工提交的。

- 模型摘要：文章报道万斯表示政府不想伤害微软，但将拒绝其申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。这暗示监管行动可能有限度，但持续施压仍可能影响微软的人才获取和运营成本。

  来源陈述：万斯表示政府将继续支持微软并保持良好关系，但将拒绝微软申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。

其他实体洞察：{'META': 1, 'NVDA': 1, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f864502-5b54e9354f7a:research_article@aadce4b4f0da24f3`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；partial；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article

- 模型摘要：中国开源模型的价格战正在挤压Anthropic等前沿实验室的经济性，迫使它们加速发布新模型。对微软而言，其投资的OpenAI/Anthropic生态面临来自中国低成本开源模型的竞争压力，可能削弱微软AI产品（如Copilot、Azure OpenAI服务）的定价能力和差异化优势。

  来源陈述：Anthropic was reported to be weighing a new model before its IPO, with cheap open-weight rivals 'especially in China' squeezing the economics, and shipped Claude Opus 5.5, its first release since calling for pacing the frontier, framing pacing as a way of 'remaining competitive with China.'

其他实体洞察：{'AMZN': 1, 'GOOG': 1, 'META': 1, 'NVDA': 1, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`。

#### [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-microsoft-expands-local-ai-push-with-nvidia-rtx-spa-269045ffa982:research_article

- 模型摘要：微软推出搭载NVIDIA RTX Spark的Surface Laptop Ultra，将本地AI能力推向PC，强化Windows作为AI代理平台的地位，对微软自身AI产品线构成直接利好。

  来源陈述：微软在活动中推出了搭载NVIDIA RTX Spark的Surface Laptop Ultra，定价从2599美元起，最高5899美元。

- 模型摘要：微软推出RTX Spark驱动的Surface Laptop Ultra，定价最高达5,899美元，与苹果MacBook Pro形成竞争。

  来源陈述：Reuters notes that Apple's MacBook Pro with a 40-core graphics chip, 128 GB of memory, and 2 TB of storage costs $6,700, although the two machines are not directly comparable.

其他实体洞察：{'NVDA': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-microsoft-expands-local-ai-push-with-nvidia-rtx-spa-269045ffa982:research_article@4a9cc17a39bf92f4`。

#### The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article

- 模型摘要：文章指出微软是过去两个月贡献标普500约90%涨幅的少数几只股票之一，且科技/AI板块盈利增长强劲（Q2标普盈利增长35.1%），但文章同时强调AI资本开支能否变现、利率和估值方向是牛市能否延续的关键，对微软构成中性偏正面的背景信息。

  来源陈述：文章指出微软是过去两个月贡献标普500约90%涨幅的少数几只股票之一（与Meta、Alphabet、Apple、Nvidia并列），并强调AI和科技公司能否变现资本开支是牛市能否延续的核心。

其他实体洞察：{'META': 1, 'GOOG': 1, 'NVDA': 1, 'AMZN': 1, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88ff3f-4805f491ac34:research_article@4b65c379c3719d90`。

#### [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article

- 模型摘要：AMD扩大AI服务器产能并锁定供应链，Foxconn为AMD主要AI服务器供应商且为微软等云服务商组装服务器，AMD产能扩张可能增加对微软的AI服务器供给，利好微软AI基础设施部署。

  来源陈述：Foxconn是AMD主要AI服务器供应商之一，其业务涵盖基板、散热模块到为包括微软和Oracle在内的云服务商组装完整AI服务器。

其他实体洞察：{'NVDA': 2, 'AVGO': 1, 'AMZN': 1, 'META': 1, 'GOOG': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article@c610ff16bd99e252`。

#### Four Years In, $40 Trillion Up: Why This AI-Fueled Bull Market Has Room to Run -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f885259-3484c501d2e9:research_article

- 模型摘要：文章指出微软对OpenAI的投资（OpenAI估值超1.4万亿美元并计划明年上市）是这轮AI牛市早期阶段的催化剂，这直接利好微软作为OpenAI主要投资者的资产价值与AI战略地位。

  来源陈述：微软对OpenAI的投资——基于OpenAI计划明年上市，其估值已超过1.4万亿美元——点燃了牛市初期的涨势。

- 模型摘要：文章指出微软对OpenAI的投资是这轮AI牛市早期阶段的催化剂，OpenAI估值超1.4万亿美元并计划明年上市，微软作为主要投资者将受益于OpenAI估值上升带来的资产增值。

  来源陈述：微软对OpenAI的投资——基于OpenAI计划明年上市，其估值已超过1.4万亿美元——点燃了牛市初期的涨势。

其他实体洞察：{'NVDA': 3, 'GOOG': 1, 'AVGO': 1, 'AMZN': 1, 'META': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f885259-3484c501d2e9:research_article@feb63d60b34870e6`。

#### [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article

没有目标专属洞察。

其他实体洞察：{'NVDA': 2, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-ai-interposers-grow-larger-raising-hbm4e-cooling-ch-c5b23d4a227b:research_article@4f09ed22d73026ef`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article

没有目标专属洞察。

其他实体洞察：{'NVDA': 1, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`。

### NVDA

#### Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD

来源：ibkr_news；full；检查状态：failed_no_retry。

工具响应或结构失败，保持拒绝，不重试。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f883520-7db846a53c29:research_article@357638c4867df6c9`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information inference escapes declared relation path: TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article

- 模型摘要：三星计划扩展数据中心芯片设计业务，可能在未来增加数据中心AI芯片领域的竞争，对英伟达构成潜在竞争压力。

  来源陈述：三星计划扩展包括显示驱动IC、电源管理IC以及数据中心芯片设计在内的业务。

其他实体洞察：{'TSM': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`。

#### [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise

来源：trendforce_news；full；检查状态：evidence_valid_pending_human_content_review。

合法空结果，无可采纳洞察。

文档版本：`TRENDFORCE:managed-trendforce_news-news-microsoft-expands-local-ai-push-with-nvidia-rtx-spa-269045ffa982:research_article@4a9cc17a39bf92f4`。

#### Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information has unlocated evidence span: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88687f-310c9002144e:research_article

- 模型摘要：文章指出NVDA股价上周下跌2%，接近234.76买点，且TSMC和ASML财报可能左右NVDA股价。作为下游客户，台积电和ASML的财报指引将影响市场对NVDA AI芯片需求与产能的预期。

  来源陈述：Nvidia stock fell 2% to 229.28 for the week after hitting a record 243.37 intraday Tuesday. NVDA stock is just below a 234.76 cup-with-handle buy point. The TSMC and ASML earnings reports could swing Nvidia stock.

- 模型摘要：文章指出芯片和AI硬件上周表现艰难，周四因OpenAI消息引发AI需求担忧而大幅下跌。作为AI芯片核心供应商，AI需求担忧情绪对NVDA构成短期压力。

  来源陈述：Chips and AI hardware had a tough week, with hefty losses Thursday as OpenAI news revived AI demand fears.

- 模型摘要：文章指出Lumentum CEO表示光学产品在巨大AI需求下已售罄至2029年，反映AI基础设施需求强劲。作为AI芯片供应商，NVDA受益于AI硬件需求的持续旺盛。

  来源陈述：The CEO said the optical play is sold out into 2029 amid huge AI demand.

其他实体洞察：{'TSM': 2, 'ASML': 2}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f88687f-310c9002144e:research_article@3119269c40355908`。

#### [News] huawei says ascend market share has surpassed nvidia in china backs npo over cpo for optical interconnects

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information prediction requires forecast attribution: TRENDFORCE:managed-trendforce_news-news-huawei-says-ascend-market-share-has-surpassed-nvidi-aaac1643c5f2:research_article

- 模型摘要：华为称Ascend在中国市场已超越NVIDIA，且中国2026年国产AI芯片将占据高端市场近90%份额，对NVIDIA在中国的高端AI芯片业务构成直接竞争压力。

  来源陈述：华为轮值董事长徐直军称基于华为可追踪的数据，Ascend在中国已可能超越NVIDIA；TrendForce预计2026年国产AI芯片将占据中国高端AI芯片市场近90%份额。

- 模型摘要：华为Ascend 950PR已限量供货、950DT SuperPoD预计2026年底至2027年初放量，且华为产能全部售罄，显示国产AI芯片供给持续扩张，对NVIDIA在中国市场份额构成持续侵蚀。

  来源陈述：Ascend 950PR已限量供货，950DT SuperPoD仍在测试，预计2026年底或2027年初放量；华为表示产能全部售罄并希望供应链加速扩张。

- 模型摘要：华为选择NPO而非CPO路线并宣称成本降低近40%，其光学互连技术路线选择反映国产AI系统在互连层面形成差异化竞争，但该信息对NVIDIA的直接竞争影响有限且属技术路线层面。

  来源陈述：华为称早期决定采用NPO而非CPO，认为NPO在工程实现、成本、维护和良率上更优，可降低成本近40%。

其他实体洞察：{'TSM': 1, 'MU': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-huawei-says-ascend-market-share-has-surpassed-nvidi-aaac1643c5f2:research_article@e78b4821c48d9f4a`。

#### Nvidia to Invest $1B in U.S. Science Over Next 5 Years

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f8430e9-f485ed2ab427:research_article

- 模型摘要：英伟达宣布未来五年投入10亿美元支持美国科研机构及云服务商，属于公司自身战略投资公告，对NVDA本身无直接财务或需求层面的实质影响，更多是品牌与生态建设举措。

  来源陈述：英伟达宣布未来五年投入10亿美元，向高等教育研究机构提供计算和超级智能基础设施资源，并支持云服务商强化美国政府需求。

其他实体洞察：{'MSFT': 1, 'AMZN': 1, 'GOOG': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f8430e9-f485ed2ab427:research_article@fb05e832634539f1`。

#### SpaceX Stock Falls as Musk Doubles Down on Nvidia. Why It's a Big Deal. -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f818180-1fbf42af2288:research_article

- 模型摘要：SpaceX计划融资400亿美元购买Nvidia芯片，且xAI业务计划年底前在孟菲斯Colossus数据中心部署多达144万颗Nvidia芯片，直接利好Nvidia的AI芯片需求。

  来源陈述：SpaceX is aiming to raise $40 billion in funding to buy Nvidia chips, and Musk set out plans for SpaceX's xAI business to have as many as 1.44 million Nvidia chips at its Memphis Colossus data-center complex before the end of the year.

- 模型摘要：SpaceX的AI业务预计2027年营收达600亿美元（高于7月估计的380亿），2031年AI相关营收预计达5300亿美元，表明大规模AI算力采购将持续，利好Nvidia。

  来源陈述：SpaceX's AI business is now expected to generate $60 billion in 2027 revenue, up from a July estimate of $38 billion. By 2031, Wall Street projects $530 billion in AI-related revenue.

其他实体洞察：{'TSM': 1, 'ASML': 1, 'MU': 1, 'SKHY': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f818180-1fbf42af2288:research_article@b632f8f32d2c4aa5`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；partial；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article

- 模型摘要：文章指出中国AI前沿实验室处于发布和价格战状态，加速开发而非减速，且中国监管层优先发展而非安全。中国持续快速部署AI（AI+行动计划2027年70%渗透率）意味着对AI算力需求持续增长，这对NVDA的出口受限市场之外的全球需求信号是间接的。但文章同时强调美国出口管制是中国的供应侧风险，中国加速开发可能更多依赖国产替代而非NVDA芯片，因此对NVDA的净影响偏中性。

  来源陈述：中国前沿实验室处于发布和价格战状态，无人要求减速；中国AI+行动计划目标2027年70%渗透率、2030年90%，追求AI在各行业快速部署。

- 模型摘要：文章指出中国将出口管制视为供应链安全风险，认为美国通过技术垄断和出口管制'恶意切断全球AI供应链'。中国加速自研替代的动机增强，长期可能削弱NVDA对华出口市场，但这是间接推断。

  来源陈述：Framework 3.0将出口管制列为供应链安全风险，警告'某些国家利用技术垄断和单边强制措施如出口管制设置发展壁垒并恶意切断全球AI供应链'。

其他实体洞察：{'TSM': 1, 'META': 1, 'GOOG': 1, 'MSFT': 1}；共享本篇来源，不算独立来源。

文档版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`。

### TSM

#### TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information prediction requires forecast attribution: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f855c03-cef32fdea955:research_article

- 模型摘要：伯恩斯坦预测台积电今年美元营收将增长41%，且2027-2028年30%的年增长率存在上行风险，3季度营收比市场共识高3%，对台积电为利好信号。

  来源陈述：伯恩斯坦预计台积电今年美元营收将增长41%，并认为其2027-2028年30%的年营收增长预测存在上行风险，3季度营收比市场共识高3%。

- 模型摘要：文章提到投资者将关注台积电2纳米产量提升及美国进一步扩张，这些是台积电未来增长的关键驱动因素，属直接相关事实。

  来源陈述：分析师称投资者将关注台积电2纳米产量提升、潜在的美国进一步扩张以及2027年展望。

其他实体洞察：{'ASML': 1, 'AMAT': 1, 'NVDA': 1, 'AMD': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f855c03-cef32fdea955:research_article@11e02b38f83b3925`。

#### Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime

来源：semianalysis；partial；检查状态：failed_no_retry。

工具响应或结构失败，保持拒绝，不重试。

文档版本：`SEMIANALYSIS:managed-semianalysis-imap-20261008174605.3.4628be90da0302a5-mg-d1.substack.com-9122fcebcfeb:research_article@bf5d402a0e91005c`。

#### [News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-tsmc-q3-revenue-beats-forecasts-at-record-nt1-49-tr-5387da44b1d8:research_article

- 模型摘要：台积电9月及Q3营收超预期创纪录，AI需求与智能手机备货推动增长，Q3净利预计同比大增64%，Q4有望继续受NVIDIA Vera Rubin平台及2nm制程需求推动。

  来源陈述：台积电9月营收5118.6亿新台币，连续第二个月突破5000亿，Q3营收创纪录达1.49万亿新台币（467.1亿美元），同比增50%，超市场预期；LSEG分析师预计Q3净利同比增64%至7408亿新台币。

- 模型摘要：台积电Q3营收超过自身7月电话会议给出的指引区间上限，毛利率与营业利润率指引低于Q2实际值，但营收动能强劲。

  来源陈述：台积电7月电话会议给出Q3营收指引为446亿至458亿美元，毛利率65%-67%，营业利润率56%-58%，均低于Q2的67.7%和60.3%。

- 模型摘要：分析师预计Q4营收将进一步攀升，受NVIDIA Vera Rubin平台及台积电2nm制程需求推动，显示先进制程需求持续强劲。

  来源陈述：中央社引述分析师预计Q4营收将进一步攀升，受NVIDIA Vera Rubin平台和台积电2nm制程技术需求推动。

其他实体洞察：{'NVDA': 2, 'ASML': 1, 'AMAT': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-tsmc-q3-revenue-beats-forecasts-at-record-nt1-49-tr-5387da44b1d8:research_article@5178d7d63d9abf53`。

#### TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information inference escapes declared relation path: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article

- 模型摘要：台积电9月营收同比增长55%，第三季度总营收同比增长51%且超出分析师预期，显示AI芯片需求强劲，对台积电构成直接利好。

  来源陈述：台积电9月营收同比增长55%至5119亿新台币，第三季度总营收同比增长51%至1.49万亿新台币，高于分析师预期的1.46万亿新台币。

其他实体洞察：{'NVDA': 1, 'AMD': 1, 'AVGO': 1, 'SKHY': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f83824d-6d4dd398bd6a:research_article@bffae749659ba001`。

#### [News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-intel-ceo-lip-bu-tan-says-it-remains-in-musks-teraf-3133e66b25bb:research_article

- 模型摘要：文章报道马斯克正与台积电讨论Terafab合作，若达成将深化制造合作，对台积电构成潜在需求利好，但尚未确定，属推测性。

  来源陈述：马斯克本周早些时候表示正与台积电讨论在Terafab上的合作，而英特尔自4月起担任该项目的开发合作伙伴。

- 模型摘要：文章指出马斯克尚未承诺单一制造合作伙伴，英特尔和台积电都可能参与Terafab，且部分分析师认为提及台积电是谈判筹码，因此对台积电的实际订单影响不确定。

  来源陈述：CTech指出，事态发展表明马斯克尚未承诺Terafab的单一制造合作伙伴，英特尔和台积电都可能发挥作用；Seoul Economic Daily称部分分析师认为马斯克提及台积电是谈判策略。

其他实体洞察：{'NVDA': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-intel-ceo-lip-bu-tan-says-it-remains-in-musks-teraf-3133e66b25bb:research_article@840b505d5c696133`。

#### [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information attributed statement cannot be promoted to reported fact: TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article

- 模型摘要：AMD锁定台积电3-5年产能，MI450系列采用台积电2nm与CoWoS先进封装，2026下半年开始量产，2027年放量，直接利好台积电先进制程与封装产能利用率。

  来源陈述：AMD的MI450系列AI平台于2026下半年采用台积电2nm节点和CoWoS开始生产，2026年第三季度进入台积电量产，2027年放量。

- 模型摘要：AMD产能规划从1-2年拉长至3-5年，晶圆制造、封装、基板需同步就绪，CoWoS需求随AI芯片出货上升，台积电作为主要代工与CoWoS供应商将受益于更长的订单能见度。

  来源陈述：Su表示产能规划从一到两年延伸至三、四甚至五年，晶圆制造、封装和基板需同时就绪，AI芯片出货增加推升CoWoS需求。

- 模型摘要：AMD确认台积电亚利桑那新厂进展良好，并期待全球产能扩张以满足需求，显示台积电海外扩产获得客户认可。

  来源陈述：Su表示台积电亚利桑那新厂进展非常好，AMD期待看到更多全球产能扩张以满足强劲市场需求。

其他实体洞察：{'NVDA': 1, 'ASML': 1, 'AMAT': 1, 'SKHY': 1, 'MU': 1, 'AMD': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-amds-lisa-su-visits-tsmc-ai-supply-chain-partners-a-7cf7d5b8da9e:research_article@c610ff16bd99e252`。

#### Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk

来源：ibkr_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article

- 模型摘要：文章指出TSMC作为AI价值链关键供应商，其先进节点需求和产能利用率评论将作为半导体资本开支计划的早期指标，市场将密切关注其3Q财报指引。

  来源陈述：TSMC's commentary on advanced-node demand and capacity utilization will be closely watched as indicators of semiconductor capex plans.

其他实体洞察：{'ASML': 1, 'NVDA': 1, 'AMD': 1, 'AVGO': 1}；共享本篇来源，不算独立来源。

文档版本：`DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f889383-3cd843f9a782:research_article@584b37cb51b3cc69`。

#### [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround

来源：trendforce_news；full；检查状态：content_contract_failed。

拒绝原因：Information direct fact has no issuer evidence: TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article

- 模型摘要：三星退出汽车AP业务并聚焦移动AP和图像传感器，其Exynos 2700将在三星自家2nm工艺量产，可能减少对台积电等外部代工的依赖，但三星图像传感器在德州奥斯汀自家晶圆厂量产，对台积电影响有限。

  来源陈述：三星计划退出汽车AP业务，将System LSI资源转向图像传感器和下一代移动AP，Exynos 2700将在三星Foundry第二代2nm工艺上量产。

其他实体洞察：{'NVDA': 1, 'AAPL': 1, 'SKHY': 1, 'MU': 1}；共享本篇来源，不算独立来源。

文档版本：`TRENDFORCE:managed-trendforce_news-news-samsung-reportedly-plans-exynos-auto-exit-prioritiz-66e7e13ad8b2:research_article@e4284cf0c98edc5c`。

## 费用、执行和证据

有usage的已知token：{'prompt_tokens': 102987, 'completion_tokens': 28583, 'total_tokens': 131570}；供应商费用已知小计$0.017659180；1次费用未知。未知包含原8次的一次响应失败，本轮不能恢复丢失的usage。不是账户账单核对。

[全32份审阅JSON](../../tmp/information_quality_closeout_20261013/full_selected_review.json)、[剩余24份输入和预算](../../tmp/information_quality_closeout_20261013/remaining_review.md)、[24次执行账本](../../tmp/information_quality_closeout_20261013/remaining_execution_ledger.json)。

本报告是有限模型内容实验，不代替生产Brief/缓存/Routine及Phase F资格验证。


补充：已按原规则对135条洞察分别作离线诊断。12份整篇拒绝的首个失败来自其他实体；9份含至少一条技术通过、5份含目标专属技术通过。仅用于解释整篇拒绝的影响，不变更0篇采纳结论，详见 [契约归因报告](INFORMATION_SOURCE_SELECTION_CONTRACT_DIAGNOSIS.md)。
