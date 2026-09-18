# Frontier AI 原始能力 Observer 运维说明

## 目的与边界

`ai_frontier_raw_capability` 是 `ai_hardware/L1_app` 的独立 Evidence Observer。它只回答两件事：

1. 在同一 benchmark 方法与 harness（`comparability_group`）内，当前 global frontier 是否外扩（A）；
2. 对评分语义允许的 benchmark，当前最好模型是否跨过 50% 多数任务门槛（B）。

它不是综合智能排行榜，也不改写生产化/应用扩散或商业化 Observer，不进入 Chain、评分、组合、风控和交易路径。

## 来源与更新

首选 benchmark 维护方/独立第三方的免费公开 Git/CSV/JSON/README/结构化 HTML 结果：LiveBench、AutomationBench-AA、Terminal-Bench 4.0、Terminal-Bench-Science 0.1、SciCode、CritPt、OSWorld 2.0、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2 和 Humanity's Last Exam。Terminal-Bench 4.0 以 Harbor Hub 匿名 `leaderboard-read` JSON 为主源，可取得 model、agent、effort、accuracy、95% CI 与 trial 数；官方 GitHub `leaderboard/submissions/*.json` 只作审计/故障回退。EnterpriseOps-Gym 仅保留历史事件，不在默认矩阵。运行时一次批量探测各来源，再在本地解析；Git 记录 commit/blob SHA，JSON/HTML 记录 ETag/Last-Modified（若有）、结构指纹与 payload hash，README 表格记录 commit、路径和解析范围。Artificial Analysis 仅在明确授权且设置 `ATS_FRONTIER_AI_USE_AA=1` 时作为可选增强源，不是平台前置条件；其无需登录的公开结构化页面也必须同时通过 `ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES` 与 `ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED` 门禁，任一关闭即 fail-closed。官方 Lab release/model card 与 Scale HLE 可作为 `lab_self_reported`/`competitor_reported` 候选，但不会覆盖第三方结果。模型发现、成绩探测每日运行；新模型 0–30 天每日、31–90 天每三日、之后每周；每周执行全量方法/模型审计。

2026-09-17 对部署环境中的 key 做了真实只读权限探测：使用 Artificial Analysis 要求的 `x-api-key` 头访问 `/api/v2/language/models` 返回 HTTP 403，响应为 `Language models list requires a Pro subscription`；错误的 Bearer 头会返回 401。因而 Free 计划不能通过该 API 获取模型/benchmark 明细；这不影响免费公开 benchmark 路线。Free 页面仍可人工查看基础 benchmark、模型比较图和排行榜，但截图或页面抓取不等价于 API/export 权限；生产采集不依赖 AA 页面视觉元素，不绕过登录或付费墙。浏览器/Computer Use 仅用于发现公开端点和人工核验，不作为正式分数来源。

所有原始响应以 artifact 保存，解析结果以 `benchmark_score` observation 追加写入。相同评测身份数值变化产生新 vintage，绝不就地覆盖。公开来源 404/429、网络故障和方法换版只降低本次覆盖或隔离候选，不删除最近有效数据。

## 固定面板与 NA

Labs：OpenAI、Anthropic、Google/Google DeepMind、xAI、DeepSeek、Moonshot/Kimi、Tencent、Z.ai/GLM、Alibaba/Qwen。Benchmark：LiveBench、AutomationBench-AA、Terminal-Bench 4.0、Terminal-Bench-Science 0.1、SciCode、CritPt、OSWorld 2.0、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam。内部 ID `automationbench_aa` 只表示统一 AutomationBench-AA 方法，不代表必须购买 AA API。FrontierMath Erdős、FrontierScience Research、群体智慧和安全对齐本版本明确排除。

报告始终输出 11×9 表格（benchmark 为行、九家旗舰模型为列）。无数值单元格显示 `NA`，底层保留 `not_evaluated`、`pending_publication`、`not_self_reported`、`not_applicable`、`non_comparable`、`source_unavailable` 或 `withdrawn`，不得把 NA 当零分。每家 Lab 的近似版本不能填充精确旗舰列。

