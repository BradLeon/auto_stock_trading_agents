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

典型报错与归属（`cf0643a` 同样失败，故与本 change 无关）：

```text
PermissionError: persistent ingestion for <source> requires an active managed-queue lease
ValueError: neutral_fact_requires_published_document_version
```

**根因**：`e80671a feat(factset): 完成月报语义解析与月末发布` 新增 `src/ats/data/persistent_queue.py`（**不是** `aebeb2e`——初版归因有误）并引入两道治理门，两者都是合法的单写者/来源完整性属性，但**未同步测试**：

1. **受管队列租约门**：`require_queue_worker(source_id)` 校验 `ATS_PERSISTENT_QUEUE_TASK_ID` + `ATS_PERSISTENT_QUEUE_LEASE_OWNER` + `valid_source_lease(task, worker, source)`（后者要求 `task.source_id == source_id` **或** `source_id in task.scope["sources"]`）。该门被接在 **15+ 条生产写入路径**上，含 `data/stores/unstructured/platform.py:829`（平台仓储核心写方法）。`tests/conftest.py` 的 `_isolate_db` 未隔离队列库、未授予租约。
2. **中性事实须有已发布文档版本**：`platform.py:582` 要求 `latest_document_version(obs.document_id)` 存在；`:584` 另要求版本不晚于事实本身（`neutral_fact_cannot_cite_future_document_version`）。

### 3.1 已处置：158 → 47

用户决策「方案 A + 门 2 补造文档版本」，实现（`tests/conftest.py` + 5 个测试文件）：

- **门 1**：`_isolate_db` 增 `_grant_write_lease()`，在**独立数据库** `write-lease.sqlite` 中真实 `enqueue` + `claim` 一条任务，并导出 `ATS_PERSISTENT_QUEUE_SOURCE_ID` 以匹配平台仓储的 env 解析方式。租约 scope 由目录声明的 persistent 来源 + `TEST_ONLY_PERSISTENT_SOURCES` 枚举，**不设通配**——未覆盖的来源仍被拒且可见。
- **门 2**：autouse fixture 包住 `TradingMemory.save_observation` 这一**单一入口**，仅在所引文档无版本时补一份 dated 早于事实的最小版本。**生产代码零改动**；`ATS_TEST_NO_AUTO_PUBLISH=1` 可关闭钩子，`tests/test_write_lease.py` 用它断言两道门**仍会拒绝**。
- `test_chain_articles.py` 的 scheduler 断言改到当前接线（`_run_managed_article_ingestion` 替代 `ingest_configured`），保持「采集先于报告渲染」与「源故障不中断作业」原意图。

处置过程中修正了 5 处**自身引入**的问题：租约库与断言队列内容的测试冲突（5 例）、测试替身未覆盖 worker 分支（1 例）、守护测试被自动发布钩子破坏（1 例）、lease 测试未指向自建队列（2 例）、`calendar_refresh` 替身字段不全（1 例）。

### 3.2 剩余 47 例

- **1 例预存在 hang**：`test_factset_index_semantic_local.py::test_semantic_index_chart_staging_remains_shadow` 读真实 PDF 资产并跑语义抽取管线，`perl -e 'alarm 150'` 下不返回；已用 `git stash` 移除 conftest 改动后复现，**与本轮修复无关**。
- **1 例已知偶发**：`test_chief_loop::test_over_cap_order_is_revised_to_boundary_then_executed`（顺序依赖，全量失败、单文件 10/10 通过；自 2026-09-22 权威基线即如此）。
- **1 例跨文件污染**：`test_write_lease::test_a_test_can_actually_persist_an_observation` 仅在全量下失败，与本组 45 个文件共存时通过；污染源尚未定位。
- **其余约 44 例**属 `complete-target-dataflow` / `e80671a` 自身的验收缺口（如 `test_factset_index_semantic_local` 18 例、`test_factset_earnings_insight` 4 例、`test_research` 4 例、`KeyError: freshness_days` / `core_metrics` 等字段漂移），须由其负责人处置。

## 4. 本组发现并修正的自身缺陷

| 缺陷 | 发现方式 | 修正 |
|---|---|---|
| 隔离面漏了结构化库与持久队列 | `test_isolation_covers_every_known_persistence_env` 从源码派生持久面 env 清单，逐项比对后失败 | 补入 `ATS_STRUCTURED_DB_PATH`、`ATS_STRUCTURED_ARTIFACT_ROOT`、`ATS_PERSISTENT_QUEUE_PATH`（共九个面）；「看起来像路径但不是持久面」者改为 `NON_ISOLATED_ENV` 显式豁免并附理由 |
| `consumers_touched_by` 只遍历 `consumer_fingerprint_paths` 的键，漏掉六个无附加路径的消费者 | 测试断言共享文件应影响全部十角色，实际只报 4 个 | `FingerprintSurface.consumers` 单独承载全部消费者 |
| 切流守卫把 23 处合法的 `read_mode()` 调用判为违规 | 首次扫描返回 24 项 | 规则改为只禁「自己改路由」；匹配用「接收者+方法」而非裸方法名（`rollback` 在四个模块里是 SQLite 事务） |
| broker 绕过检测依赖变量名 | `ib.placeOrder()` 未被识别 | 改为「导入 SDK 或构造客户端」 |
| 指纹面断言可被路径拼写绕过 | 测试自身写错路径（`src/ats/data/structured.yaml`）却通过 | 路径先按仓库根解析；不存在的路径判为笔误而非放行 |
| `test_dependency_content_change` 依赖 basetemp 位置 | worktree 中 basetemp 在仓库外，`_dependencies` 抛 `fingerprint paths must be inside the repository` | 探针文件改放 `var/pytest-fingerprint-*`（与验收脚本同做法），测试不再随调用位置改变结果 |
| 租约注入到 `queue.sqlite`，与断言队列为空的 5 个测试冲突 | `test_managed_cache_miss_local` 报 `assert [...] == []` | 租约改用独立库 `write-lease.sqlite`；测试自建队列断言内容时互不干扰 |
| 自动发布钩子被我自己的守护测试触发 | `test_a_fact_citing_an_unpublished_document_is_still_rejected` 变成通过 | 钩子加 `ATS_TEST_NO_AUTO_PUBLISH` 开关，守护测试用它关闭；开关**每次调用**检查而非安装时检查（测试用 monkeypatch 在 fixture 之后设置 env） |
| 替身未覆盖新分支 | `test_managed_article_ingestion` 报 `'Queue' object has no attribute 'valid_lease'` | 该测试测「手工 ingest 只入队」，故显式清除租约 env |
| `calendar_refresh` 替身字段不全 | `KeyError: 'source_run_id'`（CLI 在 task_id 非空时读该键） | 替身补齐 CLI 实际读取的字段 |

## 5. 复现命令

```sh
# 本组九个测试文件
./scripts/run_tests.sh \
  tests/test_broker_write_guard.py tests/test_isolation.py \
  tests/test_dataflow_assurance.py tests/test_dataflow_assurance_parity.py \
  tests/test_assurance_surface.py tests/test_cutover_guards.py \
  tests/test_write_lease.py tests/test_chain_evidence.py tests/test_chain_articles.py

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

# 全量（本机需提高删除阈值；2026-10-04 修复后实测 47F/2281P/0E）
CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=100000 ./scripts/run_tests.sh tests
```
