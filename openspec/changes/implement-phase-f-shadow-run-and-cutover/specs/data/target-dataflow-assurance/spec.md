# Spec Delta

## Purpose

本文件为 `data/target-dataflow-assurance` 的增补，新增 Phase F 前置接收、实际接入核验及 A 的版本化 Trader 行情契约要求。沿用既有资格签发与判定标准，规定证据复核、契约迁移和切流条件。

## ADDED Requirements

### Requirement: A 行情契约升级必须同步治理清单并重验受影响资格

系统 SHALL 在最终冻结/取证前，为 Trader 增加受限 MARKET_DATA runtime 读取契约并升级 contract_version；治理清单的 products/allowed_consumers/连线、contract_validation.EXPECTED_PRODUCTS、consumer_api、架构守卫、Target/契约/API 文档与测试 SHALL 一致，批准授权读取契约继续适用。新契约 SHALL 明确行情与授权输入的 domain/scope、required_evidence、vintage/completeness/fallback；保留授权证明并补充行情来源、时效与回退证明，SHALL NOT 仅以原授权证据证明新增行情能力。请求 SHALL 携带可校验的当前待审或获批修订标的、业务 scope 和执行用途；生产读取 SHALL 继续通过 exact-scope 资格门禁，无资格的候选验证 SHALL 只运行在既有隔离入口。其他角色权限、optional 政策及资格标准 SHALL NOT 放宽。

系统 SHALL 评估 manifest 全文件摘要、共享 consumer API 和消费者文件指纹的变更影响，将十消费者纳入受影响核验；旧契约/旧指纹证明 SHALL NOT 自动成为新基线资格。历史账本与验证材料 SHALL 保留，最终依赖闭包冻结并完成相应最小补验后 SHALL 追加新证据，SHALL NOT 改写原记录或豁免漂移。不相关已验收能力与可核验原始数据 SHALL 复用，SHALL NOT 因契约迁移重做全量采集。

#### Scenario: 仅修改 YAML 而代码白名单未升级

- **WHEN** Trader 清单允许 MARKET_DATA，但代码白名单、读取 API 或版本仍只允许旧授权产品
- **THEN** 系统 SHALL 判契约迁移未完成并阻断 A 接入验收
- **AND** SHALL NOT 以 YAML 声明替代实际读取证明

#### Scenario: manifest 升级影响非 Trader 消费者

- **WHEN** A 改动导致全文件 manifest 或共享 API 指纹变化
- **THEN** 系统 SHALL 按现有漂移规则拒绝旧指纹资格，并列出全部受影响消费者的最小重验范围
- **AND** 原记录 SHALL 保留可追溯，不要求重复全量采集，也不把旧证据重新标记为新基线有效

#### Scenario: 新契约只有原授权证据

- **WHEN** Trader 的新契约已有授权证明但缺必需行情来源、时效或回退证明
- **THEN** 系统 SHALL 保持对应 scope ineligible 并报告缺项
- **AND** SHALL NOT 因新增产品已列入清单而直接签发行情读取资格

#### Scenario: 无生产资格的 A 隔离验证

- **WHEN** 新 Trader 契约尚未获得生产资格而需要补验治理行情路径
- **THEN** 系统 SHALL 只允许经进程禁写和隔离账本保护的候选入口验证
- **AND** 生产读取、审批和 broker 提交 SHALL NOT 因候选成功而放行

### Requirement: Phase F 前置接收必须以版本化矩阵进行

Phase F SHALL 在切流前建立版本化的前置接收矩阵，逐项记录 A–E 各专项与数据流专项的实现、命令与证据的接收结果、缺口、责任方与依赖。矩阵 SHALL 以实际部署入口与当前路由的核验结果为准，SHALL NOT 以静态入口名称推定能力达标；仅旧业务不可运行且已退出安全时 SHALL 登记为不适用，不阻塞新需求所需能力。已具备可复用运行材料与缓存的项 SHALL 登记复用，SHALL NOT 重复全量采集。

#### Scenario: 静态清单中的旧入口已不可运行

