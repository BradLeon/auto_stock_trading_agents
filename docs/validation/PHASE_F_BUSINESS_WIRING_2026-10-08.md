# Phase F 5.3 / 5.6 实际业务接线验证

日期：2026-10-08。结论：**5.3、5.6 完成，48/116 完成、68 项待办**。完成范围是实际发布/读取入口的控制与否定路径验证；生产切流、十角色最终接入、A 价格方案、真实 shadow 与完整门禁尚未验收。C3 继续停用。

## 5.3 实际发布点控制

`TradingMemory.save_task_projection` 与 `save_task_projection_envelope` 在编码/写入前检查 analyst_output；实际 Analyst CLI 也在分析之前拒绝关闭的发布边界。`DecisionAuditRepository.record_review/record_approval` 在审查/审批 SQL 前检查 approval_lifecycle，Chief 不再吞掉该边界的拒绝。Clerk 在启动、每个子步骤及完成/缺口发布前检查 clerk_publication；中途关闭时拒绝后续步骤，保留运行痕迹，不伪造 completed。

权威状态查询只读，不在业务 guard 中自动建控制库；缺库、坏库、缺边界状态均拒绝。isolated_run 只初始化本次隔离控制库的安全默认状态，不初始化生产库。

隔离/影子写入核验 SQLite `PRAGMA database_list` 的实际文件，不能用对象的 path 声明代替。隔离库在本次 root 内，影子库必须属于当前发布上下文，不能与生产 memory 路径相同；同时要求 broker 禁写。对进入隔离前已打开且被伪装 path 的生产连接逐个拒绝。Clerk 子步骤通过 bound_store 使用外层同一连接，避免隐式 get_store 写回其他库。五个真实发布点的 disabled 路径全部零写入；隔离正向写入只留在本地库。

## 5.6 当次业务 scope 资格门禁

实际九个 CLI 研究/决策/渲染入口、owned Workflow、Dispatcher、Chief graph/快照及 native consumer_api 已调用 runtime_reads。domain/consumer/contract 从真实清单取得；scope 包含本次 kind/id、配置展开的实体、冻结时间窗及适用的 event_id/version。产品列表不是 scope。Dispatcher 保留配置根和源摘要，配置漂移时停止，不把原计划重新解释为新范围。

每次入口均查询该 tuple 的资格，包括仍在稳定 legacy 的请求；查询不自动迁移。只有持久化的 exact-scope target 与接线证明/当次 eligible 同时满足才通过 target。未登记的另一实体不继承已登记实体的 target。target 资格失效且无已接入的安全回退证明时显式停止，安全回退适配仍由 5.7 实施。

Dispatcher 在返回历史结果/恢复投影前检查，调度复用前和 worker 内再次检查。新的混合实体运行只将不合格 scope 标为 blocked，独立合格分支继续；其依赖不得进入决策。Chief 快照还有独立读取门禁。owned Workflow 在认领 trigger 前检查；一个请求的入口预检失败会拒绝该请求，不冒充已完成跨请求安全回退编排。

CLI 顶层 `--read-scope-json` 接受按 consumer 键组织的真实业务 scope；嵌入调用可传 read_scope/read_as_of。Workflow 请求的 task_inputs.read_scopes 可用 task instance key 或 consumer:ProjectionScope.key，Chief 快照键为 chief:portfolio。显式实体必须匹配实际请求，时间须匹配 plan cutoff；没有历史 cutoff 的 CLI 必须在所给窗口内，不能用旧窗口为当前运行借资格。

native `read_input` 将产品查询 scope 与 business_scope 分列，后者须显式提供或来自已绑定业务入口；不同实体/消费者/cutoff 拒绝，异常在 payload 降级捕获之外。持久化输入省略 as_of 时继承绑定上下文的冻结 cutoff，不能按当前时间悄悄查询另一批资料；runtime/internal 等 current_only 输入仍不接受历史 as_of，也不把业务时点伪装成当前行情的来源时点。native API 没有 legacy 实现，生产 legacy 请求不能借它读取 target 数据。完整路径重定向且 broker 禁写的隔离候选可读本地数据形成证据；部分重定向拒绝，父进程与继承环境的子进程使用同一核验规则。隔离候选不成为生产 eligible/enforced。

