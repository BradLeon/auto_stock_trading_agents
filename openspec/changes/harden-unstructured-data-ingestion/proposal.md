## Why

阶段一真实采集证明统一文档资产可以去重、版本化和检索，但采集准入仍会把错公司、旧财季、非业绩 8-K、网页外壳和不完整订阅内容标记为可信文档。若不先修复身份、期间、完整性和来源优先级，多个 Agent 共用底层数据只会把同一份错误更高效地扩散。

## What Changes

- 把文档业务类型与文件载体分开，明确 research article、news item、company release、investor presentation、earnings transcript、regulatory filing 的语义边界，并兼容既有类型值。
- 修复 Markdown frontmatter 读取在正文包含 `---` 时截断的问题，保证目录字符数、不可变版本和统一读取 API 一致。
- 引入“候选 → 校验 → 接受/隔离”准入流程；公司身份、目标期间、文档类型或完整性无法确认时不得写成 `ok=1`。
- 将 defeatbeta/Yahoo Finance 结构化纪要作为电话会纪要主源，保留原始段落/speaker 结构；公司 IR/人工文件为权威覆盖，Tavily 降级为候选 URL 发现器。
- 修复 IBKR Historical News 日期格式和 `None`/超时处理，使已开启 TWS 时可稳定获取 Dow Jones/Briefing 标题与正文。
- 将 Newsletter 采集改为首次回填 + UID/Message-ID 增量游标，采集数量不受下游 LLM 处理上限影响，并标识全文、预览和 teaser。
- 收紧 SEC/IR release 与 deck 发现：不得把任意最新 8-K 或最大 HTML 自动当作业绩公告，deck 必须通过公司域名、公司身份和期间验证。
- 修复正式披露回归：重新校验并迁移旧 `unknown-release` 资产，按 earnings event 下载 earnings release，并将 10-Q/10-K 作为独立 regulatory filing 保存；SEC 连接失败必须形成可观测来源状态，而不是静默返回空目录。
- 补齐正式披露选择规则：使用 SEC submissions 的 form/items/primary-document 元数据，不再按 EX-99 文件大小猜测角色；支持财期多信号绑定，以及外国私人发行人的 6-K 正文、6-K 中期报告和 20-F/40-F 年报。
- 增加覆盖率、正确率、时效性、完整率和隔离原因的来源健康报告，并以真实源集成测试逐阶段验收。

## Capabilities

### New Capabilities

- `data/unstructured-ingestion`: 定义非结构化文档分类、来源优先级、候选准入、结构化纪要、Newsletter 增量采集、版本读取一致性和数据质量可观测性。

### Modified Capabilities

无。

## Impact

- 主要影响 `src/ats/data/` 的文档、纪要、Newsletter、新闻、缓存与共享资产模块，以及 `src/ats/data/articles/ibkr_news.py`。
- 本次回归修复仅追加影响 SEC/官方披露适配器、文档资产兼容迁移与专项验收，不重新采集或测试 transcript、新闻、Newsletter、IBKR 等其他来源。
- `stock_statement` 等结构化财务报表不在本次变更实现，留待后续结构化数据层统一设计；本轮只保存和验证官方非结构化披露资产。
- 文档目录与 SQLite catalog 继续兼容现有 Agent 读取接口；旧类型值保留读取兼容，新写入采用明确语义。
- 新增 defeatbeta 远程 Parquet/DuckDB 适配器及配置；网络不可用时按来源降级，不允许自动放宽身份或期间验证。
- 调度器会分离“采集全部新资产”和“限制本轮下游处理量”，但不会改变交易、评分或人工审批路径。
