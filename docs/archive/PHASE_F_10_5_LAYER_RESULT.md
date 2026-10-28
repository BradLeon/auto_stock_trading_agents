# 10.5 Layer 消费者切换结果（2026-10-17）

> 范围：`config/sectors/ai_hardware.yaml` 的 8 层，Dispatcher 任务 `layer-review`（每层一个实例，作用域 `layer:<key>`）。依据 `docs/ARCHITECTURE.md`（§4、§7.2、§8.2-5）和 `docs/archive/PHASE_F_NEW_LOGIC_SWITCH_CONDITIONS.md`。只写描述数据与分析，不含投资建议。

## 一、结论

Layer 已走完读路由切换、真实首跑、读路由切回演练、调度切换与切回演练。**终态：读路由 `target`（代次 3），调度负责人 `legacy`（代次 5：Layer 自己的演练 1→2→3，之后 Sector 演练因计划带 Layer 依赖又切过去再切回，3→4→5）。** 过程中发现并修复了一个会让所有层都被压成“平稳、信心 0.3”的缺陷（见第四节），这是这次最重要的发现。

## 二、做了什么

| 项 | 内容 |
|---|---|
| 健康检查 | `consumer_health._layer`（追加在 `check` 之后并登记进 `CHECKS`，不碰 Macro/Technical 代码）。输入描述集中在新文件 `src/ats/workflow/layer_sector_inputs.py`，健康检查和数据版本共用它，两者不会各说各话。 |
| 数据版本 | `dispatcher.default_data_vintage` 增加 `layer-review` 分支（实现在 `_layer_sector_vintage`）：每层的 `evidence:<层>:<条数>@<观测集合摘要>` 加 `knowledge:<笔记路径>@<内容哈希>`。纯本地读取，不发网络请求。 |
| 新鲜期 | `FRESHNESS_SECONDS["layer-review"]` 维持 7 天（只加了注释）。理由见第三节。 |
| 缺陷修复 | `agents/layer/layer_review.py::run`：调用方不传命题结论时，先算一次再同时给提示词和结论校验（第四节）。 |
| 测试 | 新增 `tests/test_layer_cutover.py` 19 项；`tests/test_consumer_health.py` 里“没有检查的消费者”用例原先拿 `layer` 举例，改为 `chief`。 |
| 登记 | 新文件登记进 `config/workflow/freeze_surface.yaml` 的 `src_tools`。 |

## 三、阈值与理由

Layer 评审（Dispatcher 适配器）实际只读两样东西：**命题证人的中性证据观测**（每个证人取最新 200 条，`assemble.layer_assessments`）和**各层判据知识笔记**（受治理的 `ai_hardware_knowledge_corpus`）。适配器不传横截面篮子，所以**受治理财务、共识、价格不进 Layer 提示词**（它们是 Sector 的输入，见 Sector 结果）。

健康检查**失败**只在评审做不成时：
- 行业配置读不了或没有层；证据产品读不了；
- 有观测缺少编号、标的、时间或来源文档；
- 整个行业没有任何证人的任何观测（证据抽取从未运行）；
- 所有层的判据笔记一篇都没有发布。

**警告**（评审自己的代码会处理，层会被封顶为“平稳、信心不超过 0.3”并在文字里写明）：
- 层没有命题（配置缺口）、层有命题但没有任何观测；
- 最新观测超过 120 天（`LAYER_EVIDENCE_STALE_DAYS`：证据抽取按季度财报节奏，超过一个报告期加一个月说明抽取停了）；
- 某些证人没有观测、某篇笔记未发布。

**新鲜期 7 天**：Layer 是每周六的评审，数据版本不变就复用（§8.2-5）；7 天只是给“版本看不见的变化”（换模型、改提示词）设上限，等于一个周期。

## 四、发现的缺陷与修复

