# Proposal（需求讨论稿，待确认后补 design / specs / tasks）

## Why

**要回答的问题：**

- "今天该相信系统给的结论吗？"
- "数据断了没有？"
- "今天的流程跑到哪了？"

现在回答这三个问题，要么翻 JSON 命令行（`ats data health`、`ats workflow runs`、`python -m ats.data.refresh status`），要么等飞书告警。没有一个地方能同时看全。

底层记录其实大都有了，缺的是**把它们拼起来给人看**。

- **数据采集：**
  - 约 37 个数据源、约 47 个定时任务（`config/data/schedules.yaml`）。
  - 运行记录：`var/data_refresh.sqlite` 的 `refresh_runs` / `refresh_attempts`。
  - 数据本身的最新期：`var/data.sqlite` 的 `structured_source_checks`、`data_ingestion_runs`。
  - 健康变化日志：`var/ats.sqlite` 的 `data_health_log`。
  - 每个任务已声明 `freshness_slo_days`。
- **Workflow：**
  - 运行状态：`var/ats.sqlite` 的 `workflow_runs`、`agent_runs`、`trigger_runs` 及各自的状态历史表。
  - 调度醒来记录：`var/phase_f_dispatch.sqlite`。
- **已有 Web 服务：**`ats serve`（FastAPI，127.0.0.1:8000），目前只有审批接口。

## 行业参照

| 参照 | 借鉴点 |
|---|---|
| dbt source freshness / Dagster 资产目录 | 每个数据资产三个时钟分开看：**最近一次尝试**、**最近一次成功**、**数据本身的最新时间**，并对照 SLO 打绿 / 黄 / 红。"跑成功了但没拿到新数据"是最常见的静默故障。 |
| Airflow Grid 视图 | 行是流程，列是日期，格子是状态色。点进单次运行，看每个任务的状态、耗时、错误。另外，计划时间和实际开跑时间要分开看。 |
| OpenLineage / Marquez 血缘 | 从一个产出（决策）往上追到它用的数据集及其版本。 |
| 卖方研报合规 | 每个数字标"来源，截至日期"。这一点由 `report-source-timestamps` 落地；看板里的血缘页复用同一套数据。 |

## 需求草案（四个页面）

1. **总览**
   - 一屏三块：
     - 数据源新鲜度：绿 / 黄 / 红 计数，红的置顶；
     - 今日流程时间线：计划时刻 vs 实际状态；
     - 最近一次首席决策的输入新鲜度。
   - 页面每 60 秒自动刷新。这里的"实时"取分钟级：数据源每小时检查一次，流程按天跑，秒级刷新没有意义。
2. **数据源**
   - 每个采集点一行，列为：
     - 名称、类别（结构化 / 非结构化 / 日历）、频率
     - 最近尝试（状态）、最近成功
     - **数据最新时间**、SLO、新鲜度灯
     - 连续失败次数、最近错误
   - 点进去看近 30 天运行格子和失败原因。
3. **流程**
   - Grid：行是 information-brief / fundamental / technical / macro / sector / layer / clerk / chief，列是近 14 天。
   - 点进单次运行，看：
     - 任务列表（状态、耗时、错误码、缺失的必需输入）；
     - 计划时间 vs 实际时间；
     - 重试历史。
4. **决策溯源**
   - 选一份首席决策，展开一棵树：决策 → 引用的分析师结论（`as_of`、新鲜度）→ 依赖的数据源（最新数据时间）。

**中文注释（2026-11-19 用户要求）：**每个采集点和流程的名称旁有 ⓘ，点击展开中文说明。

- 说明分四栏：是什么 / 来源 / 谁在用 / 坏了影响什么。
- 说明写在配置里（`config/data/schedules.yaml` 每个任务加 `label_zh`、`note_zh`；流程配置同理），看板只读取、不硬编码。
- 配置里缺说明的任务，在看板上标"缺说明"，不留空。

**交付方式（建议）：**固定网址、刷新即最新，不做每日静态 HTML。

- 理由：每日静态页最多会落后 24 小时，与"实时追踪"相矛盾；而 `ats serve` 已由 launchd 常驻，每次请求现查 SQLite 的成本可以忽略。

**边界：**看板只读，不提供"重跑 / 暂停"按钮，操作仍走命令行。告警仍走飞书，看板负责"查"，飞书负责"推"。

**技术取向（附录级）：**

- 在 `ats serve` 上加只读路由。
- 服务端渲染 HTML，不引入前端构建链。
- 只读打开各 SQLite 文件。

## 待你决定

- **Q1 访问方式：**只在本机打开（127.0.0.1），还是要手机通过现有 cloudflared 隧道看？
  - 走隧道就必须加鉴权。
- **Q2 优先级：**四个页面先做哪两个？
  - 建议先做「数据源」和「流程」，它们是"断了没 / 跑到哪"的直接答案；「总览」和「溯源」随后。
- **Q3 新鲜度阈值：**用各任务已声明的 `freshness_slo_days` 作红线，黄线取 SLO 的 50%？
  - 还是需要按数据源单独配？
- **Q4 历史深度：**流程 Grid 看 14 天、数据源看 30 天够不够？
- **Q5 是否要成本视图：**每次运行的模型请求数 / 费用？
  - 当前 `agent_runs` 未必记录费用，要做得另加采集。

## Capabilities

（需求确认后补。预计新增 `workflow/ops-dashboard`，并修改 `data/refresh-orchestration` 中的运维查询要求。）

## Impact

- **代码：**
  - `src/ats/runtime/server.py`
  - 新增 `src/ats/runtime/dashboard/`：查询层 + 模板
- **依赖：**已有的各个状态库，只读。
- **成本：**不涉及模型请求。
