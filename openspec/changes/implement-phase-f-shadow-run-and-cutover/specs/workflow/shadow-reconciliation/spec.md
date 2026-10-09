# Spec Delta

## Purpose

为 Phase F 提供新入口的可重放需求验收：以需求契约、固定输入和独立预期结果核验新分析、调度、风控与执行链，报告可签核、可校验且追加保存。旧侧对比仅作可选诊断，旧缺陷不作为新入口正确性基准；真实券商禁写与账本隔离继续适用。

## ADDED Requirements

### Requirement: 新入口验收必须使用可重放的固定输入

系统 SHALL 固定实际消费的数据引用、行情、账户、历史、逻辑时间、规则与模型/提示版本，并保留可恢复内容或不可变可解析引用及 hash。实际新入口与跨进程重放 SHALL 消费同一包，记录独立 run IDs、真实读取及输出 refs；SHALL NOT 以手工结果 JSON 或只有摘要代替执行。重放 SHALL NOT 查询变化中的来源。旧侧运行 SHALL 为可选诊断，不作为新入口通过前置。

#### Scenario: 输入包只有 hash
- **WHEN** 实际消费的行情或账户仅有摘要，缺少恢复内容与可解析引用
- **THEN** 系统 SHALL 拒绝将该包作为可重放验收证据

#### Scenario: 新入口独立重放
- **WHEN** 新入口实际运行和跨进程重放均有输入、读取、输出与版本记录，而旧入口失败或未运行
- **THEN** 系统 SHALL 依据新入口需求断言判定
- **AND** SHALL NOT 因缺少旧 run ID 或旧输出而拒绝该证据

#### Scenario: A 的两阶段价格固定
- **WHEN** 新入口与重放读取审批依据或执行校验价格
- **THEN** 系统 SHALL 使用包中对应阶段的治理报价、来源、币种与真实 source_as_of
- **AND** SHALL NOT 重新查询 Provider 或以逻辑时间替代报价时点

#### Scenario: 输入或实现发生漂移
- **WHEN** 行情/账户内容、逻辑时间、规则、模型/提示或相关代码配置变化
- **THEN** 系统 SHALL 判旧证据不再适用并要求新运行
- **AND** SHALL NOT 把不同输入称为同输入重放

### Requirement: 新入口验收必须按需求矩阵分级要求

每个批次 SHALL 在运行前固定需求版本、workflow/scope、所选 Event/Routine 及依赖、必需断言、输入固定程度和预期行为。只消费持久化数据的研究批次 SHALL 显式声明 runtime 不适用；实际消费 runtime 的任务 SHALL 固定真实输入；含决策/交易批次 SHALL 固定账户、行情、历史和逻辑时间。必需项 SHALL NOT 因旧侧缺失而改为不适用。

#### Scenario: 纯研究批次
- **WHEN** 批次仅消费持久化研究输入且不触及交易
- **THEN** 交易归因 SHALL 可按事先声明记为 not-applicable
- **AND** SHALL NOT 强制旧交易运行或额外运行未选 Fundamental 模式

#### Scenario: 必需面缺失
- **WHEN** 含决策/交易的批次缺少必需风控或审批证据
- **THEN** 系统 SHALL 记为 untested 或 failed 并拒绝通过
- **AND** SHALL NOT 以旧侧同样缺失或其他批次已通过替代

### Requirement: 新入口验收报告必须覆盖六类需求面

报告 SHALL 按矩阵覆盖输入快照、调度覆盖、分析输出、风控 verdict/counterproposal、审批链与交易归因，逐项关联需求、场景、预期、实际结果和证据，记为 passed/failed/untested/not-applicable。调度 SHALL 对照独立预期集合；分析与模型输出 SHALL 按预先声明的 schema、血缘、完整性、时效及语义/数值条件判定，不要求逐字一致。新旧差异 SHALL 仅列诊断附录。

