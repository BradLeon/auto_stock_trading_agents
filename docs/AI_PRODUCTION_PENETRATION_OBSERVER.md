# AI 应用层：生产化与应用扩散 Observer

## 它回答什么

固定命题 `ai_core_production_workflow_penetration` 的 `claim_definition_version=v2` 观察三个互不融合的轴：美国企业采用广度（BTOS Core）、美国员工持续工作使用（RPS/FRED）和 Anthropic GLOBAL 1P API 的任务生产化。它回答这些证据是否同步扩大、是否从局部试验走向可重复生产工作流。它不生成统一渗透率，也不进入 PEAD、Chief、组合、风险或执行。ONS BICS 的既有数据与独立 DataProduct 只保留作历史审计，不进入本命题、主动更新组、Agent context 或人类报告。

旧 v1 快照仍可重放，但 `ai_hardware/L1_app` 默认使用 v2。三轴 bundle 以至少三个来源原生可比期间判定 `expanding/stable/contracting/mixed`；期间不足或方法口径变化返回 `insufficient_history`。来源不可达时可展示库内最近成功值及 freshness，但不前向填充、不插值、不伪造共同期间。

`Usage Share` 是某职业或任务占 **1P API 全部公开产品流量** 的份额；不是该职业有多少人在使用 AI。官网所谓 Industry 是 SOC occupational major group，也不是 NAICS/GICS 行业。

## 生产化代理与四条核心序列

一个公开 cell 同时满足以下条件才称为 qualified：Usage Share `> 0`、Work Use Share `>= 80%`、Automation Share `>= 80%`、Directive Share `>= 50%`。代理版本为 `core_production_workflow_proxy_v1`；它不把四个边际概率相乘。

对 occupation 和 task 各自计算两类指标，因此得到四条核心序列：

| 指标 | 公式 | 含义 |
|---|---|---|
| 可见单元生产化率 | `100 × qualified cells / visible Usage cells` | 已公开的职业/任务 cell 中，符合代理标准的比例 |
| 生产化流量份额 | `Σ qualified cell Usage Share` | 所有合格 cell 占 Provider 总流量的份额 |

职业和任务是同一流量的不同分类视角，不能相加或平均。公开 Usage cell 不完整（例如隐私过滤）时，`published_usage_share_pct` 和 `conditional_production_traffic_share_pct` 仅作为覆盖诊断。

## 补充的职业任务组合已确认生产化覆盖与 Figure 4 式分布

对存在 O*NET taxonomy 分母的职业，还计算：

```text
confirmed_production_task_coverage_pct
  = 100 × 合格且已映射任务数 / 该职业 taxonomy 去重任务总数
```

面向读者，该指标叫作“职业任务组合的已确认生产化覆盖”。其兼容字段仍为 `occupation_production_task_coverage_lower_bound_pct`。未观察、未发布或隐私过滤的任务仍在分母，合格但未映射的新任务不会擅自分配到职业。因此它只陈述当前公开数据能够确认的覆盖；并不存在可以由当前数据观察到的“上限”。职业 CCDF（“覆盖率至少达到 x% 的职业占比”）复刻论文 Figure 4 的分布表达；其 taxonomy 依赖性使它不能进入四条核心序列或趋势状态。

## 四项核心指标的物理含义

| 指标 | 公式 | 应如何读 | 不应如何读 |
|---|---|---|---|
| 职业可见单元生产化率 | `100 × 达标公开详细职业 cell / 具有公开 Usage Share 的详细职业 cell` | 公开职业单元中达到代理标准的比例 | 该职业从业者采用率 |
| 任务可见单元生产化率 | `100 × 达标公开 O*NET task cell / 具有公开 Usage Share 的 O*NET task cell` | 公开任务单元中达到代理标准的比例 | 全部经济任务自动化率 |
| 职业生产化流量份额 | `Σ 达标详细职业 cell 的 Usage Share` | 合格职业分类 cell 承载的 1P API 总产品流量份额 | 职业的企业部署份额 |
| 任务生产化流量份额 | `Σ 达标 O*NET task cell 的 Usage Share` | 合格任务分类 cell 承载的 1P API 总产品流量份额 | 任务在所有公司的采用率 |

职业和任务是同一 1P API 产品流量的两种分类视角，四项指标不可跨粒度相加或平均。

## 如何使用

```bash
ats data ai-production --format json --snapshot
ats data ai-production --periods 2026-04 --periods 2026-05 --format markdown \
  --chart-dir /absolute/path/ai-production-charts --snapshot

# 正式 L1 Evidence workflow：必须显式指定受支持范围
# 只运行该 layer 已注册的 Observer；默认将 Markdown 和图表写到该 sector 的 output_dir
ats evidence layer --sector ai_hardware --layer L1_app \
  --periods 2026-04 --periods 2026-05

# 可指定审阅文件和图表的落盘位置；ai-production 是同一注册路径的 claim 快捷入口
ats evidence layer --sector ai_hardware --layer L1_app \
  --periods 2026-04 --periods 2026-05 \
  --output /absolute/path/L1_app_evidence.md \
  --chart-dir /absolute/path/ai-production-charts
ats evidence ai-production --sector ai_hardware --layer L1_app --format json
```

