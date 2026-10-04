# Spec Delta

## Purpose

本文件为 `data/target-dataflow-assurance` 的增补，只新增 Phase F 前置接收与实际接入核验的要求。该能力现有的 7 条需求规定了读取资格如何签发与判定，本文件规定谁接收这些资格、如何复核后使用、以及在什么条件下不得进入切流。

## ADDED Requirements

### Requirement: Phase F 前置接收必须以版本化矩阵进行

Phase F SHALL 在切流前建立版本化的前置接收矩阵，逐项记录 A–E 各专项与数据流专项的实现、命令与证据的接收结果、缺口、责任方与依赖。矩阵 SHALL 以实际部署入口与当前路由的核验结果为准，SHALL NOT 以静态清单中的旧入口名称推定其仍可稳定运行。已具备可复用运行材料与缓存的项 SHALL 登记复用，SHALL NOT 重复全量采集。

#### Scenario: 静态清单中的旧入口已不可运行

- **WHEN** 某项在静态清单中登记的入口经核验已不可运行
- **THEN** 该项 SHALL 在接收矩阵中记为未接收并注明核验方式与失败原因
- **AND** SHALL NOT 因清单中存在该条目而判定其已接收

#### Scenario: 已有可复用的运行材料

- **WHEN** 某项存在可核验的真实运行材料与缓存证据
- **THEN** 矩阵 SHALL 登记复用该证据并引用其标识与时间
- **AND** SHALL NOT 为重新确认该能力而重复执行全量采集

### Requirement: 已有资格证据必须受控登记后才可被切流使用

对既有资格证据，系统 SHALL 按 `domain_id + consumer_id + contract_version + scope` 逐项校验原始记录、产品覆盖、代码与配置指纹、前置证据、时间有效性与回退证明，通过追加式证据接口落账后再查询资格，并记录操作者、证据引用与登记结果。汇总报告的 passed、任务勾选或隔离运行成功 SHALL NOT 转换为未经验证的生产证明。缺项 SHALL 保持 ineligible 并列出最小补验范围。该登记 SHALL NOT 改变任何路由，SHALL NOT 生成风控或人工批准。

#### Scenario: 只有汇总报告 passed 而无逐项证据

- **WHEN** 某消费者只有汇总报告标记通过，缺少可核验的逐项证据
- **THEN** 登记 SHALL 保持该消费者 ineligible 并列出缺失证据
- **AND** SHALL NOT 将汇总结论记为生产资格

#### Scenario: 证据指纹已漂移

- **WHEN** 登记时发现代码或配置指纹与原验证时不一致
- **THEN** 登记 SHALL 拒绝该证据并报告漂移的具体路径
- **AND** 该消费者 SHALL 保持原稳定路由

#### Scenario: 登记动作本身不改变路由

- **WHEN** 一次证据登记成功完成
- **THEN** 该消费者的实际路由 SHALL 保持不变
- **AND** 系统 SHALL NOT 因登记成功而产生任何风控结论或人工批准

### Requirement: 十个角色的实际接入必须以运行记录核验

系统 SHALL 核验 Layer、Information、Sector、Fundamental、Macro、Technical 通过实际 CLI、Dispatcher 或 Workflow 入口的接入，保留运行标识、实际调用路径、产品与文档与 vintage 引用、投影 hash 与缺口。观点依赖 SHALL 只允许 Layer→Sector 与 Information→Fundamental 两条，SHALL 拒绝 Provider 或底层表旁路、候选材料冒充已发布数据、观点回写为共享事实。核验 SHALL 覆盖单角色、依赖子流程、完整分析流程、必要输入失败阻断与跨进程重启后引用可解析。尚未接入的消费者 SHALL 登记其依赖，SHALL NOT 伪造接入成功。

#### Scenario: 角色绕过数据产品直连 Provider

- **WHEN** 某角色的实际调用路径显示其绕过数据产品直接取数或直连 Provider
- **THEN** 该接入 SHALL 判为不合规并指出旁路位置
- **AND** 该角色 SHALL NOT 被记为已接入