首次生产运行（旧入口 `ats sector layer ai_hardware --layer all --offline`）8 层全部输出“平稳、信心 0.20/0.30”，而日志里命题结论明显是“支持”（如存储层 HBM 供给“39 条支持 vs 1 条反驳”）。原因：单层入口和 Dispatcher 适配器都**不传 `assessments`**，`_to_verdict` 因此认为“没有任何议题产出结论”，把每个有证据的层都强制成平稳、信心 ≤0.3，并在理由前加“证据缺失”。整行业评审路径（`_run_layered`）传了，所以此前没暴露。

修复：`run` 里调用方没传时先算一次（`assemble.layer_assessments`），提示词和结论校验共用同一份，**不增加模型请求**；引擎出错按“证据缺口”处理。护栏（空命题、议题没有结论时封顶 0.3）原样保留，另有测试保护。

修复前的 8 条投影（`workflow_run_id` 为空、无数据版本、全部平稳）仍以 `published` 留在 `task_projection_envelopes`；它们已被后来的 Dispatcher 投影按创建时间取代，Sector 与 `read_projection` 都只取最新的。我想把它们标成 `failed`（备份在 `tmp/phase_f_first_run_20261016/layer/`）时被权限分类器拒绝，**没有绕过，也没有改任何生产行**，见第八节。

## 五、生产记录（按顺序）

| 步骤 | 结果 |
|---|---|
| 健康检查 | `ok`，无问题；5 条警告（L1、L3 无命题；L5 证人 DOWJONES、SEMIANALYSIS 无观测；L6、L7 证人 KR_US_SUMMIT 无观测）。`tmp/.../layer/health_before.json` |
| 读路由切到 target | `consumer_cutover_log` seq 31，代次 1；观察期到 2026-10-24，批准原文为用户 10-17 指令 |
| 真实首跑（旧入口，读路由 target） | 8 层全部产出并发布投影，`layer_analyst` 与 `evidence_adjudicator` 输出均解析成功；暴露第四节缺陷 |
| 读路由切回演练 | target→legacy→target，经真实 `scoped_read("layer")` 解析，代次 1→2→3（seq 37、38）。`rollback_drill_layer.json` |
| 调度切到 dispatcher | `owner-switch` 代次 1→2，9 行（模板 + 8 层）；旧入口立即被拒：`schedule frozen or caller is not current owner` |
| 第 1 遍观察 | 8/8 成功，重算（旧投影无数据版本）；`layer_analyst` 8 次、`evidence_adjudicator` 26 次，共 34 次；用时 229 秒 |
| 第 2 遍观察 | 8/8 **全部复用**（`projection_reused`），0 次模型请求，0 条新投影；用时 12 秒 |
| 调度切回 | `owner-rollback` 代次 2→3，旧入口重新可用；终态 legacy |

观察用的是 `tmp/phase_f_first_run_20261016/layer/observe.py`：它只对 `layer-review` 一个工作流做与 `ats schedule --now` 同样的一次 `run_owned_workflow`，没有走 `--phase-e` 入口，以免误跑另一位同事正在切换的其他工作流；**没有启用任何 `phase_e_schedules` 条目，没有加载常驻进程**（`layer-review` 本来就没有定时条目，Sector 条目带它）。

## 六、模型请求数（取自 `count_llm.py` 的逐次记录，LangChain 层的真实调用，不是按请求数估的费用）

| 运行 | `layer_analyst` | `evidence_adjudicator` | 合计 | 备注 |
|---|---|---|---|---|
| 旧入口首跑（有缺陷版本） | 8 | 26 | 34 | 输入约 22.6 万、输出约 5.3 万 token；用时 8 分 54 秒 |
| Dispatcher 第 1 遍（修复后） | 8 | 26 | 34 | 输入约 22.3 万、输出约 4.9 万 token；用时 229 秒 |
| Dispatcher 第 2 遍 | 0 | 0 | 0 | 全部复用 |

总计 **68 次**，其中第一组 34 次因缺陷作废。没有重试、没有报错、没有空工具调用。`layer_analyst` 单次输出最大 5,523 token（上限 12000，**DeepSeek V4 Pro 上验证通过**），单次最长 92 秒；L1 在旧入口里单次 7,816 token、134 秒。SDK 内部的 HTTP 重试在这一层之下，看不到。

