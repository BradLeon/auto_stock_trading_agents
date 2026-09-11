# 背景

动机见 [proposal.md](./proposal.md)。现有 L1 AI 应用层 Observer 已经能够从 Anthropic Economic Index 的 Claude.ai 与 1P API 数据观察职业、任务及交互方式，但它衡量的是 Claude 流量内部的分布与生产化特征，缺少企业和就业人口分母。此次变更引入三类互补数据：

- Census BTOS Core：美国雇主企业是否已在任一业务职能使用 AI，以及未来六个月是否预计使用。
- RPS/FRED：美国 18–64 岁就业人口是否在工作中采用、上周使用、每日使用 GenAI，以及 AI 辅助工时和自报节省工时。
- ONS BICS AI：英国企业采用广度，以及在专题波次中披露的试验/有限/广泛使用、员工覆盖、业务用途和组织适配。

三个活跃来源的统计主体、分母、地区、技术范围和频率不同：BTOS 是企业比例，RPS 是就业人口比例，Anthropic 是 Claude 使用流量及可见任务。它们适合相互印证，不适合被加权成一个“AI 渗透率”。ONS 既有数据只保留作独立历史审计。

现有结构化平台已经具备不可变 artifact、series、observation vintage、derivation、entity、snapshot manifest 与 ingestion run。缺口主要有三项：

1. Adapter 目前以 `fetch()` 为主，没有统一表达“检查过但没有新发布”“未来 period 已排期但尚未发布”“本期未问 AI”等发现结果。
2. L1 Observer 的 DataProduct 和输出契约仍以 Anthropic 单源为中心。
3. Agent context 与人类审阅报告虽可由同一数据生成，但尚未对多来源口径、异步期间、图表 sidecar 和跨源印证作统一约束。

# 目标 / 非目标

## 目标

- 在不破坏现有 Adapter 的前提下，建立注册表驱动、可调度、可审计的主动发现与增量采集协议。
- 将来源数据保存为各自独立的数据集、series 和 source-native period，不前向填充或伪造共同日期；ONS 仅保留历史审计。
- 给 L1 Observer 提供三轴 evidence bundle：企业采用广度、员工持续使用、任务生产化结构。
- 所有结论、表格、图表和 Agent context 固定到同一组 observation、derivation 与 manifest，支持离线重放。
- 让读者能够从中文方法卡理解每个值的主体、分母、期间、限制和来源，并能从表格复算图表。

## 非目标

- 不采集 BTOS AI Supplement，也不为它保留待实施任务。
- 不采集 Eurostat 年度 AI 企业采用数据。
- 不回填或拼接 BTOS 2025-11-17 前的旧问题口径。
- 不把 RPS 自报使用解释为企业批准、付费席位、账户留存或正式部署。
- 不让 ONS 英国数据进入本 L1 命题、主动发现组或报告。
- 不从三轴计算综合分数、统一渗透率或投资决策信号。
- 不修改其他 sector/layer Observer 的命题、方法卡或输出结构。

# 技术决策

## 1. 采用“发现—采集—发布”三阶段协议

每个来源增加可选的 `discover()` 能力，统一返回 `DiscoveryResult`：

```python
DiscoveryResult(
    source_id="us_census_btos",
    checked_at="2026-09-09T02:00:00Z",
    status="new_release",
    latest_upstream_identity="BTOS:period_107:question_regime_v2",
    latest_available_period="2026-08-10/2026-08-23",
    candidates=[
        ReleaseCandidate(
            identity="BTOS:period_107:question_regime_v2",
            period="107",
            immutable_inputs=[...],
            methodology_fingerprint="sha256:...",
        )
    ],
    diagnostics={...},
)
```

流程如下：

```text
外部调度器 / 手工 release-check
        │
        ▼
来源注册表判断是否到期
        │
        ▼
discover：只读检查 metadata、period、文件或 series identity
        │
        ├── no_change / not_yet_published / question_not_fielded
        │         └── 只写来源检查账本
        │
        └── new_release / revision
                  ▼
              fetch candidate
                  ▼
        immutable artifact + parse + quality
                  ▼
       observation vintage + publication state
```