- **WHEN** 某项新需求必需的入口经核验已不可运行
- **THEN** 该项 SHALL 在接收矩阵中记为未接收并注明核验方式与失败原因
- **AND** SHALL NOT 因清单中存在该条目而判定其已接收

#### Scenario: 已有可复用的运行材料

- **WHEN** 某项存在可核验的真实运行材料与缓存证据
- **THEN** 矩阵 SHALL 登记复用该证据并引用其标识与时间
- **AND** SHALL NOT 为重新确认该能力而重复执行全量采集

### Requirement: 已有资格证据必须受控登记后才可被切流使用

对既有资格证据，系统 SHALL 按 `domain_id + consumer_id + contract_version + scope` 逐项校验原始记录、产品覆盖、代码与配置指纹、前置证据、时间有效性与回退证明，通过追加式证据接口落账后再查询资格，并记录操作者、证据引用与登记结果。汇总报告的 passed、任务勾选或隔离运行成功 SHALL NOT 转换为未经验证的生产证明。缺项 SHALL 保持 ineligible 并列出最小补验范围。该登记 SHALL NOT 改变任何路由，SHALL NOT 生成风控或人工批准。

资格登记 SHALL 在受影响 scope 的计划内实现/配置修改、动态接入和所需切前回滚完成的最终基线上执行。材料盘点可提前；无资格的隔离补验 SHALL 不依赖先登记生产资格。短 TTL 项 SHALL 提供可重复的最小刷新命令，并在切换时重查；SHALL NOT 将 ledger missing 全部解释为等待 TTL 窗口。共同依赖修改 SHALL 纳入全部受影响消费者，不以单个 scope 冻结掩盖共享面仍未完成。

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

静态导入扫描 SHALL 仅作辅证，SHALL NOT 以 compliant 或入口字符串替代动态记录。角色 SHALL 依据自身契约经治理产品、投影、内部状态或批准授权读取；SHALL NOT 强制所有角色直接导入同一 API 来证明合规。真实开周期入口与研究快照/run contract SHALL 使用经明确裁决的同一必需输入判定，未解决的 CATEGORY/TASK 分歧 SHALL 阻断对应链路验收，SHALL NOT 默认选择较宽松结果。

完整决策请求 SHALL 在启动前固定必需分析类别、各类别所选任务及依赖、scope 和 Fundamental 模式；开周期前 SHALL 同时核验必需类别齐全和所选任务依赖闭包成功或合法复用。Fundamental 选 Event SHALL 满足 Event，选 Routine SHALL 满足 Routine；SHALL NOT 以另一模式替代所选模式的缺失或失败，SHALL NOT 要求未选模式同时运行，也 SHALL NOT 用任一 Fundamental 模式替代其他必需分析类别。局部研究运行完成 SHALL NOT 单独构成完整决策资格。

#### Scenario: 所选 Event 失败但 Routine 有有效结果

- **WHEN** 完整决策请求选择 Event，而 Event 缺失或失败，仅 Routine 结果有效
- **THEN** 系统 SHALL 阻断创建 decision cycle 并报告所选 Event 缺口
- **AND** SHALL NOT 用 Routine 满足本次所选 Fundamental 输入

#### Scenario: 所选 Routine 满足且其他必需类别齐全

- **WHEN** 完整决策请求选择 Routine，其任务及依赖成功或合法复用，其他必需类别及 scope/schema/血缘/时效/hash 均通过
- **THEN** 完整性门禁 SHALL NOT 因未运行 Event 而阻断
- **AND** 后续风险、审批及交易门禁 SHALL 仍独立适用

#### Scenario: 所选模式满足但其他必需类别缺失

- **WHEN** 所选 Fundamental 模式满足，但完整决策请求的其他必需类别缺失或无效
- **THEN** 系统 SHALL 阻断创建 decision cycle 并报告缺失类别
- **AND** SHALL NOT 以 Fundamental 结果替代该类别

#### Scenario: 静态扫描通过但没有实际引用

