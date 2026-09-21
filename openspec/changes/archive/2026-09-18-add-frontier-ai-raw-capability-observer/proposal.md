## Why

L1 应用层目前已经能观察“生产化与应用扩散”和“商业化能力”，但缺少独立、可复算地回答“前沿生成式 AI 的原始能力边界是否持续外扩，以及是否跨过过去模型无法稳定完成的新任务门槛”的证据。模型发布和第三方评测都没有固定时间表，benchmark 还会更换版本、harness、grader 或评分语义；若只人工抄录最新排行榜，历史前沿会被网页修订覆盖，不同口径也会被误接成一条趋势。

## What Changes

- 新增 Frontier AI raw capability benchmark 结构化数据集，以免费、公开、可复核的 benchmark 维护者/独立第三方结果为主、竞对报告与 Labs 官方 release/model card 自报告为补充。**BREAKING**：默认面板从九项调整为十一项 benchmark：LiveBench、AutomationBench-AA（另带事件账本）、Terminal-Bench 4.0、Terminal-Bench Science 0.1、SciCode、CritPt、OSWorld 2.0、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam；EnterpriseOps-Gym 退出默认面板并由 Toolathlon Verified 替代。
- 首版跟踪九家 Frontier Labs 的最新公开通用旗舰：OpenAI、Anthropic、Google/Google DeepMind、xAI、DeepSeek、Moonshot/Kimi、Tencent、Z.ai/GLM、Alibaba/Qwen；ByteDance 不进入首版 cohort。旗舰资格按预定义的通用能力层级、公开可用状态与生效时间管理，专用、轻量或未公开可用模型不得静默替换当前旗舰。
- 将“新模型发现”和“新 benchmark 成绩发现”拆为两套增量流程：机器可读来源每日探测，Git README 表格来源每三日探测，新模型进入热观察期后加密探测，并以每周全量审计兜底；`NA` 从展示值扩展为可解释的底层状态，不把未评、未披露、不可比、来源不可用或撤回混为一类。
- 所有原始响应、页面切片、方法论版本和标准化观察按 immutable artifact/vintage 保存；同一成绩的修订追加新版本，不覆盖历史。每条成绩绑定 model identity、benchmark/version、harness、推理配置、评分语义、来源、score-as-of、known-at 与 observed-at。
- 建立 benchmark 可比性注册表。benchmark、harness、grader、任务集或评分规则发生破坏性变化时新建 series/comparability group，不把新旧版本静默拼接；只有存在明确桥接数据时才允许跨版本说明。
- 新增独立 L1 `raw_capability` Evidence Observer，回答两个固定命题：A. 能力前沿是否外扩；B. 指标语义允许时，是否跨过“多数任务解锁”门槛。A 比较同一可比组内当前 global frontier 与先前 global frontier；B 只对 strict/binary/accuracy 指标判断，默认门槛为 50%，优先使用置信区间下界，缺少任务级统计时只能标记暂定跨越。
- 明确区分 `model_capability_proxy`（LiveBench、SciCode、CritPt、MMMU-Pro、Humanity's Last Exam 等）与 `model_agent_stack_capability`（AutomationBench、Terminal-Bench、Terminal-Bench Science、OSWorld、Toolathlon Verified、SpreadsheetBench 2 等）。LiveBench 与 AutomationBench-AA 的 partial/composite 指标只报告 A；Terminal-Bench、Terminal-Bench Science、SciCode、CritPt、MMMU-Pro、Toolathlon Verified、SpreadsheetBench 2、Humanity's Last Exam 仅在注册指标满足 strict/binary/accuracy 语义时报告 B；OSWorld Partial 只报告 A，Binary/Strict 才可报告 B。
- AutomationBench 与 OSWorld 采用“统一可比矩阵 + 事件账本”双轨治理：不同 task set、harness、tool setting 或 metric semantic 的维护者、竞对和自报告结果进入不同 comparability group，全部保留但不得覆盖、平均或混排。
- 来源选择固定执行 `benchmark_maintainer/independent_third_party > competitor_reported > lab_self_reported`；若高优先级缺失可展示低优先级候选，但必须显式标注来源身份与不可比限制。Artificial Analysis 免费 API entitlement 不是依赖；公开页面中的结构化载荷可作为低频公开核验/采集通路，但必须经过条款、robots、请求预算和许可门禁，失败时退回官方公开载体或可审计人工导入，不得绕过登录、订阅或付费墙。
- 正式报告统一展示十一项 benchmark × 九家旗舰模型表格，覆盖不足使用 `NA`，并给出每项 benchmark 的评估方向、值域、指标含义、方法简述、可比性、来源和更新时间。B 命题按“多数任务解锁→人类基准→经济可用”三级递进展示跨越模型、得分与置信依据；后两级没有预定义阈值时省略。报告不得把综合榜单排名本身当作研究对象。
- FrontierMath Erdős 因当前绝对得分过低且横截面区分度不足、FrontierScience Research 因仅有单一 Lab 评测且缺乏共识，暂不进入默认面板；群体智慧与安全对齐也暂不纳入本次 Observer，后续需独立 proposal 评估。
- 新模型、成绩新增/修订、benchmark 方法论换版或门槛状态变化时事件触发重算；同时提供定期兜底探测、数据新鲜度、覆盖率、变更告警、历史 as-of 重放和离线 manifest replay。
- 全部采集、质量、可比性、命题、报告、图表、lineage、重放和隔离回归测试通过后直接进入 `platform` 正式 L1 报告，不保留额外 `shadow` 发布步骤；任一来源失败只降级 raw-capability 子结论，不影响生产化或商业化 Observer。

