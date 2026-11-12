# Design

## Context

动机见 proposal.md。与实现相关的现状：

- **网页适配器与调度。** `RampAIIndexAdapter` 已有 `browser` 注入点（`RampBrowserAdapter(exporter=...)`），但注册表只传 `export_dir`，没有任何 exporter 实现。定时任务 `ramp_ai_index_p10d_probe` 因此只读入站目录里的旧文件。
- **口径漂移会阻断入库。** `discovery.py` 按 scope 比较 `methodology_fingerprint`，变化时返回 `METHODOLOGY_DRIFT`，阻断自动入库。v2 的 header 全部变了，第一次探测必然触发这条规则。
- **v1 与 v2 天然分开。** series identity 由 `SeriesIdentity` 全字段哈希而来，`dimensions` 里含 `methodology_regime`。只要 v2 写 `ramp_ai_index_v2`，就会形成新序列，不会和 v1 成为同一序列的不同 vintage。
- **FRED 读取可复用。** `rps_genai_adoption` 已经实现了 FRED 读取：序列元数据校验、CSV 解析、按序列失败隔离。
- **运行环境。**
  - 机器是 8 GB 内存，交换区接近满载。
  - 出网经过 Clash，对新建 TLS 握手偶有重置（见 Yahoo 修复）。
  - 刷新控制器每小时唤醒一次，单任务上限 1800 秒。

## Goals / Non-Goals

**Goals：**
- FRED 部分无人值守、只依赖 HTTP；网页部分失败时可诊断、不静默。
- 第一次 v2 入库经显式批准，不靠关掉漂移检查。
- 一个序列只有一个 v2 来源。

**Non-Goals：**
- 不迁移、不改写 v1 数据。
- 不接 Ramp Developer API，也不接 Claude/ChatGPT 的 Ramp connector。
- 不修刷新控制器的 misfire 日志噪声。

## Decisions

### D1. FRED 部分作为独立来源 `ramp_ai_index_fred`

新增来源 `ramp_ai_index_fred`，只写数据集 `ramp_ai_adoption`，带自己的调度、刷新账本记录和新鲜度。

- **为什么拆成两个来源：** 刷新账本、重试、`freshness_slo_days`、发布模式都按来源记录。拆开后，“runner 失败不影响 FRED”由结构保证，不靠代码里的分支。
- **备选方案：** 同一来源内两条出口。放弃原因是一次运行的状态只能有一个：网页失败会把整次运行记成失败或部分成功，掩盖 FRED 已成功。
- **序列清单：** 14 条 FRED 序列登记在 `config/data/structured.yaml`，记录 FRED id → scope/segment 的映射。

  | scope | segment | FRED 序列 |
  |---|---|---|
  | `adoption_overall` | 总体 | `RAMPAIALL` |
  | `adoption_overall_models` | 3 家供应商 | `RAMPAIANTHROPIC`、`RAMPAIOPENAI`、`RAMPAIXAI` |
  | `adoption_sector` | 7 个行业 | `RAMPAICONSTRUCTION`、`RAMPAIFINANCE`、`RAMPAIHEALTHCARE`、`RAMPAIHOSPITALITY`、`RAMPAIMANUFACTURING`、`RAMPAIRETAIL`、`RAMPAITECH` |
  | `adoption_business_size` | 3 档规模 | `RAMPAISMB`、`RAMPAIMIDMARKET`、`RAMPAIENTERPRISE` |

  这份清单就是 D3 判断“FRED 是否已覆盖”的依据。
- **实现方式：** 适配器复用 RPS 的 FRED 请求与元数据校验。差异只在三处：频率要求为 Monthly；单位为 Percent；行业和供应商的 segment 名按映射表归一到 NAICS 全称和供应商名。这样网页增量与 FRED 部分能用同一套 entity id（D3 依赖这一点）。
- **调度：** 每周五 08:25 UTC 检查一次，排在 09:00 的 L1 观察器之前。FRED 的 Ramp 序列按月更新，周检足够。