#### Scenario: 必要输入失败时仍进入决策

- **WHEN** 完整分析流程在某一必需分析缺失的情况下仍进入决策周期
- **THEN** 该核验 SHALL 判失败并指出缺失的必需分析
- **AND** 该情形 SHALL NOT 被记为接入通过

#### Scenario: 重启后投影引用不可解析

- **WHEN** 跨进程重启后某角色引用的投影或数据 vintage 无法解析
- **THEN** 该核验 SHALL 判失败并指出不可解析的引用
- **AND** SHALL NOT 以空投影或默认快照继续

### Requirement: 决策与执行链的接入验证必须无券商写权限

Chief、Risk、Trader、Clerk 的实际接入 SHALL 在无券商写权限的影子或纸面环境验证，绑定研究快照、内部状态、决策修订、审批与订单与成交回报，覆盖多轮风控、重复恢复、部分与迟到成交及绩效重建。可复用隔离账本与券商模拟；真实券商连接仅只读核验。隔离证明 SHALL NOT 被冒充为当前生产账本完整性证明。

#### Scenario: 验证需要制造生产周期或真实下单

- **WHEN** 某项验证的完成条件要求制造生产决策周期、真实下单或真实部分成交
- **THEN** 该验证 SHALL 改以隔离账本与券商模拟完成
- **AND** SHALL NOT 因此放宽验证标准或跳过覆盖项

#### Scenario: 重复恢复产生重复账本记录

- **WHEN** 同一恢复动作重复执行
- **THEN** 系统 SHALL 保持账本幂等，不产生重复的成交或持仓记录
- **AND** 该重复恢复 SHALL 被记录以供审计

### Requirement: 实际缺口必须形成逐消费者处置

对核验暴露的缺口，系统 SHALL 形成逐消费者处置：已接受为 optional 的输入与已确认的覆盖缺口 SHALL 保持原政策；按最新最终版使用的来源 SHALL NOT 新增按报告年龄的降级；时间缺失与历史断链等情形 SHALL 继续显式标记为 partial。旧路径的问题 SHALL 仅登记不修复。历史不完整 SHALL NOT 自动放行，也 SHALL NOT 阻断与其无关的已验收路径。

#### Scenario: 旧路径存在已知问题但新路径已隔离

- **WHEN** 旧路径存在已知缺陷而新路径已验证隔离该影响并阻断交易
- **THEN** 处置 SHALL 登记旧路径问题但不修复旧路径
- **AND** SHALL NOT 以旧路径缺陷未修复为由阻断已通过的新路径范围

#### Scenario: 历史断链但当期数据完整

- **WHEN** 某消费者的历史数据存在断链，而当期必需数据完整
- **THEN** 该情形 SHALL 显式标记为 partial 并记录断链范围
- **AND** SHALL NOT 自动放行，亦 SHALL NOT 阻断与其无关的已验收读取路径

### Requirement: 前置门禁必须交付逐批 eligible 与 ineligible 报告

Phase F SHALL 交付逐消费者与逐批的 eligible 与 ineligible 报告，关联证据账本、实际接入运行与回退记录。仅证据与接入验收均通过的范围 SHALL 可进入实际切换；缺项 SHALL 只阻断受影响范围。完整自动交易 SHALL 仍要求全部必需输入与审批链同时满足。生产路由变更 SHALL 需明确部署授权，live 交易路径开放 SHALL 另需明确实盘授权；规划批准与前置通过均 SHALL NOT 构成该授权。

#### Scenario: 报告缺项只阻断受影响范围

- **WHEN** 某一消费者因缺回滚证明为 ineligible，而其他消费者证据齐备
- **THEN** 仅该消费者 SHALL 保持旧路由
- **AND** 其他已通过消费者 SHALL 可按其自身范围进入切换

#### Scenario: 前置通过但缺部署授权

- **WHEN** 逐批报告全部通过，但未取得生产路由变更的部署授权
- **THEN** 系统 SHALL 拒绝执行生产路由变更并报告缺少部署授权
- **AND** 前置通过 SHALL NOT 被记为已获部署授权
