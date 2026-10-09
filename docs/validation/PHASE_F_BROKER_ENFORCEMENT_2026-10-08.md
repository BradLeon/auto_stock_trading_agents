# Phase F broker 实际出口恢复验证

日期：2026-10-08。范围：任务 **1.1、2.2、2.3、2.8**；四项完成后 **43/116 完成、73 项待完成**。本报告证明启动保护和真实 IBKRBroker 实现通过模拟 transport 的工程验收，未执行生产 workflow、真实网络下单、生产路由/授权/资格变更。

## 实现与验证

| 任务 | 实际行为 | 验证 |
|---|---|---|
| 1.1 | CLI、scheduler、shadow owner、直接 shadow Dispatcher 启动先安装并断言禁令；隔离 context 把禁令传给子进程，嵌套退出恢复原环境 | 五类入口在禁令安装无效时全部拒绝；owner→实际 TriggerService/Dispatcher→IBKRBroker 和直接 Dispatcher→IBKRBroker 均拒绝，合成 Macro 分析仍发布并 complete；CLI/scheduler 入口探针、子进程禁令与撤单拒绝通过 |
| 2.2 | 缺 grant、UNSET、撤销能力、旧代、缺失/损坏权威、冻结均拒绝；每笔实际写点重验，共享互斥持续至 broker 发送完成 | 直接 broker 在无能力时不打开 session；合约验证期间冻结、批次首笔后冻结，均阻止后续提交；实际发送期间跨进程冻结等待互斥，生效后无新提交 |
| 2.3 | 当前 session 的 managedAccounts、配置预期、grant、权威账户一致；显式设置 outgoing order.account，检查回执账户；环境来自已支持账户格式 | 配置匹配但实际 DU2、空/异常账户列表、无预期的多账户、环境不符/缺字段/未知格式均拒绝；明确选择 DU1 的多账户 session 正向通过；合约验证期间账户变化被拒；回执账户不同保留 unknown |
| 2.8 | 各子进程调用真实 IBKRBroker，仅 session/transport 由 FakeIB 替换 | 两进程同意图只接收一次；同一活跃代次的不同意图正常接收；存活旧进程在换代及 A→B→A 后不能提交；缺失/损坏权威、冻结竞态、接收后退出/超时、切换进程共同旧快照竞争全部通过 |

新增测试 `test_phase_f_startup_enforcement.py` 和 `test_phase_f_broker_enforcement.py` 共 **43 项**；传输替身在 `tests/phase_f_broker_harness.py`，不替换 broker 的检查与提交实现。子进程由 uv 管理的测试进程通过同一解释器启动，没有自行创建或激活 venv。

## 测量结果与复现

相关回归 **294 passed，1 warning**。包含启动/broker 新测试，以及原 guard、isolation、registry/switch/single-active、order disposition、broker risk、ownership、shadow CLI、Phase E Dispatcher、intake 和 live drill 测试。警告来自 intake 静态扫描遇到已有字符串的无效转义，不是失败。

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync pytest tests/test_phase_f_startup_enforcement.py tests/test_phase_f_broker_enforcement.py tests/test_broker_write_guard.py tests/test_isolation.py tests/test_route_registry.py tests/test_route_switch.py tests/test_route_single_active.py tests/test_order_disposition.py tests/test_broker_risk.py tests/test_workflow_ownership.py tests/test_shadow_cli.py tests/test_phase_e_dispatcher.py tests/test_intake_verification.py tests/test_live_route_drill.py -q --junitxml=docs/validation/phase_f_broker_recovery_20261008/enforcement.junit.xml
```

证据：[回归 JUnit](phase_f_broker_recovery_20261008/enforcement.junit.xml)。文档守卫、strict、依赖图、受保护摘要和生产状态只读比较见 [verification.json](phase_f_broker_recovery_20261008/verification.json)。本轮没有重跑全量，不能据此宣称整套测试全绿或相对 Phase F 前基线零回归。

## 并发与恢复约束

冻结/换代/开放与 broker 写点共享本机文件锁，5 秒无法获取时拒绝，进程退出自动释放；锁文件不能在运行中删除。后续轮询成交不占该锁。shadow owner/命令的禁写是进程范围，不在函数返回后自动恢复；其他只读服务允许无交易能力启动。隔离 context 保留原有作用域恢复语义，并补齐嵌套环境恢复和子进程继承。

提交前持久记录仲裁回执，身份是完整 cycle/revision/sequence，换代、变更账户/symbol 或 payload 都不能重新使用同一意图。回执保留 orderRef、实际账户和链路索引；unknown/submitted/partial 阻断排空。它不是另一个授权生命周期账本，不能替代原 decision/cycle/trades。未知状态须只读对账，不自动删除、过期清空或重新提交。接收后退出的测试保留 unknown，重复尝试拒绝且切换不提升代次。

环境识别只支持当次 session 返回的个人账户 DU+数字和 U+数字；未知格式拒绝。DU 的 paper 语义参考 [IBKR 官方客服说明](https://www.interactivebrokers.co.jp/en/support/customer-service.php?p=email)，账户列表来自 [TWS API managed accounts](https://interactivebrokers.github.io/tws-api/managed_accounts.html)。这些格式规则不能代替 7.13 的真实 TWS 只读证明，也不能替代实盘外部授权。

## 状态与后续

本轮未改十条受保护指纹路径、uv.lock 或生产控制配置；只读核对生产 route/授权/边界仍与第 0 组基线一致，未给生产库创建新回执表。broker、guard、registry/arbitration、启动调用链属于新增语义依赖，最终 7.14/7.17/6.1 仍须纳入对应影响/冻结核验，不能仅因不在旧指纹清单中就默认无需重验。

**后续注意/待修复项**：5.2/5.3/5.6 等实际边界/读取接线；7.4 已裁决完整性门禁；A 行情/规范化；7.5/11.3 的实际审批执行与只读对账恢复；3.7/3.9 的真实双跑和非空提交审计；7.13 TWS 只读验证及最终冻结取证。

**为何现在不修**：本轮授权范围限定四项，其工程验收已经完成；全交易链、真实影子和生产资格依赖其他任务，不以这四项的模拟 transport 结果冒充完成。**C3 继续停用**，实际门禁总体、生产读/调度切流、实盘切流仍未验收。
