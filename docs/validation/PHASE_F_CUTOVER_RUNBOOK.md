# 切流控制平面运行手册（Phase F）

> 权威声明：本手册中的每个开关名、命令与判据都由 `tests/test_cutover_cli.py`
> 与 `tests/test_cutover.py` 逐条校验。**手册与代码不一致时以代码为准，并请修本手册**——
> 手册里的开关名如果代码没有，它会被信任而不会生效。

> 本手册记**怎么操作**。实施进度、验证到什么程度、待处理项与统一 fix 顺序见
> [`PHASE_F_GROUP_PROGRESS.md`](PHASE_F_GROUP_PROGRESS.md)。

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

## 11. 十角色接入核验（F.0.3–F.0.5）

### 11.1 为什么需要隔离接入验收入口

`F.0.2` 的实测结论是 **0/10 合格**，原因全部为 `assurance_ledger_missing`——
生产库 69 张表中**没有** `dataflow_assurance_events`，即**从未登记过逐项证据**。
这是诚实的起点，但如果补验入口本身要求被证明的资格，资格就**不可获得**而非 merely
未获得（design 决策 11）。故设隔离入口：它在进程禁写与隔离账本之下运行，**不以生产
资格为前置**，其运行证据用于后续登记。

```bash
ats intake verify                     # 十个角色，输出可解析 JSON
ats intake report                     # 同一结论的 Markdown 报告
ats intake digest                     # 稳定摘要 + 证据标识
ats intake verify --consumer-id trader # 单个角色
ats intake not-tradable --consumer-id trader   # 演示该拒绝规则
ats intake evidence-index             # 证据索引（本节表格的机读来源）
ats intake disposition                # 逐消费者处置
```

### 11.2 当前实测结论：5/10 合规

| 消费者 | 到达入口 | 结论 | 发现 |
|---|---|---|---|
| `layer` | 是 | 合规 | — |
| `information` | 是 | 合规 | — |
| `sector` | 是 | 合规 | — |
| `macro` | 是 | 合规 | — |
| `technical` | 是 | 合规 | — |
| `fundamental` | 是 | **不合规** | 未触达受治理读取面 |
| `chief` | 是 | **不合规** | 未触达受治理读取面 |
| `risk` | 是 | **不合规** | 未触达受治理读取面 |
| `trader` | 是 | **不合规** | 已停用的取价实现 + 未触达受治理读取面 |
| `clerk` | 是 | **不合规** | 未触达受治理读取面 |

**自动下单已停用（任务 7.9 裁决，2026-10-06）**：`AUTO_EXECUTION_ENABLED = False`。
下单前需要参考价把「按金额」换算成股数、给市价单算滑点保护，而**行情数据未授权给
trader**（`MARKET_DATA` 的 `allowed_consumers` 只有 `technical`/`risk`，trader 的
`products` 只有 `APPROVED_EXECUTION_AUTHORIZATION`，且授权链十项字段只有行情**时点**、
没有价格）。这是契约与现实不符，不是可以顺手改的越权读取。

停用方式是**拒绝且记录原因**，而非让取价失败后静默算出 0 股——那等于丢弃订单：

```bash
$ ats intake verify --consumer-id trader
{"consumer_id": "trader", ..., "violations": [
  {"kind": "disabled_bypass", "location": "ats.trader.execute:358", ...},
  {"kind": "governed_read_surface_not_reached", "location": "ats.trader.execute", ...}]}
```

`disabled_bypass` 与 `provider_bypass` 分开报告：代码**仍在**（方案 A 要复用它），但
当前**不可达**。两者都判不合规——保留的实现未获授权前不能算通过。

**为何不补授权**：授权清单是被证据指纹约束的文件，改它会作废全部已登记证据。运行
期间以**人工在券商下单**替代，是 2026-10-06 的决定。

**后续开通方式**：按**方案 A（补授权）**单独开发——给 trader 的 `products` 补声明行情，
让参考价取自受治理读取面，恢复 `AUTO_EXECUTION_ENABLED` 与 `_last_price_enabled`
（当前保留但无人调用的那一份实现）。**该改动须在任何资格证据登记之前完成**
（design 决策 9），否则作废全部已登记证据。