兼容策略：`RuntimeSourceSpec` 新增 discovery factory、check cadence、release identity strategy 和条件模块声明；尚未实现 `discover()` 的旧 Adapter 继续走现有 `fetch()`，不会被迫迁移。Anthropic 在本次变更中迁移到新协议，其他结构化来源可后续逐步采用。

选择该方案，是因为 metadata 检查通常远小于完整下载，且“没有新数据”本身是运维事实。备选方案是每周直接调用所有来源的 `fetch()`；它会重复下载大文件、难以区别未来排期与已发布数据，也无法正确表达 ONS 新波次没有 AI 问题，故不采用。

## 2. 新增来源检查账本，而不滥用 ingestion run

新增 `structured_source_checks`：

| 字段 | 含义 |
|---|---|
| `check_id` | 来源、检查时间和候选身份的内容哈希 |
| `source_id` / `dataset_id` | 被检查的数据源和数据集 |
| `checked_at` | 实际检查时间 |
| `status` | `no_change`、`new_release`、`not_yet_published`、`question_not_fielded`、`methodology_break`、`unreachable`、`validation_failed`、`succeeded` |
| `latest_upstream_identity` | 官方端当前最新可识别版本 |
| `latest_ingested_identity` | 本地最近已成功入库版本 |
| `latest_available_period` | 官方已发布且可读取的最新业务期间 |
| `candidate_identities_json` | 本次发现的候选列表 |
| `request_identity_json` | URL、参数、ETag/Last-Modified 或 metadata hash |
| `diagnostics_json` | 未发布、未问卷、漂移或错误细节 |

真正开始下载/解析时仍创建 `structured_ingestion_runs`。这样 discovery-only、no-change 和问卷未出现不会产生空 artifact 或虚假 ingestion。Health 查询从检查账本与 ingestion run 合并得到 `last_checked_at`、`latest_upstream`、`latest_ingested`、`latest_available_period` 和最近运行状态。

选择新表而不是把字段塞进 `structured_ingestion_runs.note`，是为了可查询、可测试，并能在没有 ingestion run 时保存检查结果。该表为追加式记录；同一检查内容可以通过 `check_id` 幂等。

## 3. 统一调度入口，但保留来源原生频率

外部 scheduler 每周调用一次：

```text
ats data release-check --group ai_adoption --ingest-new
```

也支持：

```text
ats data release-check --group ai_adoption
ats data release-check --source us_census_btos --ingest-new
ats data release-check --source anthropic_economic_index --force-check
```

统一入口只负责选择“到期”的来源并汇总逐来源结果；业务频率仍由来源声明：

| 来源 | 检查频率 | 业务频率 | 没有更新时的语义 |
|---|---|---|---|
| Anthropic Economic Index | 每周 | release event，未承诺固定日期 | `no_change` |
| Census BTOS | 每周 | 通常双周 | 未来排期为 `not_yet_published` |
| RPS/FRED | 每周 | 季度 | `no_change`，不因跨月而 stale |
| ONS BICS AI | 每周 | BICS 波次；AI 为条件模块 | 新波未问 AI 为 `question_not_fielded` |

来源级锁使用 `source_id + candidate identity`，避免手工运行与 scheduler 同时采集同一版本。某来源失败不阻断其他来源；总状态可为 `partial`。

不将该功能耦合到交易运行时 scheduler。数据更新由现有外部 cron/launchd/部署平台调用统一 CLI，避免交易时钟、市场日历和研究数据 cadence 互相污染。

## 4. 每个来源保留独立 dataset、artifact 与 period basis

注册如下：

| source_id | dataset_id | period_basis | 核心统计单位 |
|---|---|---|---|
| `anthropic_economic_index` | `ai_work_adoption` | `calendar_month` / `research_snapshot` | Claude 流量、SOC 职业、O*NET 任务 |
| `us_census_btos` | `ai_enterprise_adoption_us` | `survey_reference_window` | 美国 employer business |
| `rps_genai_adoption` | `ai_worker_adoption_us` | `calendar_quarter` | 美国 18–64 岁 employed adult |
| `ons_bics_ai` | `ai_enterprise_adoption_uk` | `survey_wave` | BICS 覆盖内英国 business |

