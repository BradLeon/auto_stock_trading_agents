# Phase F 13.3：交易安全停止与已验收模拟路由恢复

## 实际交付

本轮在 11.3 的实际交易仲裁上新增 `restore_stopped_simulation` 隔离恢复入口。安全停止仍使用实际 `freeze_submissions`：持久冻结新签发和提交，保留已有批准修订、订单、成交、仲裁回执和历史，不切回旧交易实现。

恢复必须持有原停止 token、当前 expected_generation、实际 lifecycle 和当前目标证明。目标仅限全持久化面物理隔离下的无网络 FakeBroker simulation，核验当前 transport/账户、paper 环境、隔离证明文件与完整业务实现指纹。目标 gate 在排空前、换代前和开放前重读；不能确认可用时持续冻结。gate 是隔离证明适配，不是生产资格或部署/实盘授权。

恢复在实际跨进程 authority_lock 内执行，读取全部券商意图回执和实际模拟账本的 decision cycles，不允许单周期或调用者编造的空 lifecycle 隐藏其他已批准周期/未知回执。未终结阻断；排空后换新代次再开放，不签发 grant。任何失败均不 abort 原停止；已换代但开放失败时可按同 token、当前代次和新鲜证明完成恢复，不重复换代。旧代 grant 不复活，新的订单仍需完整 Risk/人工批准修订、Trader A 执行报价与新代绑定。

## 验证内容

测试使用实际 Risk → revision/review/人工批准 → Trader A → FakeBroker 接收 → SQL trades → Clerk reconcile → 生命周期 → 停止/恢复。行情、账户、审批通道明确 fixture；没有预写 submitted/filled 冒充实际提交。

| 场景 | 实际验证 |
|---|---|
| partial / unknown | 在真实模拟接收后制造部分成交或未知回执；停止后实际签发和 FakeBroker 写点拒绝，空 lifecycle 也不能掩盖回执 |
| 次日迟到成交 | 停止期间 Clerk 只读承接、归因并重复对账；不解除冻结、不增加签发计数、不重复 fills，原 revision 不改 |
| 恢复同名已验收路由 | 先实际接收和对账建立目标证明，再停止/排空/换代 1→2/开放；同名旧 grant 拒绝，新审批和新 grant 可产生实际新订单 |
| 目标缺失/过期/漂移/账户错误/不可用 | 保持原代次、停止 token、历史及回执不变，实际出口继续拒绝 |
| lifecycle 缺失或遗漏另一个已批准周期 | 真实账本和回执核验拒绝恢复，不以调用方汇总替代在途盘点 |
| 换代后的 open 故障或证明到期 | 新代次也保持冻结；目标缺证继续停止，明确有效恢复不重复换代 |
| 竞争恢复 | 错误 token、过期代次不释放他人停止，不修改当前代次 |
| 重启 | 新 uv 进程重读原 SQL 冻结，实际签发及 FakeBroker 提交拒绝；不是进程内布尔状态 |
| 物理隔离缺失 | 恢复入口在访问生产状态之前拒绝，isolated 标签不能替代能力 |

目标证明由测试在实际模拟订单接受/成交后生成，关联同账户/环境的真实持久回执、当前源码闭包和有效期；恢复时重读原文件及回执。该证明仅支持所选隔离模拟目标，不能标记 production eligible/enforced。

## 证据及影响

原始日志/JUnit/原库定位与只读状态摘要保留于 [本轮证据目录](phase_f_trade_stop_20261009/)。首轮 [core_v1.xml](phase_f_trade_stop_20261009/core_v1.xml) 33 passed；补充全账本周期盘点后的 [core_verified.xml](phase_f_trade_stop_20261009/core_verified.xml) 127 passed。单独的停止全场景检查见 [stop_final.xml](phase_f_trade_stop_20261009/stop_final.xml)。格式整理前的阶段结果和为固定源码而中断的独立运行也保留，不作为最终版本报告。

源码修改限 `execution/route_switch.py`，复用现有 route registry/authority_lock/lifecycle/Clerk，不改 MARKET_DATA/Trader 权限、manifest、生产路由或批准记录。新 tests 和文档另列；完整源码/提示闭包仍为 465 路径，但该文件摘要改变，旧报告/指纹保持历史，不转换为当前版本签核。固定源码上的 Routine/Event capture/独立进程重放另行补验，报告保持 unsigned，最终资格仍由 6.1/6.4 承接。

结论：13.3 为隔离交易安全停止和可用已验收模拟目标恢复验收，不等于生产恢复、TWS 验证或实盘开放。

最终停止场景 **15 passed**、相关仲裁/授权回归 **127 passed**、固定源码 Routine/Event 与研究独立重放 **6 passed**、文档记录守卫 **20 passed**。这些运行有重叠，不将通过数直接相加。实际四份原报告重新生成 [动态索引](phase_f_trade_stop_20261009/dynamic_index.json)：三份可供明确签核，一份缺行情失败；全部保持 unsigned。原库和原报告没有改写。

重读原库的 [订单全链](phase_f_trade_stop_20261009/order_chains.json) 和 [提交安全](phase_f_trade_stop_20261009/submission_safety.json) 均 passed；[机器汇总](phase_f_trade_stop_20261009/verification.json) 核对当前 80/116、13.3/11.3 完成、显式依赖无环、实现摘要与生产状态。子/父 change strict validation 通过，父 change 保留既有 `sector/layer-analyst` 归档目标缺失 INFO；本轮没有执行归档。Ruff 与 `git diff --check` 通过。

生产 [运行前账本](phase_f_trade_stop_20261009/production_before.json) 与 [运行后账本](phase_f_trade_stop_20261009/production_after.json) 的 trades/fills 逐行摘要一致（52 trades、13 fills）。[运行前控制状态](phase_f_trade_stop_20261009/authorities_before.json) 与 [运行后控制状态](phase_f_trade_stop_20261009/authorities_after.json) 的 route/issuance/freeze/history、cutover、dispatch、批次/授权、release/owner/schedule 摘要全部一致；当前完整实现闭包 465，仅 `src/ats/execution/route_switch.py` 相对本轮运行前改变。

**后续注意/待修复项**：13.1/13.2/13.10 读、调度及审批/Clerk 全边界安全恢复继续待办；11.4 交易 runbook 完整收口与最终基线/资格按依赖推进。当前 API 专用于隔离 rehearsal，生产恢复不得调用该模拟证明入口或直接 abort_freeze 绕过在途/目标核验。

**为何现在不修**：13.3 只覆盖所选实际无网络模拟目标，不能用其成功替代正式生产适配或其他边界证明；没有获得 IBKR（含 Paper）写权限，生产 C3 保持停用。
