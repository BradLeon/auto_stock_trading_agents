# Tasks

## 1. 共用模块

- [x] 1.1 新增 `src/ats/agents/provenance.py`，提供以下函数（design 第 1 项）：
  - `info_time`
  - `observation_sources`
  - `age_text`
  - `describe_vintage_ref`
  - `cite_projection`

  验证：单测覆盖三档取值顺序、`observed_at` 不被使用、联不到文档、库里现有全部 `data_vintage_refs` 前缀、新鲜度标记。
  - 2026-11-19：已实现，`tests/test_report_provenance.py` 10 项通过。信息时间顺序在实施时修订为"发布 → 采集 → 文档日期"，数据期只作附注（见 proposal 第 5 项）。

## 2. 首席

- [x] 2.1 `ChiefResult.citations`。`graph/chief._write_chief_report` 用快照条目加投影表的 `data_vintage_refs` 准备引用索引。验证：单测构造快照，断言索引字段齐全。
- [x] 2.2 `chief/report.render` 改动：
  - 决策表、证据权衡表、持仓复查表的引用列改用 `cite_projection`；
  - 加未知引用标记；
  - 文末新增「依据清单」。

  验证：渲染单测；用 11-16、11-18 两轮真实快照渲染并人工核对。
  - 2026-11-19：已用真实快照渲染。11-16 那轮的 4 个截断 ID 在依据清单中显示为未知引用；11-18 那轮的引用全部解析成功。
- [x] 2.3 `assemble.projection_context_block`：逐条投影行补 `as_of` 与距本轮时长；标题行去掉截断 ID。验证：单测断言上下文中没有 18 位截断 ID，且每条投影行带 `as_of`。

## 3. 层分析与基本面

- [x] 3.1 `sector/report.render_layer` 新增 `source_stamps` 参数，判读表和逐家读数表加「来源 · 信息时间」列。`sector/review.py` 批量解析后传入。验证：渲染单测；用真实库的一层观测渲染并人工核对。
- [x] 3.2 `pead/report`：共识、行情、实际值、财务快照四段标信息时间；预期表注明建表时间。实现前核对各 `as_of` 的语义。验证：渲染单测。
  - 2026-11-19 实施结果：档案各段的 `as_of` 都是运行时间，不是数据时间。因此按真实语义处理：
    - 共识：新增 `ExpectationSet.consensus_as_of`（取共识快照的 `_data_as_of`），标真实采集时间与距本次运行时长；
    - 行情：标"取于本次运行"；
    - 预期表：标"建于本次运行"，并注明「依据/来源」为分析师自述；
    - 实际值：不加时间。它是本次运行从纪要抽取的，已有"纪要来源"一行；
    - 财务快照：原本就带 `as_of` 和逐期期末，不改。

## 4. 收尾

- [x] 4.1 跑直接相关测试（chief、sector report、pead report、provenance）。
  - 2026-11-19：首席、层报告、基本面报告、上下文相关的 17 个测试文件共 224 项通过；另 3 个文件 32 项通过。
  - 1 项失败 `test_sector_allocation_projection_published_with_input_refs`，在改动前的代码上同样失败，属既有红灯。
- [x] 4.2 用真实数据各渲染一份首席决策与层分析样例，放在 `var/` 下供用户审阅（不写入 Obsidian 目录）。
  - 样例位置：`var/report-provenance-samples/`。层分析的样例是"来源列"在真实观测上的解析结果，没有重跑层分析（重跑会产生模型请求）。
