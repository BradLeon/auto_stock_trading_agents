# Design

## Context

动机见 proposal.md，规则原文见 Claude 文稿《ANT 投资观测体系与行动指南》。与实现相关的现状：

- 研究任务以 `WorkflowTaskSpec` 声明式注册（`src/ats/workflow/run_contracts.py`），由 dispatcher 按 `config/workflow/research_schedules.yaml` 与 `event_routes.yaml` 调度。
- 角色投影统一使用 `agent/task_projection.py` 的外壳，并按角色 schema 校验。重大事件评审（`fundamental_material_review`）是最近新增角色的先例。
- 报告发布在 `workflow/report_publishers.py`，看板在 `runtime/dashboard/outputs.py`，飞书按状态变化推送的机制见提交 `0174d56`。
- 公司风险项由 `agents/fundamental/material.py::build_register` 生成，只在建立财报基线时刷新；`MaterialReviewView.hit_items` 是自由字符串，不做校验。
- 数据依赖全部来自 `ant-data-onboarding`。

## Goals / Non-Goals

**Goals:**
- 文稿里每一条数值规则都由代码计算，结果可重放、可追溯到输入观测。
- 数据稀疏时（首份 10-Q 之前，公司披露类指标大多只有 S-1 一期）诚实地显示"未覆盖"，不制造信号。
- 每周一次判读调用，没有重复请求。

**Non-Goals:**
- 不做通用的持仓监测框架；配置结构只满足 ANT。
- 不决定主理人如何响应信号，也不涉及仓位档位（见 `ant-decision-integration`）。
- 不回测规则阈值的好坏；阈值是用户预先登记的计划。

## Decisions

### D1 独立任务，而不是扩展基本面分析师
- 基本面的职责是 PEAD 预期差，范围是全部 target，而且按规格不读取行业层面的内容。
- 监测要同时读公司披露、行业份额、评测和发布登记，是对单一持仓论点的核对。
- 放在独立任务里，输入边界清楚（只读数据），也不改变基本面的投影语义。
- 两者通过风险项键（D9）共用一套问题编号。

### D2 配置结构
`config/thesis/ANT.yaml` 分四段，与文稿的结构一致：

```yaml
version: 1
source_doc: https://claude.ai/artifact/AMHBJmHvwmwXZXZJmXXp3r
questions:
  - key: "1a"
    label: 算力开支的可持续性
    indicators:
      - key: 1a.a
        label: 推理算力费用 ÷ 收入
        source: {product: company_disclosure_metrics, metric: serving_compute_to_revenue, entity: ANT}
        frequency: quarterly
        deteriorate: {kind: consecutive_rise, periods: 2}
rules:
  exit:   [{id: E1, all_of: [...]}, ...]
  reduce: [{id: R1, question: "1a", any_of: [1a.a, 1a.b]}, ...]
  add:    [{id: A1, ...}, ...]
priority: {gate: "2a", gated: "4"}
discipline:
  no_add_before_first_earnings: true
  lockup_dates: [2026-12-16, 2027-01-06, 2027-01-20, 2027-02-03]   # 以 424B4 核对后为准
  lockup_window_days: 7
  review_points: {after_earnings: true, weeks_after_release: 8}
```

`deteriorate` 只允许一组封闭的数值形状：`consecutive_rise`、`consecutive_fall`、`below_previous`、`not_recovered_within`、`window_shorter_than_previous`。没有数值形状的指标（3b、3c、4c）声明为 `judged: true`，交给模型判读。

### D3 数值条件由代码算，语义交给模型
项目里的约定是"配置声明范围，不声明语义"。这里的数值条件是用户预先登记的交易计划，对应架构不变式"计划写在结果之前"，所以由代码执行。这和过去 `supports_when` 的错误不同：那次是把"某段话对命题意味着什么"写成了标量；这次的条件只针对一个数字序列的形状，没有语义成分。语义部分（口径是否变化、是否一次性因素、定性指标）一律交给模型判读并留痕，模型不能改变代码的结论。

### D4 指标到数据的映射

| 指标 | 数据来源 | 恶化条件 |
|---|---|---|
| 1a.a 推理/训练算力费用 ÷ 收入 | 公司披露指标 | 推理占比连续两季上升；或训练占比上升而收入增速未加快 |
| 1a.b 未来 4 季承诺付款 ÷（收入×4） | 公司披露指标 + GAAP 收入 | 连续两季上升 |
| 1a.c 边际承诺强度 | 派生指标 | 连续两季高于历史中位数 |
| 1b.a GPU 租赁价格 | GPU 价格（可能未覆盖） | 连续 3 个月下跌 |
| 1b.b RPO 覆盖率 | 派生指标 | 连续两季下降 |
| 2a.a–b 评测领先 | 前沿评测 | 多数 toB 智能体评测失去第一 |
| 2a.c 领先窗口 | 发布登记 + 评测 + 成本（D5） | 比上一代短 |
| 2a.d 客户选择 | 披露指标（10 万美元客户数）、Ramp、OpenRouter | 10 万美元客户数环比增速放缓；份额持续下降 |
| 2b.a 每任务成本溢价 | 评测成本 | 溢价下降且份额下降 |
| 2b.b 推理毛利 | 公司披露指标 | 降价后连续两季下降 |
| 2b.c 旗舰 token 价格 | OpenRouter 价格 | 通缩加速 |
| 3a 新增年化收入、宣传与 GAAP 差距 | 披露 + GAAP + 收入序列 | 新增额环比下降；差距持续扩大 |
| 3b、3c 非 coding 披露、收入结构 | 已准入文档 | 模型判读 |
| 3d 非 coding 评测 | 前沿评测 | 落后 |
| 4a 竞品发布后份额回撤 | 发布登记 + OpenRouter/Ramp（D6） | 8 周内未恢复 |
| 4b 云渠道占比 | 公司披露指标 | 云渠道绝对额下降 |
| 4c 生态 | 已准入文档 | 模型判读 |
| 4d NRR、GRR | 公司披露指标 | 单季降幅扩大；或 GRR 下降 |

