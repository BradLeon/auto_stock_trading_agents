# Phase F 5.16：持久部署许可与逐次调用（2026-10-10）

依据 [TARGET_WORKFLOW_DATAFLOW.md](../TARGET_WORKFLOW_DATAFLOW.md) §7/8/10/11/13/15，以及用户“上线批准一次，长期运行直到主动停用”的裁决。本项交付工程实现和隔离回归，**没有签发生产资格、签核报告、激活生产路由或连接/写入 TWS**。

## 已实现的协议

正式持久联合部署请求必须显式声明 `deployment_policy: until_stopped`，每个成员使用 `time_policy: per_invocation`，同时明确合法消费者用途 `deployment_purpose`。workflow、domain/consumer/contract、实体集合、边界、规则和预期代次随规范请求被摘要绑定到不可修改的授权记录；只读取当前代次对应的 committed 操作。历史有限许可继续按原期限检查，不延展原记录，也不将一个远期窗口当成永久授权。一次部署动作凭据到期禁止再提交动作，但不使显式持久运行许可到期；主动撤销仍逐次生效。

普通分析用途为 Layer `layer-review`、Information `information-brief`、Sector `sector-review`、Fundamental 所选 `fundamental-routine` 或 `fundamental-event`、Macro `macro-review`、Technical `technical-review`。Chief/Risk/Trader/Clerk 使用各自角色用途。用途必须属于对应消费者，不能换名借用另一模式或消费者的资格。批准域内只允许下列明确变换；其他 scope 字段逐项精确相等，不使用近似匹配。

Fundamental 统一入口按实际 request/CLI `mode`，或既有 PEAD `phase=score` 解析 Event 用途；不靠统一入口的函数名称猜测模式。嵌套调用也必须与原绑定用途一致，Routine 许可不能借给 Event，反向亦同。Clerk 的同一调用绑定不能更换原记账查询窗口；跨日新请求生成新的实际窗口，不刷新旧 worker 的代次或截止。

| 变换 | 明确约束 |
| --- | --- |
| 连续 tick / 跨日 | 独立 `as_of` 与实际输入/query scope；部署身份不随时点变化 |
| Event 版本变化 | 部署明确 `event_rule.route_kind` 与当前 `event_routes.yaml` 摘要；实际 Event/version 来自只读日历，必须是当前、已发布可见、来源质量可接受、满足材料与路由规则的版本 |
| Risk/Trader 的 cycle 身份 | 显式 `invocation_rule={kind: decision, id_source: cycle_id, entities: subset}`；部署批准的实体集合固定，实际订单实体必须是其子集；输入也只能读本次订单实体。真实决策入口从冻结研究要求恢复所属 workflow，交易 revision/review/Boss 批准另行验证 |

固定 Event/version 授权不能与规则授权混用，也不会放行其他版本。Event 更新不要求重复批准部署；旧 worker 的输入/事件绑定改变或日历版本过时仍拒绝。规则配置变化、实体/用途/Workflow/边界扩大需要新的明确部署请求和批准。

## 实际接线与记录

`read_windows.bind_plan` 为实际计划的每个必需 task scope 和 Chief 生成稳定 read identity 与逐次 `read_invocations`，保留实际事件、独立时点和 run 引用。Dispatcher 在执行、复用之前验证，实际 adapter 与 Chief 入口使用同一绑定。独立角色入口自动选择匹配的持久登记；不存在这种登记的 legacy 独立入口沿用其原守卫，不借默认 legacy 身份获取目标数据。原有限 explicit scope 保留原语义。

Risk/Trader 决策装饰器绑定真实 cycle 和实际行动实体，Clerk 独立入口保存当次查询窗口；运行时 native 数据接口必须在明确绑定下读取，消费者、实体、输入截止和 Event 规则逃逸均拒绝。窗口推进不重置 logical trigger 身份；已完成触发不会因为许可换绑而再执行。

每次读取与实际发布前重查许可未撤销、当前资格、签核报告适用性、停止状态和代次。Input 的来源时间/新鲜度继续由原治理接口与 Trader A 执行；部署许可不替代当前行情/账户，也不替代单笔交易审批。报价接口原有的 `purpose` 继续表示审批前/批准后的报价阶段，不与部署用途复用；持久模式的报价阶段保留该角色的原调用绑定，币种、订单意图和审批条件仍由 PriceRequest 核验。资格失效不会删除部署许可；已经持久停止的范围不能仅靠刷新证据恢复。

Technical 的实际 Runtime Gateway 历史行情读取也追加真实 bar/source 时间、原始 queried_at、symbol/period 与内容摘要；不是以请求时点替换来源时间。Dispatcher 的新执行、投影复用、共享 trigger 复用均进入同一许可绑定，复用重新检查当前权限与原投影的新鲜度，并记录原投影引用/时间/摘要。复用保留原投影的 as_of，不生成伪造的新输入；没有实际消费记录的调用仍标为未验证。

业务证券行情请求必须属于本次实际实体，提供者响应也不得增加未请求 symbol；越范围在提供者调用前拒绝。现有 Technical 策略固定使用的 `^VIX`/`^VIX3M` 是受保护代码声明的辅助指标，单独记录实际请求与返回时间，不加入可交易实体或扩张 portfolio 权限。

