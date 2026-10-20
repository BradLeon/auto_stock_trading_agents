# 10.5 第二批：Macro 切换结果（2026-10-17）

> 范围：只做 Macro（工作流 `macro-review`，消费者 `macro`）。依据 `docs/TARGET_WORKFLOW_DATAFLOW.md` 与 `docs/PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`，照搬 Technical 的路径。未提交任何代码；未启用 `phase_e_schedules` 节奏、未加载常驻 `schedule --phase-e`、未启用任何数据任务；未改投影 schema；未碰 Layer/Sector、PEAD、ibkr_news、交易/审批路径。

## 一句话结论

Macro 读路由已切到 target 并保持在 target；一次真实评审（DeepSeek V4 Pro，24000 max_tokens）输出**正常解析**并已发布投影；读路由与调度两套切回演练均做完；**调度终态 legacy**。整个过程**只发出 1 次模型请求**（费用以 OpenRouter 回传的 usage 计为 0.0285 美元）。

## 一、做了什么

| 项 | 位置 | 内容 |
|---|---|---|
| 健康检查 `macro` | `src/ats/workflow/consumer_health.py`（`_macro`，登记进 `CHECKS`） | 只读：不调模型、不经 `get_store()`、不调任何 provider。读取评审实际读的四类已存数据并判定 |
| 已存输入读取器 | `src/ats/data/products/macro_inputs.py`（`stored_input_state`、`input_vintage_refs`） | 健康检查与数据版本共用同一次只读读取；区域数据直接读 `metric_series`，因为 `RegionalProducts.snapshot` 会登记派生行（写库），只读连接下会报错 |
| 数据版本 | `src/ats/workflow/dispatcher.py`（`default_data_vintage` 增加 `macro-review` 分支） | 由已存数据生成引用，**不联网**；输入不变则整批复用（TARGET §8.2-5） |
| 新鲜期 | `src/ats/workflow/phase_e.py` | **未改**，保持 `macro-review` 7 天（理由见第三节） |
| 切回演练探针 | `tmp/phase_f_first_run_20261016/rollback_drill.py` | 增加 `_probe_macro` 与 macro 的批准原文 |
| 测试 | `tests/test_macro_cutover.py`（新，19 项） | 健康检查的失败/警告边界、读取器、数据版本、复用判定、`macro-review` 调度切换 |

没有新增 src 文件，因此 `config/workflow/freeze_surface.yaml` 无需改动（`tests/test_phase_f_scoped_intake.py` 通过）。

### 健康检查阈值（均写在 `consumer_health.py` 常量里）

只有“评审做不成”才判失败；单项缺口一律是警告。

| 输入 | 失败（problems） | 警告（warnings） | 阈值与理由 |
|---|---|---|---|
| 已存指标序列（25 个，`macro_series`） | 产品读不出；一个序列都没存；已存数量不到已定义的 **50%**（`MACRO_SERIES_MIN_COVERAGE`）；**所有**日频序列都超过日频过期线（`STALE_DAYS['daily']`=10 天，沿用数据产品里已有的发布滞后口径）——说明日更采集已停 | 某序列未存；某序列过期（按产品的分频过期线，月度/季度已含发布滞后）；最新一条已存观测的采集时间距今超过 **5 天**（`MACRO_SERIES_COLLECTION_DAYS`，覆盖周末/长周末，仍无新数据说明 `macro_series_daily` 可能停了） | 评审的象限判定读多个序列，缺一两个仍能出结论；半数以上缺或日频全停才算做不成 |
| 已准入主题新闻（`macro_theme_news`，回看窗口取 `config/macro.yaml` 的 `search.recency_days`=21） | 产品读不出；有文档缺版本/哈希/来源；**所有主题合计 0 条**（采集明显坏了） | 某主题 0 条；全部新闻里最新发布时间超过 **7 天**（`MACRO_NEWS_NEWEST_DAYS`） | 评审没有新闻也能写“无已准入宏观材料”，但整套为 0 说明采集链路断了，此时切读路由不安全 |
| 区域月度出口（台湾 IC、韩国半导体） | 无 | 无数据；最新月份结束后已过 **45 天**（`MACRO_REGIONAL_STALE_DAYS`，约一个月发布滞后加半个月宽限，即漏了一次月度发布）；读不出也只是警告 | 评审代码里区域数据是“附加项，从不阻塞” |
| FactSet 指数材料 | 无 | 无已发布报告；报告日距今超过 **14 天**（`MACRO_FACTSET_STALE_DAYS`，周更报告漏了两期）；读不出也只是警告 | 评审代码里缺少该材料只会让对应判断为空 |

### 数据版本引用（复用依据）

`default_data_vintage` 对 `macro-review` 返回（均来自已存数据）：

