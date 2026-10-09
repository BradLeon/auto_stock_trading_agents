# Phase F 3.3：六面新入口需求断言

本轮范围是 `implement-phase-f-shadow-run-and-cutover` 的 3.3。使用 uv 的离线既有环境；生产 owner、路由、资格与 C3 保持原状态。外部数据、模型响应、人工审批响应及券商传输是显式 fixture，业务角色、Dispatcher、Risk、审批绑定、Trader、Clerk 和 SQLite 记录执行真实代码。

## 交付与结论

`acceptance_assertions.evaluate` 逐项执行固定矩阵，返回 `passed/failed/untested/not-applicable`，保留预期、criteria、实际结果、refs 与原因。必需项只有全部 passed 才能形成该次断言通过结论；这不是正式签核或生产资格。无旧侧参数和差异接受入口；额外旧诊断数据不会改变结论。

矩阵升级为 `new-entry-matrix-v2`，运行前固定每个 task 的触发身份和有限 JSON 条件，支持 eq/in/range/contains/nonempty。模型输出按 schema、完整性、实体语义、内容 hash、有效期/时效、生产 attempt 与依赖/vintage 血缘校验，可声明语义集合或数值允许区间，不比较旧措辞。旧 v1 矩阵和所有历史记录保留，不被恢复成当前 v2 验收证明。

| 面 | 核验依据 | 缺口与违约 |
|---|---|---|
| 输入 | 可恢复内容与 hash、scope/class/逻辑时间、实际读取身份属于固定数据/模型集合 | 只有 hash、未捕获读取、伪不适用拒绝；无读取测量为 untested |
| 调度 | 配置扩展的计划/独立触发身份、SQL run hash、dispatcher claim、实际 outcome 与发布 refs/代次 | 共同遗漏、缺成功发布、同一投影重复发布、旧代发布失败；不同产物发布不误算重复 |
| 分析 | 重解析实际投影及 attempt、role/scope/schema/hash、有效期/时效、依赖与 vintage | 未解析引用为 untested；失败任务、篡改、过期、血缘/实体错误失败 |
| 风控 | 当前完整 revision hash、实际 review、全量 per-order verdict、三类 basis、违约及 counterproposal、预先条件 | 缺 review/full result 为 untested；review 绑定/订单覆盖矛盾、非法边界失败；预期拒绝也可以通过 |
| 审批 | 存储的人工身份/渠道、完整当前 revision、有效 review round、审批时间与预先决定 | 有旧审批但当前修订未审查是 failed；无实际人工审批为 untested |
| 归因 | 隔离库、研究 refs、revision/review/approval、非空意图/receipt、提交 payload hash、route/账户/代次、完整订单及重试、Clerk fills | 空 receipt/fills/未测重试不能通过；错单、错链、重复提交、数量/加权价格不一致失败 |

调度断言按一个固定 WorkflowPlan 的触发/任务闭包核验；3.10 再按选定窗口组织各实际新入口及重放。风控/审批/交易未事先固定场景条件时仍为 untested，不能仅凭完整 JSON 判通过。

## 验证记录

结论：**3.3 断言实现完成**，当前 68/116、48 项待办；最终相关回归 **136 passed / 222.85s**，由 [acceptance_final.xml](phase_f_requirement_assertions_20261009/acceptance_final.xml) 记录。首次扩大回归的 [acceptance.xml](phase_f_requirement_assertions_20261009/acceptance.xml) 保留：133 passed、3 failed，原因是新测试把研究固定时钟错误延伸到实时两阶段报价，真实 source_as_of 成为未来报价而被拒绝；已修正测试时钟边界，不改报价来源时点。其他开发期夹具问题包括 scope 缺显式实体、把不同产物误算重复发布及重放库 config_root 不同；判据保留计划 hash、scope 和来源时间校验。

专项包括新旧不同/旧缺失可通过、共同遗漏拒绝、伪 passed 不生效、必需面缺测、内容/血缘篡改、数值区间和非有限值、独立 trigger 遗漏/重复/代次，以及实际新 Technical/六角色研究与完整模拟交易链。研究机制测试使用明确的 synthetic role adapter；实际业务测试使用真实角色，不能把二者混称生产全范围成功。完整交易组件的 inputs 仍明确 untested，当前测试没有冒充 3.10 的新入口独立 capture/run/replay 证明。

实际逐项输出及重开失败可查看 [assertion_samples.json](phase_f_requirement_assertions_20261009/assertion_samples.json)，它从最终 JUnit properties 提取，原始 JUnit 保留。记录一致性 **20 passed**，见 [records.xml](phase_f_requirement_assertions_20261009/records.xml)；子/父 change strict 通过，父原有 archive INFO 保留。任务依赖无未知项/无循环，3.3 四项前置均已完成，见 [verification.json](phase_f_requirement_assertions_20261009/verification.json)。保护并集仍为 100 路径，当前完整源码/提示闭包为 461，见 [source_hashes.json](phase_f_requirement_assertions_20261009/source_hashes.json)；历史指纹不改写，最终冻结/资格取证仍在 6.1/6.4。

## 实际发现：重开库污染当前修订

**未修复的共享状态缺口**：[reopen_counterexample.json](phase_f_requirement_assertions_20261009/reopen_counterexample.json)。首次打开空库时 `_migrate_legacy_decisions` 因没有 legacy inventory 返回、没有写 migration ledger。原生 Chief 后续向兼容 `decisions` 表写入镜像，新进程 Clerk 重开库时再导入为额外 `legacy_revision`，推进当前 revision；新 revision 没有对应 review/approval，旧已批准 hash 仍留在成交记录中。

同进程实际链可以通过组件断言；重开后审批断言 failed，风控缺测，归因失败。JUnit 的 `blocked_shared_revision_reopen` 与 `failed_assertions_after_reopen` 记录两条批准场景的实测变化。检测器回归通过只代表正确识别失败，**不代表此业务恢复已合格**。它影响新入口共享状态，不能以旧专属缺陷排除，也不能接受差异放行。7.5 历史的成交恢复结果保留，当前完整恢复结论受此新增反例限制。

**后续注意/待修复项**：3.4 修复兼容迁移重复导入 native cycle 的问题，验证首次空库、混合历史、重复重开与批准修订不漂移；原始历史仍保留。该项在完整交易 3.10/13.4–13.7 验收前必须闭合。继续 3.4 → 3.5 追加式报告/签核 → 3.6 checker；3.10 独立运行/重放及最终资格取证仍待办。

**为何现在不修**：本轮 3.3 交付需求检测能力并保存实际失败，修复与重新运行属于依赖其结论的 3.4。当前仅完成断言实现，未把发现的问题、未测的输入报告链或跨进程恢复重标为通过。正式报告保存/signoff、版本适用性与部署资格仍由后续任务独立核验。
