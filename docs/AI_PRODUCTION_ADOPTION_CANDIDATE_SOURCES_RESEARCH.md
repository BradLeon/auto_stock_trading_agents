# AI 生产化与应用扩散候选信源调研

> 核验日期：2026-09-09
> 研究范围：美国 Census BTOS、BTOS AI Supplement、Eurostat `isoc_eb_ai` / `isoc_eb_ain2`、英国 ONS BICS、FRED/RPS GenAI Adoption Tracker
> 目标命题：**已经达到技术可用门槛的 AI 能力，有多少进入持续运行的生产工作流，并以多快速度渗透行业、职能和任务？**

## 一、结论摘要

这批信源值得纳入 L1 AI 应用层，但不能被合并成一个“AI 渗透率”。它们测量的是不同对象、不同分母和不同阶段：

| 信源 | 实际测量对象 | 建议定位 | 接入优先级 |
|---|---|---|---|
| Census BTOS Core | 美国雇主企业中，过去两周在任一业务职能使用 AI 的企业占比 | 企业采用广度的高频主序列 | **P0：接入** |
| RPS/FRED | 美国 18–64 岁就业人口中，工作采用、上周使用、每日使用及 AI 辅助工时占比 | 员工扩散与持续使用的季度主序列 | **P0：接入** |
| BTOS AI Supplement | 企业内部职能、员工任务、增强/替代/新任务及组织投入 | 生产化深度的官方研究快照 | **P1：接入，事件驱动** |
| Eurostat | 欧盟 10 人以上企业使用 AI 的比例、技术类型和业务用途 | 国际结构性年度基准 | **P2：接入** |
| ONS BICS AI | 英国企业 AI 广度、使用深度、员工日常使用、用途和组织适配 | 生产化深度的补充快照 | **P2：监听后接入** |

最合适的证据架构是四条并列轴，而不是单一综合分：

1. **企业采用广度**：BTOS Core；Eurostat 和 ONS 用作跨区域结构性参照。
2. **员工持续使用**：RPS 的工作采用、上周使用、每日使用和辅助工时。
3. **组织嵌入深度**：BTOS Supplement 的职能广度、工作流调整、培训和投资；ONS 的 extensive / limited / pilot 及员工日常使用覆盖。
4. **任务生产化结构**：现有 Anthropic 1P API Observer 的职业/任务生产化代理。

其中，BTOS 和 RPS 正好补上 Anthropic 数据最重要的分母缺口：Anthropic 的 `Usage Share` 是 Claude 流量在职业或任务间的份额，不是企业采用率，也不是从业者采用率。官方调查不能替代 Anthropic 的任务遥测，Anthropic 也不能替代官方调查的企业和就业人口分母。

## 二、先把“采用率”拆成可比较的物理量

| 观察层 | 分子 | 分母 | 能回答什么 | 不能回答什么 |
|---|---|---|---|---|
| 企业采用广度 | 报告在规定期间使用 AI 的企业 | 目标企业总体 | 有多少企业已开始使用 | 企业内有多少员工、任务或工时已 AI 化 |
| 员工使用广度 | 报告工作中使用 GenAI 的就业者 | 目标就业人口 | 有多少员工接触或使用 | 是否经企业批准、是否嵌入正式工作流 |
| 使用持续性 | 上周或每个工作日都使用的就业者 | 目标就业人口或工作采用者 | 使用是否从偶发走向重复 | 是否自动化了完整流程 |
| 组织嵌入深度 | 多职能使用、工作流调整、培训、资本投入等 | 企业或 AI 使用企业 | 企业是否为 AI 改造组织 | 改造是否已产生因果性的生产率提升 |
| 任务生产化结构 | 达到工作用途、自动化、Directive 阈值的 Claude 流量单元 | 可见 Claude 1P API 职业/任务单元或流量 | 哪些任务更像生产部署 | 全经济采用率、全行业员工渗透率 |

