# Verification

## 3.1 台北白天完整链路演练（2026-11-25，周三，台北上午）

**结论：通过。** 研究 → 首席（overnight venue）→ 审批 → 执行前检查走通；三笔订单 venue 都是 `overnight`，执行前的实时报价检查全部通过。没有任何订单到达券商。

运行时代码为工作区中 1.1–2.3 的实现（尚未提交）。证据文件在 `var/logs/overnight-3.1/`（`research.log`、`chief.log`、`approve.log`、`run_chief.py`）。

### 运行方式

- 研究：`ats workflow run` 依次跑 `information-brief`、`fundamental-routine`、`technical-review`（10:04–10:16 台北），三项 `status: complete`。
- 首席：`start_chief_cycle(source="scheduled", channel="pending", dry_run=False)`，即 `chief-daily` 的同一路径（见 `run_chief.py`）。
  - 用 `scheduled` 来源：手动 `ats chief run` 的来源是 `chief`，只走常规时段规则。
  - 不用演练模式（`dry_run=True`）：演练模式下 Trader 在授权与执行前检查之前就返回，测不到“执行前检查”。改为在**实盘锁定（C3）**下运行，与每天的 `chief-daily` 相同；运行前断言 `execution_locked() == True`，券商提交被 C3 拒绝。
- 审批：`ats approval approve chief-20261125-021622 --revision 798f10c39826473d --actor claude-rehearsal-3.1`（10:26–10:27）。

### 时段判定（1.1）

`chief_session(now)` 在 10:0x 台北返回 `overnight`，`source="ibkr"`：OVERNIGHT 合约（NVDA）的交易时段为 11-24 20:00 至 11-25 03:50 纽约，与日历一致。

### 首席周期

- 周期 `chief-20261125-021622`。风险状态 EMERGENCY（只许去风险）：第 1、2 轮风控驳回 ASML、COHR（修复态必须改善至少一项已破限指标），主理人两次修订；第 3 轮修订 3 通过，进入 `pending_approval`。
- 上一晚未审批的 `chief-20261124-144500` 按既有规则被标为 `superseded`。

| 订单 | 数量 | 限价 | venue | 定价报价（session / 买卖价 / 一档数量） |
|---|---|---|---|---|
| sell DRAM | 200 | 34.79 | overnight | overnight / 34.96–34.99 / 750–950 |
| sell NVDA | 120 | 150.55 | overnight | overnight / 151.30–151.48 / 505–196 |
| sell TSM | 70 | 336.16 | overnight | overnight / 337.84–338.69 / 87–79 |

盘口深度上限（2.2）没有触发：卖单看买一，最紧的 TSM 为 87 × 337.84 × 5 ≈ $147k，订单约 $23.6k。

### 审批截止（1.2）

- 审批请求的 `approval_deadline` = 2026-11-24T23:50-05:00，即 **11-25 12:50 台北**（收盘 16:50 台北前 4 小时）。
- `ats approval show` 显示：`⏰ 审批截止：11-25 12:50 台北（隔夜时段 11-25 16:50 台北 收盘前 4 小时）；超时批准不会执行，需重新审批`。
- 待审批提醒经配置的通道发出，日志里看不到正文；正文带截止时间的逻辑由单测 `test_an_overnight_proposal_carries_its_deadline_to_every_approval_surface` 覆盖。
- 10:26 批准，早于截止，可执行。截止后被拒绝的情形只有单测覆盖，这次没有实测。

### 执行前检查

批准时先用新快照对同一修订重审，结论仍为 approved；Trader 随后做执行前检查，周期终态 `approved_not_executed`（`approved_not_executed:live_lock`）。记录的检查：

| 检查 | 结果 |
|---|---|
| authorization_fields | ok |
| route_binding | 未通过，仅信息项（实盘锁定下没有交易路由，属预期） |
| orders_match_approved_revision | ok |
| live_quote_and_funds DRAM / NVDA / TSM | ok / ok / ok |

执行前读取的新报价都来自隔夜时段：

| 标的 | session | 买 / 卖 | 点差 | 一档数量 | 报价时间（UTC） |
|---|---|---|---|---|---|
| DRAM | overnight | 35.00 / 35.04 | 0.114% | 1150 / 2700 | 02:27:36 |
| NVDA | overnight | 151.69 / 151.83 | 0.092% | 140 / 747 | 02:27:39 |
| TSM | overnight | 338.82 / 339.89 | 0.315% | 59 / 159 | 02:27:41 |

三笔都被 C3 在券商交接点拒绝（`auto execution disabled`），没有订单到达券商。

### 观察

- 链路耗时：研究约 12 分钟（10:04–10:16），首席约 10 分钟（10:16–10:26，含两轮风控修订）。按 D1 的时间表（08:30 研究、10:30 首席），10:40 左右可以审批，距 12:50 截止约 2 小时。
- 批准命令打印“批准到执行共 69.0 秒，快照时限 60 秒”，但检查仍通过。这是既有提示，本 change 没有改动这部分。
- 收盘撤单（2.1）这次没有实测：实盘锁定下系统没有挂单，03:40 纽约的任务只会做一次只读查询。dispatcher 还在跑旧代码，要重启后才会注册这个任务（属于 3.2）。

## 3.2 切换调度（2026-11-25 11:38 台北）

用户决定跳过 3.1b（盘前演练），直接按调整后的 D1 切换，并观察当晚运行。

**改动：**
- `research_schedules.yaml`：information-brief 19:00 台北；fundamental-routine、technical-review 20:00 台北。
- `trade_schedules.yaml`：chief-daily 21:00 台北。
- 以上 cron 均声明为 `Asia/Taipei`，原值写在 YAML 注释里，便于回滚。

**调度展开**（`expected_triggers`，生产配置，11-25 12:00 至 11-28 23:00 台北）：
- 11-25（周三）：19:00 information-brief；20:00 fundamental-routine、technical-review；21:00 chief-daily。
- 11-26（周四，感恩节）：研究照跑，没有 chief-daily。
- 11-27（周五）：研究照跑，21:00 chief-daily。这天提前收盘（13:00 纽约），审批截止是台北 22:00。
- clerk-daily 仍为 07:00 台北（纽约交易日的次日早上）。

**重启：** 执行 `launchctl kickstart -k gui/<uid>/com.ats.dispatcher`，PID 由 60165 变为 5518。启动日志：`dispatcher started: workflows=information-brief,fundamental-routine,technical-review,macro-review,sector-review,clerk-daily,chief-daily`。启动补跑只回放了 clerk-daily 11-25 07:00 的 tick（已完成，取回第一次的结果）；chief-daily 的回溯窗口只有 4 小时，昨晚 21:00 的 tick 不在窗口内，没有补跑。

**切换前补的护栏：** 首席的 tick 如果在它所属时段的审批截止之后才被补跑，就不运行，记 `skipped_session_closed`（`detail=tick_session_expired`）。没有截止的常规时段按收盘时间判断。在现有 4 小时回溯窗口下，这条只对隔夜时段的 tick 有实际作用（例如 10:30 的 tick 在 12:50 之后才补跑）。

**待观察：** 11-25 21:00 台北的 chief-daily 首次按新时间触发，使用延长时段 venue，审批截止 11-26 01:00 台北。延长时段 venue 还没有生产演练（3.1b 跳过），盘前报价能否读到要看这次运行。
