# 10.5 Sector 消费者切换结果（2026-10-17）

> 范围：工作流 `sector-review`，作用域 `sector:ai_hardware`，依赖恰好 8 份有效 `LayerAnalysis`（`docs/PHASE_F_10_5_LAYER_RESULT.md`）。依据 `docs/TARGET_WORKFLOW_DATAFLOW.md`（§4、§7.2、§8.2-4/5）。只写数据与分析，不含投资建议。

## 一、结论

Sector 走完读路由切换、真实首跑、读路由切回演练、调度切换（连同 Layer 依赖）和切回演练。**终态：读路由 `target`（代次 3），`sector-review` 与 `layer-review` 调度负责人均为 `legacy`（代次 3 与 5）。** 第二遍观察 9 个任务（8 个 Layer + 1 个 Sector）全部复用，0 次模型请求，0 条新投影。

但这次暴露了**两个与数据流无关的模型输出问题**，它们让 Dispatcher 发布的那条 `SectorAllocation` 是降级的（理由栏只有“(轮动合成不可用)”）：`layer_rotation` 在 DeepSeek V4 Pro 上输出触顶 8192 token、被截断；`structure_analyst` 也有近一半调用触顶。详见第五、七节，需要你决定。

## 二、做了什么

| 项 | 内容 |
|---|---|
| 健康检查 | `consumer_health._sector`（与 Layer 一起追加在 `check` 之后并登记进 `CHECKS`）。输入描述在 `src/ats/workflow/layer_sector_inputs.py`。 |
| 数据版本 | `dispatcher.default_data_vintage` 的 `sector-review` 分支（`_layer_sector_vintage`）：`financials:`（30 只标的的完整报告包来源、期次与观测集合摘要）、`consensus:`（各标的快照时间摘要）、`factset:`（报告版本与状态）、`market-session:`（最近一个美股交易时段，周末映射到周五收盘后）、`layer-budget-bound:`（层预算开关）。8 份 Layer 投影已经是计划层面的输入引用（`projection:<id>:<hash>`），Layer 重算就会让 Sector 重算，不需要重复列。纯本地读取，不发网络请求。 |
| 新鲜期 | `FRESHNESS_SECONDS["sector-review"]` 维持 7 天（只加了注释），见第三节。 |
| 超时 | `TIMEOUT_SECONDS["sector-review"]` 1200→**3600** 秒。真实首跑用了 21.4 分钟；按原来的 1200 秒，Dispatcher 会取消这次尝试并整体重试（第一个工作线程仍在调用模型），请求数翻倍。 |
| 测试 | 新增 `tests/test_sector_cutover.py` 22 项（健康检查阈值与只读保证、数据版本稳定性与周末映射、计划恰好绑定 8 个 Layer 实例、复用判定、Layer 依赖必须同为 Dispatcher 负责人）。 |

## 三、阈值与理由

Sector 评审（Dispatcher 适配器）读：8 份 `LayerAnalysis` 投影；每层横截面（受治理成分股财务、平台共识、运行时价格）；各层命题证据（再评一遍，仅用于存档与报告）；一份 FactSet 行业材料（跨层轮动用）。

**失败**（评审做不成或做错的情形）：
- 行业配置读不了或没有层；
- 8 层里任何一层没有可用的 `LayerAnalysis`（缺失或哈希/schema 不符）。原因：Dispatcher 计划会自己补 Layer，但读路由为 target、调度仍是 legacy 时唯一可跑的入口 `ats sector review` 会读每层最新投影，少一份就拒绝运行，所以“先跑 Layer”是硬条件；
- 产品读不了（财务、共识）；
- 整个 30 只标的的截面没有任何受治理财务，或没有任何平台共识（平台数据断了，且不允许回退到供应商）。

**警告**：
- 某层部分标的没有受治理财务（这些标的按“层内中性”计分）；
- 共识早于 14 天（`SECTOR_CONSENSUS_STALE_DAYS`，同共识模块的 `MAX_AGE_DAYS`）或个别标的没有共识；
- Layer 投影早于 7 天（Dispatcher 会重建）；
- FactSet 行业材料不可用（轮动提示词会明说“未作为正式输入”）。

**不探测运行时价格、市值、贝塔、远期市盈率**：它们在评审运行时抓取，健康检查不调供应商；缺一只只影响那只标的的动量/估值因子。

**新鲜期 7 天**：Sector 是每周六评审。版本里含 Layer 投影编号、财务、共识、FactSet 与交易时段，所以 7 天主要是版本看不见的变化（换模型、改提示词）的上限。**降级结果也会被复用到数据版本变化为止**（见第七节第 1 条）。

## 四、生产记录（按顺序）