#### Scenario: 新调度遗漏必需触发
- **WHEN** 独立预期集合中的必需触发没有新路径实际执行记录
- **THEN** 该断言 SHALL 失败并列出触发身份
- **AND** SHALL NOT 因旧路径也未执行而通过

#### Scenario: 新输出符合需求但与旧输出不同
- **WHEN** 新输出满足事先声明的需求与允许范围，旧输出不同或不可获取
- **THEN** 系统 SHALL 允许该需求断言通过
- **AND** SHALL NOT 要求接受旧差异作为前置

#### Scenario: 必需断言没有实测
- **WHEN** 某必需项无可追溯实际结果
- **THEN** 报告 SHALL 记为 untested 并保持该范围未通过
- **AND** SHALL NOT 用汇总 passed 或辅助旧侧比较掩盖缺项

### Requirement: 新入口需求失败必须修复并重验

必需需求 failed 或 untested SHALL 阻断对应范围通过，修复或补验后 SHALL 追加新运行与结论。系统 SHALL NOT 用差异接受、旧侧同样失败或临时放宽阈值将其转为通过。旧业务缺陷与新旧差异 SHALL 仅登记；若其仍影响新入口、共享状态或写权限，SHALL 按对应新需求失败处置。既有 optional/no_coverage/partial 政策 SHALL 保留并显式核验。

#### Scenario: 新风险门禁失败
- **WHEN** 新入口绕过审批或接受不符合批准约束的订单
- **THEN** 该范围 SHALL 失败并在修复后重跑
- **AND** SHALL NOT 通过接受与旧逻辑的差异放行

#### Scenario: 旧业务错误已被隔离
- **WHEN** 旧侧存在业务缺陷，而新入口满足需求且旧侧不能污染其状态或越权写入
- **THEN** 旧缺陷 SHALL 只作登记
- **AND** SHALL NOT 阻断新入口通过

### Requirement: 切流必须校验新入口验收报告的适用性

dry-run、CLI 与执行器 SHALL 引用真实新入口验收报告并强制校验：需求版本及 scope 匹配、有效签核未撤销、全部必需断言 passed、输入/实际 run/输出可解析且代码配置指纹适用。空 ID、缺 checker、不可读、failed/untested 必需项 SHALL 拒绝。旧比较报告 SHALL 保留历史类型，不自动转换为新验收报告；有效旧诊断差异 SHALL NOT 构成新验收阻塞。

#### Scenario: 只有旧比较工具报告
- **WHEN** 报告只有左右 JSON、matched 数量或旧差异接受记录
- **THEN** 切流 SHALL 拒绝以其替代新入口实际需求验收

#### Scenario: 报告撤销或版本不符
- **WHEN** 报告已撤销、需求/scope 不符或实现配置已漂移
- **THEN** 系统 SHALL 拒绝切流并报告原因

#### Scenario: 缺旧侧但新入口证据齐备
- **WHEN** 新入口报告各项适用性通过，仅无旧侧比较
- **THEN** 报告校验 SHALL NOT 因缺旧侧而拒绝
- **AND** 资格、恢复与部署授权 SHALL 仍独立核验

### Requirement: 影子期必须从进程能力层禁止新路径真实下单

新路径在影子期 SHALL NOT 具备向券商提交真实订单的进程能力。该禁止 SHALL 由能力层实现并覆盖全部下单出口，SHALL NOT 仅依赖调用方自觉传入的模拟标志。缺少该能力的进程 SHALL 拒绝启动影子任务并报告缺失项。该禁令 SHALL 先于任何接入核验与影子运行可用。

#### Scenario: 影子任务尝试下单

- **WHEN** 影子运行中的新路径到达下单调用点
- **THEN** 系统 SHALL 拒绝该调用并记录为影子期禁止的真实下单尝试
- **AND** 该记录 SHALL 成为可审计的拒绝证据
- **AND** SHALL NOT 因订单被拒而将该次影子运行标记为失败

