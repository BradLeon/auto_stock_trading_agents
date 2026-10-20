# 任务 12.1：消费者清零证明（2026-10-17）

> 结论先说：**27 个登记的退役条目全部保持 pending，0 个可退出。** 这不是缺陷，而是 12.1 要证明的事：新路径已经在生产上跑起来了，但旧的生产读写**仍然存在**，所以不能退役。下面分三层证据，并说明还差什么才能让条目离开 pending。

## 一、证明连到了真实的生产调用链

7 个研究工作流现在都由 Dispatcher 负责并在生产上执行过（`var/phase_f_dispatch.sqlite` 的认领记录，终态 complete）：

| 工作流 | 已完成的认领 | 最近一次 |
|---|---|---|
| information-brief | 51 | 10-16 |
| fundamental-routine | 44 | 10-16 |
| fundamental-event | 2 | 10-16 |
| layer-review | 89 | 10-17 13:28 UTC（常驻进程） |
| sector-review | 6 | 10-17 13:37 UTC（常驻进程） |
| macro-review | 6 | 10-17 13:28 UTC（常驻进程） |
| technical-review | 51 | 10-17 |

负责人切换的批准原文和代次见 `ats dispatch owner-log`；旧入口失权（`schedule frozen or caller is not current owner`）已对 Layer、Macro、Technical、Information、Fundamental 实测。

## 二、静态清零核验（`ats retirement zero / decide`）

对 27 个登记条目的 53 个登记消费方逐一给出结论（全文见 `docs/archive/phase_f_first_run_review_20261016/retirement_zero_20261017.txt` 与 `retirement_decide_20261017.txt`）：

| 结论 | 数量 | 含义 |
|---|---|---|
| zero | 3 | 已经走受治理读取面：`trader.execute`、`agents.sector.review`、`agents.fundamental` |
| not_zero | 5 | 仍绕过受治理读取面：Chief 的 3 处（`chief.assemble` 的旧表直读、`sector_reviews`、`store.direct_trade_reads`）、`agents.risk_officer.review`、`execution.clerk` |
| partial | 45 | 无法静态判定：登记的消费方是模块路径或函数（如 `runtime.cli`、`graph.pead`、`memory.store.*`），没有对应的角色编号映射（`MODULE_TO_ROLE`），工具拒绝猜测 |

**覆盖是否完整：** 53 个消费方每一个都有结论，没有被跳过。`decide` 的结果是 27 个条目全部 pending、可退出 0。

## 三、本次发现并修正的一处核验偏差

`agents.fundamental` 原先被判 not_zero：扫描只看三个入口模块（`entry`、`routine`、`event`），而 Fundamental 在新路径上真正读取公司数据的是 `agents.fundamental.inputs`，由 `graph.pead` 调用，扫描从未到达，所以一个已经切换的消费者被说成「从未接入受治理读取面」。

已把 `ats.agents.fundamental.inputs` 和 `ats.graph.pead` 加入声明的入口点（`intake_verification.CONSUMER_ENTRY_POINTS`）。结果：
- Fundamental 现在判为 zero（可到达受治理读取面）。
- 同时扫描如实暴露 `graph.pead` 里 4 处旧的供应商直读（第 26、144、145、367 行附近）：它们是切换前的回退路径，只在读路由为 legacy 时执行，是 G3「迁移期退回旧路径」要保留的东西。守卫测试已改为**按模块和条数钉住**这 4 处，新增的绕过仍然会让测试失败。

## 四、旧的生产读写还剩什么（所以保持 pending）

| 类别 | 内容 |
|---|---|
| 旧读路径（路由门控，当前不执行） | PEAD 图的 4 处供应商回退；Trader 参考价读取（已停用，保留给方案 A 回退，扫描记为 `disabled_bypass`） |
| 仍绕过治理读取面 | Chief 的旧表直读，Risk Officer，Clerk |
| 旧写路径 | `memory.store` 的旧决策写路径、`client_order_id` 旧派生、`journal` 与 `performance` 旧快照、进程内恢复去重 |
| 旧调度 | `scheduler` 的硬编码串行运行与临时日记作业（旧调度器已不在运行，但代码还在） |

## 五、要让条目离开 pending 还差什么

1. **45 个 partial：** 为这些模块路径登记角色映射（`MODULE_TO_ROLE`），或把条目的消费方改成角色/入口点，之后才能出 zero 或 not_zero 的结论。这是登记工作，不是代码改造。
2. **5 个 not_zero：** Chief、Risk Officer、Clerk 要接入受治理读取面（本 change 把它们放在 Phase F 之后，实盘保持锁定）。
3. **回滚窗口与对账（12.3、12.7、12.8）：** 清零之外还要求登记观察窗口和对账，不能只靠本项放行。

**本项不放行任何条目**：`ats retirement decide` 的结果就是证明，没有为了让条目可退出而降低判据。
