# Information 首轮真实模型内容审阅（中文）

日期：2026-10-12。用户已批准原八份固定输入向指定 OpenRouter 模型外发。本报告记录真实结果与审阅判断，**整体验收未通过，尚不能核签四标的内容达标**。以下概览是对模型结果和原文定位的人工式审阅整理，不是新的模型调用或已发布生产简报。

## 执行结果

累计尝试 8 次请求，未重试任何已尝试正文、未增加其他模型或文档。第一请求 NotFoundError 后停止：隔离脚本的 `parallel_tool_calls=False` 与低价端点的严格参数支持筛选冲突，移除多余参数后仅执行剩余七份。依据为 [供应商端点目录](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash-0731/endpoints)；首次失败保留，不被修复后结果覆盖。请求函数与返回校验仍要求单一 schema 函数。

含调查／人工恢复时间累计 393.976 秒；6 份结果 schema 可解析，1 份路由失败、1 份模型结果 ValidationError。后者没有保留当时的完整 raw tool arguments／usage，无法进一步确定具体无效字段或其 token 量；已补后续诊断保存，不能倒推本轮细节。

六份可解析结果的已报告使用量合计为 prompt 18888、completion 3113、total 22001 token。这是已知小计，不是全部八次精确 token／账单；未返回 usage 的失败请求可能产生费用，因此不能声称实际账单为零或等于预约估计。无生产投影发布、无通知、无订单、无部署。

| 目标／材料 | 原模型结果 | 证据与内容检查 | 核签判断 |
|---|---|---|---|
| MRVL／Marvell 高管出售股份 | 首次路由失败 | 没有模型内容 | 未完成 |
| MRVL／Samsung Exynos 退出及 System LSI 重组 | 3 条 peer 推断 | 一条引用加入省略号，无法连续匹配原文；没有 MRVL 直接结论 | 拒绝本份输出，不能当作无消息 |
| MSFT／微软及其他企业绿卡政策新闻 | 4 条输出 | 事实摘要为空，虽引用能定位仍不构成完整结论 | 拒绝本份输出 |
| MSFT／中国 AI 安全制度 partial 材料 | 7 条 peer／目标推断 | 多条引用是改写／拼接，不能连续定位；将制度描述外推为芯片需求，缺少足够约束 | 拒绝本份输出 |
| NVDA／市场综述与 TSMC／ASML 业绩前瞻 | 8 条输出 | 事实摘要为空；目标标题相关不证明信息增量有效 | 拒绝本份输出 |
| NVDA／Samsung System LSI 重组 | 2 条推断 | 引用与非空摘要通过定位；竞争推断仍需判断 | 有限证据可审阅，未核签业务通过 |
| TSM／AI 需求、三季度营收观点 | 9 条目标／peer 输出 | 引用与非空摘要通过定位；包含过度产业链外推 | 主体事实有信息价值，不能整体核签九条推断 |
| TSM／中国 AI 安全制度 partial 材料 | ValidationError | 无可使用的 schema 结果，具体无效字段未留存 | 未完成 |

## MRVL

直接公司新闻没有取得模型结果，所以 MRVL 实际内容验收仍缺直接材料。

行业探索文章的模型结论集中于三星手机芯片／2nm 对 TSM 的潜在竞争、数据中心设计对 NVDA 的可能竞争、PMIC／数据中心设计对 AVGO 的可能影响，没有 MRVL 直接增量。输出中的 `Samsung plans to expand businesses including ... chip design for data centers` 把省略号插入引用，违反连续逐字引用要求；整份结果被拒绝，不能把坏引用忽略后当作成功或“没有重要消息”。

## MSFT

微软签证／绿卡政策新闻的四条结果只有实体、方向、引用、材料性等字段，摘要全部为空。这是 schema 原先允许默认空摘要所暴露的缺陷，不是新闻没有内容。已经修复为摘要必填且非空，正式 target 发布前也会拒绝空白摘要。

中国 AI 安全制度预览的模型试图把“强调发展、监管门槛宽松、开源竞争”等来源描述外推为 NVDA／AVGO 需求及微软云／AI 竞争影响。多数引用不是原文连续片段，必须拒绝。即使下一轮引用修好，也不能仅凭制度方向确认实际芯片采购、客户支出、价格或利润变化。partial 只能讨论已获得的可见内容，不能补写隐藏章节。

## NVDA

