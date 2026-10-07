# 测试基线：入口、测量条件与归因判据

> 为什么有这份文档：基线数字只有在**命令、依赖范围、执行环境条件**三者一起给出时才可复核。
> 曾经出现的失败是「121 failed / 263 errors」被当成回归排查，而其中 263 项 error 其实是执行环境
> 的删除配额打断了 fixture setup——环境限制伪装成了业务失败。本文件固定这三要素，并给出
> 「环境性失败 vs 业务回归」的判据。

---

## 1. 唯一入口

```bash
./scripts/run_tests.sh                       # uv sync --all-extras + uv run pytest（参数透传）
./scripts/run_tests.sh --check                # 只装依赖并校验各可选分组均可导入
./scripts/run_tests.sh --batched 8            # 受限环境下的分批取证（不能替代全量基线）
./scripts/run_tests.sh tests/test_risk.py -k survival
```

脚本内部等价于 `uv sync --all-extras` + `uv run pytest -q --tb=no -rA --basetemp=<仓库内 tmp/>`。
**不要**用 `PYTHONPATH=src .venv/bin/python -m pytest` 替代：后者不保证可选分组已装入，缺分组时
失败会伪装成业务断言失败（`ats.runtime.optional_deps` 现在会把这种缺失显式报成
「模块 + 所属 extras 分组」）。

- **依赖范围**：`pyproject.toml` 声明的全部 extras——`data`、`broker`、`memory`（chromadb）、
  `schedule`、`memory_persist`、`channel`、`dev`。测试面需要**全部**装入；部署机仍按角色挑选分组
  （见 `docs/DEVELOPMENT.md` 第 1 节），`memory` 的 chromadb 只是未接入运行时，装它不代表部署依赖。
- **环境探针**：`--check` 会导入 `apscheduler`、`pandas_market_calendars`、
  `langgraph.checkpoint.sqlite`、`ib_async`、`fastapi`、`chromadb`，任一缺失即报出模块名与分组。

---

## 2. 测量记录

### 2.1 权威测量（验收判据）

#### 2026-10-07（Phase F task 13.6，本机 uv 环境）

