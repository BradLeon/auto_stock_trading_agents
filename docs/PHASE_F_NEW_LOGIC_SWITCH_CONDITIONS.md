# 什么条件下可以切换到新代码逻辑（Phase F 任务 10.7）

> 状态日期：2026-10-16。写给决策者：**默认停在 legacy**。只有下面“通用前置条件”全部满足、并且你明确批准，才把某一批研究工作流交给新逻辑（Dispatcher）。每一步都可以一条命令切回。

## 一、先说清楚“切换到新代码逻辑”指什么

分三条边界，各自独立切换，互不连带：

| 边界 | 含义 | 现状（2026-10-16） |
|---|---|---|
| 研究读取 | 分析师读数据平台的新入口，而不是旧的提供方抓取 | **Information、Fundamental 已切到新路径**；Layer、Sector、Macro、Technical 仍在旧路径 |
| 调度 | 谁来唤醒并执行分析工作流：旧的每日 cron（legacy）还是 Dispatcher | **全部 legacy**；首批演练已做过一次“切过去再切回来” |
| 交易 | 审批、记账、实盘 | 全部不动；实盘 disabled，须独立授权 |

本文只讲**调度**这条边界（读取已有 `consumer-cutover` 的健康检查加自动切回）。“切到新代码逻辑”= 把某个研究工作流的 SQL 负责人从 legacy 改为 dispatcher，**并且**加载常驻的 `schedule --phase-e` 去唤醒它。只做前者不做后者，那个工作流会没有任何人执行。

## 二、通用前置条件（每一批都要满足，逐条可核验）

| # | 条件 | 怎么核验 | 首批（Information/Fundamental/PEAD）现状 |
|---|---|---|---|
| 1 | 该批消费者的研究读取已整体在新路径，且最近一次每日健康检查为 ok | `ats consumer-cutover status` / `daily-check` | **满足** |
| 2 | 数据采集在正常运转，新文档持续入库（10-16 起另有证据抽取、财务、Macro 新闻与指标四条试点任务，均未启用） | `var/data_refresh.sqlite` 近几天各源有成功窗口；共识刷新任务已启用 | **部分满足**：共识刷新已启用；`ibkr_news` 每窗口都是 `partial` 且重试必败（已知缺陷）；TSM 官方财报/纪要未入库 |
| 3 | Dispatcher 能把该批工作流完整跑通一次 | 一次观察：每个有数据的标的恰好一条完成认领，没数据的标的明确拒绝并留记录，无重复认领 | **满足**（10-16 再验证：Information、例行基本面 11 个标的 10 个成功，LRCX 无文档 fail-closed） |
| 4 | 旧入口失权被证明 | 切过去后直接调旧入口，必须报 `caller is not current owner` | **满足**（Information、Fundamental 例行、PEAD 准备三个旧入口均被拒） |
| 5 | 切回演练做过并留记录 | `ats dispatch owner-log` | **满足**（三个工作流各一次，代次 1→2→3） |
| 6 | 原有的每一种定时职责，在新负责人下都有入口接手 | 逐项对照：例行预期更新、事件评审、**PEAD 准备（财报前基线）** | **基本满足**：PEAD 准备已并入例行基本面（窗口内建基线、手动可刷新），待首个真实窗口验证（任务 10.6） |
| 7 | 不重复发起模型请求 | 观察期对比请求数；抽取缓存命中；投影复用生效 | **满足**：连续两遍后 0 条新简报、0 条新预期更新、0 次新抽取（数据版本引用+整批复用，新鲜期上限 72 小时） |
| 8 | 数据覆盖没有已知缺口影响该批 | ASML 有财务数据、`fiscal_label` 有滚动机制、TSM 官方文档入库 | **不满足**：ASML 在新路径无财务数据；`config/pead/*.yaml` 手填财报期仍会过期 |
| 9 | 模型花费有外部上限 | API key 管理方已设上限（用户 2026-10-16 裁决：不做应用内成本护栏） | **已由你设置** |
| 10 | 你明确批准，批准原文写入日志 | `ats dispatch owner-log` 的 `approval` 字段 | 待批准 |

**没有全部满足就不切。** 不满足的项在 `tasks.md` 里有对应待办（10.5、10.6、10.8），Phase F 内必须做完。

## 三、切换与切回的操作（已实现，均有测试）

```bash
# 切过去（需要批准原文；会检查该消费者读路由为 target、健康检查 ok；有未完成认领须说明作废理由）
uv run ats dispatch owner-switch --workflow information-brief --actor <谁> --approval "<批准原文>" --reason "<为什么>"
# 切回（不需要批准和健康检查，随时可做）
uv run ats dispatch owner-rollback --workflow information-brief --actor <谁> --reason "<为什么>"
# 看日志（只增不改）
uv run ats dispatch owner-log
```

