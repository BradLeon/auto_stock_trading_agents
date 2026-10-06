# 切流控制平面运行手册（Phase F）

> 权威声明：本手册中的每个开关名、命令与判据都由 `tests/test_cutover_cli.py`
> 与 `tests/test_cutover.py` 逐条校验。**手册与代码不一致时以代码为准，并请修本手册**——
> 手册里的开关名如果代码没有，它会被信任而不会生效。

## 0. 当前状态（截至本 change 提交时）

| 边界 | 当前路由 | 接线 | 说明 |
|---|---|---|---|
| `projection_read` | `legacy` | 已声明 | 读路径；切换需 `qualification()` 通过 |
| `analyst_output` | `legacy` | 已声明 | 分析输出 |
| `dispatcher_schedule` | `legacy` | 已声明 | 调度；切换需走交接协议（第 4 组） |
| `approval_lifecycle` | `legacy` | 已声明 | 审批链 |
| `clerk_publication` | `legacy` | 已声明 | 账本发布 |
| `live_trader` | **`disabled`** | 已声明 | **实盘未启用**，需另行取得可审计的实盘授权 |

**影子期无下单**：`execution/broker_write_guard` 在进程层禁止券商写入，禁令装在提交层
而非调用方——`check_broker_write` 置于 `IBKRBroker.place_orders` 之前。影子运行的
订单意图写入独立影子账本（`ats shadow intents`），**真实 `trades` 写入经仲裁拒绝而非
静默改道**。

**尚不可切流的原因**：`F.0.2` 的十消费者资格证据尚未登记（`ats data qualification`
相关命令），本组交付的是门禁、影子能力与演练能力，不是切换本身。

## 1. 六条边界与接线声明

每条边界声明至少一个**实际调用点**；未声明接线的边界**不能据其切流**——一个没人读的
边界照样记录路由，把它指向 target 看起来像切流而实际什么都没发生。

```bash
ats cutover state --bootstrap --db var/phase_f_cutover.sqlite   # 首次初始化并声明接线
ats cutover wiring --db var/phase_f_cutover.sqlite                # 查看接线点
ats cutover history --boundary projection_read --db <db>         # 谁在何时为何改动
```

接线点（`DECLARED_BOUNDARY_WIRING`，由测试逐条 import 校验存在）：

| 边界 | 主要调用点 |
|---|---|
| `projection_read` | `ats.workflow.cutover_routing.read_route` |
| `analyst_output` | `ats.workflow.cutover_wiring.guard_analyst_output` |
| `dispatcher_schedule` | `ats.workflow.dispatch_claims.claim` |
| `approval_lifecycle` | `guard_approval_write` + `repository.record_approval` / `record_review` |
| `clerk_publication` | `guard_clerk_publication` + `clerk.clerk_run` |
| `live_trader` | `broker_write_guard.check_grant` + `authorization.validate_authorization` |

## 2. 互斥规则

三条配对，**必须整组移动**：

| 配对 | 理由 |
|---|---|
| `projection_read` ↔ `dispatcher_schedule` | 旧调度写入的存储不再被新读者视为权威 |
| `analyst_output` ↔ `approval_lifecycle` | 决策在一个表示上作出、记录在另一个上，无从审计 |
| `approval_lifecycle` ↔ `clerk_publication` | 审批记在一条路径、发布在另一条，则该审批无人能审计 |

**推论：切一条必然经过中间不兼容态。** 因此切换顺序必须是**先切配对方、最后切目标
边界**，每一步都被接受。`ats cutover set-route` 拒绝半迁移组合并报出冲突双方：

```bash
ats cutover set-route --boundary approval_lifecycle --route target --actor <op> --reason <理由>
ats cutover set-route --boundary analyst_output      --route target --actor <op> --reason <理由>
```

## 3. 预检（只读）

```bash
ats cutover preflight --db <db>                                    # 全局
ats cutover preflight --boundary projection_read --consumer-id macro \
                       --scope-json '{"consumer": "macro"}' --db <db>   # 针对一次激活
```

**预检不改变任何路由**（不开写事务，可反复运行）。不可行时退出码 1。检查项：
权威可读、跨边界兼容、接线已声明、激活请求有效、交易开关独立性。

三类 fail-closed：同边界双活（兼容矩阵拒绝劈开）、缺资格激活新路由（`reverify` /
读门控拒绝）、**权威状态不可读**（当作「无冲突」就会放出两条活路由）。

