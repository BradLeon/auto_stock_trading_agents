# 层次分析师（Layer Analyst）

> 契约以 [`ARCHITECTURE.md`](../ARCHITECTURE.md) §7.2 为准；需求条文见
> `openspec/specs/agent/`。运维命令见 [`OPERATIONS.md`](../OPERATIONS.md)。

## 它回答什么

**产业链的这一层现在处于什么状态，层内谁在被证据验证。** 目前只覆盖 AI 硬件产业链
（`config/sectors/ai_hardware.yaml` 的八层）。它**不给配置结论**（超配/低配/清仓、给多少钱）——
那是行业分析师的事，行业分析师读的就是它的产出。

## 输入、输出与触发

| 项 | 内容 |
|---|---|
| 读什么 | 产业链数据包：分层、实体关系、行业横截面、命题证据；已准入的文档 |
| 也读什么（开关开启后） | 信息简报里的事件成分：影响面为本层/跨层且点名本层的成分，以及本层成员"重大"及以上的本公司成分（安全网）；上一轮本层的论点外冲击 |
| 不读什么 | 宏观（Chief 已经读宏观的板块倾斜，这里再读会把同一个判断算两次，层级结论变差时也分不清是产业还是宏观在变）；其他分析师的观点 |
| 产出 | 每层一份 `layer_analysis` 投影（`LayerAnalysisPayload`） |
| 谁读它 | 行业分析师（唯一的跨分析师依赖之一）、Chief |
| 什么时候跑 | 每周六行业评审之前，由 Dispatcher 作为 `sector-review` 的依赖自动补跑（`layer-review`） |
| 手动运行 | `ats workflow run --task layer-review --scope-kind layer --scope-id <层键> --inputs-json '{"layer-review":{"sector":"ai_hardware"}}'`；或用 `--scope-kind sector --scope-id ai_hardware` 跑全部八层 |
| 代码 | `src/ats/agents/layer/layer_review.py` |
| 配置 | `config/sectors/ai_hardware.yaml`：每层的成员、`claims`（要验证的命题）、`structure_notes`；`config/workflow/event_impact.yaml`：`layer` 开关与风险命题的独立来源规则 |

## 怎么判断

- **共同命题（common claims）→ 这一层处于什么状态**（`status`、`cycle_position`、`reversal_triggers`）。
- **相对命题（relative claims）→ 层内谁在被验证**（逐个标的的理由与 `cross_section` 排名）。
- **第三方数据也走同一条链**：不是公司自述的数据（海关统计、DRAM 合约价、L1 的企业采用/收入/能力读数）经 `config/data/sources.yaml` 声明为证据来源，转成带说话人、维度、方向与可复核证据行的观测，进入同一套去重、立场与判读。L1 的三个证据观察器（生产化渗透、前沿商业化、原始能力）由 `data/sources/governed_observer.py` 接入，采集任务是 `chain_l1_governed_evidence_daily`（每天 09:00 UTC）；观察器历史不足的轴不产出观测（缺口不是持平）。
- 每条证据由模型逐簇判读支持或反驳，并留下可审计的理由；配置只声明**看哪些公司、哪些维度、该由谁来说**，
  不写"哪个方向算支持"。
- 截面排名目前按证据判读的强弱与证据簇数排（`cross_section_basis` 写明依据）；财务与估值维度还没有接入。
- 证据判读结果按（模型配置、提示词实现、提示词全文）缓存，输入没变时不重复请求模型；
  `ATS_NO_JUDGEMENT_CACHE=1` 可强制重算。

## 事件成分与风险命题（event-impact-routing，开关默认关）

`config/workflow/event_impact.yaml` 的 `layer.enabled` 打开后生效；关闭时提示词、输出结构与此前完全一致，
`kind: risk` 命题在加载配置时就被滤掉。

- **风险命题（`kind: risk`）**：只写整层或跨层的共同风险，公司特有风险留给基本面的公司风险项。目前只有 L5 的
  `l5_export_market_access_risk`（出口管制与市场准入）。证据两半：链证据台账（与经营命题同一套去重与逐簇判读，
  缓存共用），以及引用的事件成分。**计数换成独立来源**：同一出版方或同一原发的转载算一个（"WSJ（经IBD转述）"
  算 WSJ），"承压""兑现"须至少 2 个独立来源、其中至少 1 个一手或权威（官方文本、公司声明或公告、主流通讯社原发）。
  别名表与权威名单在 `event_impact.yaml` 的 `risk_claims` 段，代码在 `chain/risk_sources.py`。经营命题的
  "独立公司说话人"门槛不变。风险命题**不参与**本层状态判断：风险在承压不等于需求在收缩。
- **每个层面成分必须有归宿**：归入经营命题、归入风险命题，或——都归不进且重要性不低于中等——列为**论点外冲击**
  （附是否建议新增命题）。冲击在最后一个成分之后 14 天过期，过期后只作背景、不得作为下调依据。
- **安全网成分**（成员公司的本公司事件）：只有存在整层传导时才写**补认领**并写明传导路径；否则不处理，也不据此下调。
- 每个被认领的成分都附重要性复核（agree / higher / lower + 理由），进入每周的事件评级校准报告。
- **使用率建议只能向下**，理由必须引用承压/兑现的风险命题，或重大及以上、负面、持续的冲击。

模型负责判读，代码负责核对（`agents/layer/event_inputs.py`）：未达来源门槛的"承压/兑现"降为"出现"；没有可读
成分引用的冲击与补认领被丢弃；过期冲击降为背景；没有合格依据或方向不是向下的建议被清除。被改动的地方写进 `gaps`。
另外两条（2026-11-16 回放后补）：事件成分一节放在证据块**之前**（放在长上下文末尾时模型整节没读）；`risk_claims` 为必填字段；
"中等"及以上的本层/跨层成分若既未归入命题、也未列为冲击，代码在 `gaps` 里逐条写明"未归宿"（不追加请求）。

`LayerAnalysisPayload` 主要字段：`layer`、`status`、`summary`、`findings`、`confidence`、`cycle_position`、
`reversal_triggers`、`entities`、`cross_section`、`cross_section_basis`、`cross_section_coverage`、
`common_evidence`、`divergent_evidence`、`gaps`；开关开启后另有 `risk_claims`、`off_thesis_shocks`、
`cross_scope_claims`、`utilization_advice`（均可省略，存量投影不变）。

## 失败语义

某一层分析失败时**什么都不发布**：不发投影，也不把上一轮的结论当作本轮判断沿用。行业分析师把缺失的
投影当作缺口，保守降级。