- 一次事务里同时改该工作流的模板行和所有已有标的行，不会出现“一半 legacy 一半 dispatcher”。
- 有 `running` 认领一律拒绝，不会被强行作废；`failed/pending` 必须带理由作废；历史记录不删。
- 切过去之后，旧的每日 cycle、手工 CLI（`ats analyst information`、`run_fundamental_pass`、`run_pead`）都会被拒；要让工作流自己跑起来，还需要单独批准加载 `ats schedule --phase-e` 常驻进程（现在**没有**这个 launchd 任务，也没有加载 `com.ats.schedule`）。
- 回退后条目在 `phase_e_schedules.yaml` 里仍是 `enabled: true`，但 legacy 负责人时会被忽略，不会唤醒任何东西。

## 四、首批观察发现的两个阻碍（均已处理，保留记录）

1. **Dispatcher 的例行基本面不能单独跑。** 例行基本面依赖同一标的的 Information 简报。Dispatcher 只复用“带数据版本引用”的已有投影，而定时条目从不提供这个引用，所以复用永远判“缺数据版本、需重算”；单独请求的例行任务拿不到依赖，11 个标的全部 `blocked`。同一轮里 Information 工作流倒是 10/11 成功，但每个都是重新发布（NVDA、MRVL、TSM、ASML 当天已有简报，又各发了一份）。修法二选一，**需要你决定**：A）把 Information 并入例行基本面的同一次运行（一个定时条目、一次运行，最简单，不再有独立的 Information 定时）；B）让定时条目带上数据版本引用，使跨触发复用生效（改动更大，但保留两个独立节奏）。
2. **PEAD 准备没有 Dispatcher 入口。** Dispatcher 只有“例行预期更新”和“事件评审”两个基本面适配器；`run_pead` 的准备阶段挂在例行基本面名下，负责人切到 dispatcher 后它被旧入口拦住、又没有新入口接手。财报前基线是事件评审的输入，所以不补这个入口，PEAD 不算切完。

## 五、回退条件（出现任一条，立即 `owner-rollback`）

- 每日健康检查失败（读路由会自动切回旧路径并告警，调度要手动回退）。
- 同一逻辑触发出现重复认领，或必需投影缺失、认领长时间 `running`。
- 连续两次整批运行失败，或失败原因是新逻辑本身的缺陷而不是“该标的无数据”。
- 外部 API 上限被触发（花费异常）。
- 任何券商写入尝试。

## 六、分批

- **首批：** Information、Fundamental 例行、Fundamental 事件、PEAD 准备。读取已切；调度已演练并通过复用验证；还差第 8 条（数据覆盖：ASML 财务、`fiscal_label` 滚动、TSM 官方文档）、PEAD 自动窗口的首次真实验证（任务 10.6）和你的批准。
- **第二批：** Technical、Macro、Layer、Sector 均已完成读路由切换（target）、真实运行、读与调度的切回演练（调度两遍观察，第二遍 0 次模型请求），调度终态 legacy。尚不满足的是条件 8（数据覆盖）：Layer/Sector 的证据与财务只有试点范围，Sector 轮动合成会被 DeepSeek 截断而降级，详见 `docs/PHASE_F_10_5_{MACRO,LAYER,SECTOR}_RESULT.md`；以及你对节奏、投影补齐的决定（任务 10.5）。
- **仍不在本文范围：** Chief、审批、记账、交易，须各自独立授权。

## 七、终态约定（2026-10-17 更新）

**现在的终态是 Dispatcher。** 用户 2026-10-17 裁决「7 个一起切换」后：
- 7 个研究工作流（information-brief、fundamental-routine、fundamental-event、layer-review、sector-review、macro-review、technical-review）的调度负责人都是 dispatcher，日志见 `ats dispatch owner-log`。
- 常驻进程 `com.ats.phase-e`（`deploy/launchd/com.ats.phase-e.plist`，`schedule --phase-e`，**没有** `--live`）已加载：只跑研究工作流、消费已发布的日历事件，**不进 Chief、不下单**。不要用 `com.ats.schedule.plist`——它启动的是旧调度器并带 `--live`。
- 切回：`ats dispatch owner-rollback --workflow <id> --actor <谁> --reason <为什么>`；要停掉全部：`launchctl bootout gui/$(id -u)/com.ats.phase-e`。
- 事件触发开关 `schedule.daily_stages.macro_sector_event_triggers`（现为 false）和 `pead_event_triggers`（true）现在对 Dispatcher 的日历事件路径同样生效；此前这条路径没有读它们。
- 切换时第 8 条（数据覆盖）仍有遗留：TSM/ASML Q3 官方披露（SEC）因 Clash 节点限制进不来，它们的 Q3 事件评审会因缺少已准入的官方材料而按设计不触发；解除需要用户调整 Clash 对 sec.gov 的节点或规则。
