# 证据型融资、估值与 ARR 专项验收（2026-08-25）

## 决策

证据型数值底座和隔离样本通过。`private_company_events` 标记为 `current_partial`：系统已能安全处理 OpenAI/Anthropic 少量融资、估值和 ARR 事实，但并不代表已建立大规模自动抽取。

本验收的“100% 正确”指 5 个发布观测与阶段一文档中的实体、指标语义、数值、美元单位、期间和精确字符 span 逐项一致；它不把媒体报道自动提升为公司一手披露，`source_tier` 仍是 `reliable_media`。

## 关键设计

- OpenAI 和 Anthropic 有独立、稳定的 economic entity id；证券映射为可选且当前为空，不用相关上市公司 ticker 冒充私营公司。
- 抽取结果总是先进 `needs_evidence`；即使模型 confidence=1.0 也不会自动发布。
- 候选必须关联 document/version/char span、来源等级和提取方法。默认查询只能看到至少一条仍为 `accepted` 证据的观测。
- 核验支持 `accepted` / `rejected` / `needs_evidence` / `superseded`，每次转换记录审核者、时间和原因，不删除旧候选或旧观测。
- 事件 id 按数据集、实体、事件类型、归一日期和标签稳定生成。同一轮融资的多篇转述变为同一 observation 的多条 evidence link。
- 原文只写“at a valuation”时记为 `event.valuation.reported`，不猜测 pre-money/post-money。
- 已激活事件不会被后来的不完整摘要候选降级；改变只能通过显式审核流转。

## 隔离验收结果

- 输入：阶段一隔离文档库中 3 个已入库文档版本（2 份完整媒体正文，1 份仅标题/外链的不完整摘要）。
- 候选：9 个；7 个通过核验，2 个被拒绝（正文不完整、指标/span 语义不匹配各1）。
- 发布观测：5 个，包括 OpenAI 和 Anthropic 的融资金额、reported valuation，以及 Anthropic ARR。
- 事件：3 个；Anthropic 同一轮融资的两篇转述只产生 1 个 funding event。
- 7 个 accepted 候选去重为 5 个观测；Anthropic funding 和 ARR 各保留 2 条独立媒体证据链。
- 所有 5 个发布观测的逐项 span 检查均通过，样本正确率 100%；审计日志 9 条。

## 在哪里检查

- 机器报告：`var/structured_evidence_validation_20260825_v2/validation.json`
- 隔离结构化库：`var/structured_evidence_validation_20260825_v2/evidence-smoke.sqlite`
- evidence pointer artifacts：`var/structured_evidence_validation_20260825_v2/artifacts/`
- 阶段一文档库（只读）：`var/phase1_inspection_20260824_hardened/stage1.sqlite`
- Bloomberg/Yahoo 完整正文：`var/phase1_inspection_20260824_hardened/documents/NEWS/NEWS-https-finance.yahoo.com-technology-ai-articles-anthropic-expects-match-spacex-re-512607cdb536-news_item.md`
- 第二篇转述：`var/phase1_inspection_20260824_hardened/documents/NEWS/NEWS-https-finance.yahoo.com-m-3b2d99db-61ba-3a77-ac5c-f75a051a0c8f-anthropic-ipo-may-6c2d5ad0d794-news_item.md`
- 不完整摘要：`var/phase1_inspection_20260824_hardened/documents/NEWS/NEWS-https-finnhub.io-api-news-id-9c1c904945c3a9493e34e5947e8958b71d57cb7cbfaac5b7cf8-12577bfb6815-news_item.md`

所有发布均发生在隔离验收库，没有写入生产结构化库或改写阶段一文档。