因此，Observer 应明确报告每个数值的 `statistical_unit`、`denominator_scope`、`reference_period`、`technology_scope` 和 `methodology_regime`。缺任一项，数值不得进入跨期或跨源比较。

## 三、美国 Census BTOS Core

### 3.1 数据内容与物理含义

2025 年 11 月 17 日以后，核心问题是：企业在过去两周是否在**任一业务职能**使用 AI，以及未来六个月是否预计使用。全国采用率的物理含义是：

```text
enterprise_ai_use_rate_pct
  = 100 × 加权回答“是”的企业数 / 加权回答该问题的企业数
```

这是企业计数加权的比例，不是就业人数加权比例。一个 3 人公司与一个 3 万人公司在企业采用率中各代表一个企业总体单位；分行业和企业规模数据可回答扩散是否从大型、知识密集企业向更广泛企业群体扩展。

官方 API 无需鉴权即可读取 period、问题、答案、strata 和各期间数据；下载页同时提供全国、行业、子行业、就业规模、行业×规模等 Excel 历史文件。API 适合持续 discovery，历史文件适合对账与回填。[^1]

截至本次核验，最新已发布且可由 API 直接确认的完整期间为 2026-08-10 至 2026-08-23：全国企业 AI 当前使用率为 **22.4%**，标准误 **0.36 个百分点**。作为历史参照，Census 对 2025 年 12 月至 2026 年 5 月的官方总结是，全国使用率约在 17%–20% 之间；截至 2026-05-03 为 19.8%。[^2]

### 3.2 可信度

BTOS 是 Census 官方、全国代表性的高频企业调查。每个样本年约抽取 120 万家雇主企业，分成六个双周 panel，同一企业约每 12 周受访一次；调查为自愿性质，按企业规模、行业和州等进行抽样与非响应调整。快速发布的代价是原始响应不做传统编辑，详细切片可能被抑制；官方提供标准误和质量标记。[^3]

这使它很适合回答“有多少企业采用”，但它仍是企业代表的自报数据。企业内部存在未经管理层知晓的员工使用时，BTOS 可能低于员工调查；反过来，大企业只要任一职能使用，就会被计为采用，但并不代表员工广泛使用。

### 3.3 必须处理的方法断点

2025-11-17 起，问题从“在生产商品或服务时使用 AI”改为“在任一业务职能使用 AI”。Census 观察到明显 level shift，明确从 2025-12-04 发布数据起建立新序列；旧、新序列不能直接拼接。[^4]

Observer 必须采用：

- `methodology_regime=any_business_function_v2`；核心趋势从 2025-12-04 对应的新序列开始。
- 旧口径只能作为 `producing_goods_services_v1` 独立历史 regime。
- 不跨 regime 计算增速、趋势或移动平均。
- 高频展示使用四个已发布 period 的移动平均，同时保留原值和标准误，降低 panel 轮换与抽样波动造成的误读。

### 3.4 可披露事实

- 全国企业采用率、未来六个月预期采用率及其双周变化。
- 按 NAICS 行业、企业就业规模及行业×规模的采用率差异。
- 采用是否由大企业和少数知识密集行业向更多企业扩散。
- “预期采用－当前采用”的 pipeline gap，但它只是预期，不是承诺或订单。

不可披露为事实：企业内部员工渗透、日常使用率、任务自动化率、生产率贡献、岗位替代率。

### 3.5 L1 Observer 适配结论

**适合成为企业采用广度的核心高频序列，优先级最高。** 它具备公开、免费、可信、持续更新、机器可读、可证伪和行业/规模切片等条件。它不应进入现有 Anthropic 阈值公式，而应作为独立事实轴。

## 四、Census BTOS AI Supplement

### 4.1 数据内容

第二轮 AI Supplement 的采集期为 2025-11-17 至 2026-02-08，六个双周 panel 合并成一个全国代表性快照。公开工作簿包含全国、行业、州和就业规模的 estimate 与 standard error，以及 Data Dictionary。它测量三层扩散：