| 步骤 | 结果 |
|---|---|
| 健康检查（Layer 之前） | `failed`：8 层 `LayerAnalysis` 均缺失（`health_before.json`）。证明依赖被执行。 |
| 健康检查（Layer 之后） | `ok`，8 条警告（财务覆盖薄、共识旧、FactSet 无十一行业分区） |
| 读路由切到 target | `consumer_cutover_log` seq 39，代次 1；观察期到 2026-10-24 |
| 真实首跑（旧入口 `ats sector review ai_hardware`，读路由 target） | 成功，用时 21 分 24 秒；8 份 Layer 投影被 `governed()` 路径读取，发布 `SectorAllocation`（8 份投影编号为输入引用）；写了 8 份层报告和 1 份 HTML 到 Obsidian |
| 读路由切回演练 | target→legacy→target，经真实 `scoped_read("sector")` 解析，代次 1→2→3（seq 46、47） |
| 调度切换 | 先 `layer-review`（代次 3→4），再 `sector-review`（1→2）；因为 Sector 的计划带 8 个 Layer 实例，Dispatcher 对每个任务检查负责人 |
| 第 1 遍观察 | 9 个任务全部成功：**8 个 Layer 全部复用**（Dispatcher 补齐/复用 Layer 依赖，TARGET §8.2-4/5），Sector 重算（旧投影无数据版本）；用时 1917 秒 |
| 第 2 遍观察 | **9/9 全部复用**，0 次模型请求，0 条新投影；用时 14 秒 |
| 调度切回 | `sector-review` 2→3，`layer-review` 4→5；终态均为 legacy |

计划绑定：`build_plan(sector-review)` 恰好产生 8 个 `layer-review` 实例、8 条边，Sector 的依赖就是这 8 个且只有这 8 个（测试保护）。观察同 Layer 一样用 `observe.py` 只跑 `sector-review` 一个工作流，没有启用任何定时条目或常驻进程。旧入口在切到 dispatcher 后被拒的机制已由 Layer 的演练证明，Sector 沿用同一路径。

## 五、模型请求数（`count_llm.py` 逐次记录，去重后）

| 运行 | `structure_analyst` | `evidence_adjudicator` | `layer_rotation` | 合计 | 用时 |
|---|---|---|---|---|---|
| 旧入口首跑 | 12（5 次触顶 8192） | 52 | 1（输出 8133，勉强装下） | **65** | 21 分 24 秒 |
| Dispatcher 第 1 遍（8 个 Layer 复用，0 请求） | 12（5 次触顶） | 52 | 4（**4 次全部触顶 8192**） | **68** | 31 分 57 秒 |
| Dispatcher 第 2 遍 | 0 | 0 | 0 | **0** | 14 秒 |

总计 **133 次**。没有网络错误。输入 token 约 37.8 万 / 40.6 万，输出约 12.1 万 / 14.9 万。

- **`sector_analyst`（max_tokens 24000）在分层路径里不会被调用**（只在 `layers=False` 的旧单次合成里用），所以它在 DeepSeek 上仍未验证，也不影响本次切换。分层路径实际调用的是 `layer_analyst`（Layer 已验证）、`evidence_adjudicator`、`structure_analyst`、`layer_rotation`。
- **`evidence_adjudicator`：** 52/52 解析成功，单次最大输出 4,576 token。
- **`structure_analyst`（上限 8192）：** 12 次调用里 5 次输出恰好 8192（被截断，约 140 秒一次）；旧入口首跑有 1 层连续 4 次（`run_structured` 的重试上限）全部触顶而失败；Dispatcher 第 1 遍有 2 层失败（1 层同样 4 次触顶，另 1 层是 `names` 字段被模型输出成字符串、校验失败）。失败的层按设计降级为“没有结构层打分”，不影响其余。
- **`layer_rotation`（上限 8192）：** 首跑 1 次成功（8133 token，离上限只差 59），第二遍 4 次全部触顶，轮动合成失败，`SectorAllocation` 的理由栏只剩“(轮动合成不可用)”。这条降级投影已被 Dispatcher 发布并在第 2 遍复用。
- **重复请求：** 分层评审里同一批证据簇被判读了两次——`cross_section.run_layer` 里的 `packs_for_layer` 和 `layer_assessments` 各 26 次（共 52），而 Layer 阶段已经判过 26 次；判读器没有缓存。这是既有设计，我没改（见第七节第 3 条）。
- 每个 `get_model` 调用都会给缓存的模型对象叠加一个回调，计数器初版因此重复记录；已按请求编号去重，上表为去重数。

## 六、输入覆盖（如实）

