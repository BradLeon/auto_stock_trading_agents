# Phase F 3.4 → 3.5 → 3.6：修订恢复与新入口验收报告

本轮继续 change `implement-phase-f-shadow-run-and-cutover`，使用 uv 离线既有环境。生产路由、owner、资格、授权和 C3 不变；真实 IBKR（含 Paper）没有获得写入授权。真实角色与 Dispatcher/SQL 执行，数据和模型传输为显式 fixture；交易回归使用受控无网络 FakeBroker。

## 0.1 文件与证据影响

| 路径 | 原因及影响 | 补验/历史处置 |
|---|---|---|
| `src/ats/memory/store.py` | 空库完成迁移标记；已有 revision 的 cycle 不再从兼容镜像生成新 revision；共享保护面影响十消费者 | 原生修订、混合历史、空库/重复重开及实际 Clerk 子进程恢复；历史证据不改写 |
| `src/ats/workflow/isolation.py` | 只读报告检查绑定已有隔离证据目录，不创建库/目录或初始化 authority；共享保护面影响十消费者 | 只读缺库拒绝、禁写与环境/能力恢复，相关 isolation 回归 |
| `src/ats/runtime/cli.py` | 新验收执行、报告、签核、撤销和检查命令；保护面直接涉及 chief/sector | 真实新进程 CLI 与历史命令回归 |
| `acceptance_reports.py`、`acceptance_disposition.py`、`shadow_reports.py` | 独立报告类型与失败处置、完整实现/配置指纹、正式 checker 接线 | 当前完整源码/提示闭包 463；最终 6.1/6.4 冻结前继续实现会使当前报告漂移 |

保护并集仍为 100 路径。当前新报告要求完整源码/提示与实际配置适用，不能沿用历史 458/461 闭包或旧比较签核。共享修改影响十角色的最终资格；本轮不登记生产资格、不重复全量采集，6.1/6.4 按最终依赖闭包补验。

## 实现与结论

迁移在空库首次打开也记录完成；已有 revision 的 cycle 保留原修订和兼容表原文，真正 legacy-only cycle 的可恢复/unknown 行仍诚实导入。连续重开不会追加 revision。此修复防止再发生污染；已经存入历史证据的错误修订不被删除或自动恢复为有效批准，原失败材料保留。新运行须重新形成合格链。

`acceptance_disposition` 把必需 failed 列为修复重跑、未测/其他非通过列为补验；旧诊断/差异接受不能改变断言状态。optional/no_coverage/partial 仍由原逐消费者政策处置，没有新豁免入口。

新报告类型 `new-entry-acceptance-v1` 与 `historical-comparison-v1` 分开。报告保存需求/矩阵版本、完整固定矩阵和 scope、输入 hash、实际新入口/独立重放 ID、各自 workflow run ID/矩阵 hash/输出 refs、代码与配置指纹、逐项结论、失败处置和旧诊断附录。SQLite triggers 禁止报告和签核更新/删除；签核/驳回/撤销形成绑定报告 hash 的追加事件链。失败补验要求新的实际执行 ID 和新的报告，可关联 supersedes；旧报告和旧签核不会覆盖或继承。

正式 checker 通过只读连接重新核验固定输入、实际 Dispatcher/attempt/projection/schedule 记录、独立重放、六面断言和指纹，所有必需项必须 passed，签核必须有效。旧比较 JSON/左右一致/差异接受/legacy run ID 不构成门槛或证明。dry-run、CLI、activation 和执行器的中央 `check_batch_report` 均指向新 checker；缺 checker 仍拒绝。

当前具体新入口 `dispatch_entry` 运行真实 Dispatcher，固定逻辑时钟，重放声明数据/模型输入，网络 fallback 禁止，使用全新隔离 side/run，禁止复制已有 run 冒充实际执行。只读 checker 不打开 TradingMemory，不触发兼容迁移，不创建缺失证据。

## 验证

结论：**3.4、3.5、3.6 按依赖完成，当前 71/116、45 项待办，第 3 组 9/10**。