### D2. 网页 runner：Playwright 无头浏览器，在点击前截获剪贴板写入

- **为什么不读系统剪贴板：** 无头模式下剪贴板权限不稳定。runner 在页面脚本运行前注入 `add_init_script`，包装 `navigator.clipboard.writeText`，记录按钮写入的原文。这与 2026-11-12 用浏览器手工验证的做法相同，拿到的是按钮复制的官方原文，不是截图，也没有猜测隐藏 URL。
- **流程：**
  1. 打开 `https://ramp.com/data/ai-index`。
  2. 按可见按钮切换图表：Adoption / Overall + Models、Sector、AI spend per employee、Token spend / By model maker、Token spend / By model。Token spend 先切到 Shares 模式。
  3. 逐个点击 “Get the data”，取截获的文本。
- **校验与落盘：** 每份导出先过 header 校验，再写入 `var/data/ramp_exports/<scope>.tsv`，同时写 `<scope>.json`，记录导出时间、页面 URL、按钮所在图表、SHA-256。写入采用原子替换。
- **接入点：** runner 作为 `RampBrowserAdapter` 的 exporter，由注册表在 Playwright 可用时注入。
- **任务流程不变：** 定时任务命令仍是 `ats data release-check --source ramp_ai_index --ingest-new --force-check`，不新增 CLI 动作，也就不用改控制器的命令白名单。
- **备选方案：**
  - 用 Selenium：依赖更重。
  - 直接请求页面背后的数据接口：违反“不得猜测隐藏 URL”。
  - 用桌面 App 内置浏览器：无法在 launchd 下无人值守运行。
- **资源控制：**
  - 单次只开一个浏览器上下文，运行完立即关闭。
  - 总超时 300 秒，每个图表超时 45 秒。
  - 运行间隔 10 天，内存压力可以接受。
- **失败归类：** 全部记为 `export_unreadable`，原因码细分为以下几种：
  - `browser_not_installed`
  - `navigation_blocked`：HTTP 403、挑战页，或标题不是 “Ramp AI Index”
  - `control_missing`
  - `clipboard_not_captured`
  - `timeout`

  任何一种失败都不回退去读旧入站文件。

### D3. 增量过滤：网页导出中 FRED 已覆盖的序列不入库

- **判断方式：** 解析网页导出后，按 (scope, entity_id) 判断是否在 FRED 序列清单中。在清单内的行不进入 `NativeRecord`，只在 discovery diagnostics 中参与交叉核对：同期间差值超过 0.01pp 时记 `fred_page_mismatch` warning。
- **为什么过滤在适配器里做，而不是在读取侧去重：** 读取侧去重要求每个读取方都知道两源优先级，容易漏；源头不入库，库里天然一个序列一个来源。
- **总体图的特殊处理：** 网页 `adoption_overall` 的唯一序列 RAMP_OVERALL 已由 FRED 覆盖，所以网页不再导出总体图，runner 只点 5 个按钮。但总体在 Overall + Models 导出中也会出现，同样按清单过滤。

### D4. v2 解析器

- **adoption：** `Date / Series|Sector / Adoption rate (%) / Monthly change (pp) / [Yearly change (pp)]`。
  - 行业 entity id 由 NAICS 全称 slug 生成，例如 `NAICS:accommodation_and_food_services`。
  - FRED 的行业名经映射表与之对齐。
  - 缺少同比列时不生成同比指标。
- **spend：** 列名带单位后缀 “(USD / employee / month)”，解析时去掉后缀再匹配分位数，并保留分位数顺序校验。
- **token 份额：** 宽表，第一列是周起始日期，其余每列一个模型或厂商，列名格式为 “模型名 (厂商)”。
  - 转成长表：一个 (周, 模型) 一条观测，value 为份额百分比，空值跳过。
  - 新增两个指标：`ai.ramp.token_spend_share_by_maker`、`ai.ramp.token_spend_share_by_model`。
  - 质量门：份额在 [0,100]；每周合计在 100±1 内，超出记 warning 不阻断（四舍五入和长尾导致）。
  - 若导出看起来是 Volumes 指数（任一值大于 100，或首行为 100 基准），拒绝入库并报 `methodology_drift`。