1. 企业层：当前/未来 AI 使用。
2. 职能层：15 类业务职能中的部署，包括销售营销、战略与业务发展、IT、R&D、财务、人力资源、客户服务、生产等。
3. 员工任务层：员工是否将 AI 用于工作任务；GenAI 用于写作编辑、信息搜索、文档分析等任务类别。

它还直接询问 AI 对任务的作用：执行原由员工完成的任务、增强员工任务、产生新任务；并询问替代任务的数量级、软件/设备替代、培训、招聘、算力/软件投入、云服务、数据流程、工作流变化和供应商使用。[^5]

### 4.2 分母是最大的解析风险

工作簿不是所有百分比都以“全部企业”为分母。Data Dictionary 的条件总体至少包括：全部企业、报告在任一职能使用 AI 的企业、报告 AI 执行原员工任务的企业、报告 GenAI 辅助工作任务的企业、未来预计使用的企业和未来不预计使用的企业。

例如“销售与营销 52%”是**在报告职能使用 AI 的企业中**，有 52% 在销售与营销职能使用；它不能与“全部企业中 18% 使用 AI”直接并列成同一分母，也不能读成 52% 的美国企业在销售营销使用 AI。

接入时每个 cell 必须保存：

```text
question_id
answer_id
universe_code
universe_label
estimate_pct
standard_error_pct
stratum_type / stratum_value
reference_window
release_version
```

### 4.3 当前快照揭示了什么

官方研究报告显示：补充调查期间约 **18%** 的企业在业务职能使用 AI，就业加权后为 **32%**；约 **23%** 的企业报告员工把 AI 用于工作任务，就业加权后为 **41%**。两者并不完全重合，说明既存在员工先行的 bottom-up 扩散，也存在企业已部署但员工任务尚未广泛使用的 top-down 落地时滞。[^6]

在职能采用者中，57% 只部署在 1–3 个职能，最常见的是销售营销 52%、战略与业务发展 45%、IT 41%；在报告 GenAI 任务使用的企业中，65% 只覆盖 1–3 类任务。任务作用仍以增强为主：44% 的 AI 使用企业报告增强任务，在报告任务变化的企业中 66% 只有增强；纯替代和纯新增任务各约 5%。AI 使用企业中报告就业减少约 2%，而报告替换软件或设备约 16%。这些都是描述性统计，论文明确不提供因果解释。[^6]

### 4.4 L1 Observer 适配结论

**高度适合，但只能作为 `research_snapshot` / `topical_cycle`，不能伪造成双周连续序列。** 它是本批信源中最贴近“企业职能—员工任务—组织投资—替代/增强”完整生产化链条的数据，优先级仅次于 BTOS Core 与 RPS。

Observer 可在新 supplement 发布时更新以下深度状态：

- `functional_breadth_distribution`：采用企业覆盖的职能数量分布。
- `worker_task_breadth_distribution`：任务类别覆盖数量分布。
- `augmentation / substitution / creation share`：明确分母后分别展示。
- `workflow_change / training / software_compute_investment share`。
- `top_down_only / bottom_up_only / aligned_use share`。

在没有新专题波次时，应显示 `no_new_snapshot`，而不是 stale，也不对上一快照做插值。

## 五、Eurostat `isoc_eb_ai` 与 `isoc_eb_ain2`

### 5.1 数据内容与可达性

Eurostat 的年度 ICT 企业调查由各国统计机构按 harmonised model questionnaire 执行，数据通过免费 SDMX/API 和 Data Browser 提供。需要同时接入两个数据集：

- `isoc_eb_ai`：主要按企业规模拆分。
- `isoc_eb_ain2`：主要按 NACE Rev.2 经济活动拆分。

仅接 `isoc_eb_ai` 会缺失用户关心的行业截面。API 维度包括频率、企业规模、NACE、AI 指标、单位/分母、地区和年份。尤其要保留 `unit`：`PC_ENT` 是占全部目标企业的比例，`PC_ENT_AI_TANY` 是占 AI 使用企业的比例；二者绝不能混合。[^7]