**未触达受治理读取面**（`fundamental`/`chief`/`risk`/`trader`/`clerk`）：契约声明每个
消费者的读路径是 `ats.data.consumer_api.read_input`，而这些角色的自有模块既不触达它
也不触达产品层。**这不是旁路，是接线缺口**——两者调用不同的修复工作，故为独立 kind。

扫描深度为 **0**（只读角色自有模块）。传递闭包会把整个应用的 provider 用量记到
恰好 import 了 orchestrator 的那个角色头上——实测 `trader.execute` 为拿决策图 runner
而 import `runtime.cli`，传递扫描因此产出 49 条**全属误报**的发现。

### 11.3 隔离证明不是生产账本完整性证明

隔离核验只证明**路径可用**。它读的是隔离账本与券商模拟，因此对生产账本是否完整
**不构成任何证明**。这条拒绝由 `assert_isolated_result_not_tradable()` 在使用点强制，
并可由 `ats intake not-tradable` 直接复核：

```bash
$ ats intake not-tradable --consumer-id trader
{"tradable": false, "reason": "isolated verification '(none)' cannot authorise a trade
 for trader: ... Re-verify qualification for the current scope via
 ats.data.assurance.qualification() before submitting."}
```

同理，隔离运行期间发生的生产写入会使该次验收**无效并须清除**，而不是被记一条日志。

### 11.4 证据索引

每条证据标识的格式为 `intake-verification:<digest 前 16 位>:<consumer_id>`，
由 `ats intake evidence-index` 产出。**本节表格与 digest 由
`tests/test_intake_cli.py` 逐条校验**：手册引用的标识若不在索引中，测试判失败。

| 消费者 | 证据标识 |
|---|---|
| `layer` | `intake-verification:cf0ea1e27a657763:layer` |
| `information` | `intake-verification:cf0ea1e27a657763:information` |
| `sector` | `intake-verification:cf0ea1e27a657763:sector` |
| `fundamental` | `intake-verification:cf0ea1e27a657763:fundamental` |
| `macro` | `intake-verification:cf0ea1e27a657763:macro` |
| `technical` | `intake-verification:cf0ea1e27a657763:technical` |
| `chief` | `intake-verification:cf0ea1e27a657763:chief` |
| `risk` | `intake-verification:cf0ea1e27a657763:risk` |
| `trader` | `intake-verification:cf0ea1e27a657763:trader` |
| `clerk` | `intake-verification:cf0ea1e27a657763:clerk` |

摘要只覆盖**观测**，不覆盖时间戳：两次核验同一件事必须给出同一 digest，否则
「证据相符」就成了没人能核的说法。任一发现出现或消失，digest 即改变。

### 11.5 逐消费者处置（F.0.5）

处置规则由 `workflow/consumer_disposition.py` 实现，五条都是**拒绝**而非放行：

| 情形 | 处置 | 理由 |
|---|---|---|
| 已接受的 optional 缺失 | 保持原政策 | `sec_edgar_filing_body` 的 optional 是有版本号记录的决策，补验时没看见不构成推翻它的理由 |
| 未被接受的输入缺失 | `partial` | 「这次没要求」不等于「已被接受」 |
| 已确认覆盖缺口 | `partial` 并登记 | 补验无权通过认定它不重要来消解它 |
| 最终版来源按报告年龄被判 stale | **拒绝该降级** | 在最新最终版上读取的来源，报告年龄说明不了当期数据是否可用 |
| 时间缺失 | 显式 `partial` | 缺失的时间戳不是「存在且有效」 |
| 历史断链 | 显式 `partial` 且阻断切流 | **不自动放行**：「大部分历史是好的」不是资格 |
| 旧路径缺陷 | 仅登记不修复 | 修它就要改新路径据称已隔离的那份代码 |

**范围隔离**：一个消费者的缺口**不得**阻断无关的已验收路径。若某处置声称阻断他人，
`assert_scope_isolation()` 直接拒绝——那会让本机制难用到被关掉，与目标相反。

