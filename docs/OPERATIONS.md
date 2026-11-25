# 运维手册

> 读者：运行和维护这套系统的人。讲**现在**怎么装、怎么看、怎么停、怎么修。
> 设计与契约见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，代码约定见 [`DEVELOPMENT.md`](DEVELOPMENT.md)。
> 命令默认已激活 `.venv`（或写成 `.venv/bin/ats …`）。

## 1. 有哪些服务

| launchd 服务 | 做什么 | 进程 | 节奏 |
|---|---|---|---|
| `com.ats.data-refresh` | 数据刷新控制器：结构化/非结构化采集、文档准入、财报日历刷新、FactSet 月末摄入 | `python -m ats.data.refresh tick` | 每小时一次 |
| `com.ats.dispatcher` | 研究调度（Dispatcher）：按时间表与日历事件运行 7 个研究工作流；**不进 Chief、不下单** | `ats schedule` | 常驻 |
| `com.ats.serve` | 飞书回调端点（审批已改为命令行 `ats approval`，现行审批不经过它） | `ats serve --host 127.0.0.1 --port 8000` | 常驻 |


plist 在 `~/Library/LaunchAgents/`；`data-refresh`、`dispatcher` 的模板在 `deploy/launchd/`。

```bash
launchctl list | grep com.ats                                     # 状态（PID 一列为 - 表示当前没在跑）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ats.dispatcher.plist   # 加载
launchctl kickstart -k gui/$(id -u)/com.ats.dispatcher               # 重启（改了 src/ 后必须做）
launchctl bootout gui/$(id -u)/com.ats.dispatcher                    # 停掉全部研究调度
```

- **改了 `src/ats/` 必须重启对应常驻进程。** 常驻进程懒加载模块，不重启会出现新旧代码混跑且不报错。
  重启挑在两次调度之间。
- `launchctl` 在 Claude Code 等沙箱里执行不生效，要在沙箱外执行。
- 曾出现 `data-refresh` 在切换操作后掉出 launchd 的情况，原因不明；`launchctl list`
  里看不到时重新 `bootstrap`。

## 2. 每天看什么

```bash
ats data health --consumer information   # 立即检查一个分析师读的数据产品
ats data health-log                      # 数据产品健康状态的变化记录（只在由好转坏、由坏转好时记一行）
ats dispatch owner-log               # 7 个研究工作流的调度负责人及切换历史
ats workflow runs --limit 20         # 最近的研究运行：complete / incomplete / failed
python -m ats.data.refresh status    # 各采集任务最近一次结果
ats events upcoming --days 14        # 未来两周的财报与宏观事件
ats events candidates                # 来源冲突、被挡住等你裁决的事件
```

日志在 `var/logs/`：`dispatcher.{out,err}.log`、`data-refresh.*.log`、`serve.*.log`。

**每周看一次事件评级校准报告**（event-impact-routing；事件成分开关打开后才有内容）：周六行业评审之后自动写到行业报告目录，
文件名 `事件评级校准-<日期>.md`。看四件事：

- **评级分布**：一周里各档重要性的成分数。重大、极重大突然增多或长期为零，都值得翻一下原文。
- **下游复核分歧**：基本面重大事件评审与层次分析对成分重要性的复核，不以"一致 / agree"开头的都算分歧，逐条列出。
- **层面补认领**：层次分析认为"标为本公司、其实有整层传导"的成分，是信息岗影响面漏判的记录；多了说明评级口径要调。
- **事后对照**：各档重要性对应的事发后 1–3 个交易日平均|超额收益|（相对所在层其他成员的等权篮子）。应随重要性单调上升；
  只用来校准评级，不改写历史评级。行情取不到时报告会写明。

报告最后是当周重要性最高的几个成分，供抽检。生成代码在 `workflow/event_calibration.py`，纯代码、不调用模型。

## 3. 研究调度

### 3.1 时间表与事件

时间表在 `config/workflow/research_schedules.yaml`，只有负责人是 dispatcher 时才生效：

| 工作流 | 时间 | 范围 |
|---|---|---|
| `information-brief` | 周一至周五 08:00 纽约 | `portfolio:pead-targets`；事件成分开关（`config/workflow/event_impact.yaml`）打开后，另含 observe 名单中的产业链成员与命题证人 9 家（轻量简报，不触发基本面） |
| `fundamental-routine` | 周一至周五 08:30 纽约（依赖当天的信息简报） | `portfolio:pead-targets` |
| `technical-review` | 周一至周五 08:30 纽约 | `portfolio:portfolio` |
| `macro-review` | 周六 09:00 台北 | `portfolio:portfolio` |
| `sector-review` | 周六 09:30 台北（Dispatcher 先补跑 `layer-review`） | `sector:ai_hardware` |

日历事件的路由在 `config/workflow/event_routes.yaml`，常驻进程每 30 分钟（每小时 :05、:35 UTC）消费已发布的事件，重复消费不会重复派发：

- 财报**计划**（前 14 天内）→ 信息简报 + 基本面例行（例行模式在财报前 `prep_days_before`＝3 个交易日内建立基线，缺则每个交易日补建）。
- 财报**已发布**且已准入公告或申报材料 → 信息简报 + 基本面事件评审（冻结基线、打分）。
- FOMC、CPI、PCE、NFP、GDP 发布 → 宏观评审。