跨来源 bundle 是虚拟 DataProduct，不创建一个混合 observation dataset。每个 artifact 保存官方 URL、响应/文件哈希、发布时间或可得时间、问题/方法 fingerprint、parser version 和 query scope。所有修订追加 vintage，不覆盖历史。

BTOS 和 ONS 的分层统计使用稳定 population entity，例如：

```text
BUSINESS_POP:US:ALL
BUSINESS_POP:US:NAICS:51
BUSINESS_POP:US:EMP:250_PLUS
BUSINESS_POP:US:NAICS:51:EMP:250_PLUS
BUSINESS_POP:UK:ALL
BUSINESS_POP:UK:SIC:J
```

RPS 全国值使用 `WORKER_POP:US:EMPLOYED_18_64`。来源原生行业、规模、问题 universe 和 answer 继续保存在 dimensions；不为跨国统一而改写 NAICS/SIC。

## 5. Census BTOS 以问题 fingerprint 和发布日期双重准入

发现器读取官方 periods、questions、answers 和 data metadata，不能仅按 period number 或包含“AI”的标签准入。候选必须同时满足：

1. collection/reference window 开始日期不早于 2025-11-17；
2. 问题规范化文本与批准的新口径 fingerprint 匹配，即过去两周/未来六个月在“任一业务职能”使用 AI；
3. data endpoint 已返回完整、可解析 observation；
4. 问题、答案和统计层级字典齐全。

periods API 中未来排期不等于已发布；只有数据可读取才更新 `latest_available_period`。旧“生产商品或服务”口径标记为 `out_of_scope_old_regime`，不会形成空缺或趋势起点。

每个 response share 保存 estimate 和 standard error；答案（Yes/No/Don't know 等）作为受治理维度。DataProduct 的 headline 选择 Yes，但质量校验使用完整答案集。核心字段包括 nationwide、NAICS sector/subsector、employment size 和 sector-by-size；州和 MSA 在首版过滤掉。

透明派生包括：

- 四期移动平均：最近四个已发布、同 regime、连续 BTOS period 的简单平均；输入少于四期则不返回。
- 当前—未来采用差：`current_yes_pct - expected_yes_pct`，单位为百分点；不解释为预测误差。
- 连续 period 变化：`current_period - previous_comparable_period`，单位为百分点。

不根据 standard error 自行声称统计显著性，因为连续调查和移动平均存在相关结构；报告展示 SE 与描述性变化，并明确这是方向性判断。

## 6. RPS/FRED 只接入工作口径的五条核心序列

首版白名单：

```text
RPSGENAIUSAGESHAREWORK       工作中采用 GenAI 的就业者比例
RPSGENAIUSAGESHARELWWORK     上周在工作中使用的就业者比例
RPSGENAIUSAGESHAREEDLWWOR    每日在工作中使用的就业者比例
RPSGENAIASSISTWRKHRSALL      AI 辅助工作时数/强度
RPSGENAITSALL                自报节省工作时数
```

实际注册时将 FRED series metadata 中的单位、频率、季调状态、notes 和来源机构一并固定。无需 API key 的官方 CSV 可作为默认数据入口；若配置 `FRED_API_KEY`，只增强 metadata 获取，不改变 observation identity 或使运行依赖鉴权。

release identity 为五条 series 的 `latest observation period + upstream updated_at + content hash`。每条 series 独立形成 slice，允许部分更新，但三轴 DataProduct 必须披露 RPS 内部 series period 是否齐平。

持续性派生：

```text
weekly_persistence_proxy = last_week_work_use_pct / work_adoption_pct
daily_persistence_proxy  = daily_work_use_pct / work_adoption_pct
```

它们是两个总体比例之比，不是同一 cohort 的 retention。`daily <= last_week <= adoption` 是逻辑质量门；若不满足则不计算 proxy。辅助工时和节省工时保留官方单位，不与百分比作加权。