市场／业绩前瞻综述有目标相关标题，但模型八条摘要全部为空，未构成有效 Information 内容。这也说明只提高标题关联和数量仍不足以达到需求。

Samsung 行业探索的两条输出可定位：模型把 System LSI 聚焦手机处理器／影像传感器及三星代工 2nm 描述为 TSM 潜在竞争；把数据中心芯片设计扩张描述为 NVDA 可能新增竞争。对 NVDA 的材料性 0.2、置信度 0.3，属于低信号背景判断，不是已确认 GPU 订单、替代关系或份额变化。本次没有核签它是 NVDA 的有效事实增量。

## TSM

AI 需求与营收报道的主体摘要：**原材料中的分析师称 TSMC 三季度营收比市场共识高 3%，认为 AI 需求与高端手机需求有支撑，并预测本年美元营收增长 41%，其 2027～2028 年年增长 30% 预测可能有上行风险。**这些是本文来源陈述与 Bernstein 的预测／观点，不是本系统已独立核验的财务 actuals，更不能把未来增长写成已实现业绩。主体 TSM 结论材料性 0.9、置信度 0.85，是模型评分，尚未人工核签。

九条输出还包括：AI 加速器需求对 NVDA／AMD 的影响、手机需求对 AAPL 的影响、先进工艺与美国扩产对 ASML／AMAT 的设备需求影响，以及 HBM／内存对 SKHY／MU、定制芯片对 AVGO 的推断。它们共享少量来源事实，不是九份独立证据。

尤其是把 TSMC 与 SK Hynix 写成竞争性“memory/peer”关系，产业环节混淆，不能接受；文章也没有直接给出内存订单。其他对具体客户、设备商订单或定制芯片生产的外推，都须区分“可能的静态产业链联系”与“本文证实的变化”。引用准确不自动证明推断成立，因此不能整体核签全部九条。

## 已修复与剩余依赖

已修复：隔离路由冗余参数；空摘要发布门禁；Information schema 的必填非空中文摘要、最长 400 字符连续引用；提示中区分来源事实、归属明确的分析师预测和 tentative 产业链推断；未来 schema 失败保存 raw tool arguments、usage 和无输入内容的错误字段路径。新契约／预算及引用回归 70 passed／1 个既有 SyntaxWarning，22.38 秒；冻结 schema 检查最终 7 passed，2.90 秒。旧批准输入仍按其原 schema／hash 保留，当前执行 preflight 不允许直接重用旧契约。

新的八份复验输入仍使用同一批正文，只有 prompt／schema 改变，已在本机准备且没有外发：`tmp/information_selection_implementation_20261012/round5/capture_preparation_v2/review.md`。需要追加调用额度后才能复验；首批 8 次已经用完，不能自动扩大请求数。新批预算最多 8 请求、零重试、费用规划预留 $0.50，保守预约估计 $0.042517。

后续顺序：批准追加调用额度 → 同材料新契约复验 → 分标的引用／事实与推断审阅 → 剩余实际选择及 Routine 受影响链路取证 → 适用 Phase F 重新冻结与资格。当前首轮有限实验没有证明全部候选、全部 workflow、生产资格或上线条件达标。

## 原始证据

- `tmp/information_selection_implementation_20261012/round5/approved_capture/capture.json`：首次失败。
- `tmp/information_selection_implementation_20261012/round5/approved_capture_remaining/capture.json`：保留首次失败并累计剩余尝试的最终结果。
- `tmp/information_selection_implementation_20261012/round5/live_content_review/content_review.md`／JSON：逐材料模型输出、定位及拒绝原因。此报告不替代本中文审阅中的业务判断。
- 初始批准 manifest：`387d98a12d06844ca34381581f27748e90e2ad41c8c58f49f99157472f7d356c`；新准备 manifest：`f92a0e5988eeb6d9346cd9b3ea8f7a081f402b907aaf2be56953f333607009eb`。


## 后续三步实验（2026-10-12）

上文为最初八份取证的历史结果，不是当前全部材料的最终计数。用户已批准新增64次有限调用：原8份新契约复测、其余24份已选材料检查、额外32份预算外候选抽样。完整输入保持冻结，无自动重试。最新逐标的标题、中文摘要及检查结果见 [三步实验报告](INFORMATION_SOURCE_SELECTION_THREE_STEP_RESULTS.md)，授权和依赖见 [执行计划](INFORMATION_SOURCE_SELECTION_THREE_STEP_EXPERIMENT.md)。
