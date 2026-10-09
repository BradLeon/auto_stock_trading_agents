# 7.3 / 7.5 实施与验证

实施前基线见 `phase_f_opinion_execution_20261008/before.json` 和 `before_sources.zip`。本轮复用已完成的真实六角色 Dispatcher 输入，在完整隔离环境补运行时观点边界核验、Chief 非空决策、真实 Risk/审批中断/Trader/FakeBroker/Clerk 恢复链。

影响方案：新增隔离验收运行时调用记录；补投影消费记录的 producer/scope；若实际链路暴露类型解析或累计成交恢复缺陷，修复对应 Chief/reconcile 实现并做相关回归。共享 consumer_reads 指纹漂移涉及全部十消费者，Chief 和 Clerk 私有修改另列对应依赖；旧证据不改写、不复标资格，最终补验由 7.17/6.1/6.4 承接。

外部账户、行情、模型返回和人工答复为明确 fixture，业务入口、发布、风险规则、批准绑定、Trader 提交和 Clerk 派生保持实际实现。真实 broker 禁写、生产 C3 和路由保持；本轮结果不可作为生产放行。

## 结论与范围

**7.3、7.5 完成，当前 64/116，52 项待办。** 这证明本轮受控输入下的实际接入/模拟恢复，尚不代表 Phase F 整体门禁验收、生产资格或生产切流。

7.3 在真实六角色 Dispatcher 执行中记录 Python/C 调用与治理投影返回，核对 Layer→Sector、Information→Fundamental 的实际 projection ID/hash 和子投影 input_refs。除文档处理 lease 元数据外，共享 data_* 表摘要前后相同。直接 Provider、底层仓库、Connection/Cursor SQL、跨角色原始投影读取、观点写入共享库的实际调用均使隔离验收失败，即使角色捕获拒绝异常也不能转为通过。真实 admission 留存被隔离候选；Information 的实际 DOC_DATA 读取只返回有 publication/version 的已发布文档。运行时观察只覆盖执行分支，不是安全沙箱；静态扫描仍是辅助审阅。

7.5 复用真实 Dispatcher 发布的六类研究，调用完整 Chief LangGraph，经模型边界 fixture 产生 COHR 金额型决策，再由真实 Risk 规则审查、审批中断、Trader 和无网络 FakeBroker 接收。普通订单及超单笔上限的多轮 revision 均成功，账本保留 snapshot/状态、revision/hash、review/ruleset、approval、模拟 receipt/order/fill；没有预写订单。审批前固定股数/限价；审批后价格超约束实际拒绝，原 revision 不变，新的 revision 经重新 Risk/审批，第二次人工 fixture 拒绝后结束，零模拟接收。

实际 Trader 重试不重复提交；部分成交只计累计已成交数量、仍阻止 drain。次日模拟回报补齐后，实际 Clerk 更新 filled_qty/VWAP/迟到标签与 system 归因，重建绩效/归因读模型；事实变化引起新 source_facts_hash，同事实重复重建 skipped。同窗口 Clerk 复用，新进程重新打开同一隔离数据库执行全步骤仍只有两个 exec_id，不重复入账。

## 本轮修复

- Chief 输出模型补 `Literal` 导入，避免真实非空输出退化成 No Action。
- Trader 节点独立绑定决策消费者 scope，避免继承已冻结研究时点并拒绝当前报价。
- 重新审批拒绝/不执行时清除旧 stale outcome，避免反复进入 Risk/审批。
- Information 保存 insights 的目标/隔离路径经治理文档产品解析 immutable version；先校验引用再写 Workflow opinions，不再借旧 Memory 文档桥读底层表。文档处理 begin/finish lease 仍只记录生命周期元数据。
- Clerk 累计成交恢复更新当前 VWAP/最近成交时点，避免沿用第一次部分成交均价。
- broker 只读快照以响应接收时点校验来源时间，允许请求期间生成的快照，真正未来时间仍拒绝。

共享 consumer_reads/Memory 与 manifest 漂移影响十消费者；Chief 模型输出/decide、Clerk reconcile 纳入声明依赖闭包。当前 **65 个路径（含 manifest）**，资格脚本缺项由 7.17 收敛，历史证据保持原字节。

## 验证与可复核产物

命令均用 `UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync`：

| 范围 | 结果 | 原始记录 |
|---|---|---|
| 新增 7.3/7.5 与快照时钟正反验证 | 13 passed | `phase_f_opinion_execution_20261008/acceptance-current.xml` |
| 最终 Chief/Clerk/reconcile/Trader A 核心回归 | 96 passed | `final-core-regression.xml` |
| 扩大研究/授权/Risk/产品回归 | 195 passed / 1 failed | `regression.xml` |
| 指纹/assurance/架构/切流/记录守卫 | 115 passed / 1 failed | `guards.xml` |
| 文档更新后记录/切流/指纹守卫 | 50 passed | `final-record-guards.xml` |
| 本轮前源码有限对照 | 同两条失败分别复现 | `before-product-failure.xml`、`before-parity-failure.xml` |

以上计数有重叠，不求和成全量测试数；未重跑全仓库全量。OpenSpec strict 通过，依赖仍无循环。`business-evidence.json` 从最终 JUnit 属性提取实际调用、投影、完整审计链、报价/审批卡、模拟成交、Clerk 结果及新进程输出；两条成功链追加以固定隔离目录重跑，`retained-execution.xml` 的相同测试名替换其链记录，保留实际 SQLite 供复核。`execution_records.zip` 是该实际隔离数据库的只读 backup 与对应配置，不是预置订单 fixture。中间失败/过渡运行 XML 保留，`acceptance-current.xml` 为 13 场景最终验收，不能把过渡文件名里的 final 理解为通过。

修改前对照使用 `before_sources.zip` 解包源码和原 manifest，沿用本轮未修改的测试/辅助脚本及其余配置；只证明这两个具体失败在本轮前实现同样存在，不是全量 HEAD 回归归因。资格脚本指纹缺失属于 7.17 已安排的闭包收敛；产品测试把 2026-08-19 observed_at 与本次生成的文档版本混用，遭遇未来版本拒绝，未放宽该校验。

`after.json`/`after_sources.zip` 固定最终源码和产物摘要；`verify.py` 只读验证其字节与生产 inventory。历史研究门禁报告仍保持旧摘要，受保护源码变化后其旧 verify 会报告漂移，不将旧基线重标为当前。

## 剩余工作与限制

- 7.7 按实际记录更新逐消费者处置，7.17 收敛资格脚本/完整依赖闭包及十角色补验；再按其他前置进入 6.1/6.4。本轮不签发 enforced/eligible。
- 3.9/3.10 仍需同固定输入/逻辑时钟的实际新旧双跑和六面比较；本轮单链 fixture 不代替 ShadowPack，跨进程恢复只证明同一路径重开，不是可迁移输入包验收。
- 次日成交是明确合成的后续时段回报，非实际网关观测；本轮真实 Risk 计算保留，账户、价格、模型与审批答复均 fixture，受控范围为单层/单标的，不是生产配置全覆盖。optional/no_coverage/FactSet 不改变。
- 已观察到旧 Clerk 在子步骤返回 errors 或 performance recorded=false 而未抛异常时仍可记 completed；本轮验收额外要求实际记录成功，不据 completed 单字段放行。该既有缺陷在 7.7 逐消费者处置登记，不以它冒充绩效成功，也不扩成本轮新的状态迁移改造。
- 7.13 的真实 TWS 只读核验、11.2 的模拟能力逃逸专项、切前业务回退/联合切流/调度接线、资格登记与实际生产切换仍待办。生产 C3 维持禁自动下单。