## 7. ONS BICS 以“波次 + 问卷 regime”发现条件模块

发现器从 ONS 官方 BICS dataset/release 页面识别新 wave、workbook 和 questionnaire。release identity 为：

```text
wave number + workbook URL + workbook SHA-256
```

每个波次先解析 questionnaire，构造以下 fingerprint：

```text
normalized question text + answer options + routing + population universe
```

只有已批准 fingerprint 才自动发布；模糊关键词匹配只用于产生“可能出现新 AI 问题”的 schema-drift 候选，不用于自动准入。新 BICS wave 没有支持的 AI 问题时记录 `question_not_fielded`。

数据分两层：

- 稳定 headline：企业是否使用 AI、适用时的采用技术数量。
- 条件深度：广泛/有限/试验使用、员工日常使用覆盖档位、业务职能、采用方式、培训与角色等。

每条条件指标必须保存 denominator/universe。例如“采用企业中广泛使用的比例”不得展示成“全部企业中广泛使用的比例”。如果只有一个可比较波次，作为 snapshot，状态为 `insufficient_history`。

工作簿和官方发布页出现不一致时，两者均保留 artifact，自动趋势发布进入 conflict/warning，禁止静默选择更方便的值。

## 8. 三轴 DataProduct 不做数值融合

新增 `AiAdoptionEvidenceBundle`，组合 Anthropic、BTOS 与 RPS 三个来源专用 DataProduct：

```python
DataProducts.ai_adoption_evidence_bundle(
    as_of="2026-09-09T23:59:59+08:00",
    context_mode="review",  # compact | review
)
```

返回三条独立轴：

| 证据轴 | 主来源 | 核心观测 | 能回答的问题 |
|---|---|---|---|
| `enterprise_breadth` | BTOS | 当前/未来企业采用率、行业/规模分布、四期均值 | 美国企业采用面是否扩大 |
| `worker_persistence` | RPS/FRED | 工作采用、上周、每日、辅助工时、节省工时及 proxy | 已采用者是否呈现更持续、更高强度使用 |
| `task_production` | Anthropic 1P API | 四项现有生产化指标、职业内生产化任务覆盖 | Claude 的生产部署流量与任务结构是否扩大 |

每轴包含：

```json
{
  "axis_id": "enterprise_breadth",
  "status": "expanding",
  "latest_period": "2026-08-10/2026-08-23",
  "statistical_unit": "US employer business",
  "denominator": "all in-scope employer businesses",
  "latest_values": [],
  "history": [],
  "facts": [],
  "warnings": [],
  "observation_ids": [],
  "derivations": [],
  "lineage_ref": "..."
}
```

bundle 另外生成 comparability matrix，逐对说明 statistical unit、denominator、geography、technology scope、reference period、frequency 和 methodology regime。只有这些维度全部兼容才允许计算差值或相关性；否则最多标记方向性印证或冲突。

选择 bundle composition 而不是扩展 `ai_work_adoption` dataset，是为了保持 Anthropic 的流量口径清晰，也避免“企业百分比”和“Claude 流量百分比”意外进入同一个 series/query。

## 9. 异步期间按来源原样保留

`as_of` 查询对每个来源独立选择当时已知的最新合格 observation。bundle 返回每轴真实 data period、published_at、known_at 和 age：

```text
BTOS    2026-08-10/2026-08-23
RPS     2026-Q2
Anthropic 2026-05
```

不会把季度 RPS 值复制成 8 月值，也不会把 ONS 单次专题波次补成双周序列。`asynchronous_periods` 是正常诊断，不等同 stale。Freshness 由来源 cadence/发布承诺判断，而不是对所有轴使用统一天数。

趋势最低要求：同 methodology regime 下至少三个连续 source-native periods。每轴对 headline 序列先产生透明的方向状态：

- `expanding`：有效 headline 指标整体向上，且没有同等重要的反向指标。
- `contracting`：镜像规则。
- `stable`：变化位于该指标配置的描述性容差内。
- `mixed`：同轴核心指标方向相反或序列明显反转。
- `insufficient_history`：不足三个可比较期间。
- `unavailable`：没有合格值。