指标覆盖：至少使用一种/两种/三种 AI 技术；文本分析、语音识别、生成文本/语音/代码、生成图像音视频、图像识别、机器学习数据分析、工作流自动化/决策辅助、自主机器；以及营销销售、生产、行政管理、财务、物流、安全、R&D 等业务用途，另有获取方式和不采用障碍。

### 5.2 当前可披露事实

2025 年，欧盟 10 人以上目标企业中 **19.95%** 使用至少一种 AI 技术，高于 2024 年的 13.48%；小型、中型和大型企业分别为 17.0%、30.36% 和 55.03%。信息通信业为 62.52%，专业科技服务为 40.43%，建筑业为 10.79%。文本分析使用率为 11.75%，生成图像/音视频为 9.55%，生成文字/语音/代码为 8.76%。[^8]

业务用途百分比多数以 **AI 使用企业** 为分母：2025 年营销销售 34.70%、行政管理约 31.05%、生产流程约 20.76%。这些不能直接解释为全部欧盟企业的用途采用率，除非回到带 `unit` 的原始 cell 或在相同层级下进行有血缘的条件概率换算。[^8]

### 5.3 可信度与限制

统计单位是企业，核心目标总体为 10 人以上企业；微型企业由各国自愿覆盖，不宜用于欧盟统一比较。2025 年约从 153 万家目标企业中抽取 15.7 万家。覆盖 NACE C–J、L–N 和 95.1，主总体不含金融业。数据年度采集、通常年末发布；定义和问卷会随技术变化，因此部分 AI 指标只有 2021、2023、2024、2025，且 2022 缺失，某些新技术类别会形成断点。[^7]

Eurostat 的法律与统计制度、统一问卷和国家统计机构执行使其可信度很高；但年度频率太低，且技术范围包括传统 ML、视觉、机器人等，不是前沿大模型或 GenAI 的纯代理。国家间翻译、参考期、非响应处理和问卷 routing 仍可能造成有限可比性。[^7]

### 5.4 L1 Observer 适配结论

**适合做年度结构校准，不适合做 L1 高频 heartbeat。** 建议在每年新数据发布后更新一次：

- 企业 AI 采用率及年度百分点变化。
- 按规模和 NACE 的采用率分布。
- 技术组合深度：至少 2/3 种技术的企业比例。
- 更接近生产流程的直接指标：工作流自动化/决策辅助、生产流程用途。
- 不采用障碍及其条件分母。

必须自行保存每次 API 响应和 release time；当前 dissemination API 适合拿“最新修订值”，不应被当作完整 vintage 存储。

## 六、英国 ONS BICS AI

### 6.1 数据内容

BICS 主调查每月发布两次，每个 wave 均有公开 XLSX 和历史版本；但问卷会按政策重点频繁增加、删除或修改问题，因此“BICS 高频”不等于“AI 模块固定高频”。2026 年 AI 专题报告基于 Wave 159，专题报告下一次更新时间仍是 `To be announced`。[^9]

它的长期 headline 是企业是否使用至少一种列举的 AI 技术。2026 年新加入的深度题更有价值：

- 使用程度：extensive、limited、pilot/testing、not currently、don't know。
- 企业中每日工作使用 AI 的员工占比区间。
- 业务用途：改善运营、新产品/服务、新市场、个性化等。
- 采用方式：自研、外包、购买即用、免费工具。
- AI 投资、受影响岗位、培训与技能整合、就业变化、障碍和短期计划。

### 6.2 当前可披露事实

在 10 人以上企业中，至少使用一种 AI 技术的比例从 2023 年末约 12% 升至 2026 年 6 月约 35%；但采用企业平均使用的 AI 技术数量只从约 1.4 种升到 1.6 种。2026 年 6 月，在 AI 使用企业中只有 **10%** 报告 extensive use，**15%** 报告超过一半员工在日常工作中使用 AI。信息通信业 adoption 为 58%，建筑业为 13%。[^10]

