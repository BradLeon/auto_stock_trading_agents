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

---

## 追加：恢复数据采集并重跑 TSM、ASML（2026-10-16，用户批准）

**加载 `com.ats.data-refresh`（每小时一次）。**采集前列出的来源与首个 tick 的实际结果：

| 任务 | 拉取什么 | 结果 |
|---|---|---|
| `ibkr_news_daily` | IBKR TWS 新闻标题（Yahoo 仅失败兜底，且需人工审核才准入） | **部分成功**：351 准入、9 发布；第二次尝试 `controller_RuntimeError` |
| `defeatbeta_transcript_daily` | Hugging Face 固定版本的财报电话会纪要 | 成功：111 准入、1 发布（含 **ASML Q3 FY2026 电话会纪要**） |
| `defeatbeta_sec_index_daily` | Hugging Face 固定版本的 SEC 申报索引（仅元数据） | 成功：116 准入 |
| `openrouter_rankings_p7d_ingest` | OpenRouter 模型排名（每 7 天） | 成功：714 准入/发布 |
| `ai_hardware_knowledge_daily` | 本仓库知识库清单（不对外拉取） | 无变化 |
| `sec_official_body_daily` | SEC 官方正文 | 已禁用，未运行 |
| 其余 10 个任务 | ramp、前沿模型能力、半导体出口、DRAM、TrendForce、SemiAnalysis 等 | 已错过 24 小时宽限，只记录跳过，**未补跑** |

**重跑结果（用最新文档）**
- Information：TSM、ASML 各发布新简报；因为缓存，只有新增文档才调用模型，本轮 **5 次请求**。ASML 的电话会纪要被抽取并采纳（4 条洞察全部采纳）。
- Fundamental：TSM 发布 16 条预期更新（确认 12、新增 4）、ASML 10 条（确认 6、新增 4），**2 次请求**，全部带血缘。
- **ASML 财报已纳入**：Q3 净销售额创纪录 117 亿欧元、处于指引高端；Q4 指引 142–152 亿欧元，全年 440–450 亿欧元。
- **TSM 自己的财报结果和纪要没有纳入**：数据平台里只有 10 月 15–16 日的市场文章（涨价、格芯中介层等），TSMC 官方结果与纪要尚未入库（纪要源有滞后；新闻这一轮只拿到部分）。

**用量合计**（四次运行）：25 次模型请求，费用上限估算 0.64 美元，在批准的 100 次/2 美元内。

**已知问题（不是本次改动引起，未修改）**
- `ibkr_news_daily` 10-15 窗口的队列任务已是终态 `partial`，控制器第二次尝试去重试它时抛 `RuntimeError`：采集控制器与持久队列对 `partial` 重试语义不一致。该窗口停在部分结果；10-16 16:30（纽约时间）会开新窗口再采。
- Information 每日检查的预警（10-19 起文档过期）因采集恢复而解除：最新文档为 10-16，7 天窗口可覆盖到 10-22，只要采集持续。

---

## 追加：ibkr_news 的 partial 与 30 分钟失败原因（2026-10-16，只读排查）

**运行环境**：`ibkr_news_daily` 由本机 launchd 任务 `com.ats.data-refresh` 拉取（本机 TWS 在 7496 端口监听），不是沙盒。10-16 窗口第 1 次尝试成功连上 TWS（发现 580 条、选中 351 条、0 个失败切片）。当天我用只读诊断命令 `ats data ibkr-news-diagnostics` 复测，8 个新闻源全部可用。

**一、`partial` 是常态，不是 TWS 故障。**
- 判定代码（`article_ingest.py`）：只要有“预算内读不完而延后”的文章就记 `partial`。配置的正文请求预算是每次 10 篇（`body_request_budget: 10`），而每次选中约 350 篇。
- 10-16 这次：351 条里读了 10 篇，9 篇准入发布、1 篇正文太短被隔离、341 条延后。10-12 那次也是 326 条延后。
- 采集控制器（`refresh.py:623`）把 `partial` 一律记为 `failed / article_source_partial`，并在 1 小时后重试第 2 次。
- **第 2 次必败**：持久队列把 `partial` 当终态，同一窗口的任务无法再次领取，控制器随即抛 `RuntimeError("queue_task_not_claimable")`（`refresh.py:573`）。从 9 月 29 日起每一个 `partial` 窗口（9-29、10-1、10-4、10-5、10-9、10-10、10-11、10-15）都这样：第 1 次入库成功，第 2 次 0.02 秒内报错。
- 影响：入库的 9 篇是真的；但每天固定只入库约 10 篇、延后约 340 篇，且这个窗口永远显示“失败”。

**二、10-13、10-14 等“30 分钟失败”是 TWS 无响应导致的挂起，加上一个掩盖原因的错误处理缺陷。**
- 现象：9-26、10-3、10-8、10-12（两次）、10-13（两次）、10-14（两次）的尝试都恰好跑满 1800 秒，随后记为 `controller_UnboundLocalError`；没有队列任务编号；数据平台里对应的采集运行永远停在 `running`、发现数为 0。
- **错误名是假的**：`persistent_queue.py` 的 `run_one` 在子进程超时时没有给 `payload` 赋值，却在返回时使用它，于是抛 `UnboundLocalError`，真实的超时（应记为 `command_timeout`）被掩盖。
- **挂起的机制（代码核实，触发原因是推断）**：`ibkr_news.py` 里 `reqNewsProviders()` 和 `qualifyContracts()` 都是同步调用，所用的 `ib_async` 库默认请求超时为 0（不超时）。如果 TWS 接受了连接却不回应，进程会一直等到被外层 1800 秒超时杀掉。只有连接本身带 20 秒超时。这与“采集运行停在 running、发现数 0、整整 30 分钟”完全吻合。
- **没能确认的**：当时 TWS 为什么不回应。子进程的标准错误输出没有保存（只存了哈希），Mac 的睡眠日志只保留到 10-10，无法对上。我不声称知道根因。

