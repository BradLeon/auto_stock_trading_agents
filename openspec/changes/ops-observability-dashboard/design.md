# Design

## Context

动机、页面需求和已定决策见 proposal.md。与实现相关的现状（2026-11-19 核实）：

- **服务进程**
  - `ats serve` 是 FastAPI 应用（`src/ats/runtime/server.py`），由 launchd `com.ats.serve` 常驻在 127.0.0.1:8000，目前只有审批接口。
  - 它和 dispatcher、每小时的采集 tick 都从**本仓库工作目录**运行。
- **采集记录分散在几处**
  - `var/data_refresh.sqlite`：
    - `refresh_runs`：每个任务、每个窗口一行，状态为 succeeded / no_change / quarantined / failed，并有 `source_ids_json`。
  - `var/data.sqlite`：
    - `structured_source_checks.latest_available_period`：只有 release-check 类结构化源有；
    - `structured_observations`：带 `known_at`；
    - `data_documents`：按 `source` 分组，带 `published_at`、`fetched_at`；
    - `schedule_calendar_source_runs`。
  - 时间字符串时区不一：`fetched_at` 有的是 `+00:00`，有的是 `+08:00`。**不能直接对字符串取 max**。
- **流程记录**
  - 研究流程：`var/ats.sqlite` 的 `workflow_runs`。流程名取 `request_json.requested_tasks[0]`；`agent_runs` 每个任务尝试一行。
  - 交易流程（chief-daily、clerk-daily）：`var/phase_f_dispatch.sqlite` 的 `schedule_runtime_claims`。首席结果在 `decision_cycles`。
  - 计划时刻：`config/workflow/research_schedules.yaml`、`trade_schedules.yaml`。
- **模型调用**
  - `llm/telemetry.py` 的 `UsageCallback` 挂在每个模型上，但只写日志，不入库，也没有费用。
  - 供应商有两个：
    - OpenRouter：绝大多数角色，返回 `gen-…` id；
    - DeepSeek 直连：`evidence_adjudicator`。
  - 调度器为每个任务设置了 `projection_context`（contextvar），其中有 `workflow_run_id` / `agent_run_id`。

## Goals / Non-Goals

**Goals**

- 一个固定网址，打开即是现查的最新状态。
- 四个页面加中文注释。
- 模型调用逐次入账，并能归到流程运行。

**Non-Goals**

- 不提供任何写操作（重跑、暂停、审批），操作仍走命令行和飞书。
- 不对外网开放，不做登录。
- 不改变采集或流程的调度行为，不新增告警。
- 不回填历史模型调用（日志里的旧记录不导入）。

## Decisions

### 1. 服务端渲染 + 原生 HTML，不引前端构建链

- 路由挂在现有 FastAPI 应用上，统一在 `/dashboard` 前缀下：
  - `/dashboard`：总览
  - `/dashboard/sources`、`/dashboard/sources/{job_id}`
  - `/dashboard/workflows`、`/dashboard/workflows/run/{run_id}`
  - `/dashboard/lineage`、`/dashboard/lineage/{cycle_id}`
- 页面用 Python 拼 HTML，样式内联在一个共享模板里。ⓘ 注释用原生 `<details>`，点击展开，不需要 JS。
- 自动刷新用 `<meta http-equiv="refresh" content="60">`。
- **备选方案：**React / Vue 单页应用。没选，原因：
  - 要引入 Node 构建链，而仓库目前没有；
  - 只读表格页用服务端渲染已经足够；
  - 维护者只需要懂 Python。
- **备选方案：**Jinja2 模板。没选，因为当前依赖里没有 jinja2；页面量不大，不值得为此加依赖。如果实现中拼接代码过于冗长，再改用它。

### 2. 查询层与页面分离：`dashboard/queries.py` 只返回数据

- 每个查询函数接收各库路径，以只读方式（`mode=ro`）打开 SQLite，返回 dataclass 列表。页面函数只做格式化。
- 好处：查询可以直接用临时库做单测，页面可以用构造数据做快照测试。
- 每个页面请求现开、现关连接，不做缓存。
  - 理由：数据量小，看板只有一个人用；缓存反而会制造"刷新不是最新"的困惑。
  - 单页查询超过 1 秒时再加短缓存。

### 3. 数据源页：三只钟分开，数据最新时间按源类型取

| 钟 | 取数 |
|---|---|
| 最近尝试 | `refresh_runs` 该任务最新窗口的状态与原因码 |
| 最近成功 | 该任务最近一次 `succeeded` 的窗口（`no_change` 不算成功，单独显示"上次拿到新数据"） |
| 数据最新 | 按任务的 `source_ids_json` 取各源的数据时间，见下表 |

