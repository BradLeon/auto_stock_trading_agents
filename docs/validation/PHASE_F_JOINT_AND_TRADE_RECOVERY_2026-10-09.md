# Phase F 11.3 / 5.15：交易仲裁与联合冻结恢复

**依赖补齐 5.4**：最终依赖审计发现原 5.4 未完成，因此先落实其 workflow/scope 条件矩阵，再收口 5.15。compatibility 门禁必须返回同 workflow、同完整身份的当前依赖边、有效证明及引用；每条边明确混合版本是否兼容。允许兼容的单边界请求保持其他边界和 SQL owner 不动，不兼容或缺证明拒绝，不能再套用全局固定三组配对。矩阵与重验结果随实际 scope/history/journal 保存。生产单 scope 写原语拒绝绕过协调器；对生效控制库的隔离写要求完整物理隔离。正式生产矩阵/报告证据适配仍由 9.2 提供，测试矩阵明确 fixture。

此补齐改变源码指纹，前面的阶段 index/报告仍保留原时点；最终当前版本以 `current.xml`、`dependency_v3.xml` 和 `dynamic_index_current.json` 为准。

最终收口以 [verification.json](phase_f_joint_trade_20261009/verification.json) 为准：**79/116 完成，37 项待办**；5.4、5.15、11.3 全部直接前置已完成，依赖图无环/未知项。最终固定源码检查为 [dependency_final.xml](phase_f_joint_trade_20261009/dependency_final.xml) **74 passed**、[regression_fixed.xml](phase_f_joint_trade_20261009/regression_fixed.xml) **208 passed**、[current.xml](phase_f_joint_trade_20261009/current.xml) **6 passed**、[records_release.xml](phase_f_joint_trade_20261009/records_release.xml) **20 passed**，合计 **308 项**，不表示全仓全绿。

当前原报告、实际输入/运行/refs 的 [dynamic_index_current.json](phase_f_joint_trade_20261009/dynamic_index_current.json) 与 [订单追溯](phase_f_joint_trade_20261009/order_chains_current.json)、[提交安全](phase_f_joint_trade_20261009/submission_safety_current.json) 重新只读复算通过；三份通过报告仍 unsigned，缺行情报告保留失败。最终 [生产账本](phase_f_joint_trade_20261009/production_final.json) 与 [生产权威](phase_f_joint_trade_20261009/authorities_final.json) 同各自本轮 before 摘要一致。两个 change strict 通过；父 change 既有 sector/layer-analyst 归档目标缺失 INFO 保留，本轮不归档。新模块与专项测试 Ruff、git diff --check 通过。

依赖補齐阶段又出现 15 条旧 scope 单测与 26 条安全读/恢复夹具失败：它们用生产缺省模式直接写单 scope，绕过新增矩阵检查。已把单测权威改成完整物理隔离、子进程显式禁写/uv、隔离激活明确 mode，保持生产原语拒绝；原失败日志和 XML 保留。正式 9.2 不能继续调用无 workflow/matrix 的生产单 scope 原语。

本轮交付真实仲裁交易演练和 exact-scope 联合协调器；生产切流、真实券商写入与最终资格不在本轮执行范围。源码/提示闭包增至 465 路径，原 464 路径报告保持原始记录，不能沿用为当前版本证明。

## 11.3 交易路径

实际 Risk/完整人工批准修订 → 绑定路由授权 → Trader A 报价 → 无网络 FakeBroker 接收 → trades → Clerk 对账 → lifecycle 排空 → route_switch。行情、账户、人工审批和模型为明确 fixture；订单接收、提交仲裁、SQL 记录、对账与路由代次使用真实实现。

- 部分成交 2 股时切换在 drain 拒绝；次日迟到成交经 Clerk 实际对账后，冻结/排空/换代/开放四步骤成功。
- 换代后旧代 grant 对新意图拒绝，旧授权校验返回路由/代次问题；已成交订单重试返回原记录，不生成第二笔订单。
- 使用同一仲裁协议恢复 simulation，代次 1 → 2 → 3；新批准、新绑定及新 grant 才能接收新订单。
- 换代后 open 故障保持冻结，持有新代 grant 也被 FakeBroker 出口拒绝；只读恢复定位换代已完成，明确恢复后才开放。

修复：route_switch 在代次已移动或权威不可读时不再 abort_freeze；生命周期终结集合与实际状态机对齐，保留旧历史状态兼容。

## 5.15 联合协议

`ats.workflow.joint_cutover.execute` 要求显式 batch/workflow、每个 boundary 与完整 RouteIdentity、预期 generation，以及明确 SQL-owner 动作和在途 disposition。独立不兼容请求拒绝，不自动扩大边界。live_trader 由独立交易仲裁协议管理，禁止混入本协调器。

控制库先提交 prepared 冻结 journal；随后同事务写全部 scoped routes 和追加历史；实际调度 SQL 库通过 freeze/handover 换 owner/generation；最终重验后才提交 committed 并解除 journal 栅栏。两个 SQLite COMMIT 没有冒称原子，跨库间隙通过持久冻结保护。