这组结果非常适合作为“广度增长快、深度仍浅”的可证伪结构，而不是把 35% 直接称为生产化率。ONS 自己也明确指出，headline 将轻度、用户级和嵌入生产级使用等同处理，新增深度题才开始直接弥补这个缺口。[^11]

### 6.3 可信度与限制

ONS 是英国国家统计机构，BICS 使用 IDBR 抽样框、公开权重、标准误和置信区间，数据透明度较高。但调查自愿、属于“official statistics in development”。Wave 159 收到 38,637 份响应，response rate 为 26.7%；较细切片可能不稳定。调查排除农业、油气、能源供应、公共行政与国防、公共教育和医疗，以及金融保险。部分分析还排除 0–9 人企业，因为其高权重会显著影响汇总值。[^11]

### 6.4 L1 Observer 适配结论

**适合作为英国的生产化深度快照，但暂不应承诺固定时间序列。** 最稳妥的接入方式是 metadata/watchlist：每次新 questionnaire 或 workbook 发布时检查 AI 题是否存在；同一措辞连续出现才形成 series，否则保存为独立 `question_regime`。

最值得保留的指标是 `extensive_use_share`、`pilot_only_share`、`majority_workforce_daily_use_share`、`operations_use_share` 和 `workflow/training/investment`。行业 headline 可作结构验证，但不能与美国或欧盟直接排名，除非同时披露企业规模、行业覆盖、AI 技术定义和参考期差异。

## 七、FRED / RPS GenAI Adoption Tracker

### 7.1 数据来源与物理含义

FRED 托管的是 Bick、Blandin、Deming 的 Real-Time Population Survey（RPS）GenAI Adoption Tracker 系列。RPS 是面向美国 18–64 岁人口的全国代表性在线劳动力调查，工作模块按季度更新。它直接提供 Anthropic 和 BTOS 都缺少的就业人口分母。[^12]

建议接入四条工作专用序列：

| 指标 | 定义 | 2026 Q2 |
|---|---|---:|
| Work Adoption | 就业者回答“工作中使用 GenAI”的比例 | 45.20% |
| Used Last Week for Work | 就业者中上周至少一个工作日使用的比例 | 39.23% |
| Daily Use for Work | 就业者中上周每个工作日均使用的比例 | 13.69% |
| Work Hours Assisted | 全体就业者总工时中由 GenAI 辅助的估计比例 | 6.27% |

另有 `Time Savings`：就业者自报若没有 GenAI，完成相同工作还需增加的工时，占总工时的比例；2026 Q2 为 **2.17%**。[^13]

`Work Hours Assisted` 根据使用天数和每日主动使用时长的区间回答推算上下界并取中点，非使用者按零处理。它比“是否用过”更接近使用强度，但仍不是自动化任务占比，也不是实际 token、系统日志或计费工时。

### 7.2 最适合 Observer 的派生关系

```text
weekly_persistence_ratio
  = used_last_week_for_work / work_adoption

daily_persistence_ratio
  = daily_use_for_work / work_adoption
```

按 2026 Q2 数值，工作采用者中约 **86.8%** 在上周实际使用，约 **30.3%** 达到上周每个工作日均使用。这里的比率是由同一季度总体比例相除得到的派生代理，不等同于同一受访者 cohort 的留存率，必须以 `persistence proxy` 标注。

### 7.3 可信度与限制

RPS 的优势是同一套问卷、季度更新、全国代表性权重，并在 FRED 提供稳定 series ID、CSV 下载和历史值。它还按行业和职业提供系列，可观察员工扩散的结构差异。

局限也很明确：

- 自报且技术范围是广义 GenAI，不限企业批准的软件，也不证明正式部署。
- 员工使用可能包含个人账户和 shadow AI。
- 细分行业/职业样本误差可能较大，需保存置信区间或至少标记小样本风险。
- `Time Savings` 是受访者反事实估计，不是经审计的生产率。
- FRED 标注该系列受版权约束，使用时必须按其说明引用。

