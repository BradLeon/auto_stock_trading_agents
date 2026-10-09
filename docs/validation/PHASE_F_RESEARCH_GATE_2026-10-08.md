# Phase F 研究读模型与完整性门禁

实施顺序：9.3、9.6 → 7.2 → 7.4。实施前影响方案：补 Chief 必需投影读取/渲染与跨进程解析、Sector 实际 CLI 投影消费及兼容，运行六分析实际入口，再将已裁决 CATEGORY + 所选 TASK 依赖闭包接入实际开周期。拟修改 Chief assemble/graph/state、Sector review/CLI 读适配、Dispatcher/phase_e/run_contracts/snapshot 及验证 harness；必要共享 consumer_reads/native API 与 manifest 闭包一并核对。共享面或 manifest 摘要变更影响十消费者，旧报告保持不变，当前资格按最终冻结基线重验，不扩大角色产品权限。

改前完整源码与摘要、生产只读库存见 phase_f_research_gate_20261008/before_sources.zip、before.json。所有业务验证在 uv 环境运行；实际模型响应、接收数据与只读市场数据使用明确 fixture，调用真实业务编排与发布。生产授权/资格/路由和 C3 不修改，不连接真实 broker 写入。


## 实际实现与验收边界

- **9.3**：Chief 对全部配置 scope 执行不可跳过的投影可用性检查；逐项验证 schema/content hash，渲染所有 ID/hash 与完整内容，缺失时在模型/周期前停止。跨进程读取实际落库引用，不用六个代表产物掩盖其他 scope。
- **9.6**：实际 `ats sector html` 在候选路径消费 SectorAllocation，实际 Sector review 从治理 LayerAnalysis 读取依赖；缺输入/当次资格撤销停止。兼容旧 SectorReview 的报告读取单独保留，不把它当作候选路径验收。
- **7.2**：使用默认 Dispatcher adapters 与真实角色入口、compute/publish，运行六分析角色，以及单 Technical、Information→Fundamental Routine、Layer→Sector 两条 CLI 子流程。实际文档通过隔离的持久采集队列 lease 和 document_assets.ingest 接收，Information 经 DOC_DATA 保存 document/version/publication refs。
- **7.4**：保存 `decision-requirements-v1` 的完整计划、profile/version/hash、source config hashes、所选 task/scope/schema/依赖、outcomes 和精确 manifest。Chief 组装、模型调用、首轮 Risk 与周期持久化重新解析同一要求；Dispatcher 还核验持久成功 attempt 和本次成功复用记录。Event/Routine 均通过实际六角色→Chief No Action 周期、跨进程校验和独立 Chief CLI。所选模式真实业务计算被注入失败时，另一模式的有效产物不能放行。独立 Chief 缺省 Routine，PEAD 收口显式 Event。

`config/workflow/decision_profiles.yaml` 的 technical source_configs 缩进错误一并修正。低层 snapshot/open_decision_cycle 与 intake 助手也按同一已选闭包拒绝缺失/过期依赖；历史 snapshot inventory 的任意模式查看不作为 Chief 决策依据。

业务 fixture 将 sector 配置限制为 L4_interconnect，PEAD targets 限制为 COHR，保留完整六类别 profile；旧 Chief 单元 fixture 使用两个 layer 和一个 target。外部市场序列/公司 observations/模型结构化响应是明确 fixture，角色入口、准入文档、DataProducts/ConsumerAPI 编排、投影发布、引用读取和 No Action 审计落库为实际业务实现。optional FactSet/no_coverage 与部分提取失败保持可见。**不证明生产全标的覆盖、真实模型判断、真实行情/TWS 可用性或生产资格。**

测试初期曾遗漏 runtime 的底层外部 transport fixture，出现只读 Provider 连接尝试并失败，已补齐 underlying transports；最终业务 fixture 禁止未声明 socket 网络连接。不曾连接 broker 写入。保留所有初期失败 JUnit，不覆盖成成功。

## 原始证据与检查

- `research-entries.junit.xml`：按 9.3/9.6 → 7.2 顺序得到 **38 passed**，含实际六角色与三条 CLI 入口。
- `fixed-initial.junit.xml`：最初 **6 passed / 2 failed**；两条为测试读取不存在的 plan 字段、误写 SQL 字段，真实 Routine No Action 已落库；修正断言/列名后重新验收。
- `research-final.junit.xml`：扩大完整性回归 **189 passed / 1 failed**；旧 vintage 单元 fixture 使用模块加载时刻加一分钟，长批次运行后比实际旧产物 created_at 更早，修正为实际发布时刻后补验。
- `selected-contracts.junit.xml`：最终低层 snapshot / intake / workflow contracts **117 passed**，包含所选模式失败不能被另一模式替代，以及未选模式不强制运行。
- `gate-final.junit.xml`：最终业务门禁/Dispatcher/Chief 验收 **46 passed**；包括 Event/Routine 实际周期与跨进程重读、损坏要求/manifest/配置/lineage/过期拒绝以及实际模式失败反例。
- `related-regression.junit.xml`：受影响 Trader A / 读写 scope / Chief / 授权 / assurance / state 回归 **217 passed / 1 failed**。失败 `test_single_real_order_submission_path` 的 grep 静态断言把既有 shadow_execution 的真实 broker **拒绝探针**也算作第二条真实下单路径。改前 ZIP 已含该调用，且该文件本轮字节未变，见 `static-order-guard-baseline.json`；不宣称该断言通过、不改写为零回归。上轮另有 scheduler 静态 import 断言失败，本轮未执行/未解决。
- 本轮 scoped F/I lint、OpenSpec strict、diff check 均通过；record-guards-final.junit.xml 的文档/架构/指纹/CLI 守卫 **67 passed**；全量测试未重跑，13.6/13.7 未勾选。

成功用例的 JUnit properties 保存实际 call chain、模型 fixture 上下文、发布 projections、CLI result、decision run、No Action cycle 及跨进程 plan/requirements hash；另导出 business-evidence.json 方便审阅。静态索引 current-static-index.json 只作辅助，不登记生产资格。

## 漂移与后续

Information admitted document 接线是六角色验收发现的必要共享修改，已纳入本轮影响范围。manifest/shared read API、固定决策要求、计划/Dispatcher/snapshot/schema、角色读模型、CLI/PEAD 选择入口和 profile/source config 依赖纳入指纹闭包，当前 **61 个路径**。全 manifest 摘要变更影响十消费者；旧证据保留，当前资格仍须最终冻结后由 7.17/6.1/6.4 最小补验，不能沿用旧 hash，也不需要重复全量采集。

本轮完成四项不代表完整 Phase F 或可切流。后续可推进 **7.3** 的实际观点/准入/回写拒绝验收；**7.5** 已具备前置条件，需实际非空 Chief→Risk→审批→Trader→FakeBroker→Clerk、多轮/重复恢复/部分与迟到成交。3.9/3.10 同输入业务双跑、5.8 审批快照漂移、5.15 联合动作、生产资格/部署动作和实际退役继续待办。生产 route/authorization/C3 保持原状态，真实 broker 写入不在本轮执行范围。

当前 tasks **62/116 完成**，54 项待办。after.json/verification.json 保存冻结源码与指纹闭包摘要、JUnit 计数、生产库存比对与证据 SHA256；生产库存与改前一致，逐次隔离入口另执行受保护表的前后检查。本库存比对不是生产账本完整性证明。

只读复核：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_research_gate_20261008/verify.py
```

该命令只核对本轮冻结源码/原始证据及生产库存，不登记资格、不改路由、不调用业务模型或 broker。
