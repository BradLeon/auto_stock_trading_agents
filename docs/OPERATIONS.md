# 运维手册

> 读者：运行和维护这套系统的人。讲**现在**怎么装、怎么看、怎么停、怎么修。
> 设计与契约见 [`TARGET_WORKFLOW_DATAFLOW.md`](TARGET_WORKFLOW_DATAFLOW.md)，代码约定见 [`DEVELOPMENT.md`](DEVELOPMENT.md)。
> 命令默认已激活 `.venv`（或写成 `.venv/bin/ats …`）。

## 1. 有哪些服务

| launchd 服务 | 做什么 | 进程 | 节奏 |
|---|---|---|---|
| `com.ats.data-refresh` | 数据刷新控制器：结构化/非结构化采集、文档准入、财报日历刷新、FactSet 月末摄入 | `python -m ats.data.refresh tick` | 每小时一次 |
| `com.ats.phase-e` | 研究调度（Dispatcher）：按时间表与日历事件运行 7 个研究工作流；**不进 Chief、不下单** | `ats schedule --phase-e` | 常驻 |
| `com.ats.consumer-health` | 每个分析师所读数据产品的健康检查；失败即把该分析师切回旧读路径并飞书告警 | `ats consumer-cutover daily-check` | 每天 09:30（北京时间） |
| `com.ats.serve` | 飞书审批回调（签名链接） | `ats serve --host 127.0.0.1 --port 8000` | 常驻 |

> ⚠️ **永远不要加载 `com.ats.schedule`**（`schedule --live`）。它是已退役的旧调度器，
> 会让旧级联与 Dispatcher 同时跑，并打开实盘开关。`deploy/launchd/com.ats.schedule.plist` 只为历史保留。

plist 在 `~/Library/LaunchAgents/`；`data-refresh`、`phase-e` 的模板在 `deploy/launchd/`。

```bash
launchctl list | grep com.ats                                     # 状态（PID 一列为 - 表示当前没在跑）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ats.phase-e.plist   # 加载
launchctl kickstart -k gui/$(id -u)/com.ats.phase-e               # 重启（改了 src/ 后必须做）
launchctl bootout gui/$(id -u)/com.ats.phase-e                    # 停掉全部研究调度
```

- **改了 `src/ats/` 必须重启对应常驻进程。** 常驻进程懒加载模块，不重启会出现新旧代码混跑且不报错。
  重启挑在两次调度之间。
- `launchctl` 在 Claude Code 等沙箱里执行不生效，要在沙箱外执行。
- 曾出现 `data-refresh`、`consumer-health` 在切换操作后掉出 launchd 的情况，原因不明；`launchctl list`
  里看不到时重新 `bootstrap`。

## 2. 每天看什么

```bash
ats consumer-cutover status          # 6 个分析师的读路由：target（新）/ legacy（旧）及代次
ats consumer-cutover log             # 健康检查与切换历史（每天应有一行 health）
ats dispatch owner-log               # 7 个研究工作流的调度负责人及切换历史
ats workflow runs --limit 20         # 最近的研究运行：complete / incomplete / failed
python -m ats.data.refresh status    # 各采集任务最近一次结果
ats events upcoming --days 14        # 未来两周的财报与宏观事件
ats events candidates                # 来源冲突、被挡住等你裁决的事件
```

日志在 `var/logs/`：`phase-e.{out,err}.log`、`data-refresh.*.log`、`consumer-health.*.log`、`serve.*.log`。

## 3. 研究调度

### 3.1 时间表与事件

时间表在 `config/workflow/phase_e_schedules.yaml`，只有负责人是 dispatcher 时才生效：