- 简报里评级为**重大及以上**、且首发或升级的**事件成分**（`material_development`，`config/workflow/event_impact.yaml`
  的 `routing.enabled` 打开后才生效，现为关闭）→ 本公司成分：该公司的重大事件评审（`fundamental-material`）；
  本层/跨层成分，以及产业链成员的重大本公司成分：所涉层的层次分析，随后一次行业评审（临时复审，单次最多下调一档）。
  同一节拍内同一任务与范围只跑一次（带上全部合格成分），当天出现升级或新成分可再触发；不启动 Chief。上线前须在调度负责人表里
  登记 `fundamental-material` 为 dispatcher（`ats dispatch` 的负责人切换，需批准）。

两类事件唤醒的开关在研究时间表配置的 `event_triggers`：`earnings`（财报事件，现为 `true`）、
`macro_sector`（宏观数据发布，现为 `false`，宏观只按周跑）。

这台 Mac 会睡眠。睡眠期间没有进程在跑，任务只能等醒来后补。常驻进程里有一个每 30 秒一次的「心跳」任务（不写日志、不写台账），让调度器醒来后最多晚 30 秒就按系统时钟发现并触发过点的任务；没有它，调度器会按「不计睡眠时间」的计时器继续等，晚好几个小时（2026-10-21 信息简报 20:00 的任务 22:31 才开始）。

各工作流可补跑的宽限在 `config/workflow/trigger_policies.yaml`：信息简报、基本面例行 12 小时，技术面 4 小时，宏观、行业、层次 48 小时；超出宽限记为 `misfire_grace_exceeded` 跳过。

### 3.2 可读报告

分析的结果是已发布的投影；可读报告是它们的 Markdown 副本，写到各自配置的 `output_dir`（现均为 Obsidian 的「个股分析系统」目录）。报告生成失败只记录，不影响分析本身。同一天再生成一份时不会覆盖原文件，而是写成 `…-v2.md`、`…-v3.md`。

| 报告 | 谁生成、什么时候 | 文件名 |
|---|---|---|
| 宏观周报 | 宏观任务内，每周六 | `宏观分析-<标签>-<日期>.md` |
| 技术面读数 | 技术面工作流**整次运行结束后**汇总所有标的写一份（任务是逐标的的，所以由运行结束后统一写） | `技术面读数-<标签>-<日期>.md` |
| 行业报告、层级报告、层分析页面 | 行业评审任务内，每周六 | `行业分析-…`、`层分析-…-<层>-<日期>.md`、`层分析-…-<日期>.html` |
| 产业链证据周报 | 行业评审**之后**，每周六 | `产业链证据-<标签>-<日期>.md` |
| 基本面（PEAD）报告 | 基本面例行 / 事件评审内 | `基本面分析-<标的>-<期次>.md` |
| 事件评级校准 | 行业评审**之后**，每周六；本周没有事件成分时不写 | `事件评级校准-<日期>.md` |
| 重大事件评审 | 每份基本面重大事件评审产出之后（由简报里的重大事件成分触发），每份一篇；同日再评写 `-v2` | `重大事件评审-<代码>-<日期>.md` |

本次运行只复用了已有投影、没有新发布时，不会再生成一份重复的报告。技术面报告的数据来自当次发布的投影，所以不显示 VIX 与 VIX3M 的具体数值，只显示是否触发恐慌层。

### 3.3 手动运行

```bash
ats workflow run --task macro-review --scope-kind portfolio --scope-id portfolio
ats workflow run --task sector-review --scope-kind sector --scope-id ai_hardware
ats workflow run --task fundamental-routine --scope-kind portfolio --scope-id pead-targets
ats workflow run --task information-brief --scope-kind portfolio --scope-id pead-targets
ats workflow run --task technical-review --scope-kind portfolio --scope-id portfolio
ats workflow run --task layer-review --scope-kind sector --scope-id ai_hardware            # 八层；单层见 LAYER_ANALYST
ats workflow runs --limit 5
ats workflow retry --trigger-key <key> --actor <谁> --reason <为什么>   # 只重试失败的那一个任务
```

- 依赖的数据版本未变、投影仍在有效期内时直接复用，**不重复请求模型**。要在数据没变时强制重算，
  用 `--inputs-json '{"<task_id>": {"force_recompute": true}}'`。
- 旧手动入口（`ats macro review`、`ats pead prep`、`ats analyst information` 等）已退役，会提示对应的 `ats workflow run` 写法。
- 某只票的重大事件评审要手动补跑：`ats workflow run --task fundamental-material --scope-kind entity --scope-id <SYM>
  --inputs-json '{"fundamental-material": {"component_refs": ["<简报投影 id>#<成分编号>"]}}'`（成分编号在简报的 `event_components` 里）。
- 公司风险项初始化（event-impact-routing 4.1，一次性）：`python -m ats.agents.fundamental.material --init` 先列出缺风险项的
  target 与请求数，确认后加 `--apply` 执行；之后每次建财报基线时自动刷新。要让已有清单立刻吸收近期简报里的新风险，
  加 `--rebuild`（全部重建一版，保留上一版引用；每个标的 1 次请求）。
- 某只票要手动刷新财报前基线：`ats workflow run --task fundamental-routine --scope-kind entity --scope-id <SYM>
  --inputs-json '{"fundamental-routine": {"refresh_baseline": true}}'`。

### 3.4 停用与恢复一个工作流