**三、修复建议（未实施，等你决定）**
1. 队列：`partial` 视为可接受的终态，控制器不再重试（或把延后的文章下一轮继续读，而不是重试同一任务）。
2. 队列：修复 `run_one` 超时分支的未赋值变量，让超时如实记为 `command_timeout`。
3. 适配器：给 `reqNewsProviders`/`qualifyContracts` 加超时（或统一设置库的请求超时），并保存子进程标准错误的末尾，下次挂起时能看到卡在哪一步。
4. 配置：评估把正文请求预算从 10 提高（每天只入库约 10 篇，对 11 只标的的覆盖偏薄）。

---

## 追加：Fundamental 事件模式与定时 PEAD 准备任务的生产运行（2026-10-16）

可读版：`docs/phase_f_first_run_review_20261016/PEAD_EVENT_AND_PREP.md`。累计模型请求 **42 次**（费用上限估算约 1.08 美元），在批准的 100 次/2 美元内。

| 项目 | 结果 |
|---|---|
| 事件模式 NVDA Q2 FY2027 | 成功（v7，中性观望）；但运行前该季基线是空的，所以全中性不能说明评分能力 |
| 事件模式 TSM Q2 FY2026 | 成功（v1，+0.15）；有完整基线，三向差异分歧保留。**营收一栏被错判为重大不及预期，原因是基线里的共识属于下一季（见下）** |
| 事件模式 ASML Q3 FY2026 | 新路径拒绝：平台里没有 ASML 财务数据 |
| 事件模式 TSM Q3 FY2026 | 新路径拒绝：平台里没有 TSMC 官方财报/纪要 |
| 定时 PEAD 准备 NVDA(Q3 FY2027)、MRVL、TSM(Q4 FY2026) | 成功（共 13 次请求） |
| 定时 PEAD 准备 ASML(Q4 FY2026) | 新路径拒绝：同上，无财务数据 |

**需要你决定的问题（均未处理）**
1. **生产里现在有一条假的事件评审**：TSM Q2 FY2026 最新一版把营收判成“重大负面意外”（-2.0），根因是基线里的共识（1.454 万亿新台币）是 Q3 的，Q2 实际是 1.270 万亿。事件评审是追加式的，旧版还在，但“最新”这一版会被 Chief 读到。
2. **共识与财报期没有核对**：准备任务取供应商“当前季”共识，财报发布后到供应商滚动前会错位。今天 TSM 的 Q4 准备里共识也是 Q3 的。
3. **`config/pead/{ASML,TSM,NVDA}.yaml` 的 `fiscal_label` 是手填的，现已过期**（仍是已发布的上一季）。我没有改配置，只在进程内覆盖。
4. **新路径把整个财务快照原样塞进档案和提示词**：TSM 档案的 `fundamentals_context` 从约 200 字节涨到约 568KB，MRVL 档案涨到约 778KB，Obsidian 报告也同步涨到约 590–690KB，准备任务一次要 3 分多钟。
5. **覆盖了的东西（可由 `tmp/phase_f_first_run_20261016/pead_before_validation.json` 恢复）**：NVDA Q2 FY2027、TSM Q2 FY2026 的档案被事件模式重写；MRVL 的单一档案槽位（财报期标签为空串）被下一季准备替换，原来的 Q2 评分档案不再是当前行；Obsidian 里 `基本面分析-NVDA-2027Q2.md`、`基本面分析-TSM-2026Q2.md` 被覆盖。评分台账、事件评审投影都是追加式，旧记录都在。
6. `run_event_pass` 的返回汇总恒为空（`PeadState` 缺 `event_summary` 字段），只影响汇报，不影响落库。
7. ASML 在新路径下没有财务数据：事件模式和准备都跑不了，需要先补数据或明确 ASML 走什么口径。

**每日健康检查**：在这些运行之后手动触发一次，Information、Fundamental 均 `health_ok`。

---

## 追加：共识期次错位的修复与现场清理（2026-10-16，用户批准计划）

**代码（已测试，未提交）**
- 新增共识期次/新鲜度核对 `align()`（在已登记的 `src/ats/data/consensus.py` 内，没有新增清单路径）。共识满足任一条即判不可用，估计值置空并留痕：快照距 `as_of` 超过 14 天（`stale`）；快照对应的财报日已早于 `as_of`（`event_already_passed`，TSM 那种情形）；与待准备季的发布日相差超过 10 天（旧路径已知日期时，`event_date_mismatch`）。价格目标、评级不受影响。阈值可在 `config/pead.yaml` 的 `consensus:` 下覆盖。
- 接入准备任务的新旧两条路径：`prep_fetch` 调 `align()`；`PeadState.consensus_note`、`ExpectationSet.consensus_note` 记录原因；提示词明确写出“共识不可用，不得推断或引用具体数字”；旧路径的 yfinance 共识补带 `target_event_date`。
- 12 项新测试（`tests/test_consensus_alignment.py`）；相邻 PEAD/共识套件 123 项通过；指纹关联的 11 个测试文件 233 通过、17 失败，17 个失败全在 `test_batch_manifest.py`，HEAD 上相同，漂移无新增失败。
- 新增 11 条共识刷新任务配置（每个 PEAD 目标一条，**`enabled: false` 未启用**），`ats.data.refresh validate` 通过，相关 88 项测试通过。启用需另行批准。