## 12. 逐批切流清单与 dry-run（F.0.6）

**切流的单位是「批次」而不是「边界」。** 操作员不会去切一条边界——他切的是一组消费者，
一次一批，各有观察窗口与退路。这个翻译由 `workflow/batch_manifest.py` 持有，与
`cutover.py` 分开：控制平面管「某条边界的路由**是什么**」，批次清单管「哪些消费者一起动、
怎么盯、怎么退回来」。

```bash
ats batch list                                  # 清单（Markdown）
ats batch list --json                           # 机读
ats batch dry-run                               # 全量 dry-run（不改任何路由）
ats batch show --batch-id batch-research-read   # 单批 + 其 dry-run
ats batch drift --batch-id <id> \
    --qualification-status ineligible --serving yes
```

### 12.1 五类批次

| 类别 | 覆盖 | 边界 | 备注 |
|---|---|---|---|
| `collection_publish` | 采集发布链 | `projection_read` | **已在生产运行 → 直接核验接收，不重复切换** |
| `research_read` | 六个分析角色 | `projection_read` | 只读；风险是「读错」而非「下错单」 |
| `schedule` | 调度 | `dispatcher_schedule` | 改的是「何时跑」，失败模式是重复执行 |
| `internal_state_approval` | Chief/Risk/Trader/Clerk | `approval_lifecycle` + `clerk_publication` | **两条边界必须同进同退**（审批与发布分离则该审批无人能审计） |
| `live_trader` | 真实下单 | `live_trader` | 起点 `disabled`，另需独立可审计的实盘授权 |

`collection_publish` 不带任何消费者——它没有谁的读取路由要动。给它登记「旧路由/新路由」
会**暗示一次不该发生的切换**，故 `declare` 会直接拒绝。

### 12.2 当前实测：1/5 可切换，0 个 ready

```
共 5 批 · not_switchable 2 · blocked 2 · verified_in_place 1
```

| 批次 | 结论 | 首要阻塞 |
|---|---|---|
| `batch-collection` | `verified_in_place` | — |
| `batch-research-read` | `blocked` | 六分析角色 0/6 eligible |
| `batch-schedule` | `not_switchable` | **无已演练回退** |
| `batch-internal-approval` | `not_switchable` + blocked | 无回退证明；0/4 eligible |
| `batch-live-trader` | `not_switchable` + blocked | 无回退证明；trader 0/1 eligible |

**`not_switchable` 与 `blocked` 是两件事，刻意不合并**：

- `not_switchable` = **没有安全退路，因此不得排期**。回退未演练、回退目标已退役、回退
  不可用，三者都属此列。未演练的退路**不是退路**。
- `blocked` = 门禁说现在不行（资格未取得、引用的影子报告不可引用）。

把无退路的批次报成 `blocked`，会让运维以为「补齐证据就能切」——那是错的，补证据不产生
退路。

### 12.3 dry-run 的保证

**不改任何路由。** 它没有任何能移动路由的参数，唯一写入是追加式的 dry-run 账本——那是
对世界的**读数**，不是对世界的改变。操作员会在决策前反复运行它，所以「只读」必须是结构
性的而不是承诺。dry-run 账本**追加不可改**：`blocked` 也记录，因为「查过了，不行」在
阻塞项后来解决时正是操作员需要的读数，也让「复查」与「首次查看」可区分。

**资格是重读的，不是记住的**（8.4）。资格带 TTL 且可撤销，所以每个批次在被检查的当下
重新判定。同一批声明、两次 dry-run、结论不同，说明世界变了——这正是它要暴露的。

### 12.4 资格漂移时停还是退（8.4）

| 当前资格 | 动作 | 理由 |
|---|---|---|
| `eligible` | `hold` | 未变 |
| **被撤回**（`ineligible`/`revoked`/`*_drift`/`evidence_missing`） | **`stop`，永不 `fall_back`** | 回退本身就要经过那道刚消失的资格门 |
| `degraded` + 承载流量 + 回退已演练 | `fall_back` | 唯一可达回退的情形 |
| `degraded` + 不承载流量 / 承载状态未声明 | `hold` | 没在服务就没有可退的东西 |
| 未知/无法识别的状态 | `stop` | 理由写「门禁答案读不到」，**不冒充「被撤回」** |

