# Phase F 3.2 / 5.7：新需求矩阵与读恢复

## 实施影响与验证边界

本轮按用户已批准的新入口验收裁决推进，保留旧比较工具、旧回退工具及全部历史证据。计划增加 `acceptance_matrix.py` 和 `read_recovery.py`；实际消费接线涉及 `consumer_reads.py`、`runtime_reads.py`、`cutover_routing.py`、`cutover_wiring.py`，属于 0.1 已登记的共享读取/发布保护面。停止须覆盖尚在运行的绑定入口的发布点，不能只在下一次读取时拒绝。

这些共享面改动须在最终冻结后通过 7.17/6.1/6.4 重新核对十角色资格；新恢复证明另外绑定自身及实际读/发布控制文件摘要。不得沿用旧指纹资格、不写生产资格、不改变生产路由/调度/C3、不删除历史与审计。验证仅用独立临时控制库、隔离业务库及受控外部接口。

3.2 交付运行前固定矩阵与恢复/输入验证 API，六面断言执行、签核报告、checker 和独立 runner 仍分别待 3.3–3.6/3.10。5.7 交付真实读消费点的安全停止与版本恢复；全边界联合演练、已经组装快照的依赖重验和生产恢复仍分别待 5.8/5.15/13.x/9.5。

## 验证结果

结论：**3.2、5.7 完成**；当前 67/116，49 项待办。最终相关回归 **133 passed / 67.37s**，JUnit：[acceptance_final.xml](phase_f_new_requirements_recovery_20261009/acceptance_final.xml)。此前扩大回归包含研究门禁 **105 passed / 233.20s**，后续恢复代次补强以最终 133 项为准，不将重叠测试相加。

覆盖：

- 纯持久化研究不要求旧交易/旧侧；实际 Technical/Sector/live Macro/Event 的 runtime 不可省略，模型开启时不可丢弃模型配置。
- Event/Routine 只验证所选模式，同时固定其他必需类别、任务与 scope/依赖；重算 hash 后伪造 not-applicable 或删除依赖仍拒绝。
- 真实 Chief 内部状态入口停止后旧适配器不能绕过；停止保持，其他 scope 可读，performance、路由历史和 live-disabled 保留。
- 真实 state API 一次读取的已核验结果返回到 Chief；新版本有独立 exact-scope/contract 资格、目标实现/验收引用/证明/依赖摘要，实际 trace 保存恢复版本和 refs。目标资格在测试中受控，不宣称生产资格或自动部署历史二进制。
- 缺 adapter、目标资格失败、撤销、过期、证明篡改、退役及实际读取异常均锁定停止；恢复代次改变后旧绑定 worker 不得发布，权威状态缺失不能默认为无策略。

初次扩大回归出现 4 个无控制库的旧单元 harness 失败，已补齐其模拟的恢复权威；未降低真实入口校验。原失败 JUnit 保留：[acceptance.xml](phase_f_new_requirements_recovery_20261009/acceptance.xml)。新模块和新增测试 Ruff 校验通过；既有共享文件的原有 lint 问题未作为本轮整改目标。

**后续注意/待修复项**：3.3–3.6 的断言执行/新报告/签核 checker、3.10 新入口独立 runner、5.8 已有快照依赖重验、5.15 全边界协调及 13.x 全边界演练仍待办。读取恢复注册不签发生产资格、审批或部署授权；目标验收引用不能替代正式切流报告 checker。

**为何现在不修**：这些是独立任务及前置链，本轮范围为 3.2/5.7；提前扩大实现会打乱报告/runner/联合恢复的依赖顺序。最终冻结后按 7.17/6.1/6.4 最小补验共享指纹，不覆盖旧证据。

## API 与运行边界

- `acceptance_matrix.freeze(plan, workflow_id=..., identity=..., batch_class=..., runtime_surfaces=...)` 在运行前编译矩阵；`as_row()` 可保存，`restore()` 重构并核对当前配置；`check_inputs()` 检查固定 packet 及实际 runtime 面，完整输入恢复内容继续由 3.1 校验。此 API 不执行六面断言、不生成验收签核。
- `read_recovery.register(identity, reference=..., sha256=..., actor=..., reason=...)` 仅登记独立控制库；证明必须包含 version/identity/strategy/dependency_hashes/valid_until。版本恢复另外要求 target_version/target_identity/target_identifier/implementation_sha256/accepted_report_ref/accepted_report_sha256，并核对验收引用的 scope/version/passed 与字节摘要。
- `consume_read(..., recovery_readers={accepted_version: actual_reader})` 执行注册的新版本；默认不选择任意 reader。Chief state 入口可显式传 `InternalFallback(store, recovery_readers=...)`，已有名字仅保留 API 兼容，其结果路由和 trace 区分 target/legacy。
- 恢复撤销复用 `revoke_fallback` 的 exact-scope/引用追加接口；停止事件持久保留。重新登记会改变恢复代次，旧绑定 worker 仍拒绝，须按当次资格重新进入。生产命令/runbook 正式接线按 3.6/5.13/9.1 继续，不在本轮执行。


记录收尾：20 项文档进度/Guardian 检查通过；子 change strict validation 通过，116 个任务依赖无未知编号/无环，当前 67 项完成。源码/依赖摘要：[source_hashes.json](phase_f_new_requirements_recovery_20261009/source_hashes.json)。
