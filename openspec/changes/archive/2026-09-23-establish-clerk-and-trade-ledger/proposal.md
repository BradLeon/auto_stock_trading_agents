## Why

Phase B 之后，真实下单已强制绑定不可变 decision revision 与完整执行授权，但**交易结果这一侧仍是分散且不可审计的**：`reconcile`、`marks`、`episodes`、`predictions`、`performance` 各自带 `run()`，由 `runtime/scheduler.py` 的独立 cron 与 `runtime/cli.py` 子命令 ad hoc 触发，互不共享一次事务，也没有任何编排者负责串联、对账、补偿与发布。

由此产生四类已确认的账本失真：部分成交被 `trader/reconcile.py:138` 直接改写为 `filled`（无部分成交语义）；无法匹配的成交被静默归为 `manual`（`reconcile.py:112`），没有「无法归因」这一类；IBKR `reqExecutions` 只返回当日执行，漏跑的会话既无补回也无缺口记录（`config.py:174` 已把该约束写在注释里，代码未做任何登记）；下一轮 Chief 与 Risk 仍直读 `trades` / `performance` 原始表（`agents/chief/assemble.py:213`、`risk/assess.py:575`），无法看到对账缺口。

目标文档 §11 与 §14.3 要求 Clerk 作为**确定性 Workflow Service** 承担串联、对账、补偿和发布读模型，并把订单/成交与 cycle/revision/approval 做强关联。Phase C 即为此建层。

## What Changes

- **新建 Clerk 确定性编排**：按幂等窗口串联现有 reconcile、marks、episodes、predictions、performance 的确定性计算，复用既有公式，不重写领域事实、不事后猜测事实。
- **订单/成交强关联**：新系统订单与成交的**写路径**强制 `cycle_id` / `revision_no` / `decision_hash` / `approval_id` 齐全，缺失即拒绝写入并登记审计异常；列本身除主键外一律可空（不引入 DB 非空约束），以容纳历史行、人工单与无法归因单——强制点放在写路径而非库约束，避免逼出伪造链接。
- **归属三类化**：成交归属改为 `system` / `manual` / `unattributed` 三类并保留判定依据与置信度；无法归因**一律**归为 `unattributed` 并写入显式异常项，不得删除、不得归入 manual、不得强行归类为系统单、不得伪造决策链链接。
- **补偿语义补齐**：部分成交累计与收口、迟到成交回填、撤单/拒单/过期的显式终态与依据来源、进程重启的可重入重放、漏跑窗口登记为对账缺口（受券商能力限制无法补回的窗口必须显式可见，不得伪造重放；本阶段不提供替代历史 statement 导入通道）。重放以 order attempt 序列（意图 + 重试计数）为最小单位。
- **绩效与归因可重建**：派生读模型与不可变原始记录分离，重建带方法版本与重建时间，重建不改原始事实。
- **发布 Internal State API**：组合、交易历史、绩效、归因与审计异常的读模型，携带 as-of 与完整性标记；本阶段把 Chief/Risk 的**交易历史与绩效读取段**迁移到该读模型（assembler 整体重构仍属 Phase D），旧直读登记待退。
- **LLM 边界固化**：critic 复盘与 LLM 失效判定只能写入标注/评论类字段并带来源标记，禁止写入金额、交易身份或归因；新增架构守卫阻止越界写回。
- **待退登记**：Phase C 替换或收口的旧入口逐项登记墓碑（不执行任何物理清除）。

## Capabilities

### New Capabilities

- `execution/clerk-ledger`：Clerk 确定性编排职责、订单/成交与决策链的强关联、系统/人工/无法归因三类归属、对账与补偿的可幂等重放、持仓与资金对账、LLM 不得成为账本事实的边界。
- `execution/internal-state-api`：从不可变原始记录重建绩效与归因、带 as-of 与完整性标记的内部状态读模型发布、消费方迁移与旧直读退出。

### Modified Capabilities

（无。本阶段全部为新增能力；既有 `execution/authorization-gate`、`decision/*` 的需求不变，仅在实现层复用其记录。）

## Impact

- **新增存储**：审计异常表（断链/无法归因/漏跑缺口）、Clerk 运行留痕表（幂等窗口）、派生读模型表（绩效/归因快照与方法版本）。全部走 additive migration，并在 `src/ats/data/stores/ownership.py` 登记为 Workflow Memory 归属。
- **改动的现有代码**：`src/ats/trader/reconcile.py`（归属三类化 + 部分成交 + 缺口登记）、`src/ats/journal/{marks,episodes,predictions,invalidation}.py`、`src/ats/trader/{performance,analytics}.py`、`src/ats/memory/store.py`（写入约束与读模型）、`src/ats/agents/chief/assemble.py`、`src/ats/risk/assess.py`、`src/ats/channel/context.py`、`src/ats/runtime/{cli,scheduler}.py`。
- **新增代码**：`src/ats/execution/` 下的 Clerk 编排、账本关联、状态读模型与重建模块。
- **配置**：`config/workflow/legacy_retirement.yaml` 追加 Phase C 待退项；风控/对账配置新增对账窗口与阈值项。
- **测试**：新增 Clerk 编排、强关联、归属分类、补偿重放、重建确定性与 Internal State 的专项测试；既有 `test_reconcile.py`（15）、`test_marks.py`（20）、`test_episodes.py`（22）、`test_journal_*.py` 需按新语义复核。
- **不变更**：真实下单入口仍只有 Phase B 的一条；本阶段不触碰券商下单路径与审批链语义。