**生产数据**
- TSM Q2 FY2026 事件评审（假的营收重大不及预期）和 NVDA Q2 FY2027 v7 评审（无基线的假中性）已标为 `failed`；事件评审现仅剩 ASML（10-14）。评分台账、认领引用、其他投影没动。原行备份在 `tmp/phase_f_first_run_20261016/invalidated_reviews_backup.json`。
- TSM Q2、NVDA Q2、MRVL（空标签槽位）三份档案已恢复为验证前内容，Obsidian 报告 `TSM-2026Q2`（恢复后 24366 字节，与 10-14 的原文件大小一致）、`NVDA-2027Q2`、`MRVL-latest` 已据此重新生成。
- 用修复后的代码重跑 TSM（Q4 FY2026）、NVDA（Q3 FY2027）准备：共识置空且留痕（原因：快照 9-04，已超 14 天），叙述中明确写明共识缺失；11 次模型请求。TSM 这次信号链为 0 条（上次 15 条），未排查。
- 累计模型请求 **53 次**（费用上限估算约 1.36 美元），在批准的 100 次/2 美元内。每日健康检查两消费者均 `health_ok`。

**没做 / 仍开放**
- 没有登记新冻结：注册的 `final_implementation_freeze_report_narrowed` 因改动文件已漂移；简化切流后消费者整体路由不依赖它，是否登记留待决定。
- 评分台账里仍有 TSM Q2 v1 和 NVDA v7 两条记录（追加式不删）；两者对应评审已作废。
- 共识快照仍停在 9-04：NVDA、MRVL、TSM 等的共识现在会被判不可用，直到启用刷新任务。
- 平台采集里的 `target_period` 推算（最近季报+3 个月）在财报刚发布后会错一季，核对已不依赖它，推算本身未改。
- 新路径把整个财务快照塞进档案的膨胀问题（TSM Q4 / NVDA Q3 档案和 Obsidian 报告重新变大）、ASML 在平台无财务数据、`config/pead/*.yaml` 手填财报期过期、ibkr_news 的两个缺陷，均未处理。

---

## 追加：共识刷新任务启用与 9.5 回退演练（2026-10-16，用户批准）

**共识刷新任务**：11 条（每个 PEAD 目标一条）已 `enabled: true`。启用时发现刷新控制器要求数据源声明 `requests_per_run`，`yfinance_consensus` 缺该字段，已补 `8`。手动跑一次 tick：11 条全部成功（各发布 39~42 条观测）。新快照里 NVDA（事件日 11-18）、MRVL（12-02）、MSFT（10-29）通过期次核对；TSM（10-15）、ASML（10-14）的事件日已过，被正确拒绝（厂商“当前季”尚未滚动）。NVDA 的 `target_period` 从旧快照的 2026-07-30 变为 2026-10-31，证实旧值是财报刚发布后报表滞后所致。用新快照重跑 NVDA Q3 准备：共识 EPS 2.47、营收 1090 亿被使用，无忽略标记。累计模型请求 57 次（上限估算约 1.46 美元）。

**9.5 回退演练**：Information、Fundamental 各演练一次。每步经真实入口 `scoped_read` 解析路由：切回前 target，`rollback`（代次 2）后 legacy，`switch`（代次 3）后 target；全部追加到 `consumer_cutover_log`（seq 9–12），演练后每日健康检查两消费者均 `health_ok`。「观察期内健康检查失败自动切回」由 `daily_check` 的单测覆盖，未在生产人为制造故障。

---

## 追加：10.4 首批调度切换演练（Information、Fundamental、PEAD，2026-10-16）

**用户裁决：** 不做应用内成本护栏（由 API key 管理方设上限），但逻辑上不得重复发起模型请求、能用缓存就用缓存；演练后终态留在 legacy，并写明何时可切新逻辑（`docs/PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`）；Layer/Sector/Macro/Technical 记为 Phase F 内待办（10.5）；当前只做 Information、Fundamental、PEAD。

**代码（已测试，未提交）**
- `read_windows.select()`：没有精确窗口、且消费者整体路由为 target 时，Dispatcher 计划绑定自身业务身份；精确窗口（有效或无效）仍优先，旧路径消费者仍被拒。修复前只读探针显示 `bind_plan` 被拒（`workflow has no explicitly authorized read window`），修复后 Information、Fundamental 通过、Technical（旧路径）仍被拒。
- `schedule_runtime.owner_switch/owner_log` 与 `ats dispatch owner-switch|owner-rollback|owner-log`：一个事务同时改模板行和所有标的行；去 dispatcher 需要批准原文、该消费者读路由为 target、健康检查 ok；有 `running` 认领拒绝，`failed/pending` 须带理由作废；追加日志只增不改。联合协调器路径（`runtime-freeze/handover`）原样保留。
- 12 项新测试（`tests/test_schedule_owner_switch.py`）；相邻 19 个文件 433 通过、11 个失败全在 `test_chief_snapshot.py`，在 HEAD 上同样失败（既有）。

