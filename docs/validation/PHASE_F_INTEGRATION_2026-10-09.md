# Phase F 动态索引与全链集成验收（7.8 → 13.4 → 13.5）

## 7.8 动态证据索引

以原始数据库只读复算，未复制汇总 passed 作为结论。机器索引见 [dynamic_index_verified.json](phase_f_integration_20261009/dynamic_index_verified.json)，包括原始 report DB/body hash、固定 input hash/input store、两轮实际 workflow/replay ID、分离 side/store、matrix hash、实际 reads/refs、逐项断言、当前签核与适用性。三个报告当前可供明确签核但仍 unsigned；缺行情研究报告保留 failed/untested，不能签核。生产资格和激活另行核验。

| 实际证据范围 | 可追溯入口及原材料 | 当前状态 / 未测 |
|---|---|---|
| Layer、Information、Sector、所选 Fundamental、Macro、Technical | [六角色与固定模式](PHASE_F_RESEARCH_GATE_2026-10-08.md)、[观点血缘](PHASE_F_OPINION_AND_EXECUTION_2026-10-08.md)、上述索引内 Routine/Event 各自两轮的 matrix/read/projection refs | 隔离 fixture 动态验证；真实 Provider/模型与全部生产实体窗口未测 |
| Chief、Risk、Trader、Clerk | [五角色实际消费](PHASE_F_SAFE_READS_2026-10-08.md)、[独立全链与源码指纹](PHASE_F_INDEPENDENT_RUNNER_2026-10-09.md)、索引内真实审查/批准/订单/成交 refs | 隔离模拟；报价 A 两阶段固定，生产账本完整性/资格不是本结果 |
| 部分/迟到成交、多轮审批、恢复与绩效 | [7.5 原始执行恢复](PHASE_F_OPINION_AND_EXECUTION_2026-10-08.md) 与本轮 integration.xml 中实际业务复验 | 模拟外部响应；保留实际 ledger 与 XML 属性 |
| 调度权威、去重、发布权与预期集合 | [真实调度](PHASE_F_SCHEDULE_RUNTIME_2026-10-09.md)、索引 matrix/独立预期与实际 schedule refs | 隔离通过；生产 owner/观察/激活未执行 |
| 静态扫描、指纹及契约清单 | [十角色影响](PHASE_F_CONSUMER_DISPOSITION_AND_A_REGRESSION_2026-10-08.md) | 静态扫描仅辅证；当前源闭包与原运行一致不等于已登记资格 |
| TWS 真实账户/portfolio/open orders/completed orders/fills | 任务 7.13 | **untested**；未建立网络会话，不以 FakeBroker 或生产 SQLite 代替 |
| 生产部署、观察、实盘 | tasks 6/8/9/10/11 | 未执行；C3/授权/owner/路由不变 |

必须按 label + exact scope + input hash + workflow/replay ID + store 定位，`new` / `replayed` / `sim-1` 单独不是全局身份。两种模式各自满足自身固定需求，不要求同次同时跑两种。旧侧仅辅助诊断。

## 复验入口