```bash
# 停用：定时与日历事件不再唤醒它（不需要批准，随时可做；有正在运行的认领时拒绝）
ats dispatch pause --workflow <workflow_id> --actor <谁> --reason "<为什么>"
# 恢复：交回 Dispatcher（需要批准原文；检查数据产品健康；有未完成认领须说明作废理由）
ats dispatch resume --workflow <workflow_id> --actor <谁> --approval "<批准原文>" --reason "<为什么>"
# 查看停用 / 恢复的追加式记录
ats dispatch owner-log [--workflow <workflow_id>]
```

停用的工作流不会被任何定时任务或日历事件运行；要补跑，先 `resume`，再用 `ats workflow run`。

停用期间到点的触发不会丢得无声无息：定时、手动、日历三条路径对每个被跳过的触发各写一条 `skipped_owner_legacy` 记录（同一触发只记一次，在 `ats dispatch runtime-state` 输出的 history 里可查）。
覆盖核验（`ats dispatch coverage-expected` 与 `coverage-compare`，r5 8.8 前叫 `runtime-expected` / `runtime-compare`）会把停用区间内到点的触发标为 `skipped_by_pause`，不算遗漏；因停用而作废的触发标为 `voided_by_pause`。
只有 `pause` 开出的区间才算停用：从未迁移的工作流虽然负责人也是 `legacy`，它的触发仍按遗漏处理。
旧名 `owner-rollback` / `owner-switch` 已改名，运行它们会提示新命令且不改任何状态。

> 已知技术债：库里「已停用」仍存成内部值 `legacy`（只改了对外的命令与文档用语）。长期做法是取消这个开关，
> 由 `research_schedules.yaml` 的 `enabled: false` / `event_triggers` 决定是否运行；见
> `openspec/changes/archive/2026-11-12-retire-legacy-and-migration-tooling/verification.md` 的「上线后待办」。

- 一次事务同时改该工作流的模板行与所有标的行，不会出现一半新一半旧。
- 有 `running` 认领时拒绝切换；`failed/pending` 认领须带理由作废；`owner-log` 只增不改。
- 工作进程已死亡但仍是 `running` 的认领，只在下一次负责人切换时作废，不影响定时触发。

**出现任一情况就停用该工作流：** 同一逻辑触发重复认领；
必需投影缺失或认领长时间 `running`；连续两次整批失败且原因是新逻辑缺陷（不是"该标的没数据"）；
模型请求费用异常；任何券商写入尝试。

## 4. 数据产品健康

每个研究分析师（`information`、`fundamental`、`layer`、`macro`、`sector`、`technical`）读的数据产品在两个时点检查：

- **发布时**：数据刷新控制器每次跑完任务，检查 information、fundamental、layer、macro、sector 五个分析师读的产品
  （可读、版本/哈希/来源/血缘齐全、全体标的不是空的）。technical 读的是运行时行情，不在这里检查。
- **读取前**：Dispatcher 要真正执行某个分析师的任务时（可复用上一次投影时不查），先检查它读的产品。不合格就不执行，
  该任务记为 `blocked`、缺口 `data_product_unhealthy`，整次运行 `incomplete`。

两处都只告警；同一个分析师只在状态由好转坏、由坏转好时发一次飞书并在工作流主库（`var/ats.sqlite` 的 `data_health_log`）记一行。2026-11-11 之前的记录在 `var/phase_f_cutover.sqlite` 的 `consumer_cutover_log`，只读。

```bash
ats data health --consumer <id>                  # 立即检查一个分析师读的数据产品
ats data health-log [--consumer <id>]             # 健康状态变化记录
```

读路由表与读取资格账本已在 2026-11-11 删除（switch-trade r5 9.3）：每个消费者只按自己声明的数据产品、在当次入口绑定的业务 scope 内读取，越界或产品不合格时显式失败。`ats consumer-cutover`、`ats data assurance` 已退役，用法存档见 `docs/archive/R5C_MIGRATION_COMMANDS.md`。

Chief 不在其中（交易路径未切换）。

## 5. 数据刷新

任务定义在 `config/data/schedules.yaml`（`enabled`、频率、`entities:` 展开、`{entity}`/`{source}`/`{prev_month}` 占位符）。

```bash
python -m ats.data.refresh status
python -m ats.data.refresh validate                     # 校验配置
python -m ats.data.refresh tick --job <job> --dry-run   # 看某个任务这次会做什么
python -m ats.data.refresh tick --job <job>             # 立刻跑一次
```

- 持久化采集统一走受管队列（`var/persistent_ingestion.sqlite`），每个任务幂等；只有持有有效租约的 worker
  才调用来源适配器。即时行情、期权与券商查询不排队、不存为研究事实。
- `derived` 类任务：财报日历刷新（`calendar_*`）、证据抽取、FactSet 月末摄入（每月 1 日 00:30 纽约，
  摄入上月最终版）、第三方序列的链证据采集（`chain-source-collect`）：L1 三个证据观察器每天 09:00 UTC
  （`chain_l1_governed_evidence_daily`），韩国/台湾出口与 DRAM 合约价每周一 09:30 UTC
  （`chain_regional_*_weekly`）。这类任务不调用模型。