**生产演练**
1. 备份调度权威库；`phase_e_schedules.yaml` 启用 information-brief、fundamental-routine 两个条目（legacy 负责人时被忽略）。
2. 三个工作流切到 dispatcher（代次 1→2），作废 7 条旧 failed 认领（早期预演 4 条、验证运行被新路径拒绝 3 条），全部有理由并入日志。
3. 三个旧入口（Information、例行基本面、**PEAD 准备 `run_pead`**）均被拒：`schedule frozen or caller is not current owner`。
4. 一次观察 `ats schedule --phase-e --now`（dry-run，不加载任何常驻进程）：
   - Information：11 个标的 10 个成功，各一条 dispatcher 认领；LRCX 7 天内无已准入文档，按设计拒绝（fail-closed）。抽取缓存生效：新增 16 次抽取全是新文档（TSM、ASML 0 次）；NVDA、MRVL、TSM、ASML 当天已有简报但被重新发布一份（投影复用因缺数据版本引用不生效）。
   - 例行基本面：11 个标的全部 `blocked`（依赖的 Information 简报不可复用，见 10.8）。
5. 三个工作流切回 legacy（代次 3），作废 LRCX 那条 failed 认领；证明 Dispatcher 入口返回 `legacy`、旧入口重新可用。终态：所有研究工作流 legacy，读路由仍为 Information、Fundamental target；演练后每日健康检查两消费者 ok。

**阻碍（已记为 10.6、10.8，待你决定）**
- 例行基本面单独定时不可运行：定时条目不提供数据版本引用 → 复用恒判「缺数据版本」。方案 A 把 Information 并入同一次运行；方案 B 让条目携带数据版本引用。
- PEAD 准备没有 Dispatcher 入口（Dispatcher 只有例行与事件两个基本面适配器）。
- 小问题：观察时新建的标的行代次从 1 起，与已有行（2）不一致，回退后分别是 2 与 3；不影响正确性，未处理。

---

## 追加：10.6/10.8 实现与生产验证（2026-10-16，按 TARGET 与用户裁决）

**用户裁决：** 数据版本不变则复用，上限 72 小时；不做「重大变化」自动刷新，例行 Fundamental 在财报前窗口内建一次基线，并保留手动刷新入口；基线每次建立另发版本化投影；财务快照摘要化。

**对上一节判断的更正：** 观察时例行基本面全部 blocked，直接原因是 `--now` 让两个条目拿到同一计划时刻，同一逻辑触发的复用判断拿另一计划的引用比对而失败；真实定时下还会碰到「复用永远判缺数据版本」和「引用必须逐字相等」两个更根本的问题（适配器发布时会追加自己的文档引用，所以旧判断对 Information、Fundamental 永远命不中）。

**实现**
- `dispatcher.py`：`vintage_resolver` + `default_data_vintage`（Information=准入文档版本集合；例行基本面=财务观测集合、共识快照时间、基线窗口状态）；复用判断改为「计划要求的引用必须包含」；例行基本面整批投影全有或全无地复用（原来一律重算）。同一触发复用仍按本计划引用严格校验（既有测试保护的不变量保留）。
- `phase_e.py`：Information、例行基本面新鲜期 1h→72h。
- `routine.py`：`baseline_window`（日历推导下一次财报、目标期、窗口开闭）、`ensure_baseline`（窗口内且无基线则建；手动 `--refresh-baseline` 可重建；财报发布后冻结）、`[基线 vN]` 版本化投影；`graph/pead.py`/`pead_state.py` 在新入口准备时带入本标的最新简报事实。
- `inputs.py`：`financial_summary`，每项最近 8 期的紧凑表（TSM 568KB→5KB，MRVL 664KB→7KB，NVDA 134KB→3KB），完整数据留在数据产品。
- 15 项新测试；相邻 50 个测试文件 763 通过、16 失败，全部是 HEAD 上就有的（`test_chief_snapshot` 11 项 + 另 5 项）。过程中有一次回归（`Dispatcher._task_inputs` 被个别测试以未绑定方式调用、同触发复用的严格校验被我误放宽），已修复并保留测试保护的不变量。

**生产验证（终态 legacy）**：三个工作流切到 dispatcher（代次 4）后连跑三遍 `schedule --phase-e --now`：
| 遍 | Information | 例行基本面 | 新简报/新预期更新/新抽取 |
|---|---|---|---|
| 1 | 10 成功（旧简报无版本，重算），LRCX 无文档拒绝 | 10 成功（4 个 `agent_attempt_missing`、6 个 `no_candidate` 重算），LRCX blocked | 0 / 约 50 / 0（抽取缓存全命中） |
| 2 | 10 全部 hit | 3 hit，7 因整批不复用而重算 | 0 / +48 / 0 |
| 3 | 10 全部 hit | **10 全部 hit（3 单条 + 7 整批）** | **0 / 0 / 0** |
然后切回 legacy（代次 5），作废 LRCX 的 failed 认领；每日健康检查两消费者 ok。

