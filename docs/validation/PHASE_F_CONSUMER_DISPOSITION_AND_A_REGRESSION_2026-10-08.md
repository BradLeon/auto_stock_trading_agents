# Phase F：7.7 / 7.17 逐消费者处置与 A 回归

结论：**7.7、7.17 完成；66/116，50 项待办。** 本文件不是生产资格或切流授权。

## 修改前影响确认

已保存 `phase_f_disposition_a_regression_20261008/before.json` 与 `before_sources.zip`：811 个源文件、测试、配置及规划文件；现有受保护面 65 个文件（含 manifest），生产数据库只读盘点。继承工作区改动保留，原验证记录不重写。

拟修改验证脚本，使指纹来自 manifest 的完整共享/逐消费者并集；核对 A 实际读取依赖，补齐遗漏的共享 API 文件。manifest 修改影响全部十角色，旧证据不得自动复用；本轮只生成隔离回归和补验清单，6.1 最终冻结后才由 6.4 追加资格。

逐消费者处置改为绑定具体 scope 和可核验运行产物。修正无证据时宣称“已验证隔离”的文字，保留 optional/no_coverage/FactSet 和 partial 政策。旧 Clerk 子步骤失败未影响总状态的问题只登记；4/5 组切流适配仍是工程依赖。测试时间夹具使用原本提供的固定时钟，禁止放宽未来版本准入校验。

## 逐消费者处置

处置适用于明确的隔离运行范围，不是生产资格计算。`ok` 仅表示本范围没有新登记的政策缺口；生产仍须实现接线、完整报告、资格与授权。机器记录见 `phase_f_disposition_a_regression_20261008/dispositions.json`，每行含 scope、实际产物 SHA256 和运行记录定位。

| 消费者 | 实际运行范围/记录 | 处置 |
|---|---|---|
| Layer | L4_interconnect，实际发布及 Chief 读取的 projection/hash | partial：保留实际输出中的证据缺失，不将不确定性改成完整覆盖 |
| Information | COHR，准入文档/version 与真实 brief 发布 | ok，仅所测输入；部分提取未产出不被当成完整提取成功 |
| Sector | ai_hardware，实际 Layer→Sector refs 与子投影 lineage | ok，仅本隔离配置；生产全 sector 范围未测 |
| Fundamental | COHR Routine，实际 Information→Fundamental；Event 另有实跑/门禁回归 | ok，仅所选模式和标的；optional SEC/no_coverage 不升级成必需输入 |
| Macro | portfolio，实际发布/读取 | partial：保留受控输入中 optional FactSet unavailable；不新增全局阻断 |
| Technical | COHR，真实入口消费明确市场序列 fixture | ok，仅所测序列；不证明实时 Provider/TWS 可用性 |
| Chief | DU1 / COHR / complete-chain，六类别快照与 revision | ok，仅此链；失效/缺项快照仍拒绝，不放宽模式要求 |
| Risk | 同链，真实规则/审查及审批前后报价 | ok；v2 权限与证据类型核验，价格异常拒绝不变 |
| Trader | 同链，实际授权/批准 revision、FakeBroker receipt | ok；重试不重复提交，价格变化须重审重批，第二次拒绝结束 |
| Clerk | 同链，两 exec_id、部分/迟到成交、绩效与跨进程恢复 | ok，另登记旧业务缺陷；要求子步骤/事实读回，禁止仅凭 completed 判定成功 |

历史断链、缺时间的否定用例仍为 partial，断链范围不能自动放行；独立 Technical 范围不被 Fundamental 的故障注入连带阻断。故障注入不是宣称生产存在某条特定历史断链。无实际产物的范围为 pending；产物字节漂移拒绝。工程缺口不能借“旧业务缺陷”豁免：4.2–4.7 的触发认领/发布接线、5.4/5.8–5.10/5.15 等切流适配仍须按原依赖完成。

旧 Clerk 问题：子步骤返回 errors 或 `performance.recorded=false` 但未抛异常时，总状态仍可能记 completed，且同窗口会复用。只登记、不修改状态迁移；本轮真实成功链另核对 reconcile errors 为空、performance recorded=true、累计数量/VWAP、两条 system fills 的 revision/hash/approval 绑定及重开结果。该缺陷不能证明失败的 Clerk 范围合格，也不阻断无关已验证研究范围。

## A 指纹与最小补验

受保护并集从 65 扩至 **72 个路径（含 manifest）**。新增共享保护覆盖 settings/watchlist/entities、配置解析/Ticker schema、保护面解释器与控制面；准入文档产品原本仅归 Fundamental，现由全部角色共同绑定。所有文件按完整内容 SHA256 核对，资格脚本改用 manifest 的共享与逐消费者并集，避免字面量清单漏文件。漂移影响全部十角色；原证据保持原字节，不自动升级。

