# Phase F 9.4：Information 切换与首次生产运行结果（2026-10-16）

## 一句话

Information 的研究读取已整体切到新路径；首次生产运行为 MRVL、NVDA、TSM、ASML 各发布了一份带来源血缘的简报，共 **14 次模型请求**（上限 78，用户批准上限 100 次/2 美元），费用上限估算 **0.36 美元**，**0 失败**。观察期到 **2026-10-21 04:00 UTC**。TSM、ASML 的最新财报**没有被纳入**：数据平台已准入的文档只到 10 月 12 日。

## 做了什么（按时间）

1. 切换 Information（项目内记录 `consumer_cutover_log` seq 1，批准原文为用户 2026-10-16 的回复）。
2. 首次运行撞上生产调度权威从未初始化：按你选择的「继续往前」初始化（7 个工作流负责人全部 `legacy`，generation 1，先备份），不改任何负责人。
3. 预演（不调模型、不发布）发现新路径要求每个标的各自绑定实体读取上下文，脚本据此改为逐标的运行，与 Dispatcher 的绑定方式一致。
4. 真实首跑。5. 新增并加载 launchd 任务 `com.ats.consumer-health`（每天 09:30 北京时间，只执行每日健康检查，失败自动切回旧路径并走飞书机器人告警），已手动触发一次验证，退出码 0。

## 每个标的的结果

| 标的 | 阅读文章 | 简报事实数 | 相关度 | 置信度 | 延期（预算内未读） |
|---|---|---|---|---|---|
| MRVL | 1 | 2 | medium | 0.9 | 0 |
| NVDA | 4 | 5 | high | 0.9 | 7 |
| TSM | 5 | 11 | high | 0.85 | 7 |
| ASML | 3 | 6 | high | 0.0 | 7 |

## 简报内容（每个标的前 3 条事实，已截断）

### MRVL
- 来源信息（Dow Jones Newswires（依据Form 4申报））：根据Form 4申报，Marvell Technology Inc（MRVL）副总裁Justin Scarpulla于2026年10月8日卖出1,100股公司股票，价格为每股273.59美元，交易总值300,949美元；交易后其直接持股为909股。
- 来源信息（Dow Jones Newswires（依据Form 4申报））：文章附带的MRVL历史内部人交易记录显示，总裁Chris Koopmans于2026年10月1日卖出10,000股（每股262.05美元，持股207,941股），董事长Matthew J Murphy于9月15日卖出7,500股（每股223.39美元，持股775,686股），并列出2026年4月至10月期间多位高管（Koopmans、Murphy、Bharathi、Durn、Mei

### NVDA
- 来源信息（Investor's Business Daily (Ed Carson)）：文章（IBD，作者Ed Carson）报道，Nvidia股价上周下跌2%至229.28美元，周二盘中曾创下243.37美元的纪录高点；NVDA股价略低于234.76美元的杯柄形态买点。文章认为台积电（TSMC）和ASML的财报可能影响Nvidia股价。
- 来源信息（Investor's Business Daily (Ed Carson)）：文章指出台积电（Taiwan Semiconductor）为Nvidia、AMD、博通、苹果等公司制造芯片，台积电股价上周下跌4.1%至453.31美元。
- 来源信息（Barron's（作者 Martin Baccardax））：文章报道，Nvidia在2024年春季财报发布前一天市值约7500亿美元，同月月底市值突破1万亿美元；截至2026年10月，Nvidia市值接近6万亿美元。

### TSM
- 来源信息（中央社（Central News Agency）引述分析师）：据中央社报道，分析师预测台积电（TSMC）将在2027年进一步上调制造价格，先进制程涨幅5%至10%、先进封装10%至15%、成熟制程3%至5%，反映原材料与设备成本上升。
- 来源信息（工商时报（Commercial Times））：据工商时报报道，英特尔Panther Lake高端型号采用台积电N3E制程制造的高端GPU tile，这些型号的涨价幅度最大（15%至20%）。
- 来源信息（工商时报（Commercial Times））：据工商时报报道，AMD已开始在台积电2nm制程上量产第六代EPYC处理器（代号Venice），并计划在2027年大幅增加芯片供应。

### ASML
- 来源信息（Investor's Business Daily (Ed Carson)）：IBD报道，ASML预计将于周三早间公布财报，市场预期其盈利和销售增长将大幅加速，指引（Guidance）将是关键。
- 来源信息（Investor's Business Daily (Ed Carson)）：IBD报道，ASML股价上周下跌4.7%至1,780.34美元，该荷兰芯片设备巨头目前处于三个月盘整，随着TSMC与ASML财报临近。
- 来源信息（James Ooi (Tiger Brokers)）：老虎证券的James Ooi在电子邮件中表示，ASML的订单展望将作为半导体资本开支计划的指标受到密切关注。