## 验证结果及范围

- 最终相关回归 **562 passed**，其中新增 **40 个业务接线场景全部通过**；涵盖上述实际入口、关闭/中途关闭、旧连接、隔离审批、查询越界、scope/契约隔离、混合任务、TTL/撤销复查、历史复用、配置漂移、原控制/交易出口/契约守卫。见 [final-regression.junit.xml](phase_f_business_wiring_20261008/final-regression.junit.xml)。全程通过 uv，无手动 venv；业务测试用模拟 provider/transport，没有实际券商提交。
- 扩大检查额外发现事实重处理用例 **1 failed / 560 passed**，见 [regression.junit.xml](phase_f_business_wiring_20261008/regression.junit.xml)；HEAD TradingMemory 的同文件对照 **1 failed / 3 passed**，同一失败为 `test_reprocessing_retires_old_fact_views_but_keeps_history`，见 [head-facts.junit.xml](phase_f_business_wiring_20261008/head-facts.junit.xml)。同 ID 事实退役后重存仍不可读的语义/测试期待需数据层 owner 处理，并在材料验收中判断影响 scope。
- Chief 图另行检查 **3 failed / 5 passed**，见 [current-chief.junit.xml](phase_f_business_wiring_20261008/current-chief.junit.xml)；仅恢复 HEAD Chief 模块的对照同为 **3 failed / 5 passed**，见 [head-chief.junit.xml](phase_f_business_wiring_20261008/head-chief.junit.xml)。三条仍期待自动成交的旧测试与当前 C3 停用不符，本轮未启用自动下单。
- 上述对照只替换命名模块，使用当前其他代码和测试，不是整个 HEAD、更不是前 Phase F 全量归因。四条失败保留，13.6 继续承接；最终相关回归的绿结果不能解释为全套测试全绿。原全量 `45 failed / 3072 passed / 2 deselected` 保持原时点。
- 文档/记录守卫 **20 passed**；两 change strict、依赖无环、所选前置闭合、生产只读 inventory/新增 schema 未变、源码与漂移摘要见 [verification.json](phase_f_business_wiring_20261008/verification.json)。静态调用点清单扩展为 31 个，四个读/写边界的 guard 均可解析，但未向生产登记 enforced 证明。

target 场景显式模拟外部资格/接线证明/报告适配器，使用真实业务入口和 scoped route 存储；未运行生产 provider 或注册生产资格。这些局部测试不证明真实数据完整性或所有角色已完成 native 消费迁移。

## 指纹影响与后续工作

本轮使用 0.1 已确认的取证前修改范围。三个受保护文件漂移：consumer_api 影响 **全部十角色**，repository 影响 Trader/Clerk，Clerk 影响 Clerk。before/after SHA 与影响集合见 verification.json。配置清单、assurance、授权/state API、uv.lock 等其他前置摘要不变。旧原始证据、旧报告、旧资格和旧接线包都保留，不能重标为当前有效；尤其不能重新运行旧 verifier 的“所有摘要不变”断言来掩盖本轮漂移。

**后续注意/待修复项**：5.7 安全回退、5.8 决策/审批依赖失效；5.4/5.15 兼容/联合恢复；7.11 与 9.3/9.6 的完整消费和读模型迁移；7.14–7.17 的 A 契约/治理价格与受影响回归。随后 6.1 冻结，6.4 按新 business_scope 契约更新回放/运行角色验证脚本并追加证据，6.6/6.7 产出当次资格。旧脚本未提供业务 scope 的 native 调用现在明确拒绝，不能借沿用旧接口获得接收结论；最小补验可以复用原始材料，不全量重采。

**为何现在不修**：本轮授权实施 5.3/5.6，以上任务有独立依赖与验收；局部模拟资格测试不能证明生产十角色已合格、Chief/Sector 完整迁移或安全业务回滚。数据事实重处理及旧 C3 测试期待保留为明确待办，不能用开启下单/伪造事实来消除失败。生产六边界、owner、调度、授权、qualification ledger 未改，未执行生产 workflow 或真实 live 动作。
