# Spec Delta

## Purpose

为 Phase F 提供新入口的可重放需求验收：以需求契约、固定输入和独立预期结果核验新分析、调度、风控与执行链，报告可签核、可校验且追加保存。旧侧对比仅作可选诊断，旧缺陷不作为新入口正确性基准；真实券商禁写与账本隔离继续适用。

## 2026-10-15 精简裁决

用户裁决（2026-10-15）：切流以 `docs/TARGET_WORKFLOW_DATAFLOW.md`（§1、§12.4、§14.6、§15.5、§16）及用户历次裁决为准；本 change 中超出 TARGET 的派生机制降为可选。G1 以 TARGET 为准；G2 研究读取按消费者整体切换，不再按「标的×时点」；G3 迁移窗口内出问题退回旧路径（TARGET §14.6「保留旧入口作可控回滚」），取代「停止即锁读」作为默认；G4 本 change 以追加裁决方式修订，已完成任务与证据保留为历史。安全底线不变：新路径与隔离运行禁止券商写入；任何时刻只有一个实盘入口；真实交易绑定同一 revision 的风控与 Boss 批准；读/调度/交易分别切换；审批与账本发布成对切换；调度单一负责人；旧实现墓碑退役；回滚已演练；测试全绿。依据：TARGET §16 第 12 条「本次设计和文档实施不使用 OpenSpec」；TARGET 中唯一的 exact-scope 资格表述为 2026-10-09 Phase F 期间补入；按此前机制推进切流出现结构性死循环（`docs/PHASE_F_GUARD_REVIEW.md`、`docs/PHASE_F_8_6_REGISTRATION_DRAFT.md` 第七至九节）。观察期 3 个交易日；自动切回的告警走飞书机器人。

本 spec 各要求处置如下（保留原文；「被取代」者不再作为切流前置，「修改」者以本节为准）：

| 要求 | 处置 | 修订要点 |
|---|---|---|
| 新入口验收必须使用可重放的固定输入 | 保留 | — |
| 新入口验收必须按需求矩阵分级要求 | 保留 | — |
| 新入口验收报告必须覆盖六类需求面 | 保留 | — |
| 新入口需求失败必须修复并重验 | 保留 | — |
| 切流必须校验新入口验收报告的适用性 | 修改 | 适用性按消费者/工作流判断，签核依据为可读报告与业务内容；报告不再按「标的×时点」逐一匹配切换 scope |

> 2026-10-19 归档前整理：上表处置「被取代」的要求已从下方 `ADDED Requirements` 中移除（原文保留在本 change 的 git 历史与 `docs/PHASE_F_OPENSPEC_REVISION_DRAFT.md`），处置「修改」的要求已改写为裁决后的写法，主规格因此只承载仍然有效的要求。

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

#### Scenario: Routine 定性变化不能冒充数值 KPI 更新
- **WHEN** Routine 处理未匹配基线 KPI 的第三方标题、无可量化读数的叙事或缺少可比较单位的数量
- **THEN** 当前发布与需求验收 SHALL 保留完整分类和原文，并以 null 表示未知数值
- **AND** SHALL NOT 把标题首个数字、日期、未经单位绑定的金额或零哨兵当作 KPI 新值
- **AND** 具有有效 hash 但带伪数值的定性投影 SHALL 在当前复用、Chief 决策和必需分析断言中拒绝；历史原记录保持可审计

#### Scenario: Information 请求契约与发布置信度要求一致
- **WHEN** 新 Information 路径请求事实摘要模型输出
- **THEN** 响应 schema SHALL 要求显式、非空且在 0 至 1 范围内的事实置信度，与 materiality 独立
- **AND** 缺失或 null 置信度 SHALL 拒绝发布，不能用 materiality 或默认值代填
- **AND** 历史共享响应类型及原失败记录 SHALL 保持可审计，不据兼容解析放行当前输出

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

dry-run、CLI 与执行器 SHALL 引用真实新入口验收报告并按消费者/工作流校验适用性：需求版本匹配、有效签核未撤销、全部必需断言 passed、输入/实际 run/输出可解析。签核依据 SHALL 为可读报告与业务内容；报告 SHALL NOT 再按「标的×时点」逐一匹配切换 scope。空 ID、缺 checker、不可读、failed/untested 必需项 SHALL 拒绝。旧比较报告 SHALL 保留历史类型，不自动转换为新验收报告；有效旧诊断差异 SHALL NOT 构成新验收阻塞。

#### Scenario: 只有旧比较工具报告
- **WHEN** 报告只有左右 JSON、matched 数量或旧差异接受记录
- **THEN** 切流 SHALL 拒绝以其替代新入口实际需求验收

#### Scenario: 报告撤销或版本不符
- **WHEN** 报告已撤销、需求版本不符或输入/输出不可解析
- **THEN** 系统 SHALL 拒绝切流并报告原因

#### Scenario: 缺旧侧但新入口证据齐备
- **WHEN** 新入口报告各项适用性通过，仅无旧侧比较
- **THEN** 报告校验 SHALL NOT 因缺旧侧而拒绝
- **AND** 消费者健康检查与回退方式 SHALL 仍独立核验

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

### Requirement: 固定输入验收必须区分部署许可与运行时间

固定输入包及报告 SHALL 同时记录稳定部署 scope/许可引用、独立 invocation as_of、查询窗口和实际 Event/version/source/query 时点。新入口及跨进程重放 SHALL 使用相同调用截止和固定输入，SHALL NOT 将部署期限或查询窗口 end 代入调用 as_of，SHALL NOT 重写来源时间。连续运行验收 SHALL 包含多 tick/跨日/窗口推进无需续批、撤销/主动停止、资格/报告/输入失效、旧 worker 与触发去重；单次通过 SHALL NOT 声称持续生产观察完成。

#### Scenario: 同一许可下两次输入不同
- **WHEN** 两次调用属于同一持久部署许可但实际 as_of、查询窗口或输入 hash 不同
- **THEN** 系统 SHALL 保留两次独立运行记录和固定输入，不合并为同一个输入快照


### Requirement: 本轮Routine有限验收保留后补缺口

按2026-10-14用户明确裁决，本轮MRVL、MSFT、NVDA、TSM Routine SHALL 验收现有分类及变化投影、来源引用、结构/血缘/时效和真实执行/回放。完整市场隐含预期、整合叙事/变化路径、逐KPI变化解释和可证伪条件 SHALL 保留为Phase F验收后父change 7.5待补缺口，不作为本轮有限验收新增实施前置。该限定 SHALL NOT 表述为TARGET §9.2完整能力已实现或作为部署/券商写入授权。

#### Scenario: 有限范围人工审阅通过
- **WHEN** 用户明确审阅通过固定body hash的本轮Routine报告
- **THEN** SHALL 追加签核，保留原报告、限定范围和后补缺口
- **AND** 13.8/13.9 SHALL 回写该区别，不将后补能力标完成

#### Scenario: 来源或执行缺项不获豁免
- **WHEN** 本轮Routine缺真实输入引用、有效基线、当前执行血缘、合法schema/数值或独立运行/回放证据
- **THEN** SHALL 保持该范围未通过，不以业务能力后补豁免这些必需条件