用户清单中的 `RPSGENAIUSAGESHAREEDLWALL` 是**所有用途**的每日使用率，混合工作、教育和个人用途，不适合充当 L1 工作扩散 headline；应改用工作专用的 `RPSGENAIUSAGESHAREEDLWWOR`。

### 7.4 L1 Observer 适配结论

**适合成为员工扩散与持续使用的季度核心序列。** 它不能确认企业正式生产部署，但与 BTOS 的企业视角、Anthropic 的任务遥测一起使用，能识别三种重要状态：

- 企业率与员工率同步上升：扩散同时发生在组织与员工两端。
- 员工率上升、企业率滞后：更像 bottom-up / shadow use。
- 企业率上升、每日使用或辅助工时不升：更像采购、试点或局部采用，尚未形成使用深度。

## 八、为什么各源数值不能直接融合

美联储对多个采用调查的比较给出了最直接的解释：BTOS 是企业计数加权，RPS 是员工抽样，因此天然更多代表大型雇主中的员工；企业规模与 AI 采用高度相关，统计单位、权重、问题措辞、使用重要性和受访者知情程度都会造成显著水平差异。[^14]

以下运算应禁止：

```text
错误：0.4 × BTOS enterprise rate
    + 0.3 × RPS worker rate
    + 0.3 × Anthropic production traffic share

错误：RPS worker rate - BTOS firm rate = “shadow AI rate”

错误：Eurostat EU rate 与 BTOS US rate 直接排名，
      却不说明企业规模、行业范围和 AI 定义。
```

可以做的是“证据矩阵”，每个轴保留自己的分母：

| 证据轴 | 主源 | 观察信号 | 反证信号 |
|---|---|---|---|
| 企业广度 | BTOS | 新口径下平滑采用率持续上升，更多行业/规模组参与 | 采用率停滞或回落，增长仅集中于大企业 |
| 员工持续性 | RPS | 上周/每日使用率及辅助工时同步提高 | adoption 上升但每日使用、辅助工时不升 |
| 组织嵌入 | BTOS Supplement / ONS | 多职能覆盖、工作流调整、培训和投入增加，pilot 向 extensive 迁移 | 广度增加但仍停留在 1–3 个职能、试点或轻度使用 |
| 任务生产化 | Anthropic 1P API | 达标任务/职业单元和达标流量份额扩大 | 流量增长但自动化、Directive 或工作用途下降 |
| 国际扩散 | Eurostat / ONS | 采用从数字密集行业向传统行业扩散 | 差距扩大或仅技术类别定义扩张带来表面增长 |

## 九、建议纳入 L1 Observer 的具体方式

### 9.1 固定命题建议

> **AI 的企业采用广度、员工持续使用和组织/任务嵌入深度是否同步扩大，从局部试验走向可重复的生产工作流？**

这比原命题更可证伪，因为它不把“使用过”直接当成“进入生产”。Observer 每次输出四个状态：

1. `enterprise_breadth_status`
2. `worker_persistence_status`
3. `organizational_embedding_status`
4. `task_production_status`

只有四个维度中至少三个方向一致，且没有重大 methodology break，才能给出整体 `broadening_and_deepening`。若企业广度上升但其余不升，应输出 `breadth_without_depth`；若只有 Anthropic 流量结构改善，应输出 `provider_telemetry_only`，不能外推全经济。

### 9.2 建议的核心指标

| 指标 ID | 来源 | 频率 | 作用 |
|---|---|---|---|
| `enterprise_ai_use_rate_pct` | BTOS Core 新口径 | 双周 | 美国企业采用广度 |
| `enterprise_ai_use_rate_4p_ma_pct` | BTOS 派生 | 双周 | 平滑高频噪声 |
| `enterprise_expected_use_rate_pct` | BTOS Core | 双周 | 未来六个月扩散意向 |
| `worker_genai_work_adoption_pct` | RPS/FRED | 季度 | 员工工作使用广度 |
| `worker_genai_last_week_pct` | RPS/FRED | 季度 | 最近实际使用 |
| `worker_genai_daily_pct` | RPS/FRED | 季度 | 高频重复使用 |
| `worker_genai_assisted_hours_pct` | RPS/FRED | 季度 | 使用强度 |
| `functional_breadth_distribution` | BTOS Supplement | 专题快照 | 企业内职能扩散 |
| `task_effect_augmentation/substitution/creation_pct` | BTOS Supplement | 专题快照 | 任务改变方式 |
| `extensive/pilot_use_share_pct` | ONS BICS AI | 专题快照 | 生产嵌入与试点区分 |
| `enterprise_ai_use_eu_pct` | Eurostat | 年度 | 国际结构基准 |
| 现有四条 Anthropic 生产化序列 | Anthropic 1P API | release event | 职业/任务生产化结构 |