具体 headline、容差和规则以版本化配置保存，输出 rule version、输入 observation IDs 和逐步判定，不能只返回标签。容差用于避免把舍入噪声称为变化，不代表统计显著性。

整体状态规则：

- `broadening_and_deepening`：至少三轴具有可比较历史并为扩大，其余轴没有有效反向证据。
- `breadth_without_confirmed_depth`：企业广度扩大，但员工/任务深度没有同步确认。
- `provider_telemetry_only`：只有 Anthropic 任务轴改善，企业和员工轴未确认。
- `mixed_evidence`：可比轴之间存在方向冲突。
- 其余历史不足情形保留逐轴状态，以 `insufficient_history` 为整体结论，不强行归类。

## 10. Agent context 与人类报告共享同一证据包

DataProduct 先生成完整的、确定性排序的 review model，再从中裁剪 compact context。二者不各自查询数据：

```json
{
  "claim_id": "ai_core_production_workflow_penetration",
  "claim_definition_version": "v2",
  "claim_text": "AI 的企业采用广度……？",
  "as_of": "...",
  "overall_status": "breadth_without_confirmed_depth",
  "axes": ["...三条轴摘要..."],
  "corroboration": ["..."],
  "contradictions": ["..."],
  "facts": ["带数值、期间和 observation IDs 的事实"],
  "warnings": ["口径、异步期间、缺失和自报限制"],
  "visualization_refs": ["..."],
  "manifest_id": "..."
}
```

`compact` 默认受确定性字符/事实条数预算约束，优先级依次为：命题和整体状态、三轴最新值与分母、矛盾、重要限制、来源与 manifest。它可以截断行业/职业 TOPN，但不能省略统计主体、期间、质量或将部分覆盖标成完整。

`review` 增加全量可比历史、关键行业/规模、Anthropic 职业/任务覆盖、图表描述符和公式。两档均指向同一 manifest。

中文 Markdown 固定顺序：

1. 命题、结论与历史充分性。
2. 三轴总览表。
3. 跨来源方向性印证、冲突与时间错位。
4. 美国企业采用广度（BTOS）。
5. 美国员工持续使用（RPS/FRED）。
6. Anthropic 职业/任务组合覆盖与四项生产化指标。
7. TOP occupations/tasks 等示例清单。
8. 指标公式、方法卡、限制、来源与 lineage。

## 11. 图表只展示可比较数据，并保存 sidecar

首版图表：

- BTOS：全国当前/未来采用率与四期移动平均；行业、规模最新截面。
- RPS：工作采用、上周、每日使用 small multiples；辅助工时与节省工时单独面板。
- Anthropic：复用现有四项生产化指标、职业任务覆盖分布及 TOPN 图。
- 不输出跨来源状态/期间矩阵，也不把不同来源数值放在同一纵轴或合成指数。

所有图由 Pandas/Seaborn 或等价 renderer 从 DataProduct table rows 生成。标题使用正文同名中文指标。每张 PNG 旁保存 JSON sidecar：

```json
{
  "title": "美国企业当前使用 AI 的比例",
  "metric_definition": "...",
  "unit": "percent",
  "source": "US Census BTOS Core",
  "periods": ["..."],
  "rows_hash": "sha256:...",
  "observation_ids": ["..."],
  "manifest_id": "...",
  "renderer_version": "..."
}
```

图表失败时保留 packet、Markdown、CSV/JSON 表格并返回 `visualization_warning`，不将事实判断整体标记失败。

## 12. 保留稳定 claim ID，以 definition version 表达语义扩展

继续使用 `claim_id=ai_core_production_workflow_penetration`，新增 `claim_definition_version=v2`，并更新 `ai_hardware.yaml` 中 L1 的展示命题和方法版本。这样既保持 CLI、历史输出路径和消费者引用兼容，又不会把旧 Anthropic 单源结论与新三轴结论视为同一口径。

备选方案是创建全新 claim ID。它会造成配置、命令、文档链接和历史报告迁移，且用户关注的是同一 L1 命题的扩展，收益不足，因此不采用。按 manifest 重放旧结果时继续显示 v1 文本；新运行默认 v2。

