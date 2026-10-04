# Tasks 5：Dataflow 门禁与交付验收

日期：2026-10-04。Change：`complete-target-dataflow`。这是 Dataflow change 的最终集成汇总，不是生产切流批准。

## 5.1 可复现检查

执行器：`scripts/verify_dataflow_tasks5.py`。机器结果：[`TARGET_DATAFLOW_TASK5_RECONCILIATION.json`](TARGET_DATAFLOW_TASK5_RECONCILIATION.json)。

```sh
UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync \
  python scripts/verify_dataflow_tasks5.py \
  --output docs/validation/TARGET_DATAFLOW_TASK5_RECONCILIATION.json
```

该执行器不联网、不写入生产资格账本或来源数据。它校验配置合同、catalog、既有 Tasks 3/4 零网络回放记录，随后对十个注册角色逐一执行只读 exact-scope qualification 查询。查询使用登记产品范围，不造证据，不把隔离账本结果并入生产账本。

沿用既有专项采集和解析证据，不重新请求外部来源。可复核范围如下：

| 验证面 | 结果与详细证据 |
|---|---|
| 结构化来源 | 6 个已缓存数据集；每个覆盖正常成功、重复 `no_change`、传输失败、权限失败、陈旧状态、质量拒绝、修订和历史 as-of/packet 对账。见 [Tasks 3 replay](TARGET_DATAFLOW_TASK3_REPLAY.json)。 |
| 文档和固定来源 | 7 个正文来源、DefeatBeta SEC/电话会固定链、SEC 官方原文解析和多个发布门各自保留 source-specific 的重复、修订、短文/质量拒绝、权限/网络失败、partial、预算和读回证据。见 [Tasks 3 验收矩阵](TARGET_DATAFLOW_TASK3_ACCEPTANCE.md) 与 [逐源库存](UNSTRUCTURED_REFRESH_INVENTORY.md)。 |
| 日历和版本 | BEA/BLS/FOMC 缓存原件重解析、改期、重复、失败保留 last-good、身份拒绝和 as-of 检查；见 Tasks 3 replay。 |
| 刷新队列 | 手动/日历/cache-miss 入队、幂等、无 lease 拒绝执行、worker 正常处理和 runtime 零入队等回归：`tests/test_managed_cache_miss_local.py tests/test_calendar_refresh.py tests/test_factset_semantic_schedule_local.py`，26 passed。 |
| 资格门禁 | 24 个隔离账本机制检查覆盖缺证据、过期、清单/依赖漂移、撤销、精确范围和无真实回退证据拒绝；见 [Tasks 4 replay](TARGET_DATAFLOW_TASK4_REPLAY.json)。 |
| 角色接口和回退 | 十角色缓存/运行时读回记录、五个剩余角色的 9 条新输入边正向/故障/原生回退/恢复和 10 项负向恢复检查；见 [Tasks 4 报告](TARGET_DATAFLOW_TASK4_ACCEPTANCE.md) 及其 JSON 回放。 |
| 数据/观点隔离 | 16 条消费者边与 10 个架构负例已在 Tasks 3 replay 留证；新边界/身份/路由架构回归 55 passed。 |
| 当前门禁 | 新执行器查询权威注册表、实际 qualification ledger 与十角色合同；catalog 518 项检查有效，十角色当前结果均为 `ineligible`。没有资格证据或人工写入的批准。 |

有限回归差异单独保留：Internal State、授权和 Clerk 扩展回归为 55 passed、1 failed。失败测试 `test_broker_unavailable_registers_missed_window_gaps` 把运行时减 10 天生成的 fill 与固定截止 2026-09-23 搭配；本轮运行时生成日期是 2026-09-24，位于截止日期之后，所以窗口不应纳入。未修改旧 Clerk 路径或测试，也未将该批次记为全绿。更窄的内部状态/授权/消费者组为 27 passed。其它被忽略的旧 `tests/` 文件不是唯一验收交付物；可移植的执行器与脱敏 JSON 均保留在仓库。

## 5.2 当前逐角色门禁

下表来自只读资格查询，范围为各角色在覆盖清单登记的产品集合。实际 Data DB 中每角色的资格证据历史均为 0；当前资格账本/精确范围证据缺失，因此这些状态是门禁的真实拒绝结果，不是声明分析数据不可读。稳定路由栏复述覆盖合同登记的旧入口，当前 change 未改读取开关。生产资格不得据本报告推定。

