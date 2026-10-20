# Information 过滤漏斗与采纳条件

日期：2026-10-12。这里复查的是首轮真实模型实验对应的冻结快照，不是现在重新刷新后的全量库存。没有模型调用或生产写入。

**此前把每 ticker 0／0／1／1 份证据通过结果表达为全部有效信息数量不准确。** 该实验每 ticker 只定向抽取 2 篇（优先一篇直接／peer 材料，另选一篇 publisher 探索），不是对全部候选的验收，也不是代表性随机样本。原候选池中的未处理文章不能计为无效。

统一 as_of：`2026-10-12T10:27:57.770290+00:00`；七天窗口起点：`2026-10-05T10:27:57.770290+00:00`。原快照之后 TrendForce 新取得的 5 份正文不回填此时点。

## 逐标的漏斗

| 阶段／篇数 | MRVL | MSFT | NVDA | TSM |
|---|---:|---:|---:|---:|
| 目标＋静态 signal_chain 通道返回 | 100 | 86 | 116 | 114 |
| SemiAnalysis 通道返回 | 22 | 22 | 22 | 22 |
| TrendForce 通道返回 | 49 | 49 | 49 | 49 |
| 三通道返回行数合计（有重叠） | 171 | 157 | 187 | 185 |
| 跨通道重复行消除 | 43 | 37 | 51 | 51 |
| 唯一已准入文档版本 | 128 | 120 | 136 | 134 |
| 排除七天窗口之前材料 | 86 | 82 | 93 | 92 |
| 排除发布日期未知材料 | 4 | 4 | 4 | 4 |
| 时间／完整性可用并进入排序的候选 | 38 | 34 | 39 | 38 |
| 本次选择预算内选中 | 8 | 8 | 8 | 8 |
| 选择预算延期（不代表不相关） | 30 | 26 | 31 | 30 |
| 八篇中实际定向模型测试 | 2 | 2 | 2 | 2 |
| 八篇中未作模型测试 | 6 | 6 | 6 | 6 |
| 整个候选池尚未作模型测试 | 36 | 32 | 37 | 36 |
| 请求／schema 失败 | 1 | 0 | 0 | 1 |
| 模型结果 schema 可解析 | 1 | 2 | 2 | 1 |
| 可解析但内容契约失败 | 1 | 2 | 1 | 0 |
| 引用／非空摘要检查通过 | 0 | 0 | 1 | 1 |
| 本轮人工内容核签 | 0 | 0 | 0 | 0 |

计数以每个目标对应的文章版本为单位，同一文章可以属于多个标的，不能把四列相加当作全库唯一文章数。三通道各上限 120 行，本批没有触顶；目标／peer 通道允许各类准入文档，不能把所有初始行都叫作新闻。

本批时间与完整性过滤后全部进入排序；没有额外因空可见正文、来源错配或字面关联规则被排除的候选。86／82／93／92 篇历史材料是退出本次七天新消息窗口，不等于低质量。四篇未知日期是同一组 SemiAnalysis 文档在各 ticker 中重复出现，也不是 16 篇不同文档。

这不是上游供应商全集漏斗。初始行已经通过 Data Platform 准入；尚未取得正文／尚未准入的新闻不在其中。IBKR 上游复验曾取得 644 个发现结果、合并筛选为 348 候选，326 个因正文预算延期；该采集覆盖八 ticker、九天窗口，与本漏斗四个消费 scope／七天窗口不同，不能直接拼接成同一漏斗。

## 七天候选的来源分布

| 来源 | MRVL | MSFT | NVDA | TSM |
|---|---:|---:|---:|---:|
| ibkr_news | 21 | 17 | 22 | 21 |
| trendforce_news | 16 | 16 | 16 | 16 |
| semianalysis | 1 | 1 | 1 | 1 |

## 候选关联的强度

| 排序理由（每篇只取最优命中） | MRVL | MSFT | NVDA | TSM |
|---|---:|---:|---:|---:|
| 目标公司标题命中 | 1 | 2 | 13 | 6 |
| 目标公司可见正文命中 | 2 | 3 | 9 | 11 |
| 仅静态产业链实体命中 | 28 | 21 | 13 | 17 |
| 仅 publisher 探索 | 7 | 8 | 4 | 4 |

本批没有单独按副标题／导语归类的优先命中。直接提及不等于重要，产业链提及也不等于已确认该 ticker 受益／受损。