第一行是本节的重点：**劝一个执行不了的回退，等于把操作员送去别处。**

### 12.5 完整自动交易仍需一切齐备

批次的「可切换」**不等于**可以自动交易。完整自动交易仍要求全部必需输入与审批链同时满足，
且 live 批次另需独立实盘授权。缺项只阻断受影响范围——不得据此放行其他批次，也不得为使其
可切而降低证据标准。

## 13. 读路径分批切换（11.5，需部署授权）

与第 12 章的 dry-run 分开：那一章**不能改任何路由**，这一章**会改**。所以本节的每
个动作都要求一条已登记的部署授权，且默认只决定不改动。

### 13.1 部署授权是独立的产物

**门禁全通过 ≠ 可以切流。** 授权与门禁分开是刻意的：门禁回答「技术上是否安全」，
授权回答「谁批准了这次变更」。两者合并会让「所有检查都过了」冒充「有人批准了」。

一条可审计的授权必须有四项，缺一即**不可审计**（不是「较弱的有效」）：

| 字段 | 含义 |
|---|---|
| `--authorisation` | 引用标识，唯一 |
| `--authorised-by` | 授权人 |
| `--issued-by` | 签发人（与授权人不同则更可信） |
| `--valid-until` | 有效期，过期即拒 |

外加 `--scope`：授权覆盖哪些消费者。**范围必须显式**——一份「全量授权」正是窄权限
变成宽权限的路径。

```sh
# 登记一条授权（登记后才能被 switch 引用）
ats read authorize \
  --authorisation DEP-2026-10-06 \
  --authorised-by <授权人> --issued-by <签发人> \
  --scope layer,sector,fundamental,macro,technical \
  --valid-until 2026-12-31T23:59:59+08:00 \
  --note "首批读路径切流"

# 读回（关键：授权不能只存在于命令行参数里）
ats read show-auth --authorisation DEP-2026-10-06
```

**`switch` 只接受引用，不接受命令行里现写的授权**——一周后有人问「谁批准的」，
答不上来的授权不能用。

### 13.2 先决定，后改动

```sh
# 默认只决定，不改路由。可以在循环里反复跑。
ats read plan
ats read switch --batch-id batch-research-read --authorisation DEP-2026-10-06

# 确认无误后才真正执行
ats read switch --batch-id batch-research-read \
  --authorisation DEP-2026-10-06 --apply
```

**退出码**：`switch` 只有在**全部**范围都切成功时返回 0；部分切换返回 2。
部分切换不是脚本该当成成功的结果。

### 13.3 成对切换：为什么不能只切一条

`projection_read` 与 `dispatcher_schedule` **必须同进同退**。原因写死在控制平面的
`INCOMPATIBLE` 表里：旧调度写入的存储，新读者不再视其为权威。

只切其中一条会经过一个**半迁移**状态——正是 `check_compatibility` 要拒绝的那个组合。
执行器因此从控制平面的 `INCOMPATIBLE` **派生**切换链，而不是自己重写一份；两份定义
一旦分歧，执行器就会兴高采采地移动一条预检刚刚拒绝的路由。

同理，`analyst_output` / `approval_lifecycle` / `clerk_publication` 三条也是一组。
**live_trader 不在读路径执行器的能力范围内**——否则 11.1 的实盘授权门就成了摆设。

### 13.4 部分切换是正常结果

一个批次声明一组消费者，资格却是**逐个**判定的。六个里四个合格是常态：

| 处置 | 为什么 |
|---|---|
| 整批切换 | 会把不合格的消费者也送上新路由 |
| 整批拒绝 | 会因为一个消费者而拖住已就绪的四个，而修好那四个并不能解决它 |

所以执行器**逐消费者判定**，批次结论是这些判定的归约。未切换的范围**逐个点名**并写明
理由——只说「部分切换」而不列出被扣下的是哪些，等于让操作员自己重新推一遍。