| 项 | 值 |
|---|---|
| 命令 | `./scripts/run_tests.sh -p no:randomly --junitxml=… --deselect tests/test_factset_index_semantic_local.py::test_semantic_core_passes_with_exact_index_and_technology_reviews` |
| 依赖范围 | `uv sync --all-extras`（探针 6 个可选模块全部就绪） |
| 执行环境条件 | 本机执行、**删除配额阈值提至 100000**、`--basetemp` 在仓库内 `tmp/13.6/`；`uv` 0.12.15 托管 CPython 3.12.12（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`） |
| 结果 | **45 failed / 3072 passed / 0 errors / 2 deselected（2533.42s ≈ 42 分钟）** |
| 归因后 | **0 例本 change 引入的回归**（详见下表） |

**为什么带 `--deselect`**：首次全量在 36% 处停在
`test_factset_index_semantic_local::test_semantic_core_passes_with_exact_index_and_technology_reviews`
（已登记的预存在 hang，`e80671a` 引入，语义 PDF 管线）。该 hang 使全量无法跑完，
故重跑时**只排除这一条**——被排除的是 1 个测试项（参数化 2 例），不是一批测试。
两次运行的读数分别存档，不把「跑到 36% 的部分」与「跑完全量的部分」混为一谈。

**45 例的四类归属**（`scripts/attribute_test_failures.py` + `git worktree` HEAD 对照）：

| 类 | 例数 | 判据 |
|---|---:|---|
| **A 既有失败** | 27 | 在 `git worktree add /tmp/ats-base HEAD` 的干净基线上**同样失败**（逐条 id 对照，非文件级） |
| **B 停用下单所致** | 16 | 用户 2026-10-06 裁决的 C3 决定（`AUTO_EXECUTION_ENABLED = False`，见任务 7.9）。**有意为之，不是回归**；恢复条件已记录在 `PHASE_F_GROUP_PROGRESS.md` 的 `PF-7-03` |
| **C 退役表守卫命中** | 1 | `test_data_layer_write_cutover` 命中本 change 新增的 `intake_verification.py`。**已在本轮修复**（见下） |
| **D 顺序依赖** | 1 | `test_write_lease`，隔离运行通过；全量中受前序测试影响 |

**C 类的处置**：`intake_verification.py` 的 `NEUTRAL_FACT_TABLES` / `PROJECTION_TABLES`
只是**分类标签**（让 7.3 能判定「分析角色把观点写进中立事实表」），不执行 SQL。
守卫按字面量匹配退役表名，因此命中。已把该模块加入守卫的 `SANCTIONED`——
与既有的 `ownership.py` 同属「**为管制而列出表名**」这一类，注释里写明了理由。

**加白名单后必须证明守卫没被削弱**（否则「加白名单」就是消音器）：
向该模块的**函数体内**注入一条真实的 `INSERT INTO evidence_facts`，守卫仍判失败
（`_probe_write` 被点名）——因为白名单只放了 `<module>` 作用域，函数体不在其中。
读侧的三处 `execute` 亦已逐一核对：两处参数化，一处 f-string 但只从硬编码的
6 元组 `_PRODUCTION_WATCH_TABLES` 取值、以 `mode=ro` 连接，且列的是**新表**
`data_evidence_facts` 而非退役表。

#### 历史权威测量

| 项 | 值 |
|---|---|
| 命令 | `uv run pytest`（全量） |
| 依赖范围 | `uv sync --all-extras` |
| 执行环境条件 | 执行环境**不设**文件删除配额；`uv` 托管 CPython 3.12.12（`.venv/pyvenv.cfg` 内 `uv = 0.9.25`） |
| 结果 | **135 failed / 1398 passed / 22 warnings（170.50s），error = 0** |
| 日期 | 2026-09-22 |

该数字已回填 `docs/TARGET_WORKFLOW_DATAFLOW.md` §15.1，并附按簇归因（116 项同源 = 86%）。

### 2.2 受限环境测量（仅作对照，不得用作验收判据）

| 测量 | 命令与条件 | 结果 |
|---|---|---|
| 全量，仓库内 `--basetemp` | 沙箱执行环境，删除配额 50/轮 | 120 failed / 1147 passed / **266 errors**（536s） |
| 全量，不受删除配额 | `--basetemp` 同上，关闭沙箱守卫 | 81 failed / 825 passed / **648 errors**（146s） |
| 分批取证 | `scripts/run_tests_batched.py --batch-size 6`，23 批 | 133 failed / 1161 passed / **264 errors**（877s），其中环境性 12 |
| 对照：默认临时根 | 沙箱执行环境，无 `--basetemp` | 全部测试 error（`PermissionError: EEXIST`，pytest 默认临时根无法创建） |

三次受限测量的 error 都是**同一现象**：从某个测试序号起，其后所有测试在 setup 阶段失败
（不受限那次从第 906 个测试起、共 648 项，全部为 setup 中断）。**这是环境限制，不是回归**——
被中断的测试从未执行到断言。

---

## 3. 执行环境的两条限制与影响范围

1. **默认临时根不可用**。沙箱代理了文件系统操作，pytest 创建 `/…/T/pytest-of-*` 时得到
   `PermissionError: EEXIST`，整库跑会全数 error。**对策**：`--basetemp` 指向仓库内目录
   （`scripts/run_tests.sh` 已默认如此，目录为 gitignore 的 `tmp/pytest-basetemp/`）。
2. **删除配额**。pytest 运行期清理临时编号目录会累计删除量，触及配额后守卫以 `SystemExit` 打断
   fixture setup；`tests/conftest.py` 的 autouse fixture `_isolate_db(tmp_path, …)` 使每个测试都依赖
   临时目录，故障因此被**放大**为整库级失败。
   **影响范围**：配额触发点之后的全部测试，表现为 setup 阶段 error，与被测代码无关。
   **对策**：①在不受该配额约束的环境测量；②或按文件分批运行（`--batched`），每批一个独立
   pytest 进程与独立临时目录根。

> 分批取证**不能**替代全量基线：批次之间不共享进程内状态、临时目录生命周期与全局缓存，
> 跨批次的顺序依赖与资源竞争不会显现。它只用于**枚举失败**，判定回归仍以全量基线为准。

---

## 4. 归因判据（环境性 vs 业务性）

判定顺序（`ats.workflow.test_baseline` 实现了同样的规则，可直接跑
`render_summary(summarize(parse_junit_xml(...)))`）：

1. **setup/teardown 失败**（JUnit `<error>`）优先判为环境性候选；call 阶段失败（`<failure>`）
   是业务断言失败。
2. 命中已知环境签名即判环境性：`SAFE_DELETE_BULK_CONFIRM_REQUIRED`、删除配额、
   `pytest-of-` / 临时目录 `PermissionError`、`SystemExit`、只读文件系统、设备无空间。
3. 命中 `No module named X` 且 X 属于某个 extras 分组 → 环境性（依赖缺失），并报出模块与分组。
4. 其余按根因签名分簇：`no such table: <table>`（同一缺失表）、
   `unexpected keyword argument '<arg>'`（接口签名漂移）。
5. 单一环境性簇占比 ≥ 25% 时，判定为**整库级环境性中断**：给出放大机制说明与受影响范围，
   **不以逐项业务失败的形式呈现**。
6. 未匹配任何签名的失败进入 `unattributed`，必须标注数量与归因归属，不得以「其余失败」带过。

### 4.1 分批取证结果（2026-09-22，23 批）

| 簇 | 类型 | 规模 | 判据 |
|---|---|---:|---|
| `missing table: source_documents` | 业务 | 45 | 同一张缺失表；位于 `_retire_data_tables()` 的 DROP 名单 |
| `missing table: evidence_observations` | 业务 | 33 | 同上 |
| `interface signature drift: consumer` | 业务 | 9 | 调用方传入被调方不接受的关键字参数 |
| `missing table: data_sources` | 业务 | 7 | 同一张缺失表 |
| `missing table: newsletter_cursors` | 业务 | 4 | 同一张缺失表 |
| `missing table: document_candidates` | 业务 | 3 | 同一张缺失表 |
| `interface signature drift: legacy_repository` | 业务 | 3 | 接口签名漂移 |
| `missing table: document_versions` / `evidence_failures` | 业务 | 各 1 | 同一张缺失表 |
| `environmental: 守卫以 SystemExit 打断 fixture setup` | 环境 | 12 | setup 阶段 `SystemExit: 1` |
| `unattributed` | 业务 | 279 | 未匹配已知签名，逐项归因后登记（本阶段不要求清零） |

前 8 簇共 106 项 + 签名漂移 12 项即缺陷 ① 的同源失败面，与 §2.1 权威归因的
「101 项缺表 + 12 项签名漂移」方向一致（分批与全量的计数口径不同，故不完全相等）。

---

## 5. 复现方式

```bash
# 1. 装依赖并校验
./scripts/run_tests.sh --check

