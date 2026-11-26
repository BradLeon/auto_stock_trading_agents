# Design

## Context

动机见 proposal.md。与实现相关的现状：

- **主理人图**（`src/ats/graph/chief.py::build_chief_graph`）的节点依次为：`assemble_context` → `chief_decide` → `risk_gate` → `persist_decision`（必要时回到 `chief_revise`，次数有上限）→ `boss_review` → `trader`。
- **快照**：`agents/chief/assemble.py::build_chief_snapshot` 负责组装。可选类别 `fundamental_material` 经 `decision_profiles.yaml` 的 `optional_categories` 冻结进快照，是本次新增类别的先例。
- **风控输入边界**：`openspec/specs/agent/risk-officer-inputs` 规定，风控只读提案、组合快照、市场快照与规则包，并由架构守卫机器校验。
- **风控规则写法**：`risk/checks.py::review_revision` 逐单检查，新规则沿用 `RiskViolation(severity="hard")` 加 `_reject`；规则写在 `risk.yaml`，会改变 `ruleset_version`。
- **beta 与相关性**：beta 来自 yfinance 或 Finnhub 的现成字段，没有时按 1.0 计；相关性需要至少 20 个重叠收益日，不足时该标的是孤立簇。
- **层上限**：`risk.yaml` 中 L1_app 的静态上限为 10%，按配置比例换算为标配 6%、低配 3%。
- **交易计划**：`TradeDecision`（`schemas/decision.py`）已有 `setup`、`stop_price`、`target_price`、`planned_horizon_days`、`invalidation`，在审批前落库。

## Goals / Non-Goals

**Goals:**
- 退出信号不会被主理人静默忽略。
- 解禁窗口内不可能加仓，不依赖模型自觉。
- ANT 历史不足期间，风控读数偏保守而不是偏乐观。

**Non-Goals:**
- 不自动生成交易提案；主理人仍是唯一提案方。
- 不改变六类必需分析的齐备判定。
- 不为其他标的配置档位或代理值（机制通用，配置只给 ANT）。

## Decisions

### D1 表态关口放在主理人与风控之间，不放进风控
计划原稿把"退出信号无表态 → 驳回"放在 `risk/checks.py`，写规格时发现这违反风控输入边界：监测信号属于研究投影。因此改为在 `chief_decide`（以及 `chief_revise`）与 `risk_gate` 之间加一个确定性节点：
1. 读取快照中已冻结的监测投影，列出可行动的退出规则。
2. 核对修订中每个对应标的是否有引用该规则的提案或理由。
3. 不满足时退回主理人修订，原因结构化，计入既有的修订上限。

这样风控的输入边界不变，检查仍是确定性的，退回也复用既有的修订循环。
- 不选"只写进 skill 让主理人自觉"：用户要的是强制表态，提示词无法保证。

### D2 表态的数据形状
- 决策 schema 新增可选字段 `signal_responses`，每项为（标的、规则编号、响应类型：提案或不执行、关联提案标识或理由文字）。
- 提案本身的 `invalidation` 在 ANT 上要求引用 E 规则编号，由 skill 约束，表态关口一并检查。
- 字段可选，旧决策无需重写即可通过校验。

### D3 档位写在监测配置里
`config/thesis/ANT.yaml` 新增：

```yaml
tiers: {observe: <用户定>, standard: <用户定>, target: <用户定>}
```

- 加载时校验每档不超过所属层的静态上限（L1 为 10%）。
- 档位只约束主理人因监测信号提出的增减，风控仍按"静态上限 × 层配置比例"的有效上限审查，两者取更严的一方。

### D4 解禁窗口写进规则包
- `risk.yaml` 新增 `event_windows.ANT.lockups: [{date, days_before, days_after}]`，数据来自 `ant-data-onboarding` 16.3 用 424B4 核对的结果。
- `review_revision` 在逐单检查中加入 `lockup_window` 规则：窗口内增加经济敞口的订单判为硬违规。
- 经济敞口按 `instruments` 归一，所以通过期权或杠杆 ETF 增加 ANT 敞口也会被拦下。
- 不选只靠监测的"加仓禁止窗口"标注：标注是给主理人看的，风控规则才是硬约束。两者数据同源，不会矛盾。

### D5 代理 beta 与代理簇
- `risk.yaml` 的 `instruments.ANT` 新增 `proxy: {beta: <用户定>, cluster: <簇名或代表标的>, until_bars: 60}`。
- `risk/inputs.beta` 在拿不到 beta 且 K 线数少于 `until_bars` 时用代理值；`risk/correlation` 在重叠收益日不足时把 ANT 并入声明的簇。
- 审查结果与日报都标注"代理值"。历史够了自动切换，并在日报中注明。
- 代理 beta 的取值建议在实现时给出依据（例如 AI 簇成员 beta 的中位数，或期权隐含波动率与大盘之比），由用户确认。

### D6 快照的可选类别
仿照 `fundamental_material`，在 `optional_categories` 加 `thesis_monitor`，有效期 8 天。放在 `profiles` 之外，避免已冻结周期的计划哈希漂移。主理人上下文只呈现规则结果、复核点、禁止窗口和读数摘要，模型判读的解释作为附注。

### D7 skill 调整
`skills/chief/SKILL.md` 增加三处：
- 复核点处理监测结论的步骤。
- 持仓复核必须引用监测。
- ANT 交易的 `invalidation` 引用 E 规则编号。

## Risks / Trade-offs

- [表态关口让主理人多一轮修订，增加模型调用] → 只有可行动退出信号出现时才会触发，频率很低；计入既有的修订上限，不会无限循环。
- [修订上限用尽仍缺表态] → 周期按既有规则结束，不产生提案，并在报告中写明缺表态，由用户处理。
- [代理 beta 取值主观] → 由用户确认，在报告中标注；最长约 60 个交易日后自动失效。
- [解禁日期有误会错拦或漏拦] → 日期只取自用 424B4 核对过的结果；规则包变更会改变规则集版本，可以追溯。
- [规则集版本变化影响历史审查的复现] → 旧审查按其记录的规则集版本复现，这是既有机制。

## Migration Plan

1. 先上线快照可选类别与 skill 调整，观察一个周期：主理人能看到监测，但表态关口还不启用。
2. 再启用表态关口：用合成的退出信号回放验证后上线。
3. 解禁规则与代理值随规则包一起上线，必须早于 12/16 第一个解禁窗口。
4. 回滚：从 `optional_categories` 移除类别、关闭关口节点、撤回规则包条目，三者互相独立。

## Open Questions

- 档位的具体权重和代理 beta 的取值，由用户在实现时确认；不影响规格与任务划分。