- 财报申报每天分两班进入日历，06:00 和 18:00（台北）。每班在同一次数据刷新里按顺序跑：EDGAR 文件发现（`sec_edgar_discovery_*`，
  只查财报日在前 3 天到后 1 天内的标的，每家一次请求）→ SEC 正文（`sec_official_body_*`）→ 发布确认（`calendar_finalize_*`）。
  美股盘后 16:00 的财报，8-K 几分钟后就在 EDGAR 上，次日 06:00 那班就能确认；DefeatBeta 文件索引（每天 08:00 UTC，上游约 08:35 才更新）
  照常运行，作为全量的兜底。所有访问 sec.gov 的请求共用一个跨进程的节流（`ats.data.sec_rate`，默认每秒 4 次，SEC 上限是 10 次），
  一旦 SEC 回 429 或“Request Rate Threshold Exceeded”，所有进程暂停 10 分钟，状态在 `var/run/sec_rate.json`。
- SEC 正文任务每次最多取 20 份申报，**请求预算只算入库成功的申报**：
  取不到正文的（如没有新闻稿附件的 8-K）退还请求；已入库的 7 天内不再取；同一份在 3 个不同日子都找不到（或 5 个不同日子都失败）就不再试；
  预算用完没轮到的记为“延后”而不是失败，下次排在最前。
- 来源与数据集的完整说明见 [`STRUCTURED_DATA_OPERATIONS.md`](data/STRUCTURED_DATA_OPERATIONS.md)。

## 6. 事件日历

```bash
ats events upcoming --days 30
ats events status
ats events candidates --status conflict                 # 被挡住的冲突（不加 --status 会列出全部已准入候选）
ats events override --event-id <id> --date YYYY-MM-DD --actor <谁> --reason "<依据>"
ats events cancel   --event-id <id> --actor <谁> --reason "<依据>"
ats events withdraw --event-id <id> --actor <谁> --reason "<依据>"
```

两个来源只是精度不同（一方多给了具体钟点）时视为兼容并自动合并；日期或时段（盘前/盘中/盘后）
真不一致时，被挡住的是另一来源那一条（`conflict`），日历里该事件仍按已采用的一条（通常是 Finnhub）存在；
挂起的只是一条备选说法，距离财报日还远时可以不处理，临近时对照公司公告核一次，不一致再 `override`。

## 7. 交易路径（已切换 S1–S5；实盘锁定）

- 切换进度（2026-11-01）：Chief、风控、Trader、Clerk 改读新路径（S1；读路由表本身已于 2026-11-11 随 R5d 删除，读取只按声明与业务 scope），决策记录读取方读审计表（S2），命令行审批与账本成对切换的代码已生效（S3），**`clerk-daily` 已启用**（S4，交易日 18:00 纽约；11-02 起由 16:30 改为 18:00）。**`chief-daily` 已启用**（S5，2026-11-02；11-03 由 09:00 改为 09:45 纽约，台北冬令时 22:45，因为订单规范化只接受常规时段内的实时买卖价，开盘前的周期过不了，见 blockers B9）。批准时重审存下来的报价只验绑定，不再按 30 秒判过期（B1，`5f38b8b`）；Clerk 的账本读取各限时 30 秒、对账读失败的窗口记 `failed` 可重跑（B4，`d0da96a`）。记录见 `openspec/changes/archive/2026-11-12-switch-trade-path-and-retire-r5/blockers-2026-11-01.md`。
- Chief 每个交易日由 `chief-daily` 自动跑一次；也可手动运行：

  ```bash
  ats chief run --channel pending   # 默认 dry-run；不要加 --live 或 --yes
  ats chief show
  ```

- 审批只在命令行完成（`ats approval`），没有飞书审批卡，也没有签名链接；`com.ats.serve` 不参与审批。
- 实盘锁定：`ats live gate` 在没有授权引用时返回 `refused / no_live_authorisation`。实盘授权库里有一行来源
  未核实（已作废）的 `LIVE-X`，不得把它当作实盘授权。
- **`--yes`（自动批准）在实盘环境绝对不能用。** 系统里记录的止损价只是声明，不会在券商挂真实止损单。

### 7.1 切换后的操作

> **状态：** S1–S5 已生效；`clerk-daily` 第一次定时运行已观察到（11-03 18:00 纽约 = 台北 11-04 07:00，completed，日报已生成）；`chief-daily` 第一次定时运行（11-03 09:45 纽约）因模型输出被截断以 `manual_review` 结束（B10，已修），修复后由手动运行验证了完整链路（建周期、风控驳回一次后修订、停在 `pending_approval`），定时路径的第二次运行见 11-04 09:45 纽约。
> 各步见 `openspec/changes/archive/2026-11-12-switch-trade-path-and-retire-r5/design.md` D3。

**现状事实（切换后）**

- Chief 的上下文只读研究快照与 Internal State；决策、风控、审批都只认审计表（`decision_cycles` / `decision_revisions` / `risk_reviews` / `boss_approvals`），旧决策表只剩镜像写，观察窗口结束后随 R5 移除。
- 审批只在命令行完成（`ats approval`），没有飞书审批卡，也没有签名链接。`com.ats.serve` 不再参与审批。
- 只有**需要下单且已通过风控**的周期才会等你。纯持有、没有订单、或风控未通过的周期不会出现在待审批列表。
- 实盘仍然锁定（三道锁，见下）。批准后周期的终态是 **`approved_not_executed`（已批准未执行）**：Trader 已跑完授权十字段、新鲜度、实时报价、资金上限检查并记录每项结果，在券商交接点被拒绝，没有任何订单到达券商。

