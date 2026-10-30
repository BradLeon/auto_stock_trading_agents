# 运维手册

> 读者：运行和维护这套系统的人。讲**现在**怎么装、怎么看、怎么停、怎么修。
> 设计与契约见 [`ARCHITECTURE.md`](ARCHITECTURE.md)，代码约定见 [`DEVELOPMENT.md`](DEVELOPMENT.md)。
> 命令默认已激活 `.venv`（或写成 `.venv/bin/ats …`）。

## 1. 有哪些服务

| launchd 服务 | 做什么 | 进程 | 节奏 |
|---|---|---|---|
| `com.ats.data-refresh` | 数据刷新控制器：结构化/非结构化采集、文档准入、财报日历刷新、FactSet 月末摄入 | `python -m ats.data.refresh tick` | 每小时一次 |
| `com.ats.dispatcher` | 研究调度（Dispatcher）：按时间表与日历事件运行 7 个研究工作流；**不进 Chief、不下单** | `ats schedule` | 常驻 |
| `com.ats.serve` | 飞书审批回调（签名链接） | `ats serve --host 127.0.0.1 --port 8000` | 常驻 |


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
ats data health --consumer information   # 立即检查一个分析师读的数据产品（读路由已固定为 target）
ats data health-log                      # 数据产品健康状态的变化记录（只在由好转坏、由坏转好时记一行）
ats dispatch owner-log               # 7 个研究工作流的调度负责人及切换历史
ats workflow runs --limit 20         # 最近的研究运行：complete / incomplete / failed
python -m ats.data.refresh status    # 各采集任务最近一次结果
ats events upcoming --days 14        # 未来两周的财报与宏观事件
ats events candidates                # 来源冲突、被挡住等你裁决的事件
```

日志在 `var/logs/`：`dispatcher.{out,err}.log`、`data-refresh.*.log`、`serve.*.log`。

## 3. 研究调度

### 3.1 时间表与事件

时间表在 `config/workflow/research_schedules.yaml`，只有负责人是 dispatcher 时才生效：

| 工作流 | 时间 | 范围 |
|---|---|---|
| `information-brief` | 周一至周五 08:00 纽约 | `portfolio:pead-targets` |
| `fundamental-routine` | 周一至周五 09:00 纽约（依赖当天的信息简报） | `portfolio:pead-targets` |
| `technical-review` | 周一至周五 10:00 纽约 | `portfolio:portfolio` |
| `macro-review` | 周六 09:00 台北 | `portfolio:portfolio` |
| `sector-review` | 周六 09:30 台北（Dispatcher 先补跑 `layer-review`） | `sector:ai_hardware` |

日历事件的路由在 `config/workflow/event_routes.yaml`，常驻进程每 30 分钟（每小时 :05、:35 UTC）消费已发布的事件，重复消费不会重复派发：

- 财报**计划**（前 14 天内）→ 信息简报 + 基本面例行（例行模式在财报前 `prep_days_before`＝3 天内建立基线）。
- 财报**已发布**且已准入公告或申报材料 → 信息简报 + 基本面事件评审（冻结基线、打分）。
- FOMC、CPI、PCE、NFP、GDP 发布 → 宏观评审。

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
- 某只票要手动刷新财报前基线：`ats workflow run --task fundamental-routine --scope-kind entity --scope-id <SYM>
  --inputs-json '{"fundamental-routine": {"refresh_baseline": true}}'`。

### 3.4 停用与恢复一个工作流

```bash
# 停用：定时与日历事件不再唤醒它（不需要批准，随时可做；有正在运行的认领时拒绝）
ats dispatch pause --workflow <workflow_id> --actor <谁> --reason "<为什么>"
# 恢复：交回 Dispatcher（需要批准原文；检查读路由与数据产品健康；有未完成认领须说明作废理由）
ats dispatch resume --workflow <workflow_id> --actor <谁> --approval "<批准原文>" --reason "<为什么>"
# 查看停用 / 恢复的追加式记录
ats dispatch owner-log [--workflow <workflow_id>]
```

停用的工作流不会被任何定时任务或日历事件运行；要补跑，先 `resume`，再用 `ats workflow run`。

停用期间到点的触发不会丢得无声无息：定时、手动、日历三条路径对每个被跳过的触发各写一条 `skipped_owner_legacy` 记录（同一触发只记一次，在 `ats dispatch runtime-state` 输出的 history 里可查）。
覆盖核验（`ats dispatch runtime-expected` 与 `runtime-compare`）会把停用区间内到点的触发标为 `skipped_by_pause`，不算遗漏；因停用而作废的触发标为 `voided_by_pause`。
只有 `pause` 开出的区间才算停用：从未迁移的工作流虽然负责人也是 `legacy`，它的触发仍按遗漏处理。
旧名 `owner-rollback` / `owner-switch` 已改名，运行它们会提示新命令且不改任何状态。

> 已知技术债：库里「已停用」仍存成内部值 `legacy`（只改了对外的命令与文档用语）。长期做法是取消这个开关，
> 由 `research_schedules.yaml` 的 `enabled: false` / `event_triggers` 决定是否运行；见
> `openspec/changes/retire-legacy-and-migration-tooling/verification.md` 的「上线后待办」。

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

两处都只告警、不改读路由；同一个分析师只在状态由好转坏、由坏转好时发一次飞书并记一行日志。

```bash
ats data health --consumer <id>                  # 立即检查一个分析师读的数据产品
ats data health-log [--consumer <id>]             # 健康状态变化记录
# 读路由已固定为 target，不再提供状态命令
```

研究分析师的读路由固定为 target，没有切换入口。万一某个分析师的读路由不在 target（2026-10-20 技术面就被已移除的每日自动切回机制切过一次，表现为该工作流报 `no explicitly authorized read window`），用下面的命令恢复；它只会把路由移向 target，要求批准原文、原因和通过的数据产品检查，并写入只增不改的日志：

```bash
# （读路由已固定为 target）
ats consumer-cutover restore --consumer-id technical --actor <谁> \
    --approval "<用户批准原文>" --reason "<为什么>"