### 13.5 拒绝也要留痕

`ats read history` 记录**每一次尝试**，包括被拒绝的。「我们查过了，不行」这个读数在
阻塞项后来解决时正是操作员需要的，也是「复查」与「首次查看」的区别所在。

无授权时 CLI 仍然走执行器并落账，然后拒绝——否则这次查看在历史里不存在，而它恰恰是
最可能被回头追问的那一次。

## 14. 调度切换与回滚（11.6，需部署授权）

调度路径与读路径的差别在于**危险在哪**：读路径可以按消费者逐个判定，一批里四个合格
四个不合格是可以接受的；调度**没有这种粒度**——两条路径同时发同一个逻辑触发，工作就
跑两次（两倍 LLM 花费、两份账本写入、一份自相矛盾的对账）。所以本节检查的是**时间上的
排他性**，而不是某一瞬间的一个静态结论。

命令：`ats dispatch`（不是 `schedule`——那是 Phase E 的「运行日常周期」，一个手误的旗标
就能触发真实周期）。

### 14.1 逻辑触发身份与映射

第 4 组已建立统一身份：旧 APScheduler job 与新 schedule 解析为**同一逻辑触发**，同一
`trigger_key` 至多一个所有者。7 个旧 job → 新 schedule 的映射见 `SCHEDULE_EQUIVALENTS`，
其中 `factset_weekly_ingest` 与 `factset_monthly_ingest` **归并到同一个新 schedule**——
这正是「同一逻辑触发只能有一个所有者」的实质考验。

```sh
ats dispatch ledger        # 当前账本里的全部认领记录
ats dispatch ownership     # 所有权核验
```

### 14.2 「无冲突」与「无从核验」是两件事

**账本为空时报告不会说「通过」。** 它说：

> **这不是「无冲突」，是「无从核验」。** 空账本下的「通过」不构成任何保证：没有记录就没有
> 可以冲突的证据。

这条区分是本节最重要的设计。若把「账本里没有该 key」当作「未认领，故安全」，一个从未
使用过的账本会输出全绿的核验报告，而切流就建立在没人收集过的证据上。

### 14.3 冲突与未知也需要分开处理

| 状态 | 含义 | 处理 |
|---|---|---|
| **冲突** | 两条路径同时持有活的认领 | 停掉其中一条 |
| **未知** | 无记录，或账本读不出来 | **人工查清它到底跑没跑过** |
| **缺失** | 账本里根本没有这个 key | 先让它登记 |

把未知当已完成会**漏跑一次执行**；当作未完成会**跑两次**。两者都不能自动决定。

### 14.4 回滚去重按执行记录，不按时间窗口

```sh
ats dispatch record-execution --trigger <key> --execution-ref <ref>
ats dispatch rollback-plan
ats dispatch rollback-history
```

**绝不用时间窗口近似**：「跳过最近一小时内的」会连带跳过那些**只是在该小时内被排程、
实际没跑**的触发，于是留下一个空洞——它会在很久之后表现为一份缺失的报告，且无迹可循。

`rollback-plan` 会报 `dedup_sound`。**它是 `false` 时回滚会拒绝**，因为执行记录不完整时
回滚等于把一切重跑一遍——那比不回滚更糟，而且悄无声息。无记录的触发列为 `unconfirmed`：
**没有记录不等于记录了「没跑」**，这两者只有人能分辨。

### 14.5 切换顺序不可交换

```
freeze → inventory → dispose → prove → switch
```

在 inventory 之后才 freeze，会让某个触发在两者之间启动且永远得不到处置。
在证明所有权之前就切换，等于把调度交给一个可能已经持有活认领的路径。
`apply` 缺省为「只冻结与清点」——冻结之后仍可能有新触发，所以真正 apply 前必须**立刻复查**。

## 15. 交易路径切换与实盘授权门（11.7，默认不切实盘）

本节交付的是**门禁与演练**。真实 live route 切换需另行取得 `LIVE-` 实盘授权。

### 15.1 三种授权不可混用