## 13. 质量、幂等和血缘的发布边界

每个来源单独通过：

- schema/question/series fingerprint；
- 数值范围、单位、期间和重复冲突；
- 来源特有逻辑（BTOS answers/SE、RPS 顺序、ONS denominator、Anthropic 原有门）；
- eligible source rows 与 normalized observations 对账；
- artifact、observation、derivation、manifest lineage 完整性。

同一内容重跑返回 `no_change`；同一官方期间内容变化追加 vintage。未知问题/metric 进入 quarantine/pending mapping，不被自动忽略。一个来源失败时，bundle 可用最近成功版本，但必须显示 source age 和 failure warning；不得把旧值伪装为新值。

# 风险 / 权衡

## 14. 审阅返工设计：物理统一、紧凑血缘与趋势解释

正式运行统一使用 `ATS_DATA_DB_PATH`/`ATS_DATA_ARTIFACT_ROOT`；旧 `ATS_STRUCTURED_DB_PATH` 只保留给显式隔离测试。采集、发现、DataProducts 与 Evidence 必须由同一 factory 构造 repository，禁止跨库复制发布。

Snapshot 分为两层：主结论 manifest 只包含三轴 headline/trend 的直接输入；大规模 Anthropic 聚合、明细表和图表保存独立 derivation、输入数量、rows hash 与可展开 lineage pointer。Agent context 只携带摘要与指针，审计入口仍可展开叶子 IDs。

趋势规则升级为 `v2`。先按来源原生 period sort key 排序，再计算端点净变化、最小二乘斜率、相邻实质变化方向一致率和反向变化幅度。总体方向、斜率和多数变化一致时判定扩大/收缩；单次小回撤作为 caveat 披露。BTOS period number 必须按整数排序，展示时使用 reference window（若源数据具备）并附 wave 编号。

Markdown renderer 按章节匹配 visualization descriptor，将 PNG 直接嵌入，并列出 CSV/JSON/sidecar。图片仍不参与事实计算；renderer 失败只产生 warning。

## 15. 第二轮审阅收敛：ONS 退出与人类方法披露

ONS BICS 的专题问题稀疏、当前 headline 只有单次可比深度快照，对持续追踪命题缺少增量价值。因此它从 L1 bundle、主动更新组、manifest/context 和报告中退出；既有 observation/artifact 不删除，独立 DataProduct 和 Adapter 保留作历史审计。L1 变为 BTOS 企业采用广度、RPS 员工持续使用、Anthropic 任务生产化三轴，仍禁止数值融合。

状态/期间矩阵不提供额外可解释信息，renderer 不再生成。BTOS 趋势图内部继续按数值 wave 排序，但横轴只显示 `YYYY-MM-DD` 参考期日期，不暴露 W 编号。

趋势算法的 steps、斜率和方向一致率继续存在于结构化 packet，供测试、Agent 或审计使用；人类 Markdown 只展示趋势状态、最新值、起止变化和必要限制。正文新增来源方法表：发布机构/入口、指标定义与公式、统计主体、分母、地区、频率、参与统计维度与数量。Anthropic 额外披露 occupation/task 的可见/达标 cell 数、Usage Share 分母、生产化阈值和四项核心指标公式。

TOP occupation 的排名候选只接受受治理 entity taxonomy 中具有非 code-only `canonical_name` 的职业；过滤只作用于示例榜单，不改变总体生产化率、流量份额或底层 observation。

## 16. 第三轮审阅：Anthropic 图表信息密度

删除跨来源状态矩阵不等于收缩 Anthropic 自身的可视化。Anthropic 轴应从同一组 `period_rows` 生成双面板月度图：左图同时显示职业与任务的可见单元生产化率，右图同时显示职业与任务的生产化流量份额。正文使用同一派生行展示最近两个可比月及百分点变化，不由 Markdown renderer 二次计算。

