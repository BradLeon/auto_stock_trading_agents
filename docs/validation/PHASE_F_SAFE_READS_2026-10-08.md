# Phase F 安全回退与五角色消费边界

日期：2026-10-08。实施范围：5.7、7.11；以下先保留实施前影响方案，再记录实际结论。

拟补实际消费点的安全读适配：回退证据来自当前 exact-scope 路由历史，核对不可变证明摘要、有效期与旧实现指纹，再检查实际退役登记并执行一次真实旧读；只消费该次结果，失败停止当前调用，不发交易权限。实际 CLI/Dispatcher 的入口拒绝继续保留，未提供可消费适配的旧路不会被强制开放。

拟改 runtime_reads/cutover_routing，新增业务消费/投影读适配；接入 Fundamental Routine/Event 与 PEAD 数据节点、Chief 的内部状态、Risk runtime 账户入口、Trader 授权消费、Clerk 只读 broker/审批上下文。必要改动涉及 graph/chief、trader/execute、execution/clerk、consumer_api 及 manifest 依赖闭包（如需）；权限/价格政策不扩大，生产 C3、资格、route 和审批记录不修改。共享 API 或 manifest 若改变则影响全部十消费者；否则逐文件与当前 manifest 依赖核对影响，旧材料完整保留，最终冻结后由 7.17/6.1/6.4 补验，不豁免漂移。

改前全部 src/ats Python 文件字节与 SHA256、manifest/uv.lock 及生产只读库存已保存至 phase_f_safe_reads_20261008/before_sources.zip、before.json。现有未提交工作保留；候选运行仅使用 uv 与完整隔离，外部输入显式 fixture，无真实网络下单。本轮不提前验收 5.8、7.2/7.4/7.5、9.3/9.6 或生产切换。


## 结论与验收范围

**5.7、7.11 完成**；tasks 当前 **58/116**。这是实际消费点与安全回退的实现验收，不是最终十角色资格或生产切流验收。生产路由、资格登记、授权库存与 C3 未改变。所有业务模拟使用 uv 管理环境及完整隔离、无网络 FakeBroker；没有真实 broker 下单。

5.7：consumer_reads.consume_read 从当前 exact-scope 路由历史读取证明引用与 SHA256，核验 JSON 的 scope、有效期、旧实现摘要和当前依赖指纹，读取实际退役登记，再执行真实旧读；结果必须可用且有非空 refs，只消费这一份已核验结果。读取后重查撤销、退役与路由代次，避免检查后控制状态变化仍返回。证明撤销通过追加式 fallback_revocations 记录，禁止删除/覆盖原记录，不改变原证明字节；隔离审计新增监控该表。缺证明/过期/错误 scope/篡改/实现或依赖漂移/退役/实际不可读/撤销/紧急停用均阻断当前调用；不发交易授权。实际 Chief 内部状态读使用冻结 InternalFallback 适配；没有适配的入口继续拒绝，不强退到故障旧路。

7.11 的实际接线：

| 消费者 | 实际消费与动态证据 |
|---|---|
| fundamental | Routine 读取授权 Information brief 投影并保留 projection ID/hash；Event/PEAD 从治理财务快照、Consensus、层级证据与已接收文档包组装真实输入，发布保留 refs/文档 lineage。目标与隔离候选路径拒绝通过 Provider 补数，沿用冻结 cutoff |
| chief | 候选上下文由治理投影与 state API 组装；投影验证 owner/scope/hash/复用状态，内部状态记录 section_as_of 与完整性；legacy 外部路径保留兼容 |
| risk | 实际 review 入口与 Chief graph 的 risk_gate 独立绑定 Risk decision scope；读取只读账户 runtime、state API、规则版本，资格失败在账户读取/创建决策前停止 |
| trader | 实际 place_orders 读取治理批准授权并与原完整授权逐字段比对，再读取 A 受限报价；原审批/修订/账户/grant/代次/价格/幂等门禁保留 |
| clerk | 实际 clerk_run 复用只读 broker facade，消费账户、成交/完成订单和真实 DecisionAuditRepository 链；绩效使用同一 broker。步骤间和最终发布前重查资格，失败停止，保留已执行步骤，不重连替代 broker |

动态 read trace 保存 consumer、API、refs 与状态；正常业务留结构化日志，实际隔离入口将 trace 追加到 business_entries。五角色都有非空真实读取引用，参见 [业务产物与调用记录](phase_f_safe_reads_20261008/business-evidence.json)。研究数据和已接收文档由明确 fixture 提供，Broker 成交由 FakeBroker 模拟；这不是外部数据接收或真实市场可用性验收。