| 角色 | 产品 | 门禁状态 | 必需但缺失的资格证据 | 合同登记的稳定旧入口 | 需处理的实质缺口 |
|---|---|---|---|---|---|
| Layer | HIER_DATA、DOC_DATA | ineligible | write、admission、read、lineage、completeness、reconciliation、rollback | direct_knowledge_reads | 逐产品签署完整写入、准入、读回和回退证据；保留 no_coverage。 |
| Information | DOC_DATA | ineligible | 同上 7 项 | news_digest | 新闻/文章产品需要自身 exact-scope 资格证据；不得拿 Layer 或历史 digest 证据代替。 |
| Sector | HIER_DATA | ineligible | 同上 7 项 | sector_context | Layer 输入依赖与行业配置数据需按此 consumer 独立对账、回退。 |
| Fundamental | COMPANY_DATA、HIER_DATA | ineligible | 同上 7 项 | pead_direct_reads | 财务/电话会必须逐项有证；SEC 正文允许按 4.6 作为 optional gap，其例外不补足其他门槛。 |
| Macro | MACRO_DATA | ineligible | 同上 7 项 | macro_direct_reads | 月度与 FactSet 产品需要 exact-scope 写入、版本、读回和回退证据。 |
| Technical | MARKET_DATA | ineligible | runtime_query、timestamp、failure_semantics、no_persistence、fallback | market_data | 日线接口验证通过；期权仍因上游/来源时间不全为 partial，未签发该 consumer 的资格。 |
| Chief | PORTFOLIO_DATA、HISTORY_DATA | ineligible | internal_read、as_of、completeness、opinion_isolation、fallback | direct_ledger_reads | 实际内部状态有 9 条 broken_link，账户/历史为 partial；不得用隔离干净账本覆盖。 |
| Risk | PORTFOLIO_DATA、MARKET_DATA、RISK_RULES | ineligible | 同上 5 项 | direct_ledger_reads | 实际账户沿用 9 条断链；行情/规则单项可读不替代组合产品组资格。 |
| Trader | APPROVED_EXECUTION_AUTHORIZATION | ineligible | authorization、revision_hash、broker_submit_boundary、idempotency、fallback | direct_broker_submission | 没有生产 decision cycle 是当前状态；隔离审批链测试不是生产授权，也不为验收制造批准。 |
| Clerk | BROKER_STATE、DECISION_APPROVAL_CONTEXT | ineligible | broker_read、reconciliation、partial_fill、idempotency、fallback | direct_broker_reads | TWS 只读账户/订单/成交有效空结果通过；生产账本没有 cycle，实际部分成交演练未发生。 |

**切流处理**：十个角色均保持 ineligible；不向它们签发新的 Dataflow qualification。本 change 没有改 source/consumer 开关、launchd job 或 Trader 路由。旧路径问题仅留在差距清单；经用户确认的 SEC 正文 optional/non-blocking 例外不向财务、电话会、SEC index 或审批链扩张。

## 5.3 架构与运维文档

- [数据架构](../DATA_ARCHITECTURE.md) 已补充唯一 catalog 装配入口、两个权威 registry、managed queue/worker、launchd 与 FactSet 月报调度的边界、Layer A/B/C 清单、Evidence Observer 退役、十角色契约与逐 consumer Phase F 门禁。
- [结构化数据运维指南](../STRUCTURED_DATA_OPERATIONS.md) 已校正 registry 定义，并增加目标队列和当前 launchd 刷新操作，标出旧 `data ingest` 说明的 legacy 边界。
- [Data Sources](../DATA_SOURCES.md) 开头注明它是早期 PEAD 接入快照，不能替代权威 registry 或当前逐源状态。
- 每个 source/dataset 的真实运行、周期、owner 和差异继续以 [逐源库存](UNSTRUCTURED_REFRESH_INVENTORY.md) 为准；静态 cadence 不充当采集成功证据。FactSet 为月报 owner。

## 5.4 最终守卫

- `openspec validate complete-target-dataflow --strict`：通过。
- `scripts/verify_dataflow_tasks5.py`：通过；覆盖合同有效、Catalog 有效、既有 Tasks 3/4 replay 通过、十角色逐一查询、全角色 fail-closed、生产写入为零。
- `tests/test_architecture_guards.py tests/test_data_layer_architecture.py tests/test_target_consumer_identity.py tests/test_unstructured_consumer_routing.py tests/test_structured_runtime_boundary.py tests/test_collection_boundary.py`：55 passed。
- managed queue/calendar/FactSet schedule 三组：26 passed；Internal State/authorization/consumer targeted group：27 passed。
- 扩展 Clerk 回归的 1 个日期窗口失败及原因在 5.1 记录；未掩盖或改写旧路径。
- `git diff --check` 通过。Tasks 3/4 中的实时 TWS、行情和历史采集证据均复用存档记录，没有为了本次汇总重复联网抓取。

最终边界：Tasks 5 汇总了 Dataflow 的路径和当前门禁状态；Workflow/Agent 时序、真实跨进程恢复及生产读取切换仍由独立 Workflow/Data-Agent 集成验收处理。当前十角色均未取得生产资格。
