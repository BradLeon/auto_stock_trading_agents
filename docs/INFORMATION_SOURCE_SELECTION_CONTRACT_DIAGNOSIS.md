# 内容契约低通过率：逐洞察归因

只对原32份输出做离线拆解，不调用模型、不改变整篇拒绝规则或最终采纳状态。当前最终采纳仍为0。

30份结构有效中：2份合法空；28份非空整篇拒绝。首个拒绝原因：来源分类13、预测归属2、直接事实缺目标证据7、关联路径4、无效原文片段ID1、禁建议检查1。首个失败计数不代表每篇只有一个问题。

按原有规则分别检查单条洞察：135条中18条技术检查通过；28份被拒文章中9份有至少一条技术通过，5份有目标专属技术通过。12份的首个失败来自其他实体。技术通过仍不证明摘要完整被支持、事件真实或投资价值合格，不能把这些条目计为采纳。

根因是模型一次扩展多个实体/推断，而校验对任意一条失败整篇拒绝；来源分类还按整个片段的词汇检查。严格拒绝不等于新闻没有投资价值。本轮不改规则、不重新计入采纳。

## 被整篇拒绝、但存在单条技术通过的目标洞察

### MSFT · Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update

整篇首个失败实体：MSFT。以下只是定位检查通过的诊断条目，未采纳。

- 来源陈述：微软发言人表示公司计划向政府发送更多信息，称其美国员工大部分是美国公民，且上一财年提交的约6000份H-1B申请中80%是为现有员工提交的。

  分类：claim

  原文片段：plans to send the administration more information. She said most of its employees in the U.S. are American citizens and that of the approximately 6,000 H-1B visa applications it submitted in the last fiscal year, 80% were for existing Microsoft employees.     "Microsoft only files H-1B petitions for those who meet the rigorous standards of this visa

- 来源陈述：万斯表示政府将继续支持微软并保持良好关系，但将拒绝微软申请永久居留权的能力，直到公司认真对待优先雇佣美国工人。

  分类：claim

  原文片段：didn't want to harm the company but send them a message that they ought to hire Americans over foreign workers.     "So we're going to continue to support Microsoft and have a great relationship with Microsoft, but we're also going to deny them the ability to apply for these permanent residencies until they show that they are going to get serious about

### TSM · TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk

整篇首个失败实体：NVDA。以下只是定位检查通过的诊断条目，未采纳。

- 来源陈述：伯恩斯坦预计台积电今年美元营收将增长41%，并认为其2027-2028年30%的年营收增长预测存在上行风险，3季度营收比市场共识高3%。

  分类：forecast

  原文片段：in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

- 来源陈述：分析师称投资者将关注台积电2纳米产量提升、潜在的美国进一步扩张以及2027年展望。

  分类：claim

  原文片段：in U.S. dollar terms to increase 41% this year. Investors will be focusing on TSMC's 2-nanometer production increase, potential further expansion in the U.S. and its 2027 outlook when the chip maker announces its full 3Q earnings Thursday, the analysts say. Bernstein sees an upside risk to its current projection that TSMC's revenue will grow 30% annually in

### MRVL · Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk

整篇首个失败实体：TSM。以下只是定位检查通过的诊断条目，未采纳。

- 来源陈述：TSMC对先进节点需求和产能利用率的评论将作为半导体资本开支计划的指标被密切关注，其财报提供AI投资是否保持完好的早期信号。

  分类：claim

  原文片段：sustained into 2027," he says. Key will be major U.S. banks, which start the 3Q earnings season this week. Earnings of TSMC and ASML, key suppliers in the AI value chain, will also offer an early read on whether AI-related investment remains intact, he says in an email. TSMC's commentary on advanced-node demand and capacity utilization, and ASML's order

### TSM · [News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64

整篇首个失败实体：TSM。以下只是定位检查通过的诊断条目，未采纳。

- 来源陈述：台积电7月电话会议给出Q3营收指引为446亿至458亿美元，毛利率65%-67%，营业利润率56%-58%，均低于Q2的67.7%和60.3%。

  分类：reported_fact

  原文片段：July earnings call. As highlighted by Anue , the foundry giant had projected third-quarter revenue of US$44.6 billion to US$45.8 billion, implying 12% sequential growth and a 37% year-over-year increase at the midpoint. Based on an assumed exchange rate of NT$32 per U.S. dollar, that reportedly translated into revenue of roughly NT$1.427 trillion to

### TSM · TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com

整篇首个失败实体：AMD。以下只是定位检查通过的诊断条目，未采纳。

- 来源陈述：台积电9月营收同比增长55%至5119亿新台币，第三季度总营收同比增长51%至1.49万亿新台币，高于分析师预期的1.46万亿新台币。

  分类：reported_fact

  原文片段：bumper revenue shouldn't be ignored -- and neither should memory-chip giant Samsung's ninefold increase in profit.     Demand for AI chips is relentless.     TSMC reported a 55% revenue jump to 511.9 billion New Taiwan dollars ($16 billion) in September. That takes its total third-quarter revenue to NT$1.49 trillion ($46.7 billion) -- a 51% increase year

## 证据

[逐条检查JSON](../tmp/information_quality_closeout_20261013/contract_diagnosis.json)，[原完整8篇报告](INFORMATION_SOURCE_SELECTION_FULL_SELECTED_RESULTS.md)。