**手动基线首次生产使用（MSFT）**：目标期 `Q1 FY2027` 由日历推导，档案 20KB，`[基线 v1]` 投影已发布，Obsidian 报告 `基本面分析-MSFT-2027Q1.md`。**发现两个质量问题（未处理）：** ① 基线叙事开头写「微软即将发布 FY2026 Q4（截至 6 月 30 日）财报」，与目标期 Q1 FY2027 不符：提示词没把目标期和发布日交给模型，模型按最近一期财务数据往前猜；② 之后的例行分类调用 `fundamental_analyst`（DeepSeek V4 Pro，上限 8192）输出被截断，退回规则分类，5 条都标「新增」，整次用时约 12 分钟。

**仍未做**：PEAD 准备的自动窗口路径尚无真实窗口可验证（LRCX 约 10-18 开窗）；第二批工作流（10.5）；ibkr_news、ASML 无财务数据、手填 `fiscal_label` 等旧问题。

---

## 追加：基线叙事目标期写错与例行分类截断的修复（2026-10-16，用户批准方案）

**根因（比上一节的猜测更具体）**
- **目标期写错：** 图里准备的三个提示词和事件打分（Scorecard 标签）读的是 `state.config.fiscal_label`，即 `config/pead/*.yaml` 手填、财报后就过期的标签；运行的目标期只存在 `state.fiscal_label`，没传进配置。MSFT 的配置还写着 `Q4 FY2026`，模型便按它写。事件模式在 Dispatcher 路径下同样会用旧标签给 Scorecard 盖章（此前验证事件模式时是用进程内补丁盖住的）。
- **分类截断：** 提示词只有 1035 token，输出却到 8192 被截断；调用的 skill `fundamental-routine` 并不存在（兜底系统提示只有一句话），`reason` 字段又不限长；退回规则后驱动文字与模型判断无法区分。**失控的真实原因没法确认**：用同样的输入重放一次（用户批准的那一次诊断），正常返回 5 条结果、输出 196 token、费用约 0.0019 美元，复现不了。

**修复（已测试，未提交）**
- `graph/pead.py`：`load()` 以运行目标期覆盖配置标签（准备、事件打分、Scorecard 一并对）；新增 `target_event_note`，把目标财报、预计发布日、今天的日期，以及「该季尚未发布、Fundamentals 里最新已报季是它的上一季」写进准备的三个提示词（`prep.py` 三个函数加带默认值的 `event_note`）。
- `routine.py`：分类改用独立角色 `fundamental_classifier`（温度 0，`max_tokens` 3072，`config/settings.yaml`），上下文写明「恰好 N 条、index 必填、reason 不超过 30 字、不输出推理或额外文字」；失败（截断、解析失败、条数不符）重试一次，再失败才回退规则；回退时驱动末尾追加「｜分类器：规则回退」，摘要里带 `classifier`；用户主动不用模型（`--no-llm`）不算回退、不加标记。
- 新增 9 项测试；相关 16 个测试文件 222 项通过（含 PEAD 图、例行/事件模式、Dispatcher、研究门、成对业务、调度运行时）。

**生产验证**
- 不花钱：渲染了 MSFT 的三个准备提示词，目标期为 `Q1 FY2027`、含发布日 2026-10-28 与「尚未发布」说明，单个提示词约 8.5K 字符（此前约 15 万 token）。
- 手动刷新重建 MSFT：新发 `[基线 v2]` 投影（v1 保留），叙事开头为「即将发布 FY2027 Q1（截至 2026 年 9 月 30 日）财报」，目标期正确；分类由模型完成（确认 3、否定 1、新增 1），无回退；用时约 3 分钟（此前 12 分钟）；档案 45KB；共识 EPS 4.72 被使用（刷新任务已更新快照）。
- 单独一次分类诊断 + 本次 MSFT 重建的模型请求共约 6 次。

---

## 追加：10.5 前置考察与 Technical 切换（2026-10-16）

**前置考察**（`docs/PHASE_F_10_5_PREFLIGHT_AUDIT.md`）：只有 Technical 具备切换条件。Layer/Sector 的中性证据全部停在 2026-08（证据抽取挂在旧调度器的 PEAD 窗口里，开关 `pead_observe_window: false`），30 只截面标的仅 7 只有受治理财务；Macro 在分析中实时调用 Tavily 网页搜索，违反 §12.1；四个角色的投影都比 §7.3 薄（Sector 只有行业级立场）。数据流补齐方案见 `docs/PHASE_F_DATAFLOW_REMEDIATION_PLAN.md`，待审阅。

**Technical（用户批准先按原 10.5 计划推进）**
- 代码：`consumer_health._technical`（运行时价格网关；单只缺价是警告，整池无 4 天内 K 线且重试一次仍失败才失败）；`default_data_vintage` 增加 Technical（纽约交易日 + 收盘前/后，不发网络请求）；新鲜期 1h→24h；轻量调度切换允许作废「已以 execution_error 结束、却因写过旧表而仍标 running」的认领（真实运行中的认领仍不可作废）。新增/调整测试 9 项，相邻 7 个测试文件 102 项通过。
- 生产：健康检查 ok（11 只 K 线 10-15）→ 读路由切到 target（代次 1）→ 首跑 `ats technical review`：12 只读数、投影已发布、旧表与 Obsidian 报告 `技术面读数-技术面-2026-10-16.md` 已写，SKHY/DRAM/LYTE 因 K 线不足 200 根未评估 → 切回演练（target→legacy→target，代次 3）→ 调度切到 dispatcher → 两遍观察：第一遍 10 只成功，第二遍 10/10 复用、0 新投影，SKHY 按设计拒绝 → 切回 legacy（代次 3）。每日健康检查三个消费者均 ok。
- 不调模型，没有券商写入。
- **发现：** SKHY 无读数时 Dispatcher 适配器仍会把一条空的旧表记录写入，导致认领卡在 running；本次按「已结束」作废并留痕，适配器本身未改。