#### Scenario: 影子运行缺少能力禁令

- **WHEN** 部署的进程不具备禁止真实下单的能力
- **THEN** 影子任务 SHALL 拒绝启动
- **AND** 接入核验 SHALL 同样拒绝在此条件下开始
- **AND** 报告 SHALL 指出缺失的能力项

### Requirement: 下单禁止的验收以提交审计为主证据

影子期无未审批真实下单的验收 SHALL 以新路径的券商提交调用与拒绝审计记录、能力校验结果、隔离演练中的券商模拟接收记录为主要证据。真实成交账本未被污染 SHALL 作为另一条独立验收，二者不互相替代。真实只读核验 SHALL 按 route、账户与订单归因排除既存合法成交。意料之中的模拟提交被能力层阻断 SHALL 计为成功，意外真实提交尝试 SHALL 成为独立差异或批次停止条件。

#### Scenario: 没有实际影子订单或提交尝试

- **WHEN** 影子账本与提交审计为空，只有 helper 的模拟 fixture 测试
- **THEN** 影子期无真实下单与订单全链验收 SHALL 标为未测
- **AND** SHALL NOT 因生产 trades 无新增而判影子期验收完成

#### Scenario: 券商已接收但本地未落库

- **WHEN** 券商侧已接收一笔订单而本地成交账本无对应新增
- **THEN** 系统 SHALL 以提交调用审计为准判定存在真实下单尝试
- **AND** SHALL NOT 因账本无新增而认定无真实下单

#### Scenario: 旧路由合法成交使账本新增

- **WHEN** 影子期内旧活跃 route 产生合法成交，或切换前订单迟到成交
- **THEN** 该新增 SHALL 按 route 与订单归因排除于新路径越权之外
- **AND** 系统 SHALL NOT 因此判定新路径违反禁令

#### Scenario: 意外真实提交尝试

- **WHEN** 审计发现新路径出现一次未被能力层阻断的真实提交尝试
- **THEN** 系统 SHALL 将其记为独立差异并触发该批次停止条件
- **AND** SHALL NOT 归入常规差异明细

### Requirement: 影子订单与真实订单账本必须隔离

影子运行产生的订单意图 SHALL 写入独立影子账本，SHALL NOT 写入真实成交账本或参与持仓、绩效与资金对账。真实下单记录 SHALL NOT 出现在新入口验收报告中充当已成交证据；影子意图 SHALL 保留可重建所需的全部归因字段。

#### Scenario: 影子意图进入真实成交账本

- **WHEN** 影子运行的订单意图被写入真实成交账本
- **THEN** 系统 SHALL 判失败并指出该写入
- **AND** 该记录 SHALL NOT 参与持仓、绩效与资金对账

#### Scenario: 影子归因可独立重建

- **WHEN** 审计方请求重建某次影子运行的下单意图
- **THEN** 系统 SHALL 能从影子账本重建该意图及其归因字段
- **AND** 重建 SHALL NOT 改变任何原始记录

### Requirement: 新入口验收报告必须可签核且不可事后改写

报告 SHALL 包含需求与矩阵版本、输入 hash、scope、实际新入口/重放 run IDs、输入/输出 refs、实现配置指纹、逐项断言及缺口。报告与签核/驳回/撤销 SHALL 追加保存并记录操作者、时间与理由，不覆盖原结论。修复补验 SHALL 生成新运行及新报告；旧诊断结果 SHALL 与验收断言分列，SHALL NOT 自动承接旧比较签核。

#### Scenario: 驳回报告
- **WHEN** 审阅者驳回新入口报告
- **THEN** 系统 SHALL 追加驳回记录并保留原报告

#### Scenario: 补验后重新签核
- **WHEN** 必需断言经新运行补齐后再次签核
- **THEN** 系统 SHALL 关联新报告与旧缺口并保留此前失败/未测结论
- **AND** SHALL NOT 原地改写旧报告为通过