## Capabilities

### New Capabilities

- `data/frontier-ai-capability-benchmarks`: 定义旗舰模型发现、十一项 benchmark 成绩采集、事件账本、动态覆盖状态、不可变 vintage、方法论版本、可比性分组、增量更新、质量门和历史查询契约。
- `evidence/ai-raw-capability-observer`: 定义 L1 原始能力边界 Observer 的 A/B 命题、global frontier 与门槛判断、正式报告、图表、Agent context、事件触发重算和故障隔离契约。

### Modified Capabilities

- `data/structured-ingestion`: 增加不规则发布数据的发现/热观察/兜底审计、方法论变更检测、不可变成绩修订及可解释缺失状态要求。
- `data/structured-query`: 增加按模型、Lab、benchmark/version、harness、推理配置、可比组、来源和历史 `as_of` 查询成绩矩阵、global frontier 与覆盖状态的要求。

## Impact

- 数据配置：新增 Labs、模型 lineage/旗舰资格、benchmark 方法论、来源优先级、探测频率、新鲜度和门槛策略注册。
- 数据层：新增 LiveBench Git CSV/JSON、Terminal-Bench Git submission JSON、Terminal-Bench Science 公开 leaderboard JSON、OSWorld 官方结果/事件、Toolathlon Verified 官方榜、SpreadsheetBench 2 项目/论文事件、公开 Artificial Analysis 结构化页面（AutomationBench-AA、SciCode、CritPt、MMMU-Pro、HLE）以及 Labs release/model card 适配器；原始 artifact 与方法论快照、model/benchmark identity、成绩 vintage、coverage state、comparability group、事件账本和 raw-capability DataProduct。
- Evidence：`ai_hardware/L1_app` 新增第三个独立 Observer 轴 `raw_capability`，与既有生产化和商业化 Observer 分开运行、分开失败，不进入 Chain、评分、组合、风控或交易路径。
- 输出：新增统一十一乘九评测表、事件证据账本、benchmark 方法卡、A/B 判定表、frontier 变化和门槛跨越可视化、机器 packet、compact Agent context、CSV/JSON、sidecar 与 snapshot manifest；所有输出共享 observation IDs 和 rows hash。
- 运维：模型目录每日探测，JSON/CSV/Git submission 成绩每日探测，README 表格每三日探测，新模型 30 天热观察、31–90 天降频观察、每周完整审计；使用 ETag/Last-Modified、Git commit/blob SHA 与 content hash 做条件更新；无变化记录 `no_change`，方法论换版触发隔离和迁移审阅。
- 测试：覆盖新模型发现、旗舰替换规则、十一项 benchmark 与事件证据解析、来源优先级、精确模型版本、NA 状态、修订 vintage、as-of、版本断序、桥接、A/B 计算、置信状态、报告表格、图表字体、离线重放与既有 L1 Observer 隔离回归；2026-09-17 真实数据核验报告作为新版验收基线，synthetic fixture 仅可用于解析测试。