| 工作流 | 时间 | 范围 |
|---|---|---|
| `information-brief` | 周一至周五 08:00 纽约 | `portfolio:pead-targets` |
| `fundamental-routine` | 周一至周五 09:00 纽约（依赖当天的信息简报） | `portfolio:pead-targets` |
| `technical-review` | 周一至周五 10:00 纽约 | `portfolio:portfolio` |
| `macro-review` | 周六 09:00 台北 | `portfolio:portfolio` |
| `sector-review` | 周六 09:30 台北（Dispatcher 先补跑 `layer-review`） | `sector:ai_hardware` |

日历事件的路由在 `config/workflow/event_routes.yaml`，常驻进程每天 06:10、18:10（UTC）消费已发布的事件：

- 财报**计划**（前 14 天内）→ 信息简报 + 基本面例行（例行模式在财报前 `prep_days_before`＝3 天内建立基线）。
- 财报**已发布**且已准入公告或申报材料 → 信息简报 + 基本面事件评审（冻结基线、打分）。
- FOMC、CPI、PCE、NFP、GDP 发布 → 宏观评审。是否唤醒受 `config/settings.yaml` 的
  `schedule.daily_stages.macro_sector_event_triggers` 控制（现为 `false`）；财报事件受
  `pead_event_triggers` 控制（必须保持 `true`，它与新事件路径共用）。

错过触发时刻（机器睡眠）在 `misfire_grace_hours`（6 小时）内补跑，超出则记为 `misfire_grace_exceeded` 跳过。

### 3.2 手动运行

```bash
ats workflow run --task macro-review --scope-kind portfolio --scope-id portfolio
ats workflow run --task sector-review --scope-kind sector --scope-id ai_hardware
ats workflow run --task fundamental-routine --scope-kind portfolio --scope-id pead-targets
ats workflow runs --limit 5
ats workflow retry --trigger-key <key> --actor <谁> --reason <为什么>   # 只重试失败的那一个任务
```

- 依赖的数据版本未变、投影仍在有效期内时直接复用，**不重复请求模型**。要在数据没变时强制重算，
  用 `--inputs-json '{"<task_id>": {"force_recompute": true}}'`。
- 负责人是 dispatcher 时，旧入口（`ats macro review`、`ats pead prep`、`ats analyst information` 等）
  会报 `caller is not current owner` 并拒绝，防止新旧两条路径重复跑。
- 某只票要手动刷新财报前基线：`ats analyst fundamental --mode routine --symbol <SYM> --refresh-baseline`
  只在负责人为 legacy 时可用；dispatcher 下用 `workflow run` 并带 `force_recompute`。

### 3.3 切换负责人与回退

```bash
# 切回旧路径（不需要批准，随时可做）
ats dispatch owner-rollback --workflow <workflow_id> --actor <谁> --reason "<为什么>"
# 切到 Dispatcher（需要批准原文；检查读路由为 target、健康检查 ok；有未完成认领须说明作废理由）
ats dispatch owner-switch --workflow <workflow_id> --actor <谁> --approval "<批准原文>" --reason "<为什么>"
```

- 一次事务同时改该工作流的模板行与所有标的行，不会出现一半新一半旧。
- 有 `running` 认领时拒绝切换；`failed/pending` 认领须带理由作废；`owner-log` 只增不改。
- 工作进程已死亡但仍是 `running` 的认领，只在下一次负责人切换时作废，不影响定时触发。

**出现任一情况就回退：** 每日健康检查失败（读路由会自动切回，调度要手动回退）；同一逻辑触发重复认领；
必需投影缺失或认领长时间 `running`；连续两次整批失败且原因是新逻辑缺陷（不是"该标的没数据"）；
模型请求费用异常；任何券商写入尝试。

## 4. 读路由（每个分析师读新路径还是旧路径）

```bash
ats consumer-cutover status
ats consumer-cutover health --consumer-id <id>          # 立即跑一次某个分析师的健康检查
ats consumer-cutover rollback --consumer-id <id> --actor <谁>
ats consumer-cutover switch --consumer-id <id> --actor <谁> --approval "<批准原文>"
```