AutomationBench、OSWorld 和 SpreadsheetBench 2 的不同 task set、harness、tool setting、grader 或 metric semantic 同时写入事件账本；事件证据可查询、可画图，但不得混入统一矩阵或横向排名。

## A/B 规则

A 在同一可比组比较 current/previous global frontier；正式确认要求差值 95% 置信下界大于 `max(2pp, 0.2 × historical_sd)`。没有可复算置信区间但点估计超过阈值时仅为 `provisional_expansion`；换版无 bridge 返回 `non_comparable_version_change`。

B 按三级要求递进：（1）多数任务解锁，默认要求 95% 置信下界高于 50%；（2）人类基准跨越；（3）经济可用门槛。后两级只有 benchmark 注册表预先定义阈值、口径和来源时才判断，没有定义即省略。只有点估计超过已定义阈值时为 `provisional_crossing`。LiveBench、AutomationBench-AA、OSWorld Partial 只报告 A；Terminal-Bench、Terminal-Bench-Science、SciCode、CritPt、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam 只有已注册的严格指标才报告 B。结果同时标注 `model_capability_proxy` 或 `model_agent_stack_capability`，两类不得合并。

## 报告与重放

`ats data ai-raw-capability --format markdown --chart-dir <dir>` 生成中文结论、11×9 矩阵、事件账本、A/B 表格、十一张方法卡和 PNG/CSV/JSON/sidecar。图表和报告共用 `rows_hash`、observation IDs、artifact IDs、cohort/method versions。`frontier_ai_capability` evidence bundle 会写入 snapshot manifest，并把当次矩阵、事件账本、A/B 判定和图表输入作为不可变 derived payload 一并保存；可用 `DataProducts.replay_frontier_ai_capability_snapshot(snapshot_id)` 离线复核。离线复核应使用保存的原始 artifact、数据库和 sidecar；若 hash 不一致，报告为可复现性失败。

如已获得 Artificial Analysis 权限，可将 key 放在部署环境或 `.env` 的 `ARTIFICIAL_ANALYSIS_API_KEY`（`.env.example` 只有空占位），并显式设置 `ATS_FRONTIER_AI_USE_AA=1`；不要写入 catalog、fixture 或报告。没有 key 时，默认适配器仍走公开来源；若公开来源不可用，才返回 `no_coverage`，矩阵完整显示 NA。`fixture=true` 或 synthetic 数据只能进入解析测试，禁止进入 platform。

网络与浏览器差异：Chat/浏览器会使用独立的网络栈、现有登录态并执行 JavaScript/Next.js 渲染；命令行适配器只读取公开 HTTP 响应，并且必须通过已登记的 JSON、JSON-LD、CSV 或 HTML 表格解析器。因此页面在浏览器可见，不代表旧解析器已经取到数据。Clash Verge 可在部署环境配置其 HTTP mixed port（通常 `http://127.0.0.1:7897`）：

```bash
export ATS_FRONTIER_AI_HTTP_PROXY=http://127.0.0.1:7897
export ATS_FRONTIER_AI_HTTPS_PROXY=http://127.0.0.1:7897
unset ALL_PROXY all_proxy
```

不要把 `socks5://` 地址直接交给 Python 标准库 `urllib`；它没有 SOCKS 支持。若只配置 `http_proxy`/`https_proxy`，`urllib` 也会读取它们；显式的 `ATS_FRONTIER_AI_*_PROXY` 优先用于该适配器。受限执行环境可能禁止访问宿主机 `127.0.0.1`，这时应在本地终端运行采集任务，或使用允许网络的执行环境，而不是把 NA 解释成来源没有数据。

## 故障处理

先运行 `ats data frontier-capability-health` 查看最近成功时间、覆盖率和 ingestion history。来源不可用时保持旧值并标记 stale；解析失败、身份不清、值域越界或 breaking method change 进入候选隔离。修复后重新运行 daily probe/full audit；不得通过手工填分、跨方法插值或把 self-report 升级为第三方成绩。
