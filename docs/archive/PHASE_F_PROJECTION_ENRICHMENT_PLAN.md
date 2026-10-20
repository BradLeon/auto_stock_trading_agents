# 投影补齐方案：让 Chief 读到 TARGET §7.3 要求的完整内容（2026-10-17，待审阅）

> 本文只是方案，没有改投影字段，也没有重跑任何分析。你已经决定「投影补齐需要做」，这里要你确认的是**怎么补**。

## 一句话

Chief 读到的是各分析师发布的「投影」。现在四个投影都比 TARGET §7.3 要求的薄，而**缺的内容分析师本来就算出来了，只是发布时丢掉了**。方案是给四个投影加**可选的新字段**，把已有的结果带出去，不改老数据、不改 Chief 的读取代码。Macro 的「情景」是例外：它现在没有来源，需要让模型多产出一小段。

## 补完之后，Chief 多看到什么

| 投影 | 现在 Chief 能看到 | 补完后多看到（均来自已有结果） |
|---|---|---|
| 层级分析 | 一个层级状态 + 一句总评 + 要点列表 + 信心 | **标的横截面排名**（每个标的在本层的位置：强/中/弱、证据簇数、依据是自述还是有交叉印证）、**共同证据与差异证据**（指向具体议题和证据编号） |
| 行业配置 | 一个行业立场 + 目标权重 + 一句理由 | **三级配置**：行业 → 8 个层（配置、预算、信心）→ 层内标的（观点、权重）；每一级带事实引用（层投影编号、证据编号）；轮动合成是否可用 |
| 宏观评审 | 四档 regime + 摘要 + 指标列表 | **象限与状态**、**利率路径**、**传导**（各主题的判断）、**主要风险**、**证伪条件**、**行业倾向**、**时间范围**、**情景** |
| 技术面评审 | 信号 + 摘要 + 价位 | **趋势状态**（评分、站上哪几条均线）、**失效条件**（具体到价位或指标）、**建议敞口上限**及其变化、市场级恐慌标记 |

下面是行业配置补完后的样子（节选，数字为示意）：

```json
{
  "sector": "ai_hardware", "stance": "overweight", "target_weight": 0.62,
  "rationale": "…",
  "layers": [
    {"layer": "L7_foundry_pkg", "status": "expanding", "allocation": "超配", "budget": 0.12, "confidence": 0.75,
     "evidence_refs": ["claim:cowos_capacity", "projection:layer_analysis:L7…"]},
    {"layer": "L3_dc_power", "status": "steady", "allocation": "标配", "budget": 0.05, "confidence": 0.20,
     "gaps": ["本层无命题"]}
  ],
  "names": [{"symbol": "TSM", "layer": "L7_foundry_pkg", "view": "增持", "weight": 0.10}],
  "rotation": {"available": true}
}
```

## 需要你拍板的 5 件事

**1. 加字段的方式：只增不改（推荐）。**
新字段全部可选，空的时候不进内容哈希。这和「信息简报」当初补六要素的做法一样：已发布的老投影继续有效，不需要升版本、不需要重算、不需要改 Chief 的读取。代价是老投影里这些字段是空的，直到下一次重新分析。另一个做法是升版本，所有旧投影作废，要全部重跑，我不推荐。

**2. 宏观「regime」和正文不一致。**
投影里的 regime 是代码从象限算出来的四档（现在是 `transition`），评审正文由模型写（现在是「risk-off｜…」）。两者各有用处，我建议**都保留并标清楚**：`regime` 仍由代码决定，另加 `narrative_regime` 放模型的原话，Chief 看到两者的标签不同。另一个做法是让投影的 regime 跟模型走，但那会让一个可复核的代码结果被叙述覆盖，我不推荐。

**3. 宏观「情景」从哪来。**
传导、风险、证伪条件、时间范围都有现成来源；**情景没有**。我建议让模型在周评审里多产出 2–3 个情景（名称、触发观察、对行业倾向的影响），属于同一次调用，不额外增加请求；推理预算已受上限约束。另一个做法是只用「证伪条件」当唯一的反面情景，但这不满足 §7.3 的「情景」。

**4. 层级分析的「横截面排名」用什么依据。**
Dispatcher 调 Layer 时没有传「截面篮子」（需要财务、共识、价格），而且目前只有 9 只标的有受治理财务，用它排名会大面积缺失。建议**排名依据用证据判读的结果**（每个标的在本层命题上的强弱和证据簇数），这是 Layer 本来就算出来的，不依赖财务。财务/估值维度的排名等数据补齐后再加。投影里会写明排名的依据和覆盖率。

**5. 技术面「失效条件」「建议敞口」不用模型。**
这两项由代码确定性地给出：失效条件来自已有的规则（跌破 200 日线、期限结构倒挂、评分分项的变化），建议敞口就是已有的敞口上限。不新增模型调用。

## 对 Chief 的影响

Chief 读取时本来就把整份内容原样放进上下文，所以新字段会**自动**进入 Chief 的提示词，不用改读取代码。要注意的是长度：行业配置的三级展开最长，我会限制列表条数（层内标的每层最多 5 个、证据引用每项最多 8 条），并在 Chief 的一行摘要里加上最关键的新字段（例如宏观的象限、技术面的建议敞口）。

## 实施步骤与停点

1. 改四个投影的内容定义（只增可选字段）和发布处，测试：老投影仍通过校验、哈希不变、新字段被带出、列表条数受限。
2. 宏观：模型输出加 `scenarios`；发布处带出象限、利率路径、传导、风险、证伪、情景、`narrative_regime`。
3. 技术面：由确定性读数生成失效条件和建议敞口。
4. 不调模型做验证：用已存的评审结果重新生成投影，核对内容。**停点：把样例投影给你看。**
5. 经你确认后各跑一次真实分析验证（Layer、Sector、Macro；技术面不调模型）。这会产生一次模型费用：Layer 约 34 次请求，Sector 约 65 次，Macro 1 次，按关闭推理后的实际用量报告。
6. 记录结果，更新 10.5 的状态。

## 附录：技术细节

- 投影内容定义在 `src/ats/agent/task_projection.py`（`LayerAnalysisPayload`、`SectorAllocationPayload`、`MacroReviewPayload`、`TechnicalReviewPayload`），基类禁止多余字段，所以新字段必须先加进定义。`InformationBriefPayload` 已有「空值不进哈希」的先例（`payload_json` 里 pop 空字段）。
- 发布处：`agents/layer/layer_review.py::_publish_projection`、`agents/sector/review.py::_publish_allocation_projection`、`agents/macro/review.py`（约 281 行）、`agents/technical/review.py`（约 208 行）。
- 数据来源：Layer 的 `claim_attributions` 和证据判读的 `entity_readings`；Sector 的 `layer_verdicts`（含 `allocation`、`name_calls`、预算）；Macro 的 `MacroReview`（`quadrant`、`rate_path`、`themes`、`top_risks`、`falsifier`、`sector_tilts`）；Technical 的 `TechnicalReading`（`score_detail`、`target_exposure`、`panic_fired`、`bear_fired`、`sma*`）。
- Chief 渲染在 `agents/chief/assemble.py::projection_context_block`，整份内容以 JSON 原样放入。
- 改投影发布处和内容定义会让相关文件的指纹变化，沿用现有处理，不重新登记冻结。