**受治理成分股财务：** 30 只截面标的里 9 只有（AMD、AMZN、GOOG、KLAC、MRVL、MSFT、MU、NVDA、TSM），21 只没有。各层缺口：L2 云 3/6（META、CRWV、SPCX）；L3 电力冷却 4/4（全缺，截面为空）；L4 互联 6/7；L5 芯片设计 1/4（AVGO）；L6 存储 5/6；L7 代工封装 0（TSM 有）；L8 设备 3/4（ASML 因包门槛被拒、AMAT、LRCX）。**没有扩大任何数据任务。**

**平台共识：** 30 只都有快照，但 19 只早于 14 天。评审读这些旧快照时会走“缓存缺失”策略，**往持久采集队列里入队补抓任务**（日志里 `managed refresh`）；队列里已有 28 条 `cache_miss` 待处理任务（有些是 10-11 起就在的），目前没有任何计划任务消费它们，所以快照没有变化。这是评审读取时的既有副作用，不是我启用的数据任务，但值得知道。

**FactSet：** 最近一份报告 2026-09-25，状态 `registered_no_data`（十一行业分区未发布），轮动提示词里明说“未作为正式输入”。

**Layer 依赖：** 8 份有效投影（Layer 结果文档），L2、L4–L8 为 `expanding`，L1、L3 为 `steady`（两层无命题）。

**运行时价格：** 首跑与第一遍观察都取到了（yfinance 运行时），未单独探测。

## 七、问题与待你决定

1. **降级的 `SectorAllocation` 会被复用。** `layer_rotation` 失败时评审照常发布投影（理由为“(轮动合成不可用)”），Dispatcher 在数据版本不变时最多复用 7 天，一次偶发失败会粘住整周。当前最新的那条就是降级的（编号 `88964010…`，旧入口首跑那条正常的是 `c1b28490…`，已被它取代）。选项：A）把轮动失败当作任务失败（适配器多一道检查，工作流 `incomplete`，不复用）；B）保持现状。我没改。
2. **两个角色在 DeepSeek V4 Pro 上经常触顶 8192：** `layer_rotation`（8133、4×8192）、`structure_analyst`（5/12）。10-16 例行分类器也出现过同类现象（1K 提示词跑到上限），当时靠独立角色、温度 0、重试解决。是调高 `max_tokens`、把这两个角色也设为温度 0、还是精简提示词，需要你定；我没动 `config/settings.yaml`，因为不确定是长度不够还是模型失控，调高上限在失控时只会多花钱。
3. **证据判读重复三次、没有缓存**：Layer 一次、Sector 的 `packs_for_layer` 一次、`layer_assessments` 一次，提示词相同。可做进程内或持久缓存（键：命题 + 证据簇集合，随模型/提示词版本失效）。涉及缓存位置和失效策略，属于设计决定，没做。
4. **Sector 一次运行约 20–32 分钟**（大部分是 `structure_analyst` 的触顶重试）。超时已调到 3600 秒；若上面第 2 条解决，时间会大幅下降。
5. **旧入口 `ats sector review` 在读路由 target 时每次都会触发 8×（运行时行情 + 共识缓存缺失入队 + 结构层调用）**，且要求 8 份 Layer 投影都存在，所以周六旧级联里 Layer 必须先跑。
6. `target_weight=1.0` 是各层预算之和截到 1 的既有公式，不是新逻辑；投影结构未改（TARGET §7.3 的三级配置仍是待你决定的事项）。
7. 无关的既有失败：`tests/test_sector_viz.py` 4 项（`run_sector_html` 的夹具用不存在的行业名 `test_sector`，`scoped_read` 在解析业务范围时抛 `RouteUnavailable`），`tests/test_sector_allocation.py` 1 项（夹具写死 `valid_until` 2026-09-30，已过期）。均与本次改动无关。
8. 与 Layer 相同：数据版本看不见模型/提示词变化；新证据或新共识会让对应版本变化而重算。

## 八、测试

`tests/test_sector_cutover.py`（22 项）、`tests/test_layer_cutover.py`（19 项）与相邻 20 个文件共 345 项通过，4 项失败全部在 `tests/test_sector_viz.py`，是上面第 7 条的既有失败。

## 九、文件

- 新增：`tests/test_sector_cutover.py`（与 Layer 共用 `src/ats/workflow/layer_sector_inputs.py`）
- 修改：`src/ats/workflow/phase_e.py`（`sector-review` 超时 3600 + 新鲜期注释）、`src/ats/workflow/consumer_health.py` 与 `dispatcher.py`（见 Layer 结果文档）
- 记录与脚本：`tmp/phase_f_first_run_20261016/sector/`（健康检查、切换与演练记录、各遍观察结果、逐次请求日志）；脚本在 `tmp/phase_f_first_run_20261016/layer/`