- **WHEN** 某角色只有静态 compliant 结果，缺实际 run、产品/文档/vintage 引用或必要投影 hash
- **THEN** 实际接入 SHALL 保持未验证并列出最小补验
- **AND** SHALL NOT 把该角色算作实跑接入完成

#### Scenario: 快照完整性与运行契约冲突

- **WHEN** snapshot complete 而 run contract 因必需任务缺失判 incomplete
- **THEN** 系统 SHALL 报告分歧并阻断该情况的接入通过结论
- **AND** 权威规则裁决后 SHALL 从实际开周期入口验证一致的必要输入拒绝行为

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

Chief、Risk、Trader、Clerk 的实际接入 SHALL 在无券商写权限的影子或纸面环境验证，绑定研究快照、内部状态、决策修订、审批与订单与成交回报，覆盖多轮风控、重复恢复、部分与迟到成交及绩效重建。可复用隔离账本与券商模拟；允许经全部审批/执行门禁向无网络 FakeBroker 提交，由实际 Trader 生成非空订单而非预写 submitted/filled。真实券商连接（含 IBKR Paper）本 change 仅只读核验，生产 C3 保留。隔离证明 SHALL NOT 被冒充为当前生产账本完整性证明。

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

旧路径业务缺陷、新旧结果差异与旧侧缺失 SHALL 仅登记，不要求修复或差异接受；仍能触发、发布、提交或污染新状态的旧入口 SHALL 完成失权/关停证明。历史与共享状态若仍为新入口必需 SHALL 按新需求核验，不据旧问题分类豁免。未接线消费者可暂不切换，但其未完成状态 SHALL 明确保留，不用于宣告十角色接入验收完成。

#### Scenario: 旧路径存在已知问题但新路径已隔离

- **WHEN** 旧路径存在已知缺陷而新路径已验证隔离该影响并满足其需求
- **THEN** 处置 SHALL 登记旧路径问题但不修复旧路径
- **AND** SHALL NOT 以旧路径缺陷未修复为由阻断已通过的新路径范围

#### Scenario: 历史断链但当期数据完整

- **WHEN** 某消费者的历史数据存在断链，而当期必需数据完整
- **THEN** 该情形 SHALL 显式标记为 partial 并记录断链范围
- **AND** SHALL NOT 自动放行，亦 SHALL NOT 阻断与其无关的已验收读取路径

### Requirement: 前置门禁必须交付逐批 eligible 与 ineligible 报告

Phase F SHALL 交付逐消费者与逐批的 eligible 与 ineligible 报告，关联证据账本、实际接入运行与回退记录。仅证据与接入验收均通过的范围 SHALL 可进入实际切换；缺项 SHALL 只阻断受影响范围。完整自动交易 SHALL 仍要求全部必需输入与审批链同时满足。生产路由变更 SHALL 需明确部署授权，live 交易路径开放 SHALL 另需明确实盘授权；规划批准与前置通过均 SHALL NOT 构成该授权。

报告 SHALL 分列模块交付、动态隔离验收、资格签发与生产执行状态。门禁实现验收 SHALL 需要真实入口的正反行为及切前集成证明；全部 ineligible 既不禁止隔离验收，也不自动构成门禁验收完成。已获授权 SHALL 按具体动作/边界/scope 核对，SHALL NOT 无视既有授权笼统要求再次授权，亦 SHALL NOT 由未知来源库记录推断已获实盘授权。

#### Scenario: 报告缺项只阻断受影响范围

- **WHEN** 某一消费者因缺安全停止/版本恢复证明为 ineligible，而其他消费者证据齐备
- **THEN** 仅该消费者 SHALL 保持未切换或安全停止状态
- **AND** 其他已通过消费者 SHALL 可按其自身范围进入切换

#### Scenario: 前置通过但缺部署授权

- **WHEN** 逐批报告全部通过，但未取得生产路由变更的部署授权
- **THEN** 系统 SHALL 拒绝执行生产路由变更并报告缺少部署授权
- **AND** 前置通过 SHALL NOT 被记为已获部署授权

### Requirement: 取证基线与受约束文件不得被切流动作动摇