## 需要你注意的

- **ASML 简报置信度为 0.0**：简报置信度取各条证据置信度的**最小值**，ASML 有两条证据（陈述类型为「claim」「forecast」）被模型给了 0.0，拉低了整体。这是 Information 输出质量的观察项，不是故障，没有改动。
- **没有纳入 TSM/ASML 最新财报**：已准入文档止于 2026-10-12，数据刷新任务 `com.ats.data-refresh` 未加载。要纳入需要先做数据采集，这是另一类生产写入，未获批准，我没有执行。
- **每标的各有 7 篇候选被延期**（NVDA/TSM/ASML）：这是选择器对相似材料的既有排序与预算规则，不是错误。
- **调度权威账本里有 12 条 information-brief 手动认领**：4 条 failed（实体绑定错误的早期预演）、8 条 complete（4 条无模型预演 + 4 条真实首跑）。
- 生产库 `ats.sqlite` 多了空表 `information_processing_runs`，现已有 13 条 succeeded 记录。

## 生产上实际写了什么

- `var/phase_f_cutover.sqlite`：Information 消费者整体路由（target，generation 1）+ 审计记录（切换、每日健康检查）。
- `var/phase_f_dispatch.sqlite`：调度权威 4 张表，7 个工作流负责人全部 legacy；12 条手动认领。
- `var/ats.sqlite`：4 份 information_brief、13 条抽取缓存、13 条处理记录，其他角色一概未写。
- `~/Library/LaunchAgents/com.ats.consumer-health.plist`：新增，已加载；`com.ats.schedule`、`com.ats.data-refresh` 未动。
- 没有任何券商写入，没有改其他边界与消费者的路由。

## 下一步

观察 3 个交易日（10 月 16、19、20 日）；期间每日 09:30 自动检查。之后切换 Fundamental（含定时 PEAD 准备任务），需要你另行批准。
逐次的模型请求账本：`tmp/phase_f_first_run_20261016/ledger_run1.jsonl`；自主决策记录：`tmp/phase_f_first_run_20261016/decision_log.md`。

---

## 追加：Fundamental 切换与首次生产运行（2026-10-16，用户指示「不等观察期了，直接进入 Fundamental」）

用户明确放弃原计划的「先观察 Information 3 个交易日」，Information 的观察期与每日检查照常继续。

- 切换前健康检查通过（Information 的新简报满足上游要求）；Fundamental 研究读取整体切到新路径（`consumer_cutover_log`，generation 1），批准原文为用户该次回复；观察期同样到 2026-10-21 04:00 UTC。
- 首跑用真实入口 `run_fundamental_pass`（例行模式）为 MRVL、NVDA、TSM、ASML 各跑一次：**4 次模型请求，0 失败，费用上限估算 0.10 美元**；累计两次首跑共 18 次请求、约 0.46 美元，均在批准的 100 次/2 美元内。
- 生产里记录的 `fundamental_analyst` 输出上限为 **8192**，说明统一标准已生效。

| 标的 | 事实变化 | 发布的预期更新 | 分类 |
|---|---|---|---|
| MRVL | 2 | 2 | 新增2 |
| NVDA | 5 | 5 | 新增5 |
| TSM | 11 | 11 | 确认7、新增4 |
| ASML | 6 | 6 | 确认4、新增2 |

24 条更新全部带来源血缘引用；其他角色、边界、其他消费者的路由没有变化；调度权威里新增 4 条 `fundamental-routine` 认领（complete）。

## 需要你回来后决定的预警

1. **周一（2026-10-19）09:30 的每日检查预计会让 Information 自动切回旧路径并发飞书告警。**原因：数据平台已准入的文档止于 2026-10-12，而检查要求最近 7 天内有已准入文档；10 月 19 日起整个标的池都没有。这是正确的信号（数据采集已经停了），不是故障。要避免，需要恢复数据采集（加载 `com.ats.data-refresh`，每小时一次，是对外采集并写数据平台的生产动作，我没有擅自启动，因为它此前被关着，可能是你有意的）；也可以接受自动切回。
2. Fundamental 的每日检查要求上游简报在 7 天内（我刚把 3 天放宽为 7 天，因为没有每日运行 Information 时周末就会让 Fundamental 误切回）。
3. **定时 PEAD 准备任务**（`run_pead` 的 prep 阶段）现已在 Fundamental 的整体路由覆盖范围内，但**我没有运行它**：它很耗模型、会写 dossier，且调度器当前没有加载。启动调度器后它才会运行，并且会走新路径、使用已初始化的调度权威。这一路径尚未在生产验证。
