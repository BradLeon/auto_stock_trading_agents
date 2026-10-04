## Why

Phase F 的读取切流不能仅凭既有数据平台代码、配置或已归档 change 推断目标 Dataflow 已贯通。需要在切流前逐域、逐消费者核验从来源刷新、准入、发布到读取和回滚的完整路径，补齐实际缺口，并对未通过路径保持稳定旧路由。

## What Changes

- 建立目标 Dataflow 节点与关键连线的实现、配置、owner、消费者、旧入口、证据和状态矩阵；以可复现的运行证据确认 `verified | needs_refactor | missing | blocked`，不把文档声明视为验收结果。
- 核验并按缺口最小补齐 Source Catalog 到 Refresh Controller 的有效调度绑定，以及结构化、非结构化采集和准入、quarantine、共享事实、Data Products、Runtime Data Gateway、Internal State API 的路径。刷新可以由受控外部调度器触发，但必须有可观察的执行和失败记录。
- 采用一个机器加载入口和两个领域注册表：`config/data/catalog.yaml` 只负责索引/装配，`config/data/structured.yaml` 与 `config/data/unstructured.yaml` 是持久化来源定义的唯一权威文件；将旧配置、动态 source ID 和实际采集入口作为迁移对照，按“已登记且旧流程有实际入口 / 已登记但旧定时流程未启用或无旧 owner / 旧消费者有输入但注册表缺项或身份不合格”三类逐源盘点 Layer Analyst 的数据。
- 所有会写入持久层的结构化和非结构化采集，无论来自定时、事件、手动还是允许的 cache miss，都必须先进入统一受管持久化采集队列；worker 是调用采集 adapter 和写入 raw/gate/publish 链路的唯一执行者。实时行情、期权和券商即时查询继续直达 Runtime Data Gateway，不进入该队列。
- 为 SEC filing 与业绩电话会定义可固定版本和追溯的上游来源链；优先使用登记的固定来源，不以搜索引擎结果作为持久化材料的隐式 fallback。SEC 原文必须回溯到官方 filing URL/accession，电话会必须按标的与财季对齐。
- 按用户确认，将 `sec_edgar_filing_body` 官方原文作为可选输入：获取或解析失败保留错误与来源状态，正文允许为空，记为“已接受非阻塞缺口”，不单独阻塞消费者或 Phase F 读取切流。此例外不代表采集成功，不放宽财务报表、电话会、其他来源及审批/交易门禁；仍须验证消费者的空值处理和错误可见性。
- 以目标文档 §4 的数据包和角色连线定义 Data Products→Agent 读取契约，覆盖 Layer、Information、Sector、Fundamental、Macro、Technical、Chief、Risk、Trader 与 Clerk；本 change 负责数据侧接口、来源/as-of/完整性和隔离门禁，Agent 内部业务逻辑及跨角色 Workflow 联调仍由相应 Phase 和后续全链路集成验收负责。
- 将旧 `Evidence Observer` 视为被 Layer Analyst 取代的历史角色，不新增同级角色或独立消费者；其仍有价值的受管来源和中性证据产品迁入 Layer 的产业链数据包，并保留旧身份墓碑与 lineage。
- 为各数据域和直接消费者记录唯一写入/查询 owner、读取契约、版本及 as-of/血缘、切流开关、回滚演练和待退旧路径；只有证据齐备时发布 Phase F 可读取切流的门禁结果。
- 对已验证组件复用现有实现；对缺口作最小兼容修复，并验证来源故障、陈旧、准入拒绝、修订、观点回写隔离及旧新差异归因。
- 本 change 不重复 Phase A 的中性证据写侧迁移、Phase C 的 Clerk、Phase D 的 Agent 职责拆分或 Phase E 的事件日历；也不执行 Phase F 的实际读取、调度或交易切流。

## Capabilities

### New Capabilities

- `data/refresh-orchestration`：将数据源更新策略与实际刷新执行、失败状态和可追溯运行记录连接起来，明确其与事件日历触发的边界。
- `data/target-dataflow-assurance`：逐节点/连线与逐域/消费者验证目标 Dataflow，并对 Phase F 发放或拒绝带证据的读取切流资格。

### Modified Capabilities

无。现有结构化/非结构化数据内容、查询、统一数据层及内部状态的业务语义保持不变；采集触发方式由可能直调收敛为统一受管队列，并增加跨路径的刷新、角色读取和验收契约。

## Impact

涉及 `src/ats/data` 的 catalog、采集、products、runtime、rollout/cutover，以及相关 CLI/运维入口和数据配置；涉及 `ats.memory`、Agent/Workflow、Clerk 作为边界与消费者的只读核验。将新增 Dataflow 覆盖与切流资格记录、可复现验收命令及运维说明。既有读取路由默认不变；未通过门禁的路径不得被 Phase F 的总开关带动切换。