六个消费者：`information`、`fundamental`、`layer`、`macro`、`sector`、`technical`。按消费者整体切换；
每次切换代次加一，日志只增不改。Chief 不在其中（交易路径未切换）。

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
  摄入上月最终版）。
- 来源与数据集的完整说明见 [`STRUCTURED_DATA_OPERATIONS.md`](data/STRUCTURED_DATA_OPERATIONS.md)。

## 6. 事件日历

```bash
ats events upcoming --days 30
ats events status
ats events candidates                                   # 被挡住的冲突
ats events override --event-id <id> --date YYYY-MM-DD --actor <谁> --reason "<依据>"
ats events cancel   --event-id <id> --actor <谁> --reason "<依据>"
ats events withdraw --event-id <id> --actor <谁> --reason "<依据>"
```

两个来源只是精度不同（一方多给了具体钟点）时视为兼容并自动合并；日期或时段（盘前/盘中/盘后）
真不一致时挡住，事件不会唤醒任何工作流，直到你裁决。

## 7. 交易路径（尚未切换）

- Chief、风控主管、Boss 审批、Trader、Clerk **没有切到新路径，也没有定时排程**。Chief 只能手动运行：

  ```bash
  ats chief run --channel feishu_bot   # 默认 dry-run，推飞书审批卡；绝不要加 --yes
  ats chief show
  ```

- 审批卡上是签名链接（不是飞书原生回调），点击后由 `com.ats.serve` 校验签名并恢复执行。
- 实盘锁定：`ats live gate` 在没有授权引用时返回 `refused / no_live_authorisation`。实盘授权库里有一行来源
  未核实的 `LIVE-X`，在核实前不得把它当作实盘授权。
- **`--yes`（自动批准）在实盘环境绝对不能用。** 系统里记录的止损价只是声明，不会在券商挂真实止损单。

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
5. 加载第 1 节的四个服务（不包括 `com.ats.schedule`），按第 2 节检查。

## 9. 已知问题与排障

| 现象 | 原因与处理 |
|---|---|
| `ibkr_news` 采集曾每天挂满 30 分钟 | TWS 对 `reqContractDetails` 不回复、`ib_async` 默认无限等待；已加 20 秒超时与符号检索回退。若再出现，先看 TWS 是否登录 |
| `ibkr_news` 每个窗口都是 `partial` | 正文请求预算 10 篇而每次选中数百篇，属常态；控制器把 `partial` 当失败重试、队列把它当终态，第二次会抛 `queue_task_not_claimable`——已知缺陷，未修 |
| sec.gov 对后台进程不可达 | 本机 Clash fake-ip（198.18.x），命令行进程到 SEC 的 TLS 被重置，浏览器正常。影响外国申报人（如 TSM）的官方披露 |
| 审批链接突然 404 / 不可达 | Clash TUN 用 GVisor 栈时会重置 cloudflared 到边缘 7844 端口的长连接；把 TUN 堆栈改为 System，`pkill cloudflared` 重起，更新 `FEISHU_APPROVE_BASE` 并重启 serve。人在 Mac 前也可以直接在本机打开 `http://127.0.0.1:8000/feishu/approve?...` |
| 抽取角色偶发「空工具调用」 | 隐藏推理吞掉了工具调用；`run_structured` 第一次空返回后会关闭推理重试 |
| 某只票的信息简报 `incomplete` | 多半是该标的 7 天内没有已准入资讯（数据缺口），看数据刷新与资讯覆盖，不是新逻辑缺陷 |
| 定时任务没跑 | 看 `phase-e.err.log` 是否有 `misfire_grace_exceeded`；或负责人被切回 legacy（`ats dispatch owner-log`） |

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
- 历史手册（切换期间的逐 scope 授权、批次切流、持久部署许可、旧上线 checklist）已归档在
  [`archive/`](archive/)：`PHASE_F_OPERATIONS.md`、`PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`、
  `PHASE_F_PERSISTENT_RUNTIME.md`、`GO_LIVE.md`。