**每天会发生什么**

| 工作流 | 时间（工作日，仅 NYSE 交易日） | 内容 |
|---|---|---|
| `clerk-daily` | 18:00 纽约（收盘后；台北次日 07:00） | 对账：把成交与持仓写入账本（只认审计表） |
| `chief-daily` | 21:00 台北（纽约 08:00 盘前，夏令 09:00；排在 19:00/20:00 的研究之后；2026-11-25 起，原为 09:45 纽约，见 7.2） | 跑一个决策周期（非演练、实盘锁定）；盘前用延长时段 venue；有订单且过风控则进入 `pending_approval`，并给你发一次提醒（不带链接），审批截止为常规收盘前 4 小时 |

**成交读取的两层保障（B11，tasks 11.12）：** TWS 的 `reqExecutions` 只返回账户“自午夜起”（TWS 本地时间 = 台北）的成交，所以台北 07:00 的 Clerk 看不到前一晚午夜前的成交。补救有两层：(1) dispatcher 里的 `phase_e:fills-capture` 每天台北 23:58 读一次当日成交（`ats clerk capture-fills` 可手动跑一次；只记成交，不动 Clerk 窗口），迟到超过 10 分钟就跳过；(2) 每次 Clerk 对账后用 IBKR Flex 对账单（`.env` 的 `IBKR_FLEX_TOKEN`、`IBKR_FLEX_QUERY_ID`，查询 `ats-trades`，美东时间，落后一个营业日）补入库里没有的成交，建账日之前的不补，已有同一笔成交（含手工补录行）的不重复（`ats clerk flex-backfill --dry-run` 只看）。Flex 拉不到只记在对账摘要的 `flex` 里，不让 Clerk 失败。令牌一年到期，到期前要在 IBKR 网页重新生成并改 `.env`。

**隔夜时段下单（tasks 11.13）：** 订单的 `execution_basis.venue` 为 `regular`（默认，SMART，常规时段实时买卖价）或 `overnight`（合约交易所 `OVERNIGHT`，IBEOS）。定时的首席运行若落在隔夜时段（美东周日 18:00 起、周一至周四 20:00 起，至次日 03:50；周五夜间不开）就用 overnight：价格读 OVERNIGHT 合约的实时买卖价（≤30 秒、点差 ≤1%，合约交易时段已关则 fail-closed），只下限价单，审批与执行前检查沿用订单绑定的 venue。审批截止、收盘撤单、盘口深度上限、盘前延长时段 venue 与前移后的时间表见 7.2。

两者在 `config/workflow/trade_schedules.yaml` 里默认 `enabled: false`（对外叫“停用”）。启用某一个：把它改成 `enabled: true` → `ats dispatch resume --workflow <id> ...`（第 3.4 节）→ **最后**重启 `com.ats.dispatcher`，并用 `launchctl list` 确认新 PID、看 `var/logs/dispatcher.err.log` 里的 `dispatcher started: workflows=…` 一行含该工作流。每一步都需要你批准。**必须先 `resume` 再重启**：Dispatcher 在启动时才按负责人决定注册哪些交易工作流，`resume` 前启动的进程不会注册它（2026-11-01 启用 `clerk-daily` 时核实）。停用：`ats dispatch pause --workflow <id> ...`，不需要批准。

**审批：查看 → 批准 / 修改 / 拒绝**

```bash
ats approval pending                  # 待审批周期、修订 hash、订单、风控结论、已等多久
ats approval show <cycle_id>          # 完整审批对象：快照、订单、风控审查、锁定原因
ats approval approve [<cycle_id>] [--actor <谁>]     # 终端里：看审批卡，输入 approve 确认
ats approval revise  [<cycle_id>] [--note "<修改意见>"]
ats approval reject  [<cycle_id>] [--note "<备注>"]
ats approval approve <cycle_id> --revision <hash 前缀，至少 12 位>   # 脚本或管道：必须写明
```

- **终端里不用敲 hash：** 不带周期时，只有一个待审批周期就直接选中，多个则让你选序号。命令打印审批卡（含修订号和 hash），你输入完整的 `approve` / `revise` / `reject` 才执行，其他任何输入都取消；`revise` 会提示输入意见。答复绑定的是这张卡上的 hash，确认前若出现更新的修订会拒绝。
- `show` 默认把研究快照引用折叠成一行汇总，`ats approval show <cycle_id> --full` 看全部。

- `approve` 本身就是确认动作：命令先取新的组合快照，对**同一修订**确定性地重新风控，打印与审批时相比的变化，然后立即继续执行；
  不会再弹第二次审批，也不会生成新的修订。快照新鲜度预算是 60 秒，所以不要把“确认”和“执行”拆成两步。
