# Phase F 任务 1 组基线（保护与回归）

本文件记录 `implement-phase-f-shadow-run-and-cutover` 任务 1.1–1.8 的实施基线：新增的进程级保护、隔离运行环境、资格机制回归与切流守卫，以及**执行本组时发现的既有基线变化**。

命令一律用 `./scripts/run_tests.sh`（`uv sync --all-extras` + `uv run pytest`）。本机对测试命令单独提高环境删除阈值（`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000`），仅作用于该命令。

## 1. 本组交付与实测

| 任务 | 交付 | 测试 |
|---|---|---|
| 1.1 | `ats/execution/broker_write_guard.py`，check 接入 `IBKRBroker.place_orders` | 14 passed |
| 1.2 | `ats/workflow/isolation.py`，九个持久面重定向 + 禁令同装 | 12 passed |
| 1.3 | 脚本 22 项机制探针移植为 pytest | 32 passed |
| 1.4 | 脚本与 pytest 覆盖 parity（AST 双向核对 + 行为对照） | 6 passed |
| 1.5 | `ats/workflow/assurance_surface.py` 受指纹面清单 | 16 passed |
| 1.6–1.7 | `ats/workflow/cutover_guards.py` 切流不变量与豁免纪律 | 21 passed |
| 1.8 | 本文件 | — |

第 1 组合计 **101 项测试全绿**（`tests/test_broker_write_guard.py`、`test_isolation.py`、`test_dataflow_assurance.py`、`test_dataflow_assurance_parity.py`、`test_assurance_surface.py`、`test_cutover_guards.py`）。

守卫状态：`scan_cutover()` 返回 **0 违规**；`CUTOVER_EXCEPTIONS` 为 **0 条**（豁免表按构造为空，新增绕过必须声明 module + target + 理由 + 收敛阶段）；活动阶段 `Phase F`，词汇表延展至 `Phase G` 供后续声明。

## 2. 受指纹约束的文件面

由 `assurance_surface.load_surface()` 从 `config/data/target_dataflow_coverage.yaml` 派生，不手工抄写。**10 个路径**，其中 `config/data/target_dataflow_coverage.yaml` 的变更会作废**全部已登记证据**：

| 路径 | 作用域 | 作废的消费者 |
|---|---|---|
| `config/data/target_dataflow_coverage.yaml` | 全部证据 | **每一条已登记证据** |
| `src/ats/data/assurance.py` | required | 全部十角色 |
| `src/ats/data/consumer_api.py` | required | 全部十角色 |
| `config/data/structured.yaml` | required | 全部十角色 |
| `config/data/unstructured.yaml` | required | 全部十角色 |
| `config/risk.yaml` | risk | risk |
| `src/ats/execution/state_api.py` | chief / risk | chief、risk |
| `src/ats/execution/authorization.py` | trader | trader |
| `src/ats/decision/repository.py` | trader / clerk | trader、clerk |
| `src/ats/execution/clerk.py` | clerk | clerk |

**切流期的硬约束**：运行时切流只改发布覆盖层（`var/structured_data/releases.yaml`）与本 change 独立控制状态，不得修改上表任一路径。`assert_outside_fingerprint_surface()` 在动作边界拒绝此类改动，并列出将被作废的消费者。

**注意**：`src/ats/execution/authorization.py` 只绑定 `trader`（不是 `trader` + `clerk`）。任务 2.5 为授权加代次绑定会触及该文件，故必须落在取证（第 6 组）之前。

## 3. 既有基线变化（**非本 change 引入**）

执行 1.8 时全量结果为 **141 failed / 2188 passed / 0 errors**，而 2026-10-04 早先的权威基线是 **1 failed / 1954 passed**。经 worktree 对照确认这批失败**来自本 change 之前的并行提交**：

对照方法（同一 harness：worktree + 独立 basetemp + `PYTHONPATH` 指向对照树）：