```

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

## 7. 交易路径（尚未切换）

- Chief、风控主管、Boss 审批、Trader、Clerk **尚未切换，也没有定时排程**。Chief 只能手动运行：

  ```bash
  ats chief run --channel feishu_bot   # 默认 dry-run，推飞书审批卡；绝不要加 --yes
  ats chief show
  ```

- 审批卡上是签名链接（不是飞书原生回调），点击后由 `com.ats.serve` 校验签名并恢复执行。
- 实盘锁定：`ats live gate` 在没有授权引用时返回 `refused / no_live_authorisation`。实盘授权库里有一行来源
  未核实的 `LIVE-X`，在核实前不得把它当作实盘授权。
- **`--yes`（自动批准）在实盘环境绝对不能用。** 系统里记录的止损价只是声明，不会在券商挂真实止损单。

### 7.1 切换后的操作（草稿，随 S3 上线生效；生效时用本小节替换上文）

> **状态：草稿。** 在 `switch-trade-path-and-retire-r5` 的 S3 上线前，上文仍是现状。本小节随各步上线逐段生效，
> 每一步都需要你批准（见 `openspec/changes/switch-trade-path-and-retire-r5/design.md` D3）。

**现状事实（切换后）**

- Chief 的上下文只读研究快照与 Internal State；决策、风控、审批都只认审计表（`decision_cycles` / `decision_revisions` / `risk_reviews` / `boss_approvals`），旧决策表只剩镜像写，观察窗口结束后随 R5 移除。
- 审批只在命令行完成（`ats approval`），没有飞书审批卡，也没有签名链接。`com.ats.serve` 不再参与审批。
- 只有**需要下单且已通过风控**的周期才会等你。纯持有、没有订单、或风控未通过的周期不会出现在待审批列表。
- 实盘仍然锁定（三道锁，见下）。批准后周期的终态是 **`approved_not_executed`（已批准未执行）**：Trader 已跑完授权十字段、新鲜度、实时报价、资金上限检查并记录每项结果，在券商交接点被拒绝，没有任何订单到达券商。

**每天会发生什么**

| 工作流 | 时间（工作日，仅 NYSE 交易日） | 内容 |
|---|---|---|
| `clerk-daily` | 16:30 纽约 | 对账：把成交与持仓写入账本（只认审计表） |
| `chief-daily` | 10:30 纽约（台北晚上） | 跑一个决策周期（非演练、实盘锁定）；有订单且过风控则进入 `pending_approval`，并给你发一次提醒（不带链接） |

两者在 `config/workflow/trade_schedules.yaml` 里默认 `enabled: false`（对外叫“停用”）。启用某一个：把它改成 `enabled: true` → 重启 `com.ats.dispatcher` → `ats dispatch resume --workflow <id> ...`（第 3.4 节），每一步都需要你批准。停用：`ats dispatch pause --workflow <id> ...`，不需要批准。

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

**`ats cutover state` 里的六个边界开关不是权威状态。** 它们从未接线（`wired: false`），切换后仍显示 `legacy` / `disabled`，
与事实不符，R5 会删除它们。真实的状态看：读路由用 `ats data health`，调度负责人用 `ats dispatch owner-log`，
切换发生在哪个提交见 `openspec/changes/switch-trade-path-and-retire-r5/verification.md`。

**回退**

1. 停用：`ats dispatch pause --workflow chief-daily --actor <谁> --reason "…"`，再停 `clerk-daily`（不需要批准）。
   从未恢复过的工作流还没有调度归属记录，`pause` 会拒绝并报 `workflow has no declared schedule owner`。这表示它本来就是停用的（dispatcher 不注册它，`launchctl` 重启后日志里只列研究工作流），这一步跳过即可（2026-10-29 T0.7 演练实测）。
2. 必要时按 S3 → S2 → S1 倒序 revert 对应提交，重启 `com.ats.dispatcher`，用 `launchctl list` 确认 PID。镜像写保留，旧读取方仍能看到观察期内的决策，回退不需要恢复数据。
3. 实盘锁定期间没有在途订单需要排空，但要做一次路由冻结核验，确认没有未终结授权。

**观察窗口的停止条件**（任一出现就停用两个工作流并记录）：出现真实券商提交；主理人上下文出现旧表读取；批准的 hash 与风控修订不一致，或旧修订被接受；
决策链（快照 → 修订 → 风控 → 审批）断裂；Clerk 对账与 TWS 不一致且无法解释；同一触发重复执行或同一输入重复发模型请求；主理人连续 2 个交易日因非数据缺口原因失败。

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

## 10. 数据与备份

| 文件 | 内容 |
|---|---|
| `var/ats.sqlite` | 领域记录：决策、绩效、风控、Workflow Memory 与投影 |
| `var/data.sqlite` | 数据平台：原始与准入数据、数据产品 |
| `var/data_refresh.sqlite`、`var/persistent_ingestion.sqlite` | 采集账本与受管队列 |
| `var/phase_f_*.sqlite` | 调度认领、读路由、切换与授权台账（只增不改） |
| `var/checkpoints.sqlite` | 等待审批的暂停态 |
| `var/phase_f_evidence/` | 台账引用的证据文件；`tmp/` 下同名路径是指向这里的符号链接，清空 `tmp/` 后按 `MANIFEST.json` 重建 |

都不进 Git，自行定期备份。`tmp/` 只放可随时清理的临时文件，**被台账引用的证据不要写进 `tmp/`**。

## 11. 旧实现退役

逐条目的清零、对账与墓碑判据见 [`PHASE_F_RETIREMENT_RUNBOOK.md`](PHASE_F_RETIREMENT_RUNBOOK.md)。
`ats retirement` 只读，不改登记；退役任何条目都需要你明确批准。

## 12. 诊断工具

- 新旧结果差异诊断：`ats shadow`，用法见 [`SHADOW_COMPARISON_RUNBOOK.md`](SHADOW_COMPARISON_RUNBOOK.md)（只作诊断，不是验收标准）。
- 历史手册（切换期间的逐 scope 授权、批次切换、持久部署许可、旧上线 checklist）已归档在
  [`archive/`](archive/)：`PHASE_F_OPERATIONS.md`、`PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`、
  `PHASE_F_PERSISTENT_RUNTIME.md`、`GO_LIVE.md`。