- **v2 标记：** 所有 v2 记录写 `methodology_regime: ramp_ai_index_v2`。methodology fingerprint 也纳入 regime 名。

### D5. 口径换代的显式批准

- **配置方式：** 在 `config/data/structured.yaml` 的 Ramp 两个来源下声明 `approved_methodology_regimes: [ramp_ai_index_v2]`。
- **discovery 的判定：** 检测到 fingerprint 变化时，若当前候选的 regime 不同于上次、且在批准清单内，记 `approved_regime_transition`，按新版本处理，不返回 `METHODOLOGY_DRIFT`。同一 regime 内的 header 变化仍按漂移阻断。
- **备选方案：** 清空 source check 历史，让第一次 v2 探测“没有上一次可比”。放弃原因是会丢失漂移诊断的可追溯性，也是在绕过护栏。

### D6. 读取侧按 regime 过滤

- **默认行为：** `ramp_ai_index` 数据产品的各查询函数新增参数 `methodology_regime`，默认 `ramp_ai_index_v2`，按观测 dimensions 过滤。
- **v1 审计：** 显式传 `ramp_ai_index_v1` 可读 v1。
- **旧 scope：** `model_market_share_overall` 只存在于 v1；v2 下查询它返回空结果并带 `scope_discontinued`。
- **观察器：** `work_adoption.py` 不需要知道 regime，靠默认值即得 v2。
- **新增产品函数：** `ramp_token_spend_share_series(scope=...)`，同时覆盖 `token_spend_share_by_maker` 和 `token_spend_share_by_model`。

### D7. 过期报警与 no_change 的约束

- **入站文件的使用条件：** 适配器只在入站文件的导出时间（读 `<scope>.json`；无 json 时读文件修改时间）不早于 10 天时，才把它当作本轮上游结果。否则必须调用 exporter；exporter 不可用或失败则 `export_unreadable`。
- **过期报警：** 网页来源的新鲜度门槛设为 35 天（`freshness_slo_days: 35`），由现有新鲜度检查报 `export_stale`。
- **为什么是 35 天：** 网页数据按月更新，加一个 10 天探测周期的余量。

## Risks / Trade-offs

- **[Ramp 拦截无头浏览器]** 先用 Chromium 的新无头模式和常规 UA；被拦则报 `navigation_blocked`。FRED 部分不受影响。若长期被拦，再评估改用有头模式，或调整调度时段。
- **[页面再次改版，按钮文案或布局变化]** 按可见文本定位按钮，失败时报 `control_missing`，并保存页面截图到诊断目录。截图只用于人工排查，不进入数据。
- **[FRED 与网页口径日后分叉]** 交叉核对 warning 会显示出来；FRED 侧的值不被网页覆盖。
- **[Playwright 与 Chromium 的磁盘和内存占用]** 约 150 MB 磁盘；运行时约 300–500 MB 内存，十天一次。作为可选依赖，未安装时只影响网页部分。
- **[周度份额与月度采用率混读]** 期间粒度在 dimensions 和图表横轴上区分；读取方不得跨 scope 拼接。

## Migration Plan

1. 合入代码与配置（新来源、批准的 regime、FRED 周任务）。runner 任务沿用原任务 id。
2. 用户确认后安装 `playwright` 与 Chromium。
3. 手动执行一次 FRED 来源的 `release-check --ingest-new`，核对 14 条序列入库；手动执行一次网页来源，核对增量入库与交叉核对结果。
4. 观察下一次 FRED 周检与 runner 十天探测的真实定时结果。
5. **回滚：**
   - 关闭两个任务。
   - 读取侧默认 regime 改回 v1，即可回到旧数据。
   - v2 序列与 v1 互不覆盖，回滚不需要删数据。