控制库追加 `read_invocation_receipts`：保存部署与实际调用身份、workflow/部署用途、as_of/query window、Event 来源/版本、run/执行 plan hash、许可/报告引用、资格与报告 scope、read/publication/recovery generation、原始实际读取 refs/source_as_of/queried_at/payload hash 和整次读取摘要。回执不可 UPDATE/DELETE。未实际读取的入口单列 `read_status=untested`，不把空记录当成消费覆盖；嵌套诊断 trace 不会截断实际入口回执。隔离记录标明运行模式，不能作为生产清零证明。

## 时点修正

新 Dispatcher 输出的 Task Projection 描述冻结请求的逻辑 `as_of`，`created_at` 保留实际产出时间，source/data vintage refs 不变；不修改历史投影或原始证据。Chief 按同一输入截止读取固定分析投影；后续决策要求校验仍用当次检查时点计算新鲜度，并核对原固定 cutoff，不能借逻辑时钟延长交易行情/账户有效性。

这一修正解决“固定 as_of 的新输入已经产生，但 Chief 又用变化的当前时间读取，导致绑定截止不一致”，以及分析结果把稍后的产出时间当作本请求输入时点的问题。实际全链回归验证该规则，不以旧结果作为正确性基准。

## 使用示例与边界

Technical 单实体稳定 scope 示例：

```json
{"kind":"entity","id":"COHR","entities":["COHR"],"time_policy":"per_invocation","deployment_purpose":"technical-review"}
```

Event scope 在相同基础上声明 `event_rule={route_kind,config_sha256}`，不预填某个真实事件 ID/version。每次调用绑定真实发布记录；没有日历、材料、当前版本或精确映射保持拒绝。Rule digest 必须取实际配置文件摘要，不能随意填写。批准的是固定范围，不能用一个 portfolio 范围推定所有实体任意可用。

6.4/6.7 的 intake/preflight 必须使用最终实际部署 scope 和当前适用的新报告，不能将阶段一的示例点身份直接升级；报告及原始证据时点不改写。当前实现闭包新增 `invocation_bindings.py` 和相应测试，共享指纹面影响全部十消费者。5.16 后仍需 6.1 最终冻结、6.4 接收真实证据、6.6/6.7 当次资格，以及正式签核/部署和观察；本项不是上线批准。

## 验证证据

原始定向日志与 JUnit 保留本地 `docs/validation/phase_f_persistent_runtime_20261010/`，按用户规则不入 Git。中间失败保留，用于定位 legacy 入口误伤、回执导入、Chief 固定时点接线及实际行情/复用回执缺口；不作为最终通过证据。新增集成测试的导入/构造错误也保留原始记录，修正后重跑，不据此排除产品失败。

并行补验的 Routine 链曾因真实报价超过 60 秒被 Risk 拒绝；拒绝证据保留，期限与报价时点未修改。相关并行过程及随后尚未补齐入口记录的串行过程已中止，不称为完整通过；最终回归改为串行。两个实际持久入口测试在 `native_recording_ready.*` 中通过：治理报价拒绝其他 cycle，以及真实 Technical Dispatcher 两次请求/实际读取/复用在一个不可修改的部署许可下运行。

扩展串行回归 `final_serial.*` 得到 423 passed、6 failed：六项均为 Phase E 假适配器单测缺少正式读部署许可，被生产 admission 拒绝。修正是假适配器使用物理隔离候选环境，保留生产缺许可拒绝；未添加全局 target、伪资格、跳过或豁免。该六项和当次全部模式/窗口协议在 `final_mode_and_dispatcher.*` 的 60 项复核中通过。最后新增 Clerk 嵌套窗口反例并在最终受影响范围重新测量；最新结果记录如下。

| 最终核验 | 结果 |
| --- | --- |
| `final_scope_recheck.*`：实际持久入口、模式/窗口/用途边界、Phase E DAG/复用/超时、读窗口协议、固定快照、业务发布守卫、独立 runner | **113 passed**，包括 Routine/Event 完整采集和跨进程离线重放；10 个已知 JUnit `record_property` 格式警告 |
| 按 `(classname, name)` 合并 `final_serial.xml` 与较晚的 `final_scope_recheck.xml` | **432 个唯一用例当前通过，0 个未闭合失败/跳过**；最后受影响范围复核覆盖全部六个早期失败。不是单轮全仓全绿，也不替代 13.6 的全量逐例归因 |
| 当前保护闭包诊断 | **545 文件、十消费者**，无未登记实现依赖；诊断未保存为 6.1 的正式冻结 |
| OpenSpec strict、`git diff --check` | 通过 |

测试均使用项目权威入口 `UV_OFFLINE=1 UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache ./scripts/run_tests.sh … --junitxml=…`，由 uv 同步全部 extras 后执行；各日志包含独立 basetemp。最终源代码上的 113 项复核后只更新规划/报告文档，未继续改变实现或测试。

5.16 已勾选；change 当前 **99/117 完成、18 项待办**。下一步为 6.1 最终冻结，再推进 6.4 → 6.6 → 6.7 的实际资格接收/验证；签核、生产部署、观察和退出仍按各自依赖与授权进行。本项没有签署生产运行批准，未执行 TWS 操作，未改变生产 C3。