消费者资格同时受两类证据约束：纳入指纹策略的代码与配置文件内容，以及消费者契约清单自身的摘要。运行时切流 SHALL 只使用发布覆盖层与独立的控制状态，SHALL NOT 修改这两类已取证对象。确需修改时 SHALL 在最终状态上重新验证并追加证据、重算全部受影响消费者资格，SHALL NOT 改写旧证据或放宽指纹与清单标准。

#### Scenario: 取证后修改共同依赖文件

- **WHEN** 某项切流工作修改了被多个消费者共同纳入指纹的代码或配置文件
- **THEN** 该动作 SHALL 被判为动摇取证基线并须重算全部受影响消费者资格
- **AND** SHALL NOT 只为当前涉及的消费者重新取证

#### Scenario: 修改消费者契约清单

- **WHEN** 有人为便利切流而修改消费者契约清单（例如增删证据类型或回退路由声明）
- **THEN** 系统 SHALL 判该修改使全部已登记证据漂移并要求重新取证
- **AND** SHALL NOT 以「只是补充声明」为由绕过

#### Scenario: 切流实现本身触碰受约束文件

- **WHEN** 实现切流、影子或交易门禁的改动落在受指纹约束的路径内
- **THEN** 该改动的取证顺序 SHALL 安排在重新登记证据之前完成
- **AND** SHALL 在最终代码与配置上重算受影响消费者资格

### Requirement: 隔离接入验收不得以生产资格为前置

在消费者尚未取得生产读取资格时，系统 SHALL 提供隔离接入验收入口，使实际接入可在无生产资格条件下被核验。该入口 SHALL 运行于进程级禁写与隔离账本之下，SHALL NOT 产生生产路由变更、审批结论或真实账本记录；其产出的运行证据可用于资格登记。提交真实交易前 SHALL 仍按当次决策的 scope 重新验证资格，隔离验收结果 SHALL NOT 本身等同于生产资格。

#### Scenario: 无生产资格时执行接入核验

- **WHEN** 某消费者尚无生产读取资格而需核验其实际接入
- **THEN** 系统 SHALL 允许经隔离接入验收入口执行该核验
- **AND** SHALL NOT 因缺生产资格而使核验无法开始

#### Scenario: 隔离验收产生生产副作用

- **WHEN** 隔离接入验收产生了生产路由变更、审批结论或真实账本记录
- **THEN** 系统 SHALL 判该次验收无效并报告越界副作用
- **AND** SHALL 清除其产生的生产侧影响

#### Scenario: 以隔离验收结果直接放行交易

- **WHEN** 某范围仅有隔离接入验收记录而提交真实交易
- **THEN** 系统 SHALL 拒绝该提交并要求按当次 scope 重新验证资格
- **AND** 隔离验收记录 SHALL NOT 被当作生产资格


### Requirement: 五角色实际读取必须保留治理来源

系统 SHALL 在 fundamental/chief/risk/trader/clerk 实际消费点执行既有权限与当次 scope 门禁。研究输入 SHALL 经治理产品或投影，内部状态 SHALL 经 state API，Trader SHALL 经受限 runtime 行情与完整批准授权执行；Clerk SHALL 读取真实执行回报及审批上下文，并在步骤间与发布前重查资格。实际调用 SHALL 保留 API、来源 refs 与状态，SHALL NOT 用静态导入、预置 refs 或扩大产品权限代替接线。

#### Scenario: 实际五角色留存消费引用

- **WHEN** 隔离候选运行实际 Fundamental、Chief、Risk、Trader 与 Clerk 入口
- **THEN** 系统 SHALL 留存各入口真实 API/投影/状态/授权/执行回报引用及结果状态
- **AND** SHALL 保持报价、授权与发布门禁；隔离记录 SHALL NOT 本身构成生产资格

#### Scenario: Clerk 步骤间资格失效

- **WHEN** Clerk 已完成一个子步骤但当次 scope 资格随后失效
- **THEN** 后续子步骤与最终发布 SHALL 停止
- **AND** SHALL 保留已完成步骤的审计，SHALL NOT 为继续运行建立替代 broker