- 重新风控若给出**不同**结论（例如价格波动后超限），命令拒绝执行并说明差异；需要你 `revise` 或 `reject`。
- `revise` = 驳回当前修订并给出修改意见：Chief 只据此再改一次，新修订重新过风控、重新等你；一个周期最多 2 次，第三次会被拒绝。
- `reject` = 周期终止（`approval_rejected`）。
- 脚本或管道必须写明周期和 hash 前缀：旧修订、旧周期的答复不会落到新修订上。hash 不匹配、短于 12 位、周期已终态都会被拒绝，报错会说出当前修订的 hash 前缀。
- 同一周期重复 `approve` 会直接返回第一次的结果，不会再执行一次。
- 新一轮 `chief-daily` 开始时，仍在等待的上一周期会被标为 `superseded` 并给你发一次说明；已经批准过的不受影响。
- 没有人点审批、周期一直 `pending_approval` 本身不是故障，只会过期；不要因此用 `--yes`。
- 手动跑一个周期：`ats chief run --channel pending --live`（`--live` = 非演练，与 `chief-daily` 一致；实盘锁定下批准后终态为 `approved_not_executed`）。不加 `--live` 是演练，批准后不走 Trader 的检查。`--no-execute` 只跑到风控，但同样会留下一个待审批周期，直到被下一轮 supersede，目前是已知行为。

**三道实盘锁（本 change 一律不动）**

1. `trader/execute.py` 的 `AUTO_EXECUTION_ENABLED = False`（C3）：生产里任何券商提交都被拒绝；
2. 实盘路由 `live_trader` 为 `disabled`；
3. `ats live gate` 没有有效的实盘授权引用。授权库里那行 `LIVE-X`（占位字段、来源未核实）按你 2026-10-21 的确认作废，作废记录追加、原记录保留，实盘门不会再把它当授权。

**切换期的边界开关已不再可查。** `ats cutover` 已随 r5 8.4 退役（只剩提示桩）；它显示的六个边界开关从未接线，本就不是权威状态，
读路由与切换控制库已在 R5d（2026-11-11）停用，库文件留作历史只读。真实的状态看：数据产品用 `ats data health`，
调度负责人用 `ats dispatch owner-log`，切换发生在哪个提交见 `openspec/changes/archive/2026-11-12-switch-trade-path-and-retire-r5/verification.md`。

**怎么读 `ats dispatch owner-log`。** 它是"每个工作流的定时触发由谁负责"的审计日志：负责人要么是 `legacy`（旧的 launchd 触发），要么是 `dispatcher`。
每一条是一次切换或回退，看这几个字段就够了：`workflow`（哪个工作流）、`action`（`switch` 切过去 / `rollback` 切回）、`from_owner → to_owner`、`at`（时间）。
一个工作流**当前**归谁，看它在日志里的**最后一条**。`approval` 字段原样记录你当时授权这次切换的回复，因为切换负责人是生产变更，
日志要留下"谁批准的"的证据，所以读起来像聊天记录；它不参与工作流的运行。现在日志里有 7 个研究工作流（`information-brief`、`fundamental-routine`、`fundamental-event`、
`technical-review`、`macro-review`、`layer-review`、`sector-review`），最后一条都是 `dispatcher`。`clerk-daily` 与 `chief-daily` 在 S4、S5 启用时各有一条 `resume` 记录，带着你的批准原话（`ats dispatch owner-log` 看最后一条确认当前负责人）。

**回退**

1. 停用：`ats dispatch pause --workflow chief-daily --actor <谁> --reason "…"`，再停 `clerk-daily`（不需要批准）。
   从未恢复过的工作流还没有调度归属记录，`pause` 会拒绝并报 `workflow has no declared schedule owner`。这表示它本来就是停用的（dispatcher 不注册它，`launchctl` 重启后日志里只列研究工作流），这一步跳过即可（2026-10-29 T0.7 演练实测）。
2. 必要时按 S3 → S2 → S1 倒序 revert 对应提交，重启 `com.ats.dispatcher`，用 `launchctl list` 确认 PID。镜像写保留，旧读取方仍能看到观察期内的决策，回退不需要恢复数据。
3. 实盘锁定期间没有在途订单需要排空，但要做一次路由冻结核验，确认没有未终结授权。

**观察窗口的停止条件**（任一出现就停用两个工作流并记录）：出现真实券商提交；主理人上下文出现旧表读取；批准的 hash 与风控修订不一致，或旧修订被接受；
决策链（快照 → 修订 → 风控 → 审批）断裂；Clerk 对账与 TWS 不一致且无法解释；同一触发重复执行或同一输入重复发模型请求；主理人连续 2 个交易日因非数据缺口原因失败。

### 7.2 隔夜与盘前时段运行

> **状态（2026-11-25）：** 时段判定、审批截止、隔夜收盘撤单、盘口深度上限、盘前延长时段 venue 已实现（overnight-session-scheduling 1.1–2.4）。
> 隔夜 venue 已于 11-25 台北上午演练通过（3.1）。**调度已于 2026-11-25 11:38 台北前移**（3.2，你决定跳过盘前演练 3.1b，直接切换并观察当晚运行）；两周观察见 change 的 3.3。

**时间表（前移后，cron 一律用 `Asia/Taipei` 声明，冬夏令不调整）**

| 任务 | 现在（台北冬令） | 前移后（台北） |
|---|---|---|
| `clerk-daily` | 07:00 | 不变 |
| `information-brief` | 21:00 | 19:00 |
| `fundamental-routine`、`technical-review` | 21:30 | 20:00 |
| `chief-daily` | 22:45 | 21:00（纽约 08:00 盘前，夏令 09:00），用延长时段 venue |
| 审批截止 | 无 | 常规收盘前 4 小时：冬令台北 01:00，夏令 00:00；提前收盘日更早（如感恩节次日 13:00 收盘，截止台北 22:00） |
| 隔夜挂单撤单 | 无 | 03:40 纽约（冬令台北 16:40，夏令 15:40），只处理隔夜 venue 的单 |

