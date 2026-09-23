# 验收记录（任务 9.2 / 9.3）

日期：2026-09-23 ｜ 分支：`feat/rebuild_workflow_dataflow` ｜ HEAD：组 8 提交后

## 保真性对照（9.2）

- 基线提交：`96d3f81`（本 change 实施前最后一个提交，独立 worktree `/tmp/ats-baseline`）。
- 对照方式：同一组「本阶段被修改」的测试文件，分别以基线代码与当前代码运行
  （同一 venv、同一机器；worktree 侧 `PYTHONPATH=<wt>/src` + 主仓库 `.venv`）。
- 文件集：`test_risk.py` `test_risk_validator.py` `test_chief_graph.py` `test_chief.py`
  `test_server.py` `test_order_tagging.py` `test_journal_idempotency.py`
  `test_journal_entries.py` `test_legacy_retirement.py` `test_trader.py`。
- 结果：
  - 基线：**115 passed / 0 failed**（36s）
  - 当前：**117 passed / 0 failed**（44s；净增 2 个用例 =
    `test_order_ref_distinguishes_revisions`、`test_duplicate_callback_after_restart_still_deduped`）
- 结论：**无未归因失败**。本阶段改造的测试（裁剪断言改边界断言、回调幂等改持久化、
  订单标识改派生式）全部以「预期变更」形式落地，且在基线与当前两侧各自全绿。
- 本阶段新增测试文件（基线不存在，不参与对照）：
  `test_decision_audit_store.py`、`test_decision_domain.py`、`test_decision_snapshot.py`、
  `test_decision_risk_review.py`、`test_chief_loop.py`、`test_approval_narrowing.py`、
  `test_execution_authorization.py`、`test_e2e_approval_chain.py`。

## 真实交易入口与 paper 默认（9.3）

- 全仓库扫描断言：`place_orders` 的唯一非 broker/facade 调用方是
  `src/ats/graph/chief.py` 的 trader 节点（`test_chief_loop.py::test_single_real_order_submission_path`）。
- 真实订单必须持有完整执行授权（`place_orders` 缺授权即拒绝，
  `test_execution_authorization.py::test_place_orders_without_authorization_is_rejected`）。
- 默认 `ChiefDecisionState.dry_run = True`，默认链路不触碰券商
  （`test_e2e_approval_chain.py::test_default_state_is_dry_run_and_never_places`）。
- 端到端清单（§15.2）10/10 通过：首轮通过、一次驳回后通过、多次驳回后通过、
  全部驳回、超过三轮、No Action、审批拒绝、快照过期后重新风控、
  券商提交结果不确定、paper/dry-run 默认。