最终报告/CLI 回归 **49 passed / 86.69s**，见 [reports_cli_final.xml](phase_f_acceptance_reports_20261009/reports_cli_final.xml)。其中新报告模块 16 项包括真实 Technical 新入口/独立重放、无旧侧、签核/撤销、实际 SQL/hash/配置/消费者覆盖、补验新运行/新报告、追加触发器、缺身份签核拒绝、跨进程执行/检查、dry-run 与 activation/read executor 拒绝。错误消息遗漏 report ID 已修复，原失败没有删；签核身份强化后的 [reports_final.xml](phase_f_acceptance_reports_20261009/reports_final.xml) 16 passed 也保留。以最新 49 项和最终指纹为当前 report/checker 验证，不自动复用开发期间已漂移的报告。

扩大相关回归原始结果 **602 passed / 13 failed / 381.66s**，见 [acceptance.xml](phase_f_acceptance_reports_20261009/acceptance.xml)，保留全部失败。覆盖新矩阵/六面断言、决策迁移/快照/风控、真实 Chief/Risk/审批/Trader/FakeBroker/Clerk、两阶段 A、部分/迟到成交、跨进程重开、旧报告、batch/cutover、隔离及 optional/coverage 处置。两条实际批准场景的重开后各必需业务面通过，完整交易组件的 inputs 仍如实 untested，不是完整交易报告已可签核。

| 扩大回归失败 | 归因/处理 | 对本轮的影响 |
|---|---|---|
| 1 条 cutover CLI 报错未包含报告 ID | 新 checker 的消息缺口已修复；最后 49 项全部通过，含该用例 | 已闭合 |
| 2 条 batch drift 期待旧目标回退 | 静态 `valid/no/yes` 无已验证退役/真实可读性证明，当前安全停止 | 旧回退夹具诊断，不放宽安全策略 |
| 7 条 read executor、3 条 read CLI 期待旧目标迁移成功 | 同样缺已确认旧回退证明，实际为 fallback_retired/refused；这些测试显式用通过的 report adapter，未进入新报告 checker | 不是新报告失败，不证明生产切流已合格；后续 9.2/8.4/13.6 更新测试范围与安全恢复夹具 |

详细 node IDs、原错误及相对 3.3 冻结的源码摘要见 [expanded_failures.json](phase_f_acceptance_reports_20261009/expanded_failures.json)。`batch_manifest/read_cutover/cutover_routing/read_recovery` 四个实现与前轮摘要完全一致；12 条旧夹具失败不据此宣称全仓通过，不为使测试变绿修复旧业务或允许未经验证的回退。生产迁移/安全恢复仍由后续任务单独证明。

记录守卫 **20 passed**：[records_final.xml](phase_f_acceptance_reports_20261009/records_final.xml)，前次 [records.xml](phase_f_acceptance_reports_20261009/records.xml) 保留。子/父 change strict 通过；父原有缺目标 spec 的 archive INFO 保留。依赖无未知项/无循环，三个任务直接前置全部闭合，见 [verification.json](phase_f_acceptance_reports_20261009/verification.json)。保护并集 100，最终源码/提示闭包 463，见 [source_hashes_final.json](phase_f_acceptance_reports_20261009/source_hashes_final.json)；早期摘要另保留，不覆盖历史文件。

实际报告签核链、新进程 CLI 与重开断言见 [assertion_samples.json](phase_f_acceptance_reports_20261009/assertion_samples.json)。最终实际报告内的 implementation 与当前 463 路径逐项一致。测试计数有交集，不把扩大回归与专项相加。

## 后续

**后续注意/待修复项**：3.10 仍须完成独立 capture/各选定 workflow 的运行窗口和新进程重放、完整非空交易报告（包含真实阶段输入、审批、receipt/retry 和 Clerk refs），完整交易范围还依赖 11.2。当前真实 Technical 示例不能替代全 workflow/全部消费者或完整交易输入证明。5.4/5.15、切前恢复、最终冻结/资格及生产切流仍按各自依赖推进。父整图 11.4 不因模块报告完成直接宣告全范围验收。

**为何现在不修**：这些是独立后续任务或尚未闭合的完整交易前置，不以本轮报告模块或 fixture 签核补齐。没有真实 broker 写授权，不执行 Paper/实盘提交。历史反例、旧失败和旧报告类型继续保留。