## 4. 资格门控（5.6）

读路径在**读取时**调用 `ats.data.assurance.qualification()`，不是启动时——资格带 TTL
且可撤销，启动时检查会让撤销晚几小时才生效。

```bash
ats cutover reverify --consumer-id macro --scope-json '{"consumer":"macro"}' --db <db>
# 退出码 0=可用；1=不可用（未取证/已撤销/快照过期），reasons 列出全部原因
```

边界未激活时**不做资格检查**：否则一次无关的撤销会阻断普通 legacy 读取。

## 5. 安全回退（5.7）

三项**同时**满足才回退：

```bash
ats cutover fallback --route legacy --fallback-proof valid \
                     --fallback-retired no --fallback-available yes --db <db>
```

| 情形 | verdict | reason_code |
|---|---|---|
| 三项都满足 | `ok` | — |
| 回退证明缺失 | `blocked` | `fallback_proof_missing` |
| 回退目标已退役 | `blocked` | `fallback_target_retired` |
| 回退目标不可用 | `unavailable` | `fallback_target_unavailable` |

**未证明即不满足**：现有 `dual_read_diffs` 自述只比 presence 且从不作门禁，故不构成
回退证明。`retired` 与 `unavailable` **刻意分开**——退役但能跑 vs 在用但坏了，处置不同。

## 6. 批次登记与引用报告校验

```bash
# 先验报告对该 scope 可引用（未通过则拒绝激活）
ats cutover activate --boundary projection_read --consumer-id macro \
                     --scope-json '{"consumer":"macro"}' \
                     --report-id <report> --report-db var/shadow/reports.sqlite \
                     --actor <op> --db <db>

ats cutover active  --boundary projection_read --db <db>
ats cutover release --boundary projection_read --scope-json '{"consumer":"macro"}' \
                    --actor <op> --reason "资格撤销" --db <db>
```

报告引用校验见 `docs/SHADOW_COMPARISON_RUNBOOK.md` §4（四项：签核有效、必需面无
not-compared、无未接受差异、仍适用于当前 scope 与代码配置）。

## 7. 写入点在边界关闭时拒绝而非改道

```bash
# 临时冻结审批：后续 record_approval / record_review 写入被拒
ats cutover set-route --boundary approval_lifecycle --route disabled \
                      --actor <op> --reason "切流期间冻结审批" --db <db>
```

**拒绝而非改道**：改道让误写看起来成功，于是同一错误在窗口之外复发并真的写进生产。
`legacy` 路由**仍然放行**——只判「是否在 target」会在切流开始前就把系统弄坏。

## 8. 调度交接（与第 4 组协同）

调度边界切换走 `workflow/dispatch_claims.hand_over`：冻结新认领 → 清点未完成触发 →
逐个声明处置（承接/执行完毕/作废并记录原因）→ **证明旧执行方已停止发布** → 换代次 →
开放。无证明即拒绝承接。

`ats cutover set-route --boundary dispatcher_schedule` 只改**边界状态**；触发所有权
的移交由交接协议执行，两者不是同一件事。

## 9. 常见拒绝原因

| 拒绝信息 | 含义 | 处置 |
|---|---|---|
| `no declared wiring` | 该边界无声明接线点 | 先声明接线；不能据它切流 |
| `incompatible boundary combination` | 配对被劈开 | 先切配对方，整组移动 |
| `authority could not be read` | 控制库不可读 | 修复存储；不得当作「无冲突」 |
| `fallback_target_retired` | 回退目标已退役 | 修差异并重跑，或接受范围停止 |
| `fallback_proof_missing` | 无有效回退证明 | 补证明；不可默认当作已证明 |
| `scope mismatch` | 报告覆盖的范围不同 | 为该 scope 单独比较 |
| `predates a change` | 报告早于代码/配置变更 | 重跑影子比较 |
| `requires a fresh report` | 该 scope 曾被释放 | 重新取证 |

## 10. 与实盘授权的关系

本手册交付的是**门禁与演练**。真实 live route 切换需**另行取得可审计的实盘授权**
（`execution/live_route_switch`），部署路由变更授权与实盘授权是**两个不同授权**，
前者不蕴含后者。`live_trader` 边界起点为 `disabled`，配置错误也开不出它。