| 源类型 | 数据时间 | 标注 |
|---|---|---|
| release-check 类结构化源 | `structured_source_checks.latest_available_period` | 数据期 |
| 其他结构化源 | `structured_ingestion_runs` 最近一次 `accepted > 0` 的完成时间 | 入库 |
| 非结构化 / 文章 / 文件 | `data_documents` 该源最新 `published_at`，没有则最新 `fetched_at` | 发布 / 采集 |
| 日历 | `schedule_calendar_source_runs` 最新成功时间 | 采集 |
| 派生（产业链观测） | 不显示数据时间，只显示"最近产出" | 产出 |

- 时间在 Python 里解析成带时区的 `datetime` 后再比较，不在 SQL 里对字符串取 max。
- 2026-11-19 实施修订：原稿取 `structured_observations` 的最新 `known_at`，但实测对 54 万行联表聚合需 21 秒，不能放在页面请求里。改取入库运行记录，0.2 秒。
- 查不到数据时间的源显示"未登记"，不留空，也不拿运行时间冒充。
- **新鲜度灯：**用"距最近一次正常运行"的时长对照该任务的 `freshness_slo_days`（Q3）。正常运行指 `succeeded` 或 `no_change`。
  - 2026-11-21 修订（用户指出 BEA/BLS/FOMC 日历刚失败却显示"正常"）：灯同时看最近运行结果——连续失败 2 次及以上为红，最近一次失败为黄；每盏灯附判定原因。数据源页改为需处理项置顶、完整列表折叠。
  - 超过 SLO 为红，超过 SLO 的 50% 为黄，`enabled: false` 的任务为灰。
  - 理由：灯回答的是"链路通不通"。`no_change` 说明上游查过、链路是通的。
  - 如果按"拿到新数据"来着色，季度财报类任务（如 SEC 每日检查，SLO 3 天）在两次财报之间会长期误报红。
  - "上次拿到新数据"单独成一列，不参与着色。上游本该有新数据却一直没有的情况，交给中文注释说明（例如"季度财报之间无新数据属正常"），不做自动判断。
- 逐实体展开的任务（`consensus_daily.NVDA` 等）按定义分组，组内折叠；组的颜色取组内最差的一个。

### 4. 流程页：计划格 + 实际状态

- 行是流程定义。研究流程取自 `research_schedules.yaml` 的流程，再加上事件类流程；交易流程取自 `trade_schedules.yaml`。
- 列是近 14 天（Q4）。
- 格子的状态来源：
  - 研究流程：当天 `workflow_runs` 中该流程的状态集合，取最差的那个，并标出当天次数；
  - 交易流程：`schedule_runtime_claims`；
  - chief-daily：再叠加 `decision_cycles.status`（approved / manual_review / superseded…）。
- **按计划本该运行、但当天没有记录的格子**标为"缺跑"。计划依据流程配置里的星期与时刻。
  - 这是 Airflow Grid 最有用的一点：静默没跑比跑失败更难发现。
- 格子链接：一次运行直接到运行明细；多次运行到"当天全部运行"列表（`/dashboard/workflows/{workflow}/{day}`）；首席的格子到当轮决策溯源。
- 单次运行页显示：
  - 计划时刻 vs 实际开始/结束；
  - `agent_runs` 逐任务的状态、耗时、错误码；
  - 缺失的必需输入（`result_json` 中的缺口）；
  - 该次运行的模型调用汇总（见第 6 项）。

### 5. 决策溯源页：复用 `agents/provenance.py`

- 读取 `decision_cycles` 的快照与最新修订，逐层展开：
  - 决策；
  - 被引投影（`cite_projection`）；
  - 投影的数据版本（`describe_vintage_ref`）。
- 投影的 `input_refs` 中如果有其他投影（例如行业配置引用 8 个层分析），再展开一层，最多两层，防止树爆炸。
- 与首席报告的「依据清单」同一口径，所以两边显示的时间必然一致。

### 6. 模型调用入账：在 `llm/telemetry.record` 里写库

- 新表 `llm_calls`，放在 `var/ats.sqlite`，字段：
  - `at`、`role`、`provider`、`model`、`response_id`；
  - `input_tokens`、`output_tokens`、`reasoning_tokens`；
  - `cost_usd`、`cost_source`；
  - `workflow_run_id`、`agent_run_id`（来自 `projection_context`，不在调度上下文中时为空）；
  - `process`（dispatcher / refresh / cli）。