十角色逐一建立具备全部必需类型的合成机制正向证据，再修改隔离 manifest 或共享 consumer_api 副本：由 eligible 转为 ineligible，原 history 完全保留。Risk/Trader v1 事件在 v2 查询中不被选择。合成机制的 eligible 只验证判定器，不能当成实际业务资格。非 Trader 的产品权限、消费者行及 optional policy 与本轮前 manifest 逐项相同；未增加权限或改变 optional/no_coverage/FactSet/partial 政策。

`minimum-reverification.json` 保存完整文件摘要、增量影响、十角色契约/证据类型/TTL、实跑定位与补验要求。清单中的 scope 是本轮观测到的投影/周期范围，**不是可直接登记的生产 RouteIdentity**。6.1 必须以 0.3/8.1 选定生产实体、时间窗、事件版本实例化范围并冻结实际闭包；6.4 才可追加该范围的资格证明。未完成的调度/回退等实现仍可能改变受保护面，本轮快照不代替 6.1 最终冻结。

最小补验顺序：先完成尚待实现的依赖及受影响边界 → 冻结实际版本 → 复用已接受 source/version/hash 与归档原始材料，重跑受影响契约、读取、lineage、fallback 与交易边界 → 追加资格 → 临切流刷新短 TTL。Technical/Trader/Clerk 1 天，Chief/Risk 7 天，Information/Fundamental/Macro 30 天，Layer/Sector 90 天。不重做全量采集；失效的是资格绑定，需要检查的原材料可复用。

## 验证与复核

运行统一使用 `UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync`。

| 范围 | 结果 | 原始文件 |
|---|---|---|
| 逐消费者政策、十角色漂移/旧契约、指纹 parity、产品时点 | 80 passed | `focused-current-final.xml` |
| 扩大实际研究/交易/治理/授权/Clerk 回归 | 224 passed / 1 failed | `business-regression.xml` |
| 修正测试时钟后 A 回归 | 61 passed | `trader-current.xml` |
| 最终 manifest 下观点 lineage、非空交易/恢复与价格变化拒绝 | 4 passed / 9 deselected | `business-current.xml` |
| 文档/记录/切流守卫 | 86 passed | `record-guards.xml` |

计数有重叠，不相加成全量回归数，未运行全仓库测试。扩大检查的唯一失败是测试在 collection 时生成“1 分钟后”的时间，执行前已经过约 6 分钟，所以实际正确拒绝为 stale 而测试仍期待 future。只修测试为执行时生成，生产报价校验不变。`clock-probe.json` 使用 before ZIP 中的原测试与当前测试，模拟相同 360 秒等待，分别复现原失败及修正后未来报价拒绝。原失败 XML 保留。上轮产品测试的固定 observed_at 与实时 fetched_at 混用也只修夹具传入已有 now 参数；未来文档版本准入校验不变。

`business-evidence.json` 从 JUnit 属性提取实际 refs、投影、审批/报价、成交与 Clerk 输出；最终复跑同名属性取最终版本，其他旧运行保持原时点。`execution_records.zip` 对真实运行产生的隔离 SQLite 作只读 backup，连同实际配置/材料保存；没有预写订单。`before_sources.zip`、`after_sources.zip` 及摘要可复核完整文件，旧报告及其冻结产物均未改写。`verify.py` 验证源码/产物字节及生产控制/资格数据库只读 inventory；后续实现改动会如实报告漂移。

复核命令：`uv run --offline --no-sync python docs/validation/phase_f_disposition_a_regression_20261008/verify.py`。若重新生成运行记录，先按测试命令使用持久 `--basetemp` 生成 JUnit，再执行本目录 `build_evidence.py`，不可用历史 JSON 冒充新运行。

## 后续依赖与限制

7.8 仍依赖 3.10；3.9 仍依赖 4.6，因此下一步可先推进 **4.2 → 4.3/4.4 → 4.5/4.6**，同时按依赖推进 5.4/5.8/11.2，再做实际双跑/跨进程重放。6.1/6.4 不因本轮补验而提前完成。

实际股票范围与模型、初始账户/行情、人工答复仍为明确 fixture；六角色入口、准入文档读取/发布、风险规则、审批绑定、模拟出口、Clerk/rebuild 是实际实现。最终恢复为同一路径重开，不是新旧路径迁移/ShadowPack 双跑。旧缓存 fallback 全量脚本未在生产范围重跑，本轮核验其完整指纹函数和配对机制测试，不宣称旧 fallback 已全部通过。真实 TWS 只读、生产全范围/全历史、IBKR Paper/live 写入未验收；C3/生产路由/资格未改变。
