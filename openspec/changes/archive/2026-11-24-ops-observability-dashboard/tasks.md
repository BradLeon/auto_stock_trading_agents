# Tasks

## 0. 准备

- [x] 0.1 在 `feat/provenance-observability` 之上建 `feat/ops-dashboard`，放在独立 worktree 开发（design 第 8 项）。
  - 2026-11-19：`.claude/worktrees/ops-dashboard`，分支 `feat/ops-dashboard`。

## 1. 模型调用入账

- [x] 1.1 新增 `llm_calls` 表；`telemetry.record` 写库，带 `projection_context` 的运行 ID 与进程标签。写库失败只记日志。验证：单测覆盖写入、无上下文、写库异常不抛出。
  - 2026-11-19：`llm/telemetry.persist`，表建在 `ATS_DB_PATH`（生产为 `var/ats.sqlite`）；gateway 传入模型与供应商（openrouter / deepseek）。`tests/test_llm_ledger.py` 4 项通过。
- [ ] 1.2 核实 OpenRouter 响应是否带 `usage.cost`。不带则在 gateway 打开用量统计参数（同一请求，不额外发请求）。验证：对一个最便宜的角色发 1 次真实调用（经你批准），核对记录的费用与 OpenRouter Logs 一致。
  - 2026-11-19 调整：不专门发测试调用。上线后用第一次定时运行的真实调用核对：看 `llm_calls.cost_source` 是 provider 还是空，再与 OpenRouter Logs 对账。这样不多花一次请求。
  - 2026-11-24 归档时：前半已完成——11-19 首批 22 次调用 `cost_source=provider`，OpenRouter 响应自带费用。后半（与 OpenRouter Logs 逐笔对账）未正式完成，只按价格做过整体估算；转为日常核对，不拦归档。
- [x] 1.3 `config/settings.yaml` 新增 `llm.prices`（当前在用的两个模型），供应商未返回费用时按单价估算。验证：单测覆盖 provider / estimate / 未知三种来源。
  - 2026-11-19：`LLMConfig.prices` + `settings.yaml` 的 `llm.prices: {}`。**单价未填**：我不知道这两个模型的实际价格，不编数；DeepSeek 直连的 `evidence_adjudicator` 在填价前显示"未知"。

## 2. 查询层

- [x] 2.1 `dashboard/queries.py` 中的数据源查询：
  - 三只钟、上次拿到新数据；
  - 按源类型取数据最新时间（design 第 3 项表格）；
  - SLO 灯、逐实体分组。

  验证：临时库单测；对真实库只读跑一遍，人工核对 5 个任务（含 ibkr_news_daily、chain_evidence_daily.TSM、consensus_daily.NVDA）。
  - 2026-11-19：真实库只读跑通，121 个任务 0.9 秒；结构化源的数据时间改取入库运行记录（见 design §3 修订）。
- [x] 2.2 流程查询：
  - 14 天 Grid，含研究流程、交易流程、首席周期状态；
  - 缺跑判定（排除休市日与暂停）；
  - 单次运行明细与模型调用汇总。

  验证：单测覆盖缺跑、暂停、休市；真实库核对 11-11 technical-review 的 26 次运行。
  - 2026-11-19：Grid 0.2 秒；缺跑 / 暂停 / 休市单测通过；新增"当天全部运行"页。核对：11-10（纽约日期）technical-review 26 次运行。
- [x] 2.3 溯源查询：复用 `agents/provenance.py`，最多展开两层。验证：同一决策的溯源结果与首席报告依据清单逐条一致。
  - 2026-11-19：单测断言与首席报告用同一 `cite_projection`；真实 11-18 周期渲染核对。
- [x] 2.4 总览查询：数据源计数、待处理清单、今日流程时间线、最近一次首席决策的输入新鲜度。

## 3. 页面与路由

- [x] 3.1 共享页面骨架：样式、导航、60 秒自动刷新、ⓘ 注释组件。
- [x] 3.2 四个页面及其明细页，挂到 `server.py` 的 `/dashboard`。验证：FastAPI TestClient 对每个路由返回 200，关键文字出现；用浏览器打开本机网址目视检查。
  - 2026-11-19：另加仅本机访问中间件（design §8）。TestClient 覆盖全部路由与 403；本机预览服务（8001 端口，只挂看板）逐页目视检查。

## 4. 中文注释

- [x] 4.1 起草 `config/ops/catalog_zh.yaml`，覆盖全部启用的任务与流程，**交你审定**。
  - 2026-11-19：初稿覆盖 47 个任务定义与 10 个流程，**待用户审定**。
- [x] 4.2 覆盖率测试：启用的任务与流程都必须有注释。
  - `tests/test_ops_dashboard.py::test_every_enabled_job_and_workflow_has_a_note`。

## 5. 上线

- [x] 5.1 跑直接相关测试；合回 `feat/provenance-observability`。
  - 2026-11-19：worktree 分支 feat/ops-dashboard 快进合入（08714f4）。
- [x] 5.2 重启 serve 与 dispatcher；打开 `http://127.0.0.1:8000/dashboard` 核对四个页面；确认下一次模型调用已入账。
  - 2026-11-19：serve 与 dispatcher 已重启，四个页面已核对；`llm_calls` 自 11-19 起持续入账。