使用 uv；原库只读，脚本以 exclusive-create 保存新观察，不覆盖已有结果。`index` 的源索引只提供定位，不提供通过判据；checker 重读原库与当前源码/配置。缺库、漂移、原报告内容不匹配直接拒绝。

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python scripts/verify_phase_f_integration.py index --index docs/validation/phase_f_independent_runner_20261009/evidence_index.json --out /private/tmp/phase-f-dynamic-NEW.json
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python scripts/verify_phase_f_integration.py trace --index /private/tmp/phase-f-dynamic-NEW.json --out /private/tmp/phase-f-chains-NEW.json
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python scripts/verify_phase_f_integration.py safety --index /private/tmp/phase-f-dynamic-NEW.json --production-db var/ats.sqlite --out /private/tmp/phase-f-safety-NEW.json
```

失败不得改原数据库或已有报告。修复业务实现需新 capture/run/replay/report，不能重新贴旧签核。仅本审计脚本和测试/文档新增，未改业务源码/配置，原 464 完整闭包、100 保护路径仍可复验；脚本自身摘要另存每份新输出。

## 13.4 / 13.5 验证记录

结论：**7.8、13.4、13.5 在选定隔离范围完成**，任务总计 **76/116 完成**、40 项剩余。

- [逐笔完整订单链](phase_f_integration_20261009/order_chains_final.json)：Routine/Event 各自 new/replayed，共四轮、四笔模拟订单；每轮六研究投影、完整 revision/hash、有效 review、人类 fixture approval、intent/receipt、trade 和 Clerk fills 逐项解析。非空才通过；空 trades、断投影/修订/审查/审批、缺回报/成交反例全部拒绝。
- [提交安全](phase_f_integration_20261009/submission_safety_final.json)：四轮各一笔真实 FakeBroker 接收及一条已持久化 IBKR `isolated_run_prohibited` 拒绝。重新读仲裁 SQLite 的全部 receipt，意图/route/generation/account/environment/payload hash 与提交时原记录一致；本地 trade/批准及接受结果逐筆核对。没有以空 trades 代替 broker 证据。
- [生产 before](phase_f_integration_20261009/production_before.json) / [最终 after](phase_f_integration_20261009/production_verified_after.json)：本轮全部测试前后真实 `var/ats.sqlite` 的 **52 条 trades、13 条 fills 逐行逻辑摘要一致**（包含 WAL）；原始四轮订单的精确 order_ref/批准/hash/隔离来源没有出现在生产账本。中途 after 也保留；此证明仅限本轮窗口，TWS 仍未测。
- 新反例覆盖已接收却无本地 receipt/trade、伪批准、误选真实 route、隔离来源逃逸、缺真实拒绝、仲裁回报缺失/hash/代次漂移和无成交支持的状态变化；均停止。旧合法订单与迟到成交用独立标识保留，新订单精确归因命中则停止；不靠 symbol/account/日期的宽泛排除。

本轮新增只读审计脚本，不更改业务 gate。真实 ShadowBroker 未被阻断时记录 accepted attempt、抛异常、isolated business entry 保存 failed 且不发布 trades 的实际停止路径复验通过；FakeBroker 回报退出隔离后写入外部 store 的真实发布点拒绝也通过。部分/迟到成交、重复 Clerk 与重新审批的实际 7.5 路径复验通过。后两者的外部行情/账户/模型/人类答复均明确为 fixture，没有网络。

| 原始测试证据 | 实测结果 | 范围 |
|---|---|---|
| [integration.xml](phase_f_integration_20261009/integration.xml) / [log](phase_f_integration_20261009/integration.log) | **53 passed / 169.756s** | 初版审计 17 项及真实 ShadowBroker/隔离授权/观点与执行恢复 36 项 |
| [auditor_final.xml](phase_f_integration_20261009/auditor_final.xml) / [log](phase_f_integration_20261009/auditor_final.log) | **18 passed / 32.776s** | 最终审计器，追加真实仲裁回报缺失反例 |
| [registry_negative.xml](phase_f_integration_20261009/registry_negative.xml) / [log](phase_f_integration_20261009/registry_negative.log) | **4 passed / 3.121s** | 仲裁 payload/代次漂移、已成交状态回退及 unknown 反例 |

去重后 **58 项有效通过（22 审计 + 36 实际业务）**；重叠执行不重复累计，不宣称全仓通过。原失败测试与历史证据保留。

文档同步后的 [records.xml](phase_f_integration_20261009/records.xml) 为 **20 passed / 2.721s**；本轮合计 78 项不同检查通过。两个 change 的 OpenSpec strict 通过，git diff --check 无错误；父 change 仍有此前 sector/layer-analyst 归档目标不存在的 INFO，未进行归档。[依赖与基线核对](phase_f_integration_20261009/verification.json) 确认三个任务全部前置完成、无未知依赖或环、业务源码闭包 464 路径与原运行一致。最终 production after 在全部测试之后取得并与 before 对齐。

补查原始仲裁库曾发现审计器过严：冻结提交时 status=submitted，实际 Clerk 后 status=filled，其他字段一致。首次全字段比对正确停止收口，但把合法生命周期当作漂移；随后只允许 submitted→partial/filled、partial→filled，且当前状态必须等于实际 trade，数量/成交由原归因审计核验。没有放宽意图/批准/账户/代次/payload 指纹。初版 dynamic_index/order_chains/submission_safety 保留为阶段观察，final/verified 才包含仲裁复核，不追改原账本或原报告。

原始账本继续保存在 `var/isolated/phase_f_independent_runner_20261009_v3`，本轮真实业务重跑根 `var/isolated/phase_f_integration_20261009_tests`；不得清理或复用 basetemp 重跑。后续测试须新目录。报告仍 unsigned，本次审计不代用户签核。

**后续注意/待修复项**：生产资格、报告显式签核、TWS 只读、交易仲裁/联合故障恢复、生产观察与最终冻结仍待对应前置。3.10 当时没有生产账本 before snapshot，不能追认那段历史运行期间逐行未变；本轮增加 before/after 证明并核验当前精确归因。

**为何现在不修**：本次是选定隔离范围的证据索引和全链安全验收；生产范围、网关和部署结果需要各自真实入口证据。隔离成功和只读 SQLite 核验不能替代 TWS 或擅自形成生产资格/实盘授权。旧证据和既有失败保留。