| 授权 | 批准了什么 | 记录在 |
|---|---|---|
| **部署授权** `DEP-*` | 可以改路由 | `read_cutover` / `schedule_cutover` |
| **执行授权** | 某个决策修订通过风控与人工批准 | `AuthorizationLifecycle` |
| **实盘授权** `LIVE-*` | **可以向券商发真实订单** | 本节 |

前两者都说「系统认为这个变更是安全的」，只有第三者说「有人批准了动真钱」。
design 决策 12 已写明规划批准与前置通过**均不构成**实盘授权——本节把它变成机制而非文档里的一句话。

```sh
# 登记实盘授权：七项缺一即不可审计
ats live authorize --reference LIVE-2026-10-07 \
  --issuer human --authorised-by <授权人> --issued-by <签发人> \
  --scope trader --environment live --account <账户> \
  --valid-until 2026-12-31T23:59:59+08:00
```

**授权人与签发人分开记录**：同一人批准自己的动作是较弱的控制，而分开记录零成本。

### 15.2 门会拒绝的五种情况

| 拒绝理由 | 含义 |
|---|---|
| `no_live_authorisation` | 无实盘授权。**即使部署授权与全部门禁都通过** |
| `live_authorisation_not_auditable` | 缺字段，或拿 `DEP-*` 来请求实盘切换 |
| `live_authorisation_self_issued` | 影子/纸面运行自签。**它们可以「看」真实授权，但绝不能自己「发」** |
| `live_environment_mismatch` | 纸面授权不覆盖实盘 |
| `live_account_mismatch` | 授权给 A 账户不覆盖 B 账户 |

每种拒绝有独立理由码，因为一整面相同的文字对操作员毫无用处——他需要知道**该修哪一道门**。

### 15.3 影子运行看到真实授权的方式

```python
label = live.as_shadow_reference(auth)   # "shadow:LIVE-…@live/U123 until …"
```

带 `shadow:` 前缀，**不能**通过门。真实授权对影子运行是**对照物**，不是授权来源。

### 15.4 演练（11.3）

```sh
ats live drill run --drill-id live-2026-10-07 --environment paper --account DU1
ats live drill rollback --drill-id live-2026-10-07 --environment paper
```

演练调用**真实的** `route_switch.perform_switch`，走完冻结 → 排空 → 提升代次 → 开放四步。
但有两件事必须成立：

**演练库与真实库是不同的文件。** 演练记录若与真实切换记录混在一起，在有人问
「这次是授权过的吗」的那一刻，两者无法区分。演练库后缀 `.drill.sqlite`，每条记录都带
`mode='drill'`——真实切换记录**没有这个字段**。

**演练永远打不开实盘门。** 演练会**咨询**它（证明门确实在路径上），但无论给它什么授权
引用都必须被拒。演练若能打开那扇门，它拿到的授权就是真的了。

### 15.5 每次演练都必须说清「未证明什么」

```
本次演练未证明的事
- 只验证切换协议本身：四步的顺序与可恢复性
- 不验证券商侧行为（连接、报单、成交、部分成交、拒单）
- 不验证实盘授权会被批准
```

**演练通过不等于实盘切换获准。** 一份不说自己没证明什么的演练报告，读起来就像证明了全部。

### 15.6 回退目标不可用时保持现状

演练中把回退标记为不可用时，**通过的结果是「没有切换」**。强行切到一个不可用的回退
目标，等于把一次故障换成另一次故障。

## 16. 消费者清零、数据对账与墓碑一致性（11.8）

命令：`ats retirement`（**只读，不改登记表**）。它产出判定，以及当某条目判据已齐时
**可用于标记 retired 的措辞**——把措辞写进登记表是另一个刻意分开的动作，因为在部分
证据上悄悄晋级，正是 12.6 要防的事。

### 16.1 三个问题必须分开

| 问题 | 判据 |
|---|---|
| 还有消费方经旧实现读写吗 | 逐登记消费方核验 |
| 两条路径在**旧实现所涉全部用途**上一致吗 | 全用途对账 |
| 声称 retired 的条目真的读不到数据吗 | 墓碑 vs 实际行为 |