### 9.3 更新与状态语义

- BTOS：每次官方 release discovery；采用新口径，默认展示原值与四期均值。
- RPS：季度检查 FRED series `last_updated`；保存原始 observation date 与 vintage。
- BTOS Supplement：监听下载页和问卷变化；无新专题为 `no_new_snapshot`。
- Eurostat：每年 11–12 月重点检查，平时月度 discovery 即可；保存 API vintage。
- ONS：监测每个 BICS questionnaire 是否出现 AI 模块；同措辞连续 wave 才形成序列。
- Anthropic：维持现有 release-event 更新和独立 methodology regime。

`no_change`、`no_new_snapshot`、`question_not_fielded`、`suppressed`、`insufficient_history` 和 `methodology_break` 必须是不同状态；均不得转成零。

## 十、接入优先级与工程判断

### 第一阶段：应立即规划

1. **BTOS Core**：最能弥补企业采用率分母，API 公开，更新频率最高。
2. **RPS/FRED 工作专用系列**：最能弥补员工持续使用和工时强度分母，接入成本最低。
3. **BTOS AI Supplement**：同一官方调查内连接企业、职能、任务和组织投入，是生产化深度最有解释力的校准源。

### 第二阶段：结构补充

4. **Eurostat**：年度、可信、机器可读，适合判断扩散是否只发生在美国或知识密集行业；需同时接 `isoc_eb_ai` 与 `isoc_eb_ain2`。
5. **ONS BICS AI**：方法价值高，但 AI 题不保证每个 wave 存在。先做 release/question watcher，待相同深度题复现后再升级为正式序列。

### 暂不采用的做法

- 不使用 FRED 的 all-purpose daily series 代表工作扩散。
- 不把不同国家的 headline adoption 做无条件排行榜。
- 不把企业采用率、员工使用率和 Claude 流量份额合成一个分数。
- 不把 BTOS Supplement 或 ONS 单次专题快照插值成月度趋势。
- 不用“采用率上升”直接断言生产率、岗位替代、收入或资本回报已实现。

## 十一、最终评价

这批候选源总体上比新增另一家模型公司的零散报告更适合 L1 Observer，因为它们提供了清晰的企业或人口分母，并允许负向变化和方法审计。最值得接入的组合不是“五选一”，而是：

```text
BTOS Core                  企业采用广度（高频）
RPS/FRED                   员工持续使用与工时强度（季度）
BTOS AI Supplement         组织嵌入与任务作用（专题快照）
Anthropic 1P API           职业/任务生产化结构（平台遥测）
Eurostat + ONS             国际与方法交叉验证（低频）
```

它们共同使原命题从“Claude 流量是否更像生产使用”，扩展为“企业是否采用、员工是否持续使用、组织是否加深嵌入、任务是否达到生产代理标准”四层可证伪判断。仍然不能直接回答的，是企业席位留存、系统实际 uptime、人工审核成本、生产率因果效应和终端 ROIC；这些边界应继续写入 L1 Observer 方法卡。

## Sources