真实读入口、调度 claim 与发布事务、绑定 worker 发布及单 scope 切换重读栅栏；冻结前绑定的 worker 在解除后仍因 epoch 变化失权。缺身份的发布无法证明属于无关 scope，冻结期间保守拒绝；有身份的其他 scope 保持可用。

提交各阶段重查 exact 授权、qualification、report、fallback、enforcement 和兼容性。生产模式还必须引用控制库中绑定**整个规范请求摘要**的不可修改授权记录；缺记录、过期、撤销或不同请求均拒绝。isolated 模式只在全持久化面物理重定向且 broker 禁写生效时允许 fixture gate。execute 不签发部署授权或券商 grant。

恢复复用同 batch 与原请求，核对原调度库路径、实际代次和本 batch 的冻结 token 来源。失败保持 prepared，不删历史、不自动开放；恢复仍需当前有效门禁。已完成请求重复调用不重复换代；完成后的回退使用新显式请求、新预期代次和同一协调协议。

权威边界：scope 路由位于 ATS_CUTOVER_DB，生效调度 owner/generation 位于 ATS_DISPATCH_STATE_PATH；YAML 是安装模板，本协议不改它。release overlay 保持原映射，本轮不把全局 overlay 当 scope 激活权威，也不执行 overlay 发布。正式 CLI/报告/投影适配与调度部署仍由 9.2/10.2/10.3 承接。

真实 uv 子进程在 prepared、第一条 route INSERT、routes COMMIT、owner COMMIT 后 `os._exit(73)`：全部验证读取/调度/发布拒绝、另一 scope 可读、原请求确定恢复且不重复换代。另验整组中的一个 scope 不合格、缺接线、全局紧急关闭、门禁提交中撤销、请求变更拒绝、追加授权历史与联合回退。

## 证据与限制

原始测试、阶段失败、独立进程原库和生产只读摘要均保留于 [本轮证据目录](phase_f_joint_trade_20261009/)。专项测试 [core_release.xml](phase_f_joint_trade_20261009/core_release.xml) 21 passed；相关回归 [verified.xml](phase_f_joint_trade_20261009/verified.xml) 242 passed。

最终固定源码的 [regression_release.xml](phase_f_joint_trade_20261009/regression_release.xml) 221 passed、[independent_verified.xml](phase_f_joint_trade_20261009/independent_verified.xml) 6 passed。后者为重新 capture 的 Routine/Event 完整交易及 Technical 研究/缺行情拒绝；重新组织四份原报告的 [dynamic_index.json](phase_f_joint_trade_20261009/dynamic_index.json) 包含三份可供明确签核的 unsigned 报告和一份失败报告；[order_chains.json](phase_f_joint_trade_20261009/order_chains.json) 与 [submission_safety.json](phase_f_joint_trade_20261009/submission_safety.json) 均 passed，实际四轮非空模拟订单、提交回报、批准与 Clerk 归因可追溯。旧版索引保留，不改旧报告和签核。

本轮记录窗口的 [生产 before](phase_f_joint_trade_20261009/production_before.json) / [after](phase_f_joint_trade_20261009/production_after.json) 核验 52 trades / 13 fills 逐行摘要相同；控制库/路由代次库/调度库/部署记录库及 overlay/owner/schedule YAML 的 [before-final](phase_f_joint_trade_20261009/authorities_before_final.json) / [after](phase_f_joint_trade_20261009/authorities_after.json) 逻辑摘要一致。before-final 在收口测试之前取得，不追认首轮测试前的生产控制库状态；账本 before 也不追认更早的历史运行窗口。只读复核脚本 [authority_audit.py](phase_f_joint_trade_20261009/authority_audit.py) 不初始化状态。

初次失败包括测试误用 owner 返回类型/状态 API、错误的重复订单断言、子进程未安装内存禁写能力和异常类型断言不匹配；未放宽生产拒绝行为。回归四条旧 fallback 单测使用无 SQL 的显式模拟权威，补齐 joint 冻结模拟后通过。旧顺序全局 apply 改为拒绝，旧配对测试改验无迁移，后续正式执行器不继承此不安全行为。

完整新入口首轮 Routine 在源码修改期间触发 fingerprint drift，原运行与失败记录保留；格式整理时另一次运行中断也保留。必须在固定源码上使用新目录 capture/重放，不能修改旧报告摘要或沿用旧签核。

结论：两项交付分别为交易隔离演练和联合恢复机制，不代表生产资格、实际生产恢复或 TWS 验证完成。

**后续注意/待修复项**：13.3 的新版本安全停止/恢复验收、13.10 的审批/Clerk 全边界故障、9.2 与 10.2/10.3 正式适配和最终 6.1/6.4 仍按依赖执行。当前生产调度库仍是历史 helper schema，不能当已安装实际 runtime owner；部署必须显式安装和核验。新报告保持 unsigned。

**为何现在不修**：本轮限定 11.3/5.15，不用 fixture 授权代替生产部署决定，不把机制演练替代全范围入口验收；上述后续任务的范围和前置保持不变。生产 C3 与 IBKR（含 Paper）写禁令保留。