- `macro-series:<n>@<摘要>`：每个序列最新 `known_at`（新数据点或修订都会推进它）；
- `macro-news:<n>@<摘要>`：回看窗口内全部已准入新闻的 `document_id@version_id`；
- `macro-regional:<id>:<最新月份>@<known_at>`（每个区域序列一条）；
- `macro-factset:<报告版本>`。

指标序列或新闻读不出时返回空列表（判“缺数据版本、需重算”），避免把读失败误当成“没变化”；区域/FactSet 读不出只是少了对应引用。

## 二、新鲜期（FRESHNESS）选择

`FRESHNESS_SECONDS['macro-review']` 保持 **7 天**，没有改。理由：

1. Macro 是周度评审（`phase_e_schedules.yaml` 周六），7 天恰为一个周期；
2. 这个数字同时被 Chief 侧的“投影最大年龄”校验使用（`decision_requirements`、`acceptance_matrix`），缩到 6 天会让周六产出的评审在下一个周五就被判过期；
3. 复用本身还要过数据版本这一关：日频序列每天都有新数据，所以**实际上只有在输入完全没变（重试、同日重复触发）时才会复用**，7 天只是上限，不会让一份陈旧评审被重复使用。

## 三、生产证据（按顺序）

所有原始记录在 `tmp/phase_f_first_run_20261016/macro/`。

| 步骤 | 命令/动作 | 结果 |
|---|---|---|
| 1 每日检查 | `ats consumer-cutover daily-check`；`ats consumer-cutover health --consumer-id macro` | 原三个已切消费者 ok；macro **ok**，4 条警告（`fed_policy` 主题 21 天内 0 条新闻；台湾/韩国区域最新月份 2026-08，已过 47 天；FactSet 报告 2026-09-25，已 22 天）。25/25 序列已存、0 过期、14 个日频序列在期内；新闻 49 条 |
| 无模型预演 | `rehearse_inputs.py`：真实装配评审上下文，不写库、不调模型 | 上下文 51.6K 字符（上限 60K），九个主题、区域、FactSet 均读到 |
| 2 切读路由 | `ats consumer-cutover switch --consumer-id macro ...`（观察至 2026-10-24） | 代次 1，legacy→target |
| 3 读路由切回演练 | `rollback_drill.py macro` | target→legacy→target，代次 2 → 3；每步用真实入口装饰器解析路由确认 |
| 4 调度切换 | `ats dispatch owner-switch --workflow macro-review ...`，批准原文用你指定的那句 | legacy→dispatcher，代次 1→2；日志含健康检查证据 |
| 5 第一遍（真实评审） | `observe_pass.py pass1`：对 `phase_e_schedules.yaml` 的 macro-review 条目发一次 schedule 触发（不改条目、不启节奏） | 状态 complete；`macro-review` succeeded，尝试 1 次；发布投影 `d6769ec763ed1d7bd0b1b523fff145b6`；**模型请求 1 次**（`macro_strategist` / `MacroReviewLLMView`，提示 56,840 字符，输入 21,217 tokens，输出 3,115 tokens，81 秒，OpenRouter 回传费用 0.0285 美元）；输出**解析成功**：象限 transition，8 个主题、10 条板块倾斜、5 条主要风险、利率路径、证伪条件均有 |
| 6 第二遍 | 先确认当时的数据版本与已发布投影的版本引用完全相同，再跑 `observe_pass.py pass2` | **整批复用**：同一投影 id，`reused=true`，**模型请求 0 次，新投影 0 条** |
| 7 旧入口失权 | `legacy_entry_refused.py`（评审函数换成陷阱，保证失败时不会调模型/写库） | `ScheduleAuthorityError: schedule frozen or caller is not current owner` |
| 8 调度切回 | `ats dispatch owner-rollback --workflow macro-review ...` | dispatcher→legacy，代次 2→3；无未完成认领，认领均 complete |
| 9 收尾核对 | `ats consumer-cutover daily-check`；`ats dispatch owner-log`；SQL 认领表 | 日检各消费者 ok（含 macro）；`macro-review` 模板行与范围行均为 legacy；macro 读路由 target 代次 3；`com.ats.schedule` 未加载 |

### 模型请求计数（来自 `llm_ledger_pass1.jsonl`，不是估算）

| 阶段 | 请求数 |
|---|---|
| 健康检查、无模型预演、读路由切换与演练、调度切换/切回、旧入口失权验证 | 0 |
| 第一遍（真实评审） | **1**（无空输出重试） |
| 第二遍（复用） | **0** |
| **合计** | **1** |

### 一处偏离你给的步骤顺序（需知悉）

