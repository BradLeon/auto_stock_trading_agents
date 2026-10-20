# 信息分析师（Information Analyst）

> 契约以 [`TARGET_WORKFLOW_DATAFLOW.md`](../TARGET_WORKFLOW_DATAFLOW.md) §7.2、§9.1 为准；需求条文见
> `openspec/specs/agent/`。运维命令见 [`OPERATIONS.md`](../OPERATIONS.md)。

## 它回答什么

**最近发生了什么事实变化，可能影响谁，有多可信，还有什么没核实。** 它读新闻、研报、公告、文章、
电话会材料，把它们变成结构化的信息简报。它**不给**买卖、仓位或组合建议——那是基本面分析师和 Chief 的事。

## 输入、输出与触发

| 项 | 内容 |
|---|---|
| 读什么 | 数据平台**已准入**的文档、chunk 与中性证据事实（信息文档包）；`config/pead.yaml` 的跟踪标的。执行中不得直接调用新闻、研报或公告 Provider，正文由数据层采集 |
| 不读什么 | 任何其他分析师的观点 |
| 产出 | 每个标的一份 `information_brief` 投影（`InformationBriefPayload`） |
| 谁读它 | 基本面分析师（唯一的跨分析师依赖之一）、Chief |
| 什么时候跑 | 工作日 08:00（纽约时间）定时；财报计划与发布的日历事件也会唤醒它（见 OPERATIONS §3.1） |
| 代码 | `src/ats/agents/information/`（`entry.py` 入口、`selection.py` 候选选择、`triage.py` 材料性评估、`extract.py` 抽取、`briefs.py` 发布契约） |

## 一份简报里有什么

`InformationBriefPayload` 的主要字段：

- `headline`、`summary`、`relevance`：这条信息讲什么、和该标的的关系。
- `fact_changes`：事实变化列表——基本面分析师逐条归类的就是这个字段。
- `source_evidence`、`sources`：每条结论回到哪份文档的哪个版本、哪个片段。
- `impact_candidates`、`entities`：可能受影响的对象。
- `confidence`、`freshness`、`unverified`：可信度、时效、**待核验项**。
- `event_time`、`published_at`、`extracted_at`：三个时钟。时效按**发布时间**判，不按入库时间。
- `cluster_key`、`source_count`、`independent_sources`：同一事件的多篇报道聚成一簇。

## 发布前必须通过的规则（`briefs.py`）

1. **最少要素齐全**：`fact_changes`、`unverified` 至少各有一条，`confidence` 必须给出。缺任何一项，
   这次运行判为**失败**，不降级成一段自由文本写进去。
2. **不得含交易建议**：买、卖、加仓、仓位一类的措辞在发布边界被拦截。
3. **同一事件的多篇报道不提高可信度**：簇的大小不会让 `confidence` 变高，十篇转载仍是一个来源。
4. **该标的没有已准入资讯**时，不发布空简报；该运行记为 `incomplete`，下游的基本面例行模式会因上游依赖
   缺失而不运行。这是数据缺口，处理方向是补资讯覆盖（数据刷新），不是放宽规则。

## 常见问题

- **简报缺失 / `incomplete`**：先看该标的近 7 天有没有已准入资讯（`python -m ats.data.refresh status`、
  `ibkr_news_daily` 等任务），多半是资讯覆盖的问题。
- **抽取角色返回空**：隐藏推理吞掉了工具调用，`run_structured` 会在第一次空返回后关闭推理重试。