### 16.2 清零：未知不是零

三种状态，不是两种：

| 状态 | 含义 |
|---|---|
| `zero` | 该消费方已触达受治理读取面 |
| `not_zero` | 仍在旧路径上 |
| **`partial`** | **无法确定**（未提供扫描器，或路径无角色映射） |

**`partial` 阻断退出。** 「没查」不等于「查过没有」——若把无法确定算成零，一个从未被
检查的消费方会让条目退出，而它从未提供过证据。

**差异比对路径本身下线前也保持 `partial`。** 那条比对路径正在读两条路；它在的时候
「旧路径无人读」是用还连着旧路径的仪器测出来的。

### 16.3 对账：单一指标不算对账

```sh
ats retirement reconcile --identifier X --uses write,admission,read,lineage,completeness,reconciliation,rollback
```

只对一项 → **拒绝**，且理由写明「对账一个指标只能证明一个指标」。未对账的用途可以悄悄
漂移，很久之后表现为一份自相矛盾的报告。

**已退役的数据不可读 → 不合格**：旧路径自己的输入都没了，一致性无从建立。

### 16.4 回滚窗口：两个条件都是证据问题

未经演练 → **拒绝**（未测试的退路不是退路）；观察窗口内触发停止条件 → **保持 pending**
（回滚本身成功了，但窗口不干净）。

### 16.5 墓碑：声称 retired 却仍可读 = 失败

这是唯一能**否决一个登记表认为已完成**的检查，也正是它的价值：一条错误的「已完成」比
一条 pending 更糟——它告诉下一轮的人活已经干完了。

### 16.6 回退目标先校验，且顺序重要

```sh
ats retirement fallback-precheck --target legacy
```

退役的墓碑**不是目的地**。被拒时**不执行回退调用**——调用一个无法服务的路由再报错，
等于把一次故障换成另一次故障。

## 17. 真实 TWS 只读核验记录

任务 7.8 要求记录真实 TWS 只读核验。**本次未执行**，原因是本轮交付的是门禁与
核验机制，且真实券商连接只读核验需在有 IBKR 网关可达时进行。当前可核验的是模拟
路径：`ats ibkr`（只读探针，账户 + 持仓）与隔离核验中的券商模拟。

因此本节记录的是**未执行**这一事实，而不是伪造一份记录。`clerk` 消费者的
`broker_read` 与 `reconciliation` 两类证据在真实 TWS 只读核验完成前保持未登记——
这与其余九个消费者一样落在 0/10 之外，且不因此阻断本阶段其余工作。

## 18. 三边界独立回滚演练与影子期验收（11.9 / 13.1–13.5）

命令：`ats drill`。**本命令不会切实盘、不会改生产路由**，因此没有 `--apply`。

三条边界**各自独立**演练。一条「回滚正常」覆盖三边界的报告，会在某一条边界
回退已坏的情况下仍然通过——因为另外两条把它带过去了。每条边界报自己的结论，
任何一条都不能借用另一条的证据。

每条演练写自己的库（由它所演练的界面派生 `*.drill.sqlite`），不碰生产库。
演练记录与真实切换记录放在同一个文件里，在「这个是谁授权的」这个问题上二者
无法区分。

### 18.1 读边界回滚（13.1）

```sh
ats drill read-rollback --drill-id <id> --batch-id <batch> \
  --cutover-db var/phase_f_cutover.sqlite --batch-db var/phase_f_batches.sqlite
```

`--cutover-db`（控制平面）与 `--batch-db`（批次清单与部署授权）是**不同文件**。
从错的文件读批次会把每个批次报成不存在，从而用一个住在错误数据库里的理由
阻断演练。

演练检查：整条**配对链**回到 `legacy`、结果组合仍相容、读者实际由 `legacy`
服务、**影子/审批/账本记录未被删减**、回退在边界历史里留下带理由的两条记录。

配对链由控制平面的 `INCOMPATIBLE` 表推导，不由本模块写死。只回退链上的一条，
正是 `check_compatibility` 要拒绝的半迁移中间态。