你的顺序是“读路由切换 → 用 `ats macro review` 真实运行一次 → 切回演练 → 调度演练”。我**没有先用旧 CLI 入口跑一次**，而是把“真实运行”放在调度演练的第一遍（走 Dispatcher 入口，同一个 `macro_review.run`）。原因：CLI 入口发布的投影没有 agent 运行记录与数据版本引用，Dispatcher 判“不可复用”，如果再用 CLI 跑一遍，演练第一遍会对同一份数据**再发一次模型请求**，违反“不得重复发请求”。代价：这次没有旧 CLI 入口产出的 Obsidian 报告（见问题 1），其余（解析、投影发布、复用、旧入口失权）都已验证。

## 四、发现的问题

1. **Dispatcher 入口不写 Obsidian 周报。** 报告只在 CLI `run_macro_review` 里写（`report.write`）；`_macro_adapter` 只调 `review.run`。评审本身与投影已落库，所以对 Chief 无影响，但切到 Dispatcher 后周度 markdown 报告不会再生成（Technical 的适配器同样 `write_report=False`）。需要你决定：适配器补写报告，还是接受由 CLI 另行生成。
2. **投影的 `regime` 与叙述不一致（既有设计，非本次引入）。** 投影里的 regime 由象限映射（本次象限 transition → `transition`），而评审正文的 regime 文字是 “risk-off｜周期位置为 transition…”。Chief 读到的结构化字段与叙述方向可能不同。这属于 TARGET §7.3 投影变薄的同一类问题（待你决定是否补齐）。
3. **模型返回了配置里不存在的主题键 `monetary_policy`**（配置键为 `fed_policy`），该主题被丢弃（日志 `dropped unknown theme key`）。同时 `fed_policy` 主题近 21 天没有已准入新闻。评审里“货币政策”主题因此缺位，只剩利率路径文字。建议后续核对主题键提示或补该主题的新闻采集（上次采集时 8 条结果全部因超出 21 天窗口被丢）。
4. **`factset_earnings_assessment` 为空**：FactSet 材料已读到（上下文含该材料），但模型这次没有返回该可选结构，存档里为 `None`。DeepSeek 对大型嵌套可选字段的遵从度没有验证过，观察后续几期。
5. **数据覆盖缺口（警告项，非本次引入）**：台湾/韩国区域出口只到 2026-08（9 月数据已发布但未入库）；FactSet 指数报告停在 2026-09-25（周更漏了两期）。这两项都属于数据采集侧。
6. **测试：** `tests/test_macro_series.py::test_the_shipped_job_is_defined_and_not_enabled` 断言 `macro_series_daily` 未启用，而该任务已于今天经你批准启用，所以该测试现在失败（不是本次改动引起，我没有改它，属于数据流整改那一批的测试待更新）。`tests/test_consumer_health.py::test_a_consumer_without_a_defined_check_cannot_pass` 失败是因为同时在做的 Layer 一方已加了 `layer` 检查，该测试用 `layer` 作“无检查”的例子，需由那一方改用别的消费者。

## 五、测试结果

- 新增 `tests/test_macro_cutover.py` 19 项全部通过。
- 相邻测试：`test_macro_cutover, test_consumer_health, test_routine_baseline_and_reuse, test_schedule_owner_switch, test_phase_f_scoped_intake, test_phase_f_record_guardian, test_macro_series, test_macro_news_collection, test_macro_strategy, test_macro_regime, test_consumer_cutover`：232 通过、2 失败（即上面问题 6 所列两项，均不是本次改动引起）。

## 六、终态

| 项 | 状态 |
|---|---|
| Macro 读路由 | **target**（代次 3；观察至 2026-10-24，停止条件见切换日志） |
| `macro-review` 调度负责人 | **legacy**（模板行代次 3，范围行 legacy；无未完成认领） |
| 常驻 `schedule --phase-e` | 未加载；`com.ats.schedule` 不存在 |
| `phase_e_schedules.yaml` 的 `macro-review` | 仍是 `enabled: false`（未动） |

macro 读路由现在是 target，因此 `com.ats.consumer-health` 的每日检查会把 macro 纳入；失败时按既定机制自动切回旧读路径并告警。

## 七、待决/遗留

1. 要不要让 Dispatcher 适配器补写周报（问题 1）。
2. 若将来要让 Macro 长期由 Dispatcher 执行：除批准常驻进程外，还需把 `phase_e_schedules.yaml` 里 `macro-review` 改为 `enabled: true`（Technical 等已是这样）；这一步我按规则没做。演练时我是直接用该条目的内容手工触发，没有改配置。
3. 区域月度与 FactSet 周更的补采（问题 5）、`fed_policy` 主题新闻与主题键（问题 3）。
4. 投影变薄（问题 2）属于 §7.3 待你裁决的事项，本次未改 schema。
5. 切换条件文档第 6 节、tasks.md 与记忆目录由你合并，我没有改。
