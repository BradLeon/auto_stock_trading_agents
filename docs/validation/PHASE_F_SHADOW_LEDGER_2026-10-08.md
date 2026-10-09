# Phase F 实际隔离入口与影子账本

日期：2026-10-08。实施范围：7.1 → 3.7。以下实施前影响记录保留；最终结果见后文。

本轮复用 isolated_verification、isolated_run、实际 Chief/Risk/Trader、broker 进程禁令和追加式影子账本。拟补实际入口的 task-local store 隔离、退出审计；明确选择影子拒绝传输，经真实 IBKR submit facade 的进程禁令拒绝并记录，不开启 C3；实际 Trader 的已批准意图及拒绝绑定 run/cycle/revision/hash/approval/route/generation。Chief 显式向影子账本发布，错误调用 trades 写点直接拒绝，不暗中重定向。无网络 FakeBroker 完整提交模式继续独立适用。

拟改文件：workflow/intake_verification.py、workflow/shadow_ledger.py，新增 execution/shadow_execution.py；trader/execute.py、graph/chief.py、memory/store.py 为实际出口的最小接线改动。后两者/Trader 及其依赖改动会影响 Risk/Trader 的代码证明；现有资格不豁免漂移，旧材料保留，7.17/8.6 在最终冻结后补验。本轮不改 manifest、consumer_api、价格政策或生产资格/路由/授权。既有未提交改动保留，修改前 hash 与生产只读库存见 phase_f_shadow_ledger_20261008/before.json。

本次用户已明确授权 7.1 与 3.7 的实现，以上真实写点/Trader 接线是任务规定行为的必要改动。最终结果、测试与尚未完成的 7.2/7.4/7.5/3.9/3.10 会在完成后回写，不把合成行情的单链样例当作完整动态角色验收。

实施中补充影响：isolation.py 校验目标与生产默认/当前路径不重合，并恢复原 grant；schemas/memory.py 为返回结果增加隔离来源标记，execution/simulation.py 为模拟回报保留该标记，memory/store.py 在发布时核对实际连接。Clerk 将影子误写拒绝作为停止异常传播。以上是 7.1 的“结果不可直接放行”及 3.7 实际发布点拒绝所需接线，价格/权限契约不变；Risk/Trader/Clerk 的相关证明需在最终冻结后重验。

## 验证结果

结论：**7.1、3.7 完成；56/116 完成，60 项待办**。实际 callable harness、独立追加账本与发布点拒绝已接线；完整十角色动态验收、7.5 决策恢复链及 3.9/3.10 双跑未完成。生产 C3 不变，没有 IBKR Paper/live 网络提交。

`run_isolated_entry` 显式覆盖外层 task-local store，完整重定向与进程禁写后才运行业务 callable。路径相撞在建库前拒绝；重开已初始化隔离库不会重做历史回填；成功/异常退出都检查生产审批、route、资格、trades 等表的计数与完整行摘要。生产并发变化保守地使验收失效，不自动清理或归因。返回记录为追加式，`tradable=false`，恢复原 grant。

`shadow_execution` 只在完整隔离下显式选择。实际 Trader 的授权、审查/批准、完整修订及 A 报价门禁继续执行，随后真实 IBKR facade 的进程禁令在 session 前拒绝。正常拒绝使用 `rejected` + `shadow_refused` gate outcome，不记为已成交或技术失败。独立账本按 run/cycle/revision/sequence 固定意图；精确订单、授权、route 及审批依据存 provenance，执行报价/拒绝 ID、时间、PID、原始 guard audit 存追加 attempt。重复调用不覆盖意图，发生不同内容的同 ID 重试拒绝。

Chief 显式发布影子结果，不向 trades 静默重定向；`save_trades`/底层 INSERT 与 fills 发布点拒绝影子来源。Shadow/FakeBroker 的返回值携带来源，跨 JSON/上下文退出后仍拒绝写入生产或另一隔离根；Clerk 对发布拒绝停止，不继续执行后续步骤。模拟成交模式保持其实际账户/grant/代次/批准门禁，不能作为真实提交能力。

### 可复现的非空业务证据

通过 uv 执行 [verify_business.py](phase_f_shadow_ledger_20261008/verify_business.py)，调用实际 Chief 风控/修订/审批节点及 Trader、Chief persist。外部行情、账户/Risk 评估输入和人工通道为显式本地 fixture，不预写 submitted/filled 订单。

[业务证据](phase_f_shadow_ledger_20261008/business-evidence.json) 保留 actual entry IDs、run/cycle、两次隔离 attestation、意图与尝试：**1 笔 AAPL 意图、1 次真实 facade 禁写拒绝、0 submitted、隔离 trades/fills 为 0**，本地修订/审查/批准各 1 行。新 Python 进程从独立账本重建同一归因，原记录不变。[账本 SQL 备份](phase_f_shadow_ledger_20261008/shadow-ledger.sql) 保存原始内容与追加约束；这是基础单链样例，不是完整分析/多轮/部分成交/迟到回报验收。

### 测试与限制

最终去重：门禁功能及相关回归 **327 passed / 0 failed**（相关集 289、来源专项 16、文档/CLI 守卫 37，存在重叠）；扩大 intake 集 **72 passed / 3 failed**，三条在本轮前源码对照中同样失败。源文件/生产库存核对见 [verification.json](phase_f_shadow_ledger_20261008/verification.json)。初始失败和修复后 JUnit 全部保留。OpenSpec strict、ruff F/I 和 git diff --check 通过；生产只读库存前后相同，manifest/API/价格政策的已捕获源文件未变。Clerk 与报告模块未在 before.json 采集改前 hash，最终源文件 hash 单独登记，不宣称这两项有本轮改前字节对照。功能集初次最终复测 **146 passed**，包含真实 Clerk/Chief/Trader、跨进程重建、路径/旧连接拒绝、上下文恢复、FakeBroker 不泄漏与生产 C3 回归。

扩大检查曾为 **4 failed / 253 passed**：三条完整性规则用例仍需要 7.4 的实际规则接线；[intake_reference.py](phase_f_shadow_ledger_20261008/intake_reference.py) 使用与本轮 before.json 字节 hash 一致的 HEAD intake 源码，当前其他代码/测试不变，同样 **3 failed**，见 intake-baseline.junit.xml。这仅是命名模块对照，不能解释为全仓或前 Phase F 零回归。第四条是 runbook 将历史静态 digest 当作当前证据，现补当前工具索引并让守卫校验当前章节，历史章节/原摘要保持原样；静态索引仍不证明动态接入或生产资格。

**后续注意/待修复项**：7.2 依赖 9.3/9.6 的完整角色消费入口，继而 7.3/7.4；7.5 还依赖 7.11/5.7，3.9 还依赖 4.6。三条完整性红项、完整角色 refs/投影链、多轮与部分/迟到成交、真实新旧双跑及最终冻结后的 Risk/Trader/Clerk 资格补验继续待办。

**为何现在不修**：用户本轮指定 7.1 → 3.7，按依赖完成隔离入口和影子账本基础；不跳过前置条件直接勾选 7.4/7.5/3.9/3.10，也不以样例成功放行生产。必要业务接线的受保护面改动纳入原始影响记录，旧证据不改写，manifest/API/价格政策不变，资格不豁免漂移。