注意：计数器第一版有 bug（缓存的模型对象每次取用都叠加一个回调，同一次请求被记多次，首跑曾显示“387 次”），已改为每个模型只挂一个回调并按请求编号去重；上表是去重后的数。

## 七、输入覆盖（如实）

| 层 | 命题 | 证人 | 观测条数 | 最新观测 | 说明 |
|---|---|---|---|---|---|
| L1 应用 | 0 | 0 | 0 | — | 无命题（配置缺口），评审为“平稳、0.2” |
| L2 云 | 5 | 14 | 2066 | 10-16 | |
| L3 电力冷却 | 0 | 0 | 0 | — | 无命题（配置缺口），“平稳、0.3” |
| L4 互联 | 4 | 6 | 773 | 08-30 | 最旧，距今 48 天，仍在 120 天内 |
| L5 芯片设计 | 5 | 16 | 1841 | 10-16 | DOWJONES、SEMIANALYSIS 无观测 |
| L6 存储 | 3 | 12 | 1186 | 10-16 | KR_US_SUMMIT 无观测 |
| L7 代工封装 | 4 | 11 | 1369 | 10-17 | KR_US_SUMMIT 无观测 |
| L8 设备 | 5 | 9 | 1130 | 10-17 | |

8 层的判据笔记全部已发布。产出：L2、L4–L8 为 `expanding`（信心 0.50–0.75），L1、L3 为 `steady`（0.2、0.3，无命题）。10-16、10-17 的新证据来自试点证据抽取任务（TSM、MU、ASML）；本次**没有扩大任何数据任务**。

## 八、问题与待你决定

1. **8 条有缺陷的旧投影仍是 `published`。** 已被更新的取代，不影响读取；是否把它们标成 `failed`（需要对生产库做一次 UPDATE，我的操作被权限分类器拒绝）由你定。
2. **单层入口和 Dispatcher 适配器不带横截面篮子**，所以 Layer 投影里没有 ARCHITECTURE §7.3 说的“标的横截面排名”，提示词里也没有量化因子表（整行业旧路径有）。这是既有设计（适配器 `basket=None`），投影结构不变；要补齐属于 §7.3 的投影补齐决定，我没动。
3. **L1、L3 没有命题**，只能出“平稳、低信心”。这是配置缺口，不是数据问题。
4. **新鲜期之外的盲点**：数据版本看不见模型或提示词的变化，7 天内换了模型仍会复用旧结果；必要时手动重算。
5. 同一周内，新证据一到（例如 ASML 每天重试抽取）就会让相关层的版本变化而重算，这是预期行为，不是缺陷。
6. 无关的既有问题：`tests/test_sector_allocation.py::test_sector_allocation_projection_published_with_input_refs` 在今天失败——夹具把 `valid_until` 写死为 2026-09-30，时间一过投影被判过期（与本次改动无关，HEAD 上同样会失败）。

## 九、测试

`tests/test_layer_cutover.py`（19 项）、`tests/test_consumer_health.py`、`tests/test_routine_baseline_and_reuse.py`、`tests/test_schedule_owner_switch.py`、`tests/test_phase_f_scoped_intake.py`、`tests/test_phase_f_record_guardian.py`、`tests/test_layer_review.py` 全部通过。

## 十、文件

- 新增：`src/ats/workflow/layer_sector_inputs.py`、`tests/test_layer_cutover.py`
- 修改：`src/ats/workflow/consumer_health.py`（追加 `_layer`、`_sector`）、`src/ats/workflow/dispatcher.py`（`default_data_vintage` 一条分支 + 文件末尾 `_layer_sector_vintage`）、`src/ats/workflow/phase_e.py`（仅注释）、`src/ats/agents/layer/layer_review.py`、`config/workflow/freeze_surface.yaml`、`tests/test_consumer_health.py`（一行）
- 记录与脚本：`tmp/phase_f_first_run_20261016/layer/`（健康检查、切换记录、`count_llm.py`、`observe.py`、`rollback_drill_layer.py`、各遍观察的逐次请求日志）