### D5 领先窗口的算法
1. 以发布登记中 Anthropic 每个通用旗舰的发布日为起点。
2. 在配置声明的 toB 智能体评测上，找竞品通用旗舰首次在同一可比组内得分不低于该 Anthropic 旗舰的日期。
3. 两者都有每任务成本时，还要求竞品成本不高于 Anthropic，即按性价比追平。
4. 窗口为天数，取各评测窗口的中位数。
5. 缺成本的评测按得分计算，并标注"未做成本校正"。没有任何可用评测时，2a.c 为未覆盖。
6. 竞品成绩日期以成绩首次被采集的时间为准，精度约一周，在报告中披露。

### D6 竞品发布后份额回撤的算法
- 基线：竞品发布前 4 个完整周的 Anthropic 份额平均值（OpenRouter 用估算花费份额，缺价格时退回 token 份额；Ramp 用月度份额）。
- 回撤：发布后每周份额低于基线。
- 未恢复：发布后 8 周内没有回到基线减 1 个百分点以内（容差写在配置里）。
- 只有完整周和完整月参与计算。

### D7 复核点与禁止窗口
- 复核点：
  - 财报事件进入"已发布"状态后的首次运行。
  - 发布登记中任一旗舰发布满 8 周后的首次周六运行。
  - 不需要新的触发器：周六例行运行时判断即可。另外在 `event_routes.yaml` 的财报发布路由中加入监测任务，让财报后的复核尽快发生。
- 禁止窗口：首份季报入库前，以及每个解禁日前后 `lockup_window_days` 天。解禁日以 424B4 核对后的日期为准。

### D8 判读模型与缓存
- 新增角色 `thesis_narrator`，模型在 `config/settings.yaml` 中配置。
- 输入只有本期读数、判定、命中规则，以及 3b、3c、4c 所需的已准入文档片段（每项最多几段）。
- 缓存键为（配置哈希 + 输入观测哈希），输入不变时不重新请求。
- skill 写在 `src/ats/skills/thesis-monitor/SKILL.md`，不改 `agents/base.py`，避免其他角色的缓存失效。

### D9 风险项种子与命中键校验
- `config/pead/ANT.yaml` 新增 `risk_register_seed`：六个问题各一项，键为 1a–4，证伪条件文字取自文稿。
- `build_register` 遇到种子时以种子为范围初始化，模型只判读是否触及。
- `MaterialReviewView` 校验 `hit_items` 必须是当前清单中的键，或带"清单外"标记。
- 校验对所有 target 生效。现有评审若有不合规的键，上线前统计并报告。

### D10 调度、投影与产出
- 注册任务 `thesis-monitor`，范围为 `config/thesis/` 下已配置的标的（目前只有 ANT）。
- 周六在行业复核之后运行，在 `research_schedules.yaml` 里排在其后。
- 投影角色为 `thesis_monitor`。
- 报告经 `report_publishers` 写到 Obsidian 输出目录，看板在 `outputs.py` 新增渲染。
- 推送：与上期投影比较指标状态、规则结果和复核点标记，有变化才推送。

## Risks / Trade-offs

- [首份 10-Q 前大部分公司披露类指标只有 S-1 一期，"连续两季"无法判定] → 显示"历史不足"，不产生信号。这是预期行为，报告首段写明。
- [竞品成绩日期精度约一周，领先窗口有误差] → 报告中披露精度；规则阈值以月为量级，影响有限。
- [按 2a 门控问题 4 份额信号，依赖 2a 判断准确] → 2a 的判定结果与依据都在报告里，可人工复核。
- [新增 hit_items 校验可能让现有其他标的的重大事件评审发布失败] → 上线前用历史评审回放，统计不合规数量；必要时先告警一段时间再拒绝，由用户决定。
- [推送噪声] → 只在状态变化时推送；非复核点的结论标为"观察中"。

## Migration Plan

1. 先上线配置、引擎与投影，在非复核模式下跑两次周六运行，只发布报告，不进入主理人（主理人接入在 `ant-decision-integration`）。
2. 风险项种子与 hit_items 校验单独上线，先做历史回放统计。
3. 回滚：停用调度与事件路由中的监测任务即可；投影与报告留存，不影响其他任务。