职业内任务覆盖分布使用 `occupation_coverage_distribution.points` 绘制 CCDF：横轴为“职业内已确认生产化任务覆盖率”，纵轴为“覆盖率至少达到该水平的职业占比”。该图排在分布统计之后、TOPN 示例之前。两图均保存 source、period、rows hash、observation IDs、manifest ID 和 renderer version sidecar；它们不增加主结论 manifest 的叶子 observation 数量。

- **[BTOS periods API 包含未来排期]** → 以可读取数据、完整问题字典和日期边界共同判定 available，不以最大 period number 判定。
- **[BTOS 新旧题文本或标签相似]** → 使用规范化问题 fingerprint 与 2025-11-17 硬边界，旧 regime 明确 out-of-scope。
- **[ONS AI 问题不是每波都有，工作簿结构会变]** → questionnaire-first discovery；精确 fingerprint 才自动准入，模糊匹配只告警；`question_not_fielded` 不是 stale。
- **[FRED 序列可能修订，metadata/API 能力不同]** → 保存完整 query slice、updated metadata 和内容哈希；默认入口无需密钥，修订追加 vintage。
- **[RPS 自报使用不等于企业部署]** → 固定统计主体和不可推断事项；将 persistence 明确命名为 proxy。
- **[不同频率导致“最新”期间错位]** → 每轴显示真实期间和 age，不前向填充；跨源只做方向性印证。
- **[ONS 英国样本被误用于解释美国]** → comparability 默认为 contextual-only，正文固定为 UK supplement。
- **[综合状态掩盖单轴冲突]** → 不计算分数；整体标签必须同时返回逐轴状态、规则版本、事实与矛盾。
- **[Agent context 过长或裁剪掉关键口径]** → 从同一 review model 确定性裁剪，分母、期间、质量、警告和 manifest 为不可裁剪字段。
- **[图表与正文数据漂移]** → 图表、表格和 Agent packet 共享 rows hash 与 observation IDs；sidecar 对账测试。
- **[外部来源不可达或限流]** → 来源级重试/退避和隔离；保留最近成功数据并显式报告失败，不阻塞其他来源。
- **[周度 discovery 依赖外部调度器配置]** → CLI 与 schedule 配置都可审计，availability/health 显示 last_checked；运维验收包含真实定时入口。

# 迁移计划

1. **基础能力**：增加 source-check schema/repository、discovery protocol、注册表字段、来源级锁和统一 release-check CLI；先用 fixtures 验证，不改变现有发布路径。
2. **来源注册与 Adapter**：依次加入 BTOS、RPS/FRED、ONS；将 Anthropic discovery 迁移到统一协议，同时保留现有 ingestion 结果兼容。
3. **隔离回填**：
   - BTOS 只回填 collection start 不早于 2025-11-17 的新口径期间；
   - RPS 只回填五条工作口径官方历史；
   - ONS 只回填官方 workbook 明确可比的 AI headline/depth 波次；
   - Anthropic 不重写现有 observation。
4. **Source DataProducts**：完成来源专用查询、派生、质量和 `as_of`/vintage 验收。
5. **三轴 shadow bundle**：生成 comparability matrix、逐轴状态、compact/review context 和 manifest；与现有 v1 Observer 并行，不改变默认命题。
6. **输出验收**：在隔离数据库生成中文 Markdown、CSV/JSON、PNG 与 sidecar，核对公式、observation IDs、rows hash、字符预算和离线重放。
7. **L1 切换**：更新 `ai_hardware/L1_app` 配置到 claim definition v2，保持同一 claim ID 和按层命令；确认其他 layer 输出无变化。
8. **主动更新上线**：在部署环境启用 `ai_adoption` 周度 release-check；记录首个真实 `no_change`、新 release 或 `question_not_fielded` 状态。

回滚不删除已采集 artifact 或 observation。若 v2 Observer 或新来源出现问题，只需将 L1 配置切回 `claim_definition_version=v1`/旧 runner；新数据仍保留在隔离 dataset 中供审计，后续修复后再启用。若单一来源异常，可在 registry 暂停该 source 的自动 ingest，bundle 将其标为 unavailable/stale-with-warning，而不会污染其他轴。