## 验证结果与可复现性

所有运行使用 `UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync`，未手动激活 venv。

- 相关回归：[regression-final.junit.xml](phase_f_safe_reads_20261008/regression-final.junit.xml)，**366 passed**。该次运行后又补充了 Risk 独立 scope 与 Clerk 分步重验，最终受影响业务检查见下一项；不将先前运行冒充最终版本全量验证。
- 最终业务检查：[current-business.junit.xml](phase_f_safe_reads_20261008/current-business.junit.xml)，**180 passed**；覆盖 safe_reads、Trader A、shadow_business、business_wiring、Clerk orchestration/e2e/compensation/linkage 与 internal_state。新 safe_reads 共 **22 个场景**，含真实 Routine/Event/Chief/Risk/Trader/Clerk、回退负例、撤销与步骤间漂移。计数存在重叠，不加总。
- 扩大检查：[expanded-final.junit.xml](phase_f_safe_reads_20261008/expanded-final.junit.xml)，**78 passed / 4 failed**。三条是 7.4 尚待接线的完整性判定：`test_a_complete_run_may_enter_the_decision_cycle`、`test_a_disagreement_between_the_two_contracts_is_reported_not_hidden`、`test_a_fully_consistent_run_reports_no_disagreement`；另一条是未修改 scheduler 的直接 import 旧断言 `test_scheduler_data_accesses_use_unified_runtime_products_and_pipelines`。
- 有限改前对照：[limited-baseline.junit.xml](phase_f_safe_reads_20261008/limited-baseline.junit.xml)，同四条失败。`intake_reference.py` 从改前源码 ZIP 校验 SHA256 后替换 intake_verification，其他当前模块/测试/配置不替换；scheduler 核对改前后字节一致。该对照只说明这四条在此有限对照中可复现，不代表完整旧分支重建或证明零回归。
- 初始失败及修正过程的 JUnit 仍保留。修正了严格回退 verdict 的旧测试适配、批准授权 API 未绑定当前 route、实际业务 fixture 与旧静态断言；没有隐藏初始红项。初次 Event 测试发现 runup 补算仍会尝试只读 Provider，已改为治理输入路径不执行该补算，最终场景禁止这些 Provider 调用并通过。

最终文档/API 守卫 **46 passed**，任务记录守卫 **13 passed**，见 [record-guards.junit.xml](phase_f_safe_reads_20261008/record-guards.junit.xml)；前者见 [final-guards.junit.xml](phase_f_safe_reads_20261008/final-guards.junit.xml)；OpenSpec strict 校验通过，git diff --check 通过。业务检查复现命令：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync pytest -q -o junit_family=legacy tests/test_phase_f_safe_reads.py tests/test_phase_f_trader_a.py tests/test_phase_f_shadow_business.py tests/test_phase_f_business_wiring.py tests/test_clerk_orchestration.py tests/test_clerk_e2e.py tests/test_clerk_compensation.py tests/test_clerk_linkage.py tests/test_internal_state.py
```

## 影响、保留证据与后续依赖

当前受指纹面 **44 个路径**，原角色权限/产品清单未扩大；manifest/共享 API 的修改影响全部十消费者。改前源码完整 ZIP 与 SHA256、只读库存见 [before.json](phase_f_safe_reads_20261008/before.json)，当前指纹、变更文件及库存对照见 [after.json](phase_f_safe_reads_20261008/after.json)。生产库存核对范围是前置脚本采集的控制/数据计数与授权库存，不宣称它覆盖全局所有业务行；实际 isolated_verification 独立核对交易、审批、路由及新增回退撤销等受保护表 row digest。旧冻结报告完整保留；本轮没有登记生产资格，旧指纹不能继续当作当前可切流证明。

后续宜先完成 **9.3、9.6** 的完整 Chief/Sector 实际读模型、必需投影可用性及跨进程解析，再按依赖推进 **7.2 → 7.4** 的决策完整性门禁，随后 **7.5** 完整恢复链。7.17 十角色动态补验与 6.1/6.4 最终冻结取证继续待办；3.9/3.10 双 runner、5.8 快照审批重验、5.15 联合动作、真实 TWS 只读行情 7.13 也未因此完成。C3 保持停用。
