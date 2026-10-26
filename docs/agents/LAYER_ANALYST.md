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
| 不读什么 | 宏观（Chief 已经读宏观的板块倾斜，这里再读会把同一个判断算两次，层级结论变差时也分不清是产业还是宏观在变）；其他分析师的观点 |
| 产出 | 每层一份 `layer_analysis` 投影（`LayerAnalysisPayload`） |
| 谁读它 | 行业分析师（唯一的跨分析师依赖之一）、Chief |
| 什么时候跑 | 每周六行业评审之前，由 Dispatcher 作为 `sector-review` 的依赖自动补跑（`layer-review`） |
| 手动运行 | `ats workflow run --task layer-review --scope-kind layer --scope-id <层键> --inputs-json '{"layer-review":{"sector":"ai_hardware"}}'`；或用 `--scope-kind sector --scope-id ai_hardware` 跑全部八层 |
| 代码 | `src/ats/agents/layer/layer_review.py` |
| 配置 | `config/sectors/ai_hardware.yaml`：每层的成员、`claims`（要验证的命题）、`structure_notes` |

## 怎么判断

- **共同命题（common claims）→ 这一层处于什么状态**（`status`、`cycle_position`、`reversal_triggers`）。
- **相对命题（relative claims）→ 层内谁在被验证**（逐个标的的理由与 `cross_section` 排名）。
- **第三方数据也走同一条链**：不是公司自述的数据（海关统计、DRAM 合约价、L1 的企业采用/收入/能力读数）经 `config/data/sources.yaml` 声明为证据来源，转成带说话人、维度、方向与可复核证据行的观测，进入同一套去重、立场与判读。L1 的三个证据观察器（生产化渗透、前沿商业化、原始能力）由 `data/sources/governed_observer.py` 接入，采集任务是 `chain_l1_governed_evidence_daily`（每天 09:00 UTC）；观察器历史不足的轴不产出观测（缺口不是持平）。
- 每条证据由模型逐簇判读支持或反驳，并留下可审计的理由；配置只声明**看哪些公司、哪些维度、该由谁来说**，
  不写"哪个方向算支持"。
- 截面排名目前按证据判读的强弱与证据簇数排（`cross_section_basis` 写明依据）；财务与估值维度还没有接入。
- 证据判读结果按（模型配置、提示词实现、提示词全文）缓存，输入没变时不重复请求模型；
  `ATS_NO_JUDGEMENT_CACHE=1` 可强制重算。

`LayerAnalysisPayload` 主要字段：`layer`、`status`、`summary`、`findings`、`confidence`、`cycle_position`、
`reversal_triggers`、`entities`、`cross_section`、`cross_section_basis`、`cross_section_coverage`、
`common_evidence`、`divergent_evidence`、`gaps`。

## 失败语义

某一层分析失败时**什么都不发布**：不发投影，也不把上一轮的结论当作本轮判断沿用。行业分析师把缺失的
投影当作缺口，保守降级。