# 2. 全量（不受删除配额约束的环境）
uv run pytest -q --tb=no -rA --basetemp=tmp/pytest-basetemp/run --junitxml=tmp/baseline.xml

# 3. 归因摘要
uv run python -c "
from pathlib import Path
from ats.workflow.test_baseline import parse_junit_xml, summarize, render_summary
print(render_summary(summarize(parse_junit_xml(Path('tmp/baseline.xml').read_text()))))"

# 4. 受限环境：分批取证
uv run python scripts/run_tests_batched.py --batch-size 6
```

## 6. 逐例归因（13.6 用法）

`test_baseline` 给的是**簇**的摘要；13.6 要求对**每一例**给出根因与处置，因此需要逐例
对照。判定必须以**测试 id** 为单位，不能以文件为单位——同一个文件里可能既有既有失败
也有本轮新增。

```bash
# 4a. 逐例归因：按文件分组、按消息签名归并，并与已登记清单对照
uv run python scripts/attribute_test_failures.py tmp/baseline.xml \
  --registered tmp/13.6/registered-preexisting.txt

# 4b. 对「未登记」的失败，在干净基线上复跑同一批 id —— 这是判定「既有」的唯一硬证据
git worktree add /tmp/ats-base HEAD
PYTHONPATH=/tmp/ats-base/src uv run python -m pytest <同一批 id> -q --tb=no \
  --basetemp=/tmp/ats-base-tmp
git worktree remove /tmp/ats-base --force
```

**为什么必须在干净 worktree 上对照，而不是凭印象改判**：「看起来眼熟」不是判据。
本次 45 例里，若只按「印象」判，会把 16 例**用户明确裁决的停用下单**当成新回归去「修」——
而修它的正确动作是补授权（方案 A），不是改测试。

**顺序依赖与真回归的区分**：全量失败但在**隔离**运行下通过的，是顺序依赖
（`test_write_lease` 即此类），不是回归。反之隔离运行仍失败的才是真问题。

## 与 Phase A 验收的关系

Phase A（`openspec/changes/establish-workflow-contracts-and-green-baseline`）的实测基线、
失败归因与「需求场景 → 验证方式」对照表记录在 `docs/PHASE_A_ACCEPTANCE.md`。本文件定义
「怎么测、怎么归因」，那份文件记录「测出来是什么」。