**记录保全的计数从真实 store 读**，不采信调用方给的数字——调用方给的是关于
证据的**声明**，而这条检查问的是证据是否**幸存**。零记录时演练被拒绝：什么都没
删不等于保全了证据。

### 18.2 调度边界回滚（13.2）

```sh
ats drill schedule-rollback --drill-id <id> \
  --trigger <logical-trigger> --executed-json '{"<trigger>": ["<exec-ref>"]}' \
  --authorisation DEP-... --batch-db var/phase_f_batches.sqlite
```

去重键是**已执行记录**，不是时间窗：某个触发只是被排进窗口而没有跑，跳过它会留下
一个真实的空洞，直到很久以后才以「缺一份报告」的形式暴露。

- **执行记录不完整 → 拒绝回退**，这是通过结论。把「没有记录」当作「没有执行过」
  会让一次回退把每个触发都跑第二遍，且不报错。
- `--unconfirmed` 由调用方声明的未确认触发**同样阻断**，即便交给 `plan_rollback`
  的那份记录看起来是完整的。plan 只能看见交给它的触发。
- 授权从 `--batch-db` 读（部署授权与批次清单同库），不是从切换记录库读。

### 18.3 交易边界回滚（13.3）

```sh
ats drill trade-rollback --drill-id <id> --environment paper --account DU1 \
  --route-db var/phase_f_routes.sqlite
```

回退委托给 `route_drill` 而非重写：四步顺序的保证在 `route_switch` 里，第二份
实现可能与测试一致而与真正上线的协议不一致。

**回退与前向切换需要同等授权。** 没有 `LIVE-*` 实盘授权时回退不执行——这是
通过结论，不是失败：回退改变的是「谁可以发真实订单」。

`--fallback-unavailable` 时演练验证的是**保持现状**。强行切到一个不可用的回退
目标，等于把一次故障换成另一次故障。

### 18.4 端到端可追溯性（13.4）

```sh
ats drill traceability --run-id <run> --json
```

每笔影子或实际订单必须能追到**研究快照、决策修订、风控审查、人工批准**四条链。
缺任一条即 `untraceable`，其所在的切换判为**不合规切换**。

三种状态不得折叠：

| 状态 | 含义 | 处置 |
|---|---|---|
| `traceable` | 四条链齐备 | — |
| `untraceable` | 缺某条链 | 阻断；补证据后重核 |
| `unreadable` | 证据读不到 | 阻断；**这是「查不了」不是「没问题」** |

被**驳回**的批准不算批准；**来自其它 revision hash** 的审查/批准不满足本单。

### 18.5 影子期无未审批真实下单（13.5）

```sh
ats drill unapproved-orders --run-id <run>
ats drill acceptance --run-id <run>      # 13.4 + 13.5 一起
```

主要证据是新路径的**券商提交调用与拒绝审计记录**、能力校验结果、隔离演练的
券商模拟接收记录。

**`trades` 未污染是独立验收，不是主要证据。** 从未被写入的账本无法证明订单没有
到达券商又返回——因此：

- **券商有回执而本地无记录** → 停止条件。`trades` 检查看不见这一种。
- **旧路由合法成交 / 迟到成交** → **按归因排除**并记录归因，不删除。人工自己的
  券商成交与迟到成交在窗口内是预期内的；**不被预期的是它们被归因到影子运行**。
  归因取自 `fills.origin`（不在 `trades` 上；查 `trades.origin` 会在每个真实
  store 上抛错，把「账本干净」变成「检查跑不了」）。空 `origin` 落入
  `unattributed`——§11.1：未知既不是 system 也不是 manual。
- **下单尝试一次都没有** → `prohibition_not_exercised`，**不是通过**。没到达下单
  调用点的运行不能用来证明它会在那里被拦住。

### 18.6 演练记录与未证明的事

```sh
ats drill history --cutover-db var/phase_f_cutover.sqlite --drill-id <id>
```

每条结果都带 `limits` 字段，写明本次演练**未**证明什么：消费者是否真读到旧数据、
券商侧行为、实盘或部署授权是否会被批准。**演练通过不等于切流获准。**