---

## 追加：7 个研究工作流切到 Dispatcher 并上线（2026-10-17）

- **先决修复。** ①ASML 最新季只缺 CFO/CapEx（供应商对外国申报人滞后发布）：包门槛在「只缺这两项且同源上一季齐全」时接受并写入 `gaps`，现金流按期次对齐，不借旧季；AMZN 型缺口（新季只有 EPS）仍被拒。②手填 `fiscal_label` 每季过期：10 个配置去掉手填，交易定规模、日报、日记改用 `label_for_reads`（取最新档案的标签）。③Dispatcher 日历事件路径原先不读 `macro_sector_event_triggers`/`pead_event_triggers`，已补上。
- **8 条缺陷旧 Layer 投影**（首次 Layer 运行的缺陷版本）已备份到 `tmp/phase_f_first_run_20261016/projection_cleanup/` 并标为 `failed`；它们被调度发布台账（只增不改）和复用决策引用，所以不删除、不覆盖；读取方只取到新的。
- **切换。** 7 个工作流 `owner-switch` 到 dispatcher（批准原文写入日志）；新建并加载 `com.ats.phase-e`；启动补跑由常驻进程执行：macro-review 约 3.5 分钟（1 次模型请求）、sector-review 约 9.5 分钟，均 complete。
- **意外。** 切换过程中 `com.ats.data-refresh` 与 `com.ats.consumer-health` 两个 launchd 任务不在已加载列表里（当时系统负载很高、进程数接近上限），已重新加载；原因不明，建议留意。
- **未解决。** SEC（sec.gov）连接被重置：本机 DNS 返回 198.18.x（Clash fake-ip），Clash 为全局模式，当前出口节点到 SEC 的 TLS 被重置，沙箱内外一致；需要用户调整 Clash。10.6 待 LRCX 的真实窗口（财报 10-21 amc，prep 窗口 3 天，周一 10-19 的例行任务首次进入窗口）。

---

## 追加：TSM、ASML 最新财报的事件评审（Dispatcher 路径，2026-10-18）

**结论：工作流通过，但发现并修了两个缺陷；预期差评分本身这两家验证不了（没有财报前基线），已按「无比较依据」明确标注。**

- **缺陷 1（已修）：Dispatcher 的事件评审是空的。** `event.py` 里 `live_data` 默认 False，图在离线状态下运行：没有纪要、没有财报稿，评分表每一行都是「数据缺失，默认中性」，但认领显示 complete。现在带模型的事件评审默认读取实时文档。此前 `ats analyst fundamental --mode event` 命令行入口也有同样的默认值。
- **缺陷 2（已修）：没有任何可比较依据时，direction=0 会被读成「符合预期」。** 现在没有冻结基线、也没有共识时，评审写明「无比较依据」，信心封顶 0.1，纪要与指引的提取照常发布。
- **生产运行（模型请求按运行记录）：** TSM 2 次、ASML 2 次（actuals 提取 + 评分），Information 简报复用。
- **ASML：** 从 Q3 纪要提取到真实读数：销售 EUR 11.7B、EPS 12.95、毛利率 57.5%，以及 Q4 与全年指引区间和管理层语气信号。
- **TSM：** 提取到的是定性内容：CapEx 2026–2028 显著更高、海外产能稀释毛利率 3–4 个百分点、AI 需求信号；**没有 Q3 的财务数字**——纪要正文来自 fool.com 的整理稿，不含这些数字，官方披露要靠 SEC（本机后台进程访问不了）。
- **为什么评分是中性：** 这两家的财报都发生在新的财报前基线机制上线之前，没有冻结的基线可对照，发布后又不允许补建（防止事后诸葛）。要看到真正的预期差评审，需要一个财报前建过基线的标的（LRCX 10-21 盘后）。
- **清理：** 此前一次空评审与一次中间版本共 3 条已备份并标为 failed（`tmp/phase_f_first_run_20261016/projection_cleanup/superseded_empty_event_reviews_backup.json`），每家只保留最新一条。


---

## 追加：10.6 手动跑一遍「例行 tick」，LRCX 基线被上游简报卡住（2026-10-18，**10.6 未勾选**）

**结论：10.6 的三项判据里，只有「(a) 只有一个负责人在动」成立；LRCX 的基线没有建成，所以 (a) 的另一半、(b)、(c) 都还没验证。原因不是基线逻辑出错，而是 LRCX 近 7 天没有任何已准入资讯，信息简报没发布，例行任务按「上游依赖失败则阻断」规则被拦住。这需要一个设计取舍，下面给出选项，等你裁决。**

### 做了什么

用与常驻进程 `com.ats.phase-e` 的 `fundamental-routine` 定时任务**同一条代码路径**手动跑一遍：触发类型 `schedule`、范围 `portfolio:pead-targets`、请求 `requested_tasks=["fundamental-routine"]`，模型请求逐条计数（脚本 `tmp/phase_f_first_run_20261016/verify/run_routine_tick.py`，输出 `routine_tick_p1.json`、`llm_routine_tick_p1.jsonl`）。这验证的是**逻辑**，不是常驻进程的定时触发本身（下一次真实定时触发是周一 08:00/09:00 纽约时间）。

