# Proposal

## Why

ORCL 与三星（005930.KS）的财报文档在准入时被隔离为 `period_unresolved`：目标财期 `target_period` 为空。ORCL 的财年在 5 月结束，三星的财年口径与日历季度不同，现有财期解析给不出“第几财年第几季”。

更糟的是，`evidence-observe` 在模型已读完整篇文档之后，才在保存观测时报 `neutral_fact_requires_published_document_version`，每次都白白花掉模型请求。所以 11-03 起两家被从 `chain_evidence_daily` 名单中拿掉，它们的财报证据自此中断。

（来源：switch-trade-path-and-retire-r5 tasks 11.5）

## What Changes

- **按公司财年口径解析目标财期：** 在公司元数据中登记财年结束月，财期解析据此把披露日和财报期末换算成本公司的财年与季度。适用于 ORCL、三星，以及今后任何非日历财年的公司。
- **先判准入、后调模型：** 抽取前先检查文档版本是否已通过准入（已发布）；未通过的文档直接跳过并记录原因，不调用模型。
- **恢复覆盖：** 两家恢复进入 `chain_evidence_daily` 名单。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `data/unstructured-ingestion`：新增“非日历财年公司的目标财期按其财年口径解析”和“未通过准入的文档不得进入模型抽取”两条要求。

## Impact

- **代码：**
  - `src/ats/data/admission.py` 与财期解析模块（`fiscal`）；
  - 目标财期的生成处（财报事件 → `target_period`）；
  - `evidence-observe` 抽取入口。
- **配置：**
  - 公司财年结束月，放在现有公司元数据或 PEAD 配置中；
  - `config/data/schedules.yaml` 的 `chain_evidence_daily` 排除名单去掉 ORCL 与 005930.KS。
- **成本：** 恢复两家抽取，每家每季约一次财报请求。