**三种 venue。** 首席按运行时刻选：常规时段开着用 `regular`（SMART，常规时段）；IBKR 隔夜时段（台北冬令约 09:00–16:50）开着用 `overnight`（交易所 OVERNIGHT）；纽约交易日盘前（04:00 至开盘，台北冬令 17:00–22:30）用 `extended`（SMART，`outsideRth`，限价当日单，盘前、常规、盘后一路挂到 20:00 纽约由 IBKR 自动失效）。后两种只用于定时首席；手动 `ats chief run` 仍只认常规时段。IBKR 的“隔夜 + 当日”（OND）生效时间需要 `includeOvernight`，`ib_async` 2.1.0 不支持；从台北 21:00 提交时，`extended` 与 OND 效果相同。

**运行前的时段判定。** `chief-daily` 每次触发先判定它要用的时段是否开放：常规 → 隔夜（交易所日历，再问 IBKR 上 OVERNIGHT 合约自己的交易时段，探测标的 NVDA；连不上 TWS 时以日历为准）→ 盘前。都不开放（周末、美国节假日、盘后到隔夜之间）时不建周期、不发审批，调度历史里记一条 `skipped_session_closed`（`schedule_runtime_history`）。盘后（16:00–20:00 纽约）不算可运行时段：离当日单失效不到 4 小时。

**审批截止。** 隔夜提案截止 = 隔夜收盘 − 4 小时；盘前提案截止 = 当日常规收盘 − 4 小时。截止时间写进审批请求，出现在：审批卡正文第一行（`⏰ 审批截止：…`）、待审批提醒、`ats approval approve` 的审批卡与 `ats approval show`（已过截止会注明）。截止后：`ats approval approve` 直接拒绝；从其他入口恢复的批准，执行前检查拒绝（`approval_deadline_passed`），不下单。不能在本周期补批，等下一个首席周期。常规时段的提案没有这个截止。

**报价。** 隔夜单读 OVERNIGHT 合约的实时买卖价；盘前单读 SMART 的实时买卖价（盘前为 `session=extended`，开盘后为 `regular`，所以开盘后才批准的盘前单也能做执行前检查）。都要求 ≤30 秒、点差 ≤1%；点差超限或没有双边报价的标的本轮跳过，其余照下。按盘前报价定的限价会带入常规时段（你 11-25 确认接受）。

**未成交订单。** 隔夜 venue：dispatcher 每个工作日 03:40 纽约跑 `phase_e:overnight-session-end`，只读列出系统在 OVERNIGHT 上仍在途的订单（`ats:` 前缀；常规与盘前单、你在 TWS 手下的单不碰），有就撤单，成交记录的 `error` 记 `cancelled_session_end`，不重新提交。撤单后仍在途或撤单被拒（例如 dispatcher 进程没有写授权），会给你发「⚠️ 隔夜挂单未撤净」。盘前 venue：当日单由 IBKR 在 20:00 纽约自动失效，不另撤。实盘锁定期间系统不会有在途订单。

**流动性上限。** 隔夜与盘前单的单笔金额上限取常规单笔上限与「盘口一档数量 × 价格 × 5」中较小者（买看卖一，卖看买一）。超限走现有风控边界：违规 `overnight_book_depth` 带可接受上限，主理人按边界缩量后重新过风控。报价里没有盘口数量的单不放行。

**回滚。** 把 `config/workflow/research_schedules.yaml` 与 `trade_schedules.yaml` 的 cron 改回原值（information-brief 08:00、fundamental-routine 与 technical-review 08:30、chief-daily 09:45，均为 `America/New_York`），重启 `com.ats.dispatcher`，用 `launchctl list` 确认新 PID。时段判定、截止与撤单留在代码里不影响常规时段运行。

## 8. 首次安装

1. 依赖：`brew bundle --file=Brewfile`（FactSet 本地 OCR 用 tesseract），然后
   `uv sync --python 3.12 --extra data --extra broker --extra schedule --extra memory-persist --extra channel`。
   不要用 `pip install` 补包。
2. 密钥写入 `.env`（已 gitignore，`cp .env.example .env` 起步），不要贴进聊天或代码，明文暴露过的立即轮换：
   - 模型：`OPENAI_API_KEY`（OpenRouter）、`OPENAI_BASE_URL=https://openrouter.ai/api/v1`。
     费用上限在 OpenRouter 的 key 上设，应用内不设护栏。
   - 宏观：`FRED_API_KEY`。
   - IBKR：`IBKR_HOST=127.0.0.1`、`IBKR_PORT`（paper 7497 / live 7496）、`IBKR_CLIENT_ID`（每个进程会按 pid 偏移）。
   - 飞书群机器人：`FEISHU_BOT_WEBHOOK`、`FEISHU_BOT_SECRET`；审批链接：`FEISHU_APPROVE_BASE`（公网前缀）、`FEISHU_APPROVE_SECRET`（签名密钥）。
3. IBKR TWS 或 Gateway：开启 Socket API，Trusted IPs 加 `127.0.0.1`。TWS 每 24 小时登出一次。自检：`ats ibkr`。
4. 公网入口：`cloudflared tunnel --protocol http2 --url http://localhost:8000`，把得到的地址写进
   `FEISHU_APPROVE_BASE` 后重启 `com.ats.serve`。冒烟：`curl -m10 <BASE>/health` 应回 `{"ok":true}`。