### 结果（第 1 遍，用时 52.5 秒，状态 `incomplete`，14 次模型请求）

| 项 | 结果 |
|---|---|
| 负责人 | 只有 `dispatcher` 动手（调度负责人日志 35 行、7 个工作流均为 dispatcher）；旧入口被拒沿用 12.3 的演练与测试（`schedule frozen or caller is not current owner`），本次 tick 没有重新测试这一点 |
| 11 个标的 | 10 个的「信息简报 + 例行」都成功（GOOG、NVDA、SKHY、TSM、ASML、COHR、LITE、AVGO、MRVL、MSFT）；**LRCX 的信息简报 `missing`，例行 `blocked: upstream dependency failed`** |
| 模型请求 | `fundamental_classifier` 10 次、`research_extract` 4 次，共 14 次，0 错误 |
| 其他标的的窗口 | 都不在财报前 3 天窗口内，没有建基线（预期如此）；它们的例行预期更新正常 |
| LRCX 基线 `[基线 v1]`（目标期 Q1 FY2027） | **没有生成**；LRCX 也没有任何 `fundamental_event_review`，所以「基线先于事件评审」目前是空成立 |
| `phase-e.err.log` | 无新增错误；仍是 10-17 已记录的几条警告（Macro 丢弃未知主题键、Sector 一层结构校验失败、yfinance/证据判读连接重置后重试） |

### 为什么 LRCX 被卡住

1. LRCX 的已准入资讯：总共 5 条，最新一条是 2026-09-26，**近 7 天（`monitor.lookback_days`）为 0 条**；对比 GOOG 1、COHR 1、LITE 1、SKHY 2。`ibkr_news` 的标的清单里本来就没有 LRCX。
2. 信息简报在「完全没有资讯」时直接返回、**不发布**投影（`documents.run_document_pass` 的 `no new events` 分支）；而「有 1 条但无相关变化」会发布一份 `no_relevant_change` 简报。所以同样是「没新闻」，GOOG 有简报、LRCX 没有。
3. `fundamental-routine` 声明依赖 `information-brief`（`phase_e.TASK_DEPENDENCIES`），且工作流任务规格里没有「可选依赖」的概念；上游缺失就阻断，这是有意的 fail-closed 设计。
4. 结果：PEAD 的财报前基线是用财务与共识建的，**并不需要当天有新闻**，却被「没有新闻」拦住。

### 选项（都是设计取舍，我没有擅自改）

| 方案 | 做法 | 优点 | 代价 / 风险 |
|---|---|---|---|
| A（建议） | 让「近 7 天没有任何已准入资讯」成为信息任务的一种**成功但无投影**的结果（记为 `no_material`），例行任务据此继续，把「无资讯」写进基线的缺口说明 | 改动只限于「没有新闻」这一种情况；其他上游失败仍然阻断；基线不依赖新闻 | 要在 Dispatcher 的依赖判定里新增一种合法状态，属于依赖语义的扩展，需要你认可 |
| B | 给 LRCX 补资讯覆盖（`ibkr_news` 的标的清单、或另一条 LRCX 新闻源） | 不改依赖语义；信息简报本来就该覆盖 LRCX | 触及你暂缓的 `ibkr_news` 缺陷；而且只解决 LRCX，下一个冷门标的还会遇到 |
| C | 为「没新闻」发布一份空的信息简报 | 依赖不变 | 信息简报的六要素规则要求事实与来源证据，空简报要么违反该规则、要么要放宽规则；**不建议** |

### 时间点

LRCX 财报 10-21 盘后，prep 窗口（≤3 天）从今天起就是开的。常驻进程下一次定时触发是**周一** 08:00（信息）、09:00（例行）纽约时间；如果不改，周一和周二的例行 tick 会重复同样的 LRCX 阻断（LRCX 这一路每次只多一条 failed 认领，不发模型请求），基线最晚要在**周三 10-21 纽约时间 09:00 的例行 tick** 之前建成。方案 A 的改动不大，裁决后我可以马上实现、手动重跑三遍（建基线 → 复用 0 请求 → 确认 LRCX 事件评审前基线已冻结）并勾选 10.6。

### 没有做的

- **没有跑第 2 遍。** 第 2 遍会再产生一次 `incomplete`，与前一次连成「连续两次整批失败」，会把 12.3 的第 ④ 条停止条件人为凑出来；(b)「第二遍复用」留到 LRCX 能建基线之后一并验证。
- 没有改依赖语义、没有碰 `ibkr_news`、没有改任何配置。

---

## 追加：10.6 通过——数据平台给 LRCX 补资讯与财务，建成 `[基线 v1]`（2026-10-19，用户选方案 B）

**结论：LRCX 的财报前基线 `[基线 v1]`（目标期 Q1 FY2027）已经建成，三项判据成立，10.6 勾选。** 上一节卡住的原因是 LRCX 没有近期资讯；补资讯的过程中又连续暴露三个缺口，逐个解决后才建成。

### 一路上发现并解决的四个缺口

