# establish-clerk-and-trade-ledger 验收记录（verification）

日期：2026-09-23 ｜ 分支：`feat/rebuild_workflow_dataflow`

## 1. 专项测试（任务 9.1）

Phase C 新增的 7 个测试文件合并运行，**64 passed / 0 failed**：

| 文件 | 覆盖 |
|---|---|
| `tests/test_clerk_ledger_store.py` | 三表/幂等索引/归属/非主键可空/盘点脚本 |
| `tests/test_clerk_linkage.py` | 写路径强制、断链异常、三类归属、fill 链继承 |
| `tests/test_clerk_compensation.py` | 累计成交/迟到修正/终态依据/缺口登记/attempt 核对 |
| `tests/test_clerk_orchestration.py` | Clerk 编排链/窗口幂等/领域事实不变/指纹命中 |
| `tests/test_ledger_rebuild.py` | 绩效/归因重建确定性/方法版本/失败净空/CLI |
| `tests/test_internal_state.py` | Internal State 全段/完整性/degraded/双读比对 |
| `tests/test_llm_boundary.py` | 白名单+provenance/架构守卫/critic 不动账本 |

## 2. 端到端验收（任务 9.2，§15.4）

`tests/test_clerk_e2e.py`（paper 模式，全程 FakeBroker/桩，无真实下单）：

下单（链完整系统单）→ 部分成交（partial 留痕）→ 迟到成交回填（跨会话日，
`late_backfill` 标记，满量收口 filled，成交量加权均价）→ Clerk 编排
（`clerk_runs` 留痕、同窗口重放幂等复用）→ 绩效/归因读模型重建
（方法版本 + `source_facts_hash`）→ Internal State（as-of + completeness=complete）
→ 下一轮 Chief 经 `state_api.recent_fills` 读到成交。

## 3. 保真性对照（任务 9.3）

- 基线：`488f144`（Phase C 规划定稿、实施前）独立 worktree
  （`/private/tmp/ats-base-c`，已清理），与主仓同 venv、同 `--basetemp` 口径。
- 文件集：本阶段被**修改**的既有测试文件（新增文件基线不存在，不对照）：
  `test_reconcile / test_order_tagging / test_journal_idempotency /
  test_journal_capture / test_trader / test_legacy_retirement /
  test_chief_loop / test_e2e_approval_chain`（含 conftest 影响）。

| 侧 | 结果 |
|---|---|
| 基线 `488f144` | 90 passed，1 failed |
| 当前 HEAD | 91 passed，0 failed |

唯一基线失败：`test_single_real_order_submission_path` 断言中的双斜杠笔误
（期望 `src//ats/graph/chief.py`，grep 实际输出单斜杠）——**测试自身缺陷，
与本阶段代码无关**，且已在组 2 修复（commit `4481813`）。基线代码上该
守卫的语义（真实下单入口唯一）在基线同样成立。除此之外无未归因失败。

## 4. 交易安全（任务 9.4）

- `ChiefDecisionState.dry_run` 默认 `True`；`test_e2e_approval_chain.py::
  test_default_state_is_dry_run_and_never_places` 通过（默认态零真实下单）。
- `test_chief_loop.py::test_single_real_order_submission_path` 通过：
  `place_orders` 仅 `graph/chief.py`（图 trader 节点）一个调用漏斗，
  `trader/execute.py` 门面与 `broker/ibkr.py` 自身除外。
- 新增：`place_orders` 拒绝裸指令（缺完整执行授权整批拒单）；
  系统源真实提交缺决策链被 `save_trades` 拒写并登记 `broken_link`。

## 5. 回归

组 7 收口时 invalidation/journal/episodes/critic/clerk 回归 119 passed；
组 2/3/4/5/6 各组收口回归均全绿（详见各 commit）。

## 6. 结论

§15.4 验收清单全部满足，51 项任务全勾（`tasks.md`），无未归因测试失败。