- **费用来源，按可靠性排序：**
  1. 供应商在响应里返回的费用（OpenRouter 的 `usage.cost`），`cost_source = "provider"`；
  2. 否则按 `config/settings.yaml` 新增的 `llm.prices`（每百万 token 单价）计算，`cost_source = "estimate"`，页面标"估算"；
  3. 没有单价时 `cost_usd` 为空，页面显示"未知"，不写 0。
  - 任务 1.2 先核实 OpenRouter 响应在 langchain 的 `response_metadata.token_usage` 里是否带 `cost`。如果不带，就在 gateway 的请求参数里打开 OpenRouter 用量统计（`usage: {include: true}`）。这只是同一请求多返回一个字段，**不额外发请求**。
- **写库失败只记日志，不影响模型调用**，与现有 telemetry"绝不抛错"的约定一致。
- 并发写由 SQLite WAL 承担：dispatcher、采集 tick、命令行可能同时写。每次写一行，单独事务，`busy_timeout` 5 秒。
- **备选方案：**事后用 `gen-…` id 调 OpenRouter `/api/v1/generation` 补费用。没选，原因：
  - 每次调用多一个 HTTP 请求；
  - DeepSeek 直连没有对应接口。
  - 留作对账手段，不进主路径。

### 7. 中文注释：独立配置文件，覆盖率由测试守

- `config/ops/catalog_zh.yaml` 的结构：
  - `jobs:`，以任务定义 ID 为键（逐实体任务按定义写一条）；
  - `workflows:`，以流程 ID 为键；
  - 每条四栏：`what` / `source` / `used_by` / `if_broken`，另有一个中文名 `label`。
- 初稿由我根据配置的 `semantics`、代码与消费关系起草，**由你审定后上线**。
- 测试断言 `schedules.yaml` 里每个启用中的任务、每个流程都有注释。将来新增任务时，忘写注释会在测试里报红，看板上也会显示"缺说明"。

### 8. 仅本机访问：拒绝经隧道的请求

- `ats serve` 同时挂在 cloudflared 快速隧道后面接收飞书回调，而 `scripts/start_tunnel.sh` 转发的是整个 8000 端口。只把服务绑定在 127.0.0.1 挡不住经隧道进来的外网请求。
- 做法：`/dashboard` 前缀的请求只要带 Cloudflare 或转发头（`cf-ray`、`cf-connecting-ip`、`x-forwarded-for` 等），或者 Host 不是本机地址，或者对端不是回环地址，一律返回 403。审批接口不受影响。

### 9. 开发隔离：独立 worktree

- dispatcher、serve、每小时的采集 tick 都从本仓库工作目录运行，tick 每次是新进程，会读到当时的文件。所以在这里边改边存，生产随时可能读到半成品：`telemetry.py` 每次模型调用都会经过，`settings.yaml` 每个进程都会读。
- 做法：在 `feat/provenance-observability` 之上开 `feat/ops-dashboard`，放在独立 worktree 开发和测试。完成后合回，再重启 serve 和 dispatcher。

## Risks / Trade-offs

- **[看板查询拖慢审批接口]** 两者同在 serve 进程里。
  - 应对：查询都是只读小查询，并用 FastAPI 线程池执行。实测单页超过 1 秒再加缓存或拆进程。
- **[`llm_calls` 写库与生产写库争锁]**
  - 应对：每次一行、短事务、`busy_timeout`；失败只记日志。
- **[OpenRouter 不返回费用，估算价与实际有偏差]**
  - 应对：页面明确标"估算"，并显示 `cost_source`；可定期用 generation 接口对账（不在本 change 内）。
- **[中文注释写错]**
  - 应对：初稿经你审定再上线；注释只是说明，不参与任何判定。
- **["缺跑"判定误报（节假日、手动暂停）]**
  - 应对：用现有交易日历排除休市日；`ats dispatch pause` 期间的格子标"已暂停"，不标缺跑。

## Migration Plan

1. 在 worktree 开发并测试，合回本分支。
2. 重启 serve（加载新路由）和 dispatcher（`telemetry` 入账生效）。`llm_calls` 表在首次写入时自动创建。
3. 回退：还原提交并重启两个进程。`llm_calls` 表留着无害，看板路由随代码消失。

## Open Questions

- OpenRouter 的响应里是否直接带费用，由任务 1.2 核实。结论只影响 `cost_source` 的取值，不影响设计。