| # | 缺口 | 原因 | 处理 |
|---|---|---|---|
| 1 | LRCX 近 7 天无已准入资讯 | `ibkr_news` 的标的清单没有 LRCX（`config/data/unstructured.yaml`、`sources.yaml`） | 清单加入 LRCX |
| 2 | `ibkr_news` 每天挂 30 分钟后失败（10-13 起每日失败，最后一次成功是 10-07） | **查清了**：TWS 对 `reqContractDetails`（`qualifyContracts`）永远不回复，而 `reqCurrentTime`、`reqMatchingSymbols`、新闻来源查询都正常；`ib_async` 默认无限等待，于是一个标的卡住整轮。AAPL 同样复现，不是 LRCX 特有 | `ibkr_news.py`：连接设 20 秒请求超时；合同详情失败时回退到「符号检索」，**只有检索到唯一一个美股合同才采用**，否则仍按失败处理。新增 2 项测试 |
| 3 | LRCX 那篇已准入文章（Dow Jones 自动生成的连跌统计稿）无法抽取：模型返回「停止」但没有工具调用，8 次完全相同的重试全部为空 | `research_extract` 的隐藏推理占掉回合（消耗 466 个推理 token，正文为空）。同一请求关闭推理后第一次就返回合格结果（396 token，0 推理）。这是我 10-17 记录过的「身份绑定角色推理隐患」的实例 | `run_structured`：首次返回空工具调用后，其余重试改为关闭推理（`get_model(role, "off")`）。**不改角色配置，不改执行身份，已缓存的结果全部保留**；已配置 `reasoning: "off"` 的角色行为不变。新增 2 项测试 |
| 4 | LRCX 没有受治理的财务数据，例行被「财务输入缺失」拦住 | 每周财务任务的标的清单只有 GOOG、MU | 用 defeatbeta 的受治理摄入补上（财务包齐全，最新季 2026-06-30，即 FY2026 Q4），并把 LRCX 加入 `company_financials_weekly` |

### 三项判据（手动跑与 `fundamental-routine` 定时 tick 同一代码路径）

| 判据 | 结果 | 证据 |
|---|---|---|
| (a) 只有一个负责人在动；LRCX 基线建一次 | **成立** | 全程由 dispatcher 执行；中途我直接调用旧入口 `run_information_target('LRCX')` 被拒（`schedule frozen or caller is not current owner`）。LRCX 的 `fundamental_expectation_update` 投影共 2 条：`[基线 v1]` 一条（目标期 Q1 FY2027，叙事含「财报前 3 天建立」）加一条 `[确认]`；Obsidian 报告 `基本面分析-LRCX-2027Q1.md` 已写 |
| (b) 第二遍复用 | **LRCX 成立；整批 21/22 复用** | 第二遍 22 项全部 succeeded，21 项复用；LRCX 没有新投影、没有新请求。唯一未复用的是 GOOG 例行（1 次分类请求）：它的 `baseline-window` 数据版本引用在两遍之间从 `Q3 FY2026` 变成 `none`（窗口两次都是 closed），其余引用不变；原因我没有深查，记为观察项 |
| (c) 基线先于任何 LRCX 事件评审 | **成立（到目前为止）** | LRCX 没有任何 `fundamental_event_review`；基线 v1 已存在。「财报发布后冻结基线」的规则由 15 项测试覆盖，真实冻结要等 10-21 盘后事件发生时才能看到 |

### 模型请求（逐条计数）

| 遍 | 结果 | 请求 |
|---|---|---|
| p2 | LRCX 简报抽取失败（缺口 3） | 19（9 次抽取新入库的文章、10 次分类；LRCX 4 次空返回） |
| p3 | 同上 | 4（全部空返回） |
| 单次诊断 ×3 | 看原始返回（其中一次是我误重跑，多花 1 次）；关闭推理的对照 | 各 1 |
| p4 | 计数脚本不兼容新参数，LRCX 失败 | 1 |
| p5 | LRCX 简报发布；例行被财务缺失拦住（缺口 4） | 2 |
| p6 | **全部 complete，LRCX 基线建成** | 5（PEAD 分析 3、行业分析 1、分类 1） |
| p7 | 复用 | 1（GOOG） |

合计 35 次（不含 10-18 第一遍的 14 次），其中约 11 次是缺口 3 的空返回或诊断。

### 如实记录

- **停止条件的影响：** 今天为验证连续产生了 5 次 `incomplete`（p1–p5，原因依次是 LRCX 无资讯、抽取空返回、财务缺失，都是数据或模型输出问题，不是调度故障），p6、p7 均 `complete`。另有 6 条 `running` 认领：它们是我这些失败尝试留下的**已死亡**工作进程（最后一条历史记录均为 `execution_error`，没有存活进程），按设计只在下一次负责人切换时作废并留痕，不影响定时 tick（认领键各不相同）。
- **没做：** 常驻进程的真实定时触发（周一 08:00/09:00 纽约时间）仍待观察；LRCX 事件评审要等 10-21 盘后；GOOG 的窗口标签翻转未深查；`ibkr_news` 的另外两个缺陷（partial 被当失败、`persistent_queue.run_one` 超时分支的 `UnboundLocalError` 掩盖原因）本次没动。
- 本次 `ibkr_news` 摄入：发现 366 篇，准入 8 篇，因正文长度隔离 2 篇，356 篇因每轮正文预算上限（10 篇）延后——这是常态预算限制，不是故障。