Python consumer 仅可经 DataProducts：

```python
from ats.data.products import get_platform_data_products
from ats.agents.evidence.work_adoption import observe_ai_production_penetration

packet = observe_ai_production_penetration(
    periods=["2026-04", "2026-05"], products=get_platform_data_products())
```

`ats evidence layer` 是通用的按层只读入口：它只执行显式 `sector/layer` 的
`evidence_observers` 中已启用声明，绝不会为了方便顺带运行同一 sector 的其他 L1–L8 层。
`config/sectors/ai_hardware.yaml` 的 `L1_app` 已将本命题声明为
`ai_core_production_workflow_penetration / ai_production_penetration`；这与该层的 `claims`
分开，后者仍只服务公司证人、归因和 Chain workflow。其他已配置但尚未接入 Observer 的 layer
返回 `no_registered_observers`，而不会被 L1 的结果替代。`ai-production` 只是此命题的兼容快捷入口，仍先经同一配置声明验证。默认 Markdown 运行会持久化审阅文档并打印路径；`--format json` 保持机器接口。

输出依次展示本 Observer 专属 `methodology_card`、四序列、职业任务组合已确认生产化覆盖的分布、分布尾部完整披露、按 Usage Share 排列的高流量典型使用单位 TOP10、指标公式、facts、warnings、snapshot manifest 和图表 descriptors。覆盖率至少 50% 的职业不是典型样本：它仅表示该职业 O*NET 任务清单中，公开数据能同时确认符合代理的任务数达到一半，并不表示一半员工、工时或完整工作流已经 AI 化。没有三个连续、同 methodology、同 threshold/derivation regime 的月份，`history_status` 必为 `insufficient_history`；可以报告单月比较，不能称为持续趋势。当前受治理样本只有 2026-04、2026-05 两个月，正属于这一情形。

## 方法卡、血缘与重放

方法卡列出 provider/product/GLOBAL 范围、期间、可见/qualified 样本、taxonomy mapped/unmapped、阈值、methodology/derivation version、质量状态、限制和 manifest ID。`lineage` 是数值回溯到 observation、artifact 和 taxonomy relation 的链路；`snapshot manifest` 是一次查询所冻结的输入清单；`derivation version` 是公式实现的版本。它们共同保证以后上游 vintage 出现时仍可按当时的 `as_of` 重放。

三轴 v2 的趋势是描述性中期方向，不要求逐期单调。系统按来源原生期间排序，联合使用起点到终点净变化、最小二乘线性斜率、相邻实质变化的方向一致率和 0.5 个百分点容差；一次不改变端点与斜率方向的小幅回撤不会机械地产生 `mixed`。算法明细留在结构化 packet 供审计，不在面向读者的报告中展开。`mixed` 只表示端点与线性方向冲突或实质上涨/下跌均占较大比例，不代表统计显著性。BTOS 同时披露 standard error，但当前规则仍不声称完成显著性检验。

BTOS 的 `period` 是调查波次编号。历史必须按整数波次或真实 reference window 排序，不能按字符串把 100–107 放在 88–99 之前；图表横轴显示参考期结束日，并以 `Wxx` 辅助标注波次。

v2 主结论 manifest 只固定各轴 headline/trend 所需的可比历史。Anthropic 数千个职业/任务叶子 cell 通过版本化派生的输入数量、IDs hash、artifact IDs 和可展开 lineage pointer 引用；明细 CSV/JSON、TOPN 与图表 sidecar 保留自己的 rows hash。这样 Agent context 不会平铺数万条 ID，审计者仍可按需展开复算。

PNG 旁的 JSON sidecar 保存有序图表数据、data hash、renderer/threshold/derivation version、period range 和 manifest ID。图表不可用只产生 `visualization_warning`，不会阻塞表格、事实或 manifest。

## 失败语义与回滚

- 缺少必要指标、非法单位/范围、重复冲突、非 1P API、混合 methodology：cell 失败关闭并给出原因。
- 未发布/隐私过滤：不是零；只影响可见分母和覆盖诊断。
- taxonomy 映射不足：核心四序列仍可用，职业覆盖/CCDF 标记为补充 warning。
- 回滚只需停止调用 `observe_ai_production_penetration` 或不传 `ai_production_packet` 给 Chain report；不删除 observations、relations、artifacts 或历史 manifests，也无需数据库迁移。

Chain report 只有调用者显式传入本 claim ID 的 packet 才会添加只读附录，且不会反馈进 Chain corroboration 或交易工作流。