| 树 | 结果 |
|---|---|
| `cf0643a`（含并行会话全部成果，不含本 change 任何代码） | 158 failed / 2063 passed / 7 skipped |
| `369a221`（本 change 第 1 组完成后） | 159 failed / 2163 passed / 7 skipped |

失败集合差：**本 change 净增 0 例**。测试数差 101 = 本组新增测试。

典型失败与其归属（`cf0643a` 同样失败，故与本 change 无关）：

```text
PermissionError: persistent ingestion for requires an active managed-queue lease
```

集中于 `test_factset_index_semantic_local`（18）、`test_chain_evidence`（15）、`test_chain_articles`（13）、`test_document_assets`（9）、`test_chain_report`（8）、`test_chain_kb_review`（8）等。最可能的引入点是 `aebeb2e feat(dataflow): 建立统一持久数据流与受管刷新链`——受管队列的租约要求改变了既有采集路径的调用前提，而 `complete-target-dataflow` 归档时未在全量测试中暴露这一点。

**这 158 例是 `complete-target-dataflow` 的验收缺口，须由该 change 的负责人处置**；本 change 的任务 13.6 会对账基线，但不得把 158 例记成 Phase F 的基线。

## 4. 本组发现并修正的自身缺陷

| 缺陷 | 发现方式 | 修正 |
|---|---|---|
| 隔离面漏了结构化库与持久队列 | `test_isolation_covers_every_known_persistence_env` 从源码派生持久面 env 清单，逐项比对后失败 | 补入 `ATS_STRUCTURED_DB_PATH`、`ATS_STRUCTURED_ARTIFACT_ROOT`、`ATS_PERSISTENT_QUEUE_PATH`（共九个面）；「看起来像路径但不是持久面」者改为 `NON_ISOLATED_ENV` 显式豁免并附理由 |
| `consumers_touched_by` 只遍历 `consumer_fingerprint_paths` 的键，漏掉六个无附加路径的消费者 | 测试断言共享文件应影响全部十角色，实际只报 4 个 | `FingerprintSurface.consumers` 单独承载全部消费者 |
| 切流守卫把 23 处合法的 `read_mode()` 调用判为违规 | 首次扫描返回 24 项 | 规则改为只禁「自己改路由」；匹配用「接收者+方法」而非裸方法名（`rollback` 在四个模块里是 SQLite 事务） |
| broker 绕过检测依赖变量名 | `ib.placeOrder()` 未被识别 | 改为「导入 SDK 或构造客户端」 |
| 指纹面断言可被路径拼写绕过 | 测试自身写错路径（`src/ats/data/structured.yaml`）却通过 | 路径先按仓库根解析；不存在的路径判为笔误而非放行 |
| `test_dependency_content_change` 依赖 basetemp 位置 | worktree 中 basetemp 在仓库外，`_dependencies` 抛 `fingerprint paths must be inside the repository` | 探针文件改放 `var/pytest-fingerprint-*`（与验收脚本同做法），测试不再随调用位置改变结果 |

## 5. 复现命令

```sh
# 本组六个测试文件
./scripts/run_tests.sh \
  tests/test_broker_write_guard.py tests/test_isolation.py \
  tests/test_dataflow_assurance.py tests/test_dataflow_assurance_parity.py \
  tests/test_assurance_surface.py tests/test_cutover_guards.py

# 守卫当前树零违规
uv run python -c "
from ats.workflow.cutover_guards import scan_cutover, CUTOVER_EXCEPTIONS
print('violations:', len(scan_cutover()), 'exceptions:', len(CUTOVER_EXCEPTIONS))"

# 受指纹面清单
uv run python -c "
from ats.workflow.assurance_surface import load_surface
s = load_surface()
for row in s.as_rows():
    print(f\"{row['path']:45} {row['scope']:12} {row['invalidates']}\")"

# 全量（本机需提高删除阈值；2026-10-04 实测 141F/2188P/0E）
CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 ./scripts/run_tests.sh tests
```