## 已选择八篇与定向测试结果

### MRVL

| 来源 | 标题 | 选择理由 | 本轮模型检查 |
|---|---|---|---|
| ibkr_news | VP Scarpulla Sells 1,100 Of Marvell Technology Inc >MRVL | target_title | 请求或 schema 失败，未通过 |
| ibkr_news | TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com | target_visible_body | 未测试；不能判断有效／无效 |
| ibkr_news | TSMC Gives AI Trade Upbeat Signal-AMD and Other Chip Stocks Fall Anyway -- Barrons.com | target_visible_body | 未测试；不能判断有效／无效 |
| ibkr_news | Investors Look to 3Q Earnings for Signs of Earnings Momentum Next Year -- Market Talk | signal_chain | 未测试；不能判断有效／无效 |
| ibkr_news | The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com | signal_chain | 未测试；不能判断有效／无效 |
| trendforce_news | [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding | signal_chain | 未测试；不能判断有效／无效 |
| trendforce_news | [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround | publisher_exploration | 内容契约失败，未采纳 |
| semianalysis | Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime | publisher_exploration | 未测试；不能判断有效／无效 |

### MSFT

| 来源 | 标题 | 选择理由 | 本轮模型检查 |
|---|---|---|---|
| ibkr_news | Vance Says U.S. Will Suspend Microsoft And Other Firms From Green Card Program -- Update | target_title | 内容契约失败，未采纳 |
| trendforce_news | [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | The Bulls Need This Earnings Season to Go Ridiculously Well -- Barrons.com | target_visible_body | 未测试；不能判断有效／无效 |
| ibkr_news | Four Years In, $40 Trillion Up: Why This AI-Fueled Bull Market Has Room to Run -- Barrons.com | target_visible_body | 未测试；不能判断有效／无效 |
| trendforce_news | [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead | target_visible_body | 未测试；不能判断有效／无效 |
| trendforce_news | [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding | signal_chain | 未测试；不能判断有效／无效 |
| trendforce_news | [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround | publisher_exploration | 未测试；不能判断有效／无效 |
| semianalysis | Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime | publisher_exploration | 内容契约失败，未采纳 |

### NVDA

| 来源 | 标题 | 选择理由 | 本轮模型检查 |
|---|---|---|---|
| ibkr_news | Dow Jones Futures: S&P 500, Nasdaq Near Highs; ASML, Nvidia's Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD | target_title | 内容契约失败，未采纳 |
| ibkr_news | Dow Jones Futures Loom; ASML, Nvidia Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | Dow Jones Futures Loom; ASML, Nvidia Chipmaker Taiwan Semi To Kick Off AI Earnings -- IBD | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | Dow Jones Futures: Trump Claims Russia-Ukraine 'Energy Ceasefire'; ASML, Nvidia Chipmaker Taiwan Semi EarningsLoom -- IBD | target_title | 未测试；不能判断有效／无效 |
| trendforce_news | [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround | publisher_exploration | 引用检查通过，待业务内容审阅 |
| semianalysis | Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime | publisher_exploration | 未测试；不能判断有效／无效 |

### TSM

| 来源 | 标题 | 选择理由 | 本轮模型检查 |
|---|---|---|---|
| ibkr_news | TSMC Revenue Likely to Be Driven by Continued Growth in AI Demand -- Market Talk | target_title | 引用检查通过，待业务内容审阅 |
| ibkr_news | TSMC Sales Are a Big Win for the AI Trade. Why AMD and Other Chip Stocks Are Falling Anyway. -- Barrons.com | target_title | 未测试；不能判断有效／无效 |
| ibkr_news | TSMC Gives AI Trade Upbeat Signal-AMD and Other Chip Stocks Fall Anyway -- Barrons.com | target_title | 未测试；不能判断有效／无效 |
| trendforce_news | [News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64 | target_title | 未测试；不能判断有效／无效 |
| trendforce_news | [News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows | target_title | 未测试；不能判断有效／无效 |
| trendforce_news | [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead | target_title | 未测试；不能判断有效／无效 |
| trendforce_news | [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround | publisher_exploration | 未测试；不能判断有效／无效 |
| semianalysis | Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime | publisher_exploration | 请求或 schema 失败，未通过 |

## 证据检查的条件

1. 读取的文档已经准入，处于该消费 scope；原始文档版本、publication 及 as_of 对应关系可复查。
2. 本次新消息有可解析发布时间且在七天窗口内，非未来材料；未知日期不能以采集时间替代。
3. full／partial 及模型输入截断如实声明；teaser 不作事实输入，partial 只使用获得的可见正文。
4. 模型输出符合约定 schema；目标 universe 内的结论有非空摘要、材料性和独立置信度。修复后摘要要求中文，但中文语言和论证质量并非由 min_length 自动证明。
5. 每条引用是模型实际看到的可见正文连续片段，不能省略号拼接、改写或引用隐藏内容；能够映射回原始正文 span。
6. 不包含 Information 角色禁止的交易建议。正式简报还需记录逐结论来源／三时钟／完整性与输入血缘。

当前 target 抽取采取整篇失败策略：一条 universe 内结论的坏引用或空摘要会拒绝该篇结果，不做部分发布。因此“某篇未通过”也不能理解成每一句原始报道都没有有效信息。证据检查主要证明可追溯性，不能证明来源陈述为真、语义推断成立或消息重要。

## 采纳与验收核签的条件

系统日常采纳是把合格的目标相关结论进入本角色 InformationBrief；本轮验收核签是评价新实现是否达到业务要求，两者不应混用。首轮只是隔离捕获，未运行生产发布，所以本轮生产采纳数为零，不能据此评价材料价值。

进入简报首先须满足证据／schema／角色条件、完成去重与投影校验，并说明目标或静态产业链关系、材料性、置信度和待核验项。源事实、分析师预测和模型推断必须区分，不把关系卡中的公司出现当作真实需求、订单或份额变化的证据；多篇转述不抬高独立佐证程度。

当前代码没有统一的“材料性超过某分数才采纳”门槛：0.7／0.4 只用于 high／medium／low 分级；低材料性结论也可能发布为背景。因此不能凭 0.2 或少于若干新闻判定无效，也不能说当前自动门禁已完整判断语义质量。首轮确实暴露了推断质量缺陷，详见中文 capture 审阅。

本次业务验收还要确认四 ticker 的实际选择与输出、partial 消费、事实与推断质量、真实新入口及受影响 Routine 依赖达到计划要求。人工核签用于本次上线／改造验收，不意味着上线后每条新闻或每个 tick 都需要用户批准。

## 结论

MRVL／MSFT 的定向两篇样本未通过，不是没有有效信息；NVDA／TSM 各一篇通过证据检查，不是全量各只有一篇。四 ticker 分别有 36／32／37／36 篇窗口内候选未作本轮模型内容验收。下一步应先复验已修复的输出契约，再扩展实际业务样本；不能把预算延期和未测试算作质量淘汰。

机器明细：`tmp/information_selection_implementation_20261012/round5/filter_funnel.json`，逐文档保存版本、来源、标题和退出阶段。重建采用原隔离快照及同 as_of，未新采集或调用模型。


## 2026-10-12 三步实验完成后的状态

用户已批准的64次有限调用全部执行，零重试，64份schema有效；60份通过逐条摘要/引用检查，53份再通过禁建议词检查（含3份合法空结果）。原选择8篇/额外抽查8篇的内容契约通过数分别为MRVL 7/7、MSFT 7/6、NVDA 5/6、TSM 7/8。检查通过不等于事实真实性、目标影响或业务核签通过。

每个标的的来源、标题、中文摘要、拒绝原因、完整过滤漏斗及改进顺序见 [三步实验报告](INFORMATION_SOURCE_SELECTION_THREE_STEP_RESULTS.md)。旧两篇抽样结论保留为历史，不代表当前完整覆盖。完整实验涉及30个不同文档版本、64个目标/版本输入对；供应商返回费用合计$0.045992897，无生产简报发布。

当前尚未通过内容整体验收：优先修复输出约束/建议词误伤及无依据的关系推断，再处理模型读取前的重复和覆盖，最后评估预算。额外8篇为分层抽样，不是生产16篇排序的严格A/B；上游IBKR正文预算10→24/32比较、生产元数据修订、正式Information及受影响链路资格仍不能由本轮替代。未改OpenSpec主任务完成状态或生产预算。依据 ARCHITECTURE.md §9.1，继续复用现有Information职责和静态关系，不引入新Agent或动态叙事图。