5. 加载第 1 节的三个服务，按第 2 节检查。

## 9. 已知问题与排障

| 现象 | 原因与处理 |
|---|---|
| `ibkr_news` 采集曾每天挂满 30 分钟 | TWS 对 `reqContractDetails` 不回复、`ib_async` 默认无限等待；已加 20 秒超时与符号检索回退。若再出现，先看 TWS 是否登录 |
| `ibkr_news`、TrendForce、SemiAnalysis 每个窗口都是 `partial` | 正文请求预算 10 篇而每次选中数百篇，属常态；控制器把 `partial` 当失败重试、队列把它当终态，第二次会抛 `queue_task_not_claimable`——已知缺陷，未修 |
| sec.gov 对后台进程不可达 | 本机 Clash fake-ip（198.18.x），命令行进程到 SEC 的 TLS 被重置，浏览器正常。影响外国申报人（如 TSM）的官方披露 |
| 审批链接突然 404 / 不可达 | Clash TUN 用 GVisor 栈时会重置 cloudflared 到边缘 7844 端口的长连接；把 TUN 堆栈改为 System，`pkill cloudflared` 重起，更新 `FEISHU_APPROVE_BASE` 并重启 serve。人在 Mac 前也可以直接在本机打开 `http://127.0.0.1:8000/feishu/approve?...` |
| 抽取角色偶发「空工具调用」 | 隐藏推理吞掉了工具调用；`run_structured` 第一次空返回后会关闭推理重试 |
| 某只票的信息简报 `incomplete` | 多半是该标的 7 天内没有已准入资讯（数据缺口），看数据刷新与资讯覆盖，不是新逻辑缺陷 |
| 日历消费日志里出现 `IdentityConflict` | 同一场财报的“计划事件”记录与“财报日到了仍缺材料”的等待记录共用触发键；已改为保留旧记录并跳过，不再整批中断。事件升版（材料入库）后换新键 |
| 基本面报告第一、二节为空或“无比较依据” | 同一财季曾被存成两份档案（`FY2027Q1` 与 `Q1 FY2027`），已统一写法；基线读财报前已发布的记录。仍出现时先看 `ats pead show <代码>` 的阶段与预期维度 |
| 定时任务没跑 | 看 `dispatcher.err.log` 是否有 `misfire_grace_exceeded`；或该工作流被停用（`ats dispatch owner-log`） |
| 想知道一个事件成分触发了什么 | 查 `var/ats.sqlite` 的 `material_event_ledger`：`SELECT at,component_id,workflow_id,scope,outcome,reason,run_id FROM material_event_ledger WHERE cluster_key='<簇>' ORDER BY seq DESC`（代码里是 `material_events.ledger_rows(cluster_key=...)`）。`deduplicated` = 后续报道或同一成分；`rejected` = 引用了未登记的层或标的；`failed` 会在下一个节拍重试。`run_id` 可接着查工作流运行记录 |

## 10. 数据与备份

| 文件 | 内容 |
|---|---|
| `var/ats.sqlite` | 领域记录：决策、绩效、风控、Workflow Memory 与投影 |
| `var/data.sqlite` | 数据平台：原始与准入数据、数据产品 |
| `var/data_refresh.sqlite`、`var/persistent_ingestion.sqlite` | 采集账本与受管队列 |
| `var/phase_f_*.sqlite` | 调度认领、交易路由与授权台账（只增不改）；`phase_f_cutover.sqlite` 的读路由、切换与健康记录自 2026-11-11 起只读 |
| `var/checkpoints.sqlite` | 等待审批的暂停态 |
| `var/phase_f_evidence/` | 台账引用的证据文件；`tmp/` 下同名路径是指向这里的符号链接，清空 `tmp/` 后按 `MANIFEST.json` 重建 |

都不进 Git，自行定期备份。`tmp/` 只放可随时清理的临时文件，**被台账引用的证据不要写进 `tmp/`**。

## 11. 旧实现退役

逐条目的清零、对账与墓碑判据见 [`PHASE_F_RETIREMENT_RUNBOOK.md`](PHASE_F_RETIREMENT_RUNBOOK.md)。
`ats retirement` 只读，不改登记；退役任何条目都需要你明确批准。

## 12. 诊断工具

- 切换期工具已退役（switch-trade r5 第 8、9 组，2026-11-10/11）：`ats drill`、`ats cutover`、`ats shadow`、`ats batch`、`ats read`、`ats intake`、
  `ats consumer-cutover`、`ats data assurance` 与 `ats dispatch` 的所有权、回滚去重子命令只剩提示桩；删除前的用法存档在
  [`archive/R5C_MIGRATION_COMMANDS.md`](archive/R5C_MIGRATION_COMMANDS.md)，影子比较手册归档在
  [`archive/SHADOW_COMPARISON_RUNBOOK.md`](archive/SHADOW_COMPARISON_RUNBOOK.md)。实盘路由演练仍用 `ats live drill` / `ats live rollback-drill`。
- 历史手册（切换期间的逐 scope 授权、批次切换、持久部署许可、旧上线 checklist）已归档在
  [`archive/`](archive/)：`PHASE_F_OPERATIONS.md`、`PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`、
  `PHASE_F_PERSISTENT_RUNTIME.md`、`GO_LIVE.md`。