[^1]: U.S. Census Bureau，[BTOS Data](https://www.census.gov/hfp/btos/data)、[Historical Downloads](https://www.census.gov/hfp/btos/data_downloads)、[API Documentation](https://www.census.gov/hfp/btos/api_docs)。API 端点另见 [periods](https://www.census.gov/hfp/btos/api/periods) 与 [questions](https://www.census.gov/hfp/btos/api/questions)。
[^2]: U.S. Census Bureau，[Large Firms With at Least 20 Employees Biggest AI Users](https://www.census.gov/library/stories/2026/05/ai-use-businesses.html)，2026-05-26；最新 22.4% 来自 [BTOS period 107 官方 API](https://www.census.gov/hfp/btos/api/periods/107/data) 的全国、`AI current=Yes` cell，本报告核验于 2026-09-09。
[^3]: U.S. Census Bureau，[Business Trends and Outlook Survey Methodology](https://www.census.gov/hfp/btos/downloads/methodology/Business_Trends_and_Outlook_Survey_Methodology_V6.pdf)，更新于 2026-08-13。
[^4]: U.S. Census Bureau，[BTOS AI Core Question Updates](https://www.census.gov/hfp/btos/downloads/AI%20Question%20Wording%20Updates.pdf)，2025-12-03。
[^5]: U.S. Census Bureau，[BTOS Core and AI Content Questionnaire](https://www.census.gov/hfp/btos/downloads/BTOS%20Core%20and%20AI%20Content.pdf)；[AI Supplement Public Workbook](https://www.census.gov/hfp/btos/downloads/AI_Supplement_Table_2026.xlsx)。
[^6]: Bonney et al.，[The Microstructure of AI Diffusion: Evidence from Firms, Business Functions, and Worker Tasks](https://www2.census.gov/library/working-papers/2026/adrm/ces/CES-WP-26-25.pdf)，U.S. Census Bureau CES Working Paper 26-25，2026-04。该文是研究论文，作者明确说明结论不代表 Census 官方立场，回归结果不作因果解释。
[^7]: Eurostat，[ICT usage in enterprises—Reference Metadata](https://ec.europa.eu/eurostat/cache/metadata/en/isoc_e_esms.htm)；[SDMX 3.0 API Getting Started](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started/sdmx3.0)。
[^8]: Eurostat，[Use of artificial intelligence in enterprises](https://ec.europa.eu/eurostat/statistics-explained/index.php?title=Use_of_artificial_intelligence_in_enterprises)；[20% of EU enterprises use AI technologies](https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20251211-2)，数据提取于 2025-12。
[^9]: Office for National Statistics，[Business Insights and Conditions Survey dataset](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/datasets/businessinsightsandimpactontheukeconomy)；[BICS Quality and Methodology Information](https://www.ons.gov.uk/economy/economicoutputandproductivity/output/methodologies/businessinsightsandconditionssurveybicsqmi)。
[^10]: Office for National Statistics，[Artificial intelligence in UK businesses: 2023 to 2026](https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/articles/artificialintelligenceinukbusinesses/2023to2026)，2026-07-20。
[^11]: 同上，Data sources and quality、Future developments；ONS 明确披露 Wave 159 response rate、行业排除、企业规模处理和 headline 深度限制。
[^12]: FRED，[Generative AI Adoption Rate for Work](https://fred.stlouisfed.org/series/RPSGENAIUSAGESHAREWORK)；[Real-Time Population Survey: Generative AI Adoption Tracker](https://www.genaiadoptiontracker.com/)。
[^13]: FRED，[Use Last Week for Work](https://fred.stlouisfed.org/series/RPSGENAIUSAGESHARELWWORK)、[Daily Use for Work](https://fred.stlouisfed.org/series/RPSGENAIUSAGESHAREEDLWWOR)、[Work Hours Assisted](https://fred.stlouisfed.org/series/RPSGENAIASSISTWRKHRSALL)、[Time Savings](https://fred.stlouisfed.org/series/RPSGENAITSALL)，均为 2026 Q2、2026-08-04 更新。
[^14]: Board of Governors of the Federal Reserve System，[Monitoring AI Adoption in the US Economy](https://www.federalreserve.gov/econres/notes/feds-notes/monitoring-ai-adoption-in-the-u-s-economy-20260403.html)，2026-04-03。
