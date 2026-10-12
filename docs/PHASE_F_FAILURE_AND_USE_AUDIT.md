# Phase F：13.6 失败归因与 12.2 用途核验（2026-10-10）

验收依据为 [TARGET_WORKFLOW_DATAFLOW.md](TARGET_WORKFLOW_DATAFLOW.md)；所选任务的依赖闭包决定必需范围。选 Event 必须满足 Event，选 Routine 必须满足 Routine，其他必需分析类别不被替代。旧结果一致、旧流程成功、全仓全绿均不作为新入口的前置。共享代码、准入、血缘、隔离、停止恢复与历史保留仍需验收。

**13.6、12.2 审计实施已完成；当前 101/117 完成、16 项待办。** 所选用途覆盖仍 pending，生产资格与退役未通过；以下先前待办判断保留为历史记录，由文末最新澄清取代。 工程门禁与本轮四标的财务数据用途已通过；所选 workflow 的实际用途绑定和生产资格按后文缺口继续核验。

## 当前发现与修复

权威发现命令使用 `UV_OFFLINE=1 UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache ./scripts/run_tests.sh --tb=short --junitxml=...`，入口同步 all extras，临时目录位于仓库 `tmp/pytest-basetemp/`。完整本机发现为 **3678 passed、111 failed、0 errors，共 3789 项**，耗时 5008.81 秒。执行期间没有源码修改。日志及 JUnit 为 `docs/validation/phase_f_final_gate_20261010/local_discovery.*`。先前中断的沙盒发现不计入完整统计。

0.4 固定的 Phase F 前提交为 `6b72d05b79774b9369d324da94da15dd56b56fa0`，该提交没有跟踪测试文件。不能据此宣称所有红项都在 Phase F 前存在，也不能用已实施后的 HEAD 自比较宣称“零回归”。本轮按实际失败、代码与 fixture 的因果、新契约可达性和替代门禁逐例处置。前次 143 条失败/error 在本次发现中有 59 条通过、83 条仍失败、1 条已由“运行路由切换保持指纹”新测试替代；新失败也单独登记，没有只看历史清单。

本轮修复：

- 共享中立事实：同内容被撤销后再次合法提取，追加新的有效 vintage；保留原 superseded 行与投影。验证历史撤销间隙、重提取幂等及倒填时点拒绝，不通过清除旧 superseded 状态改写历史。
- 验收输入：畸形 projection/model 输入显式判失败，避免 `list.get` 异常中断归因；缺证据与错误输入继续拒绝。
- fixture：补齐 FactSet 完整 catalog、使用类型化账户输入、按当前月度发布契约检查 freshness；Intake drift 用未修改的保护依赖建立 fixture，生产漂移守卫保留。
- 静态守卫：退休元数据/指纹路径与活跃权重分别核验；共享闭包继续保护十消费者。Clerk 原 worker 在停止后的 read generation 拒绝保持，未放松代次守卫。
- 原始图表：两项 FactSet OCR 测试直接重开既有 content-addressed PDF，并检查 PDF 与图表 hash，不再依赖已删除的 `/private/tmp/ats-factset-review` PNG。两个测试通过；未重造材料。
- 本地验证记录：同步保护并集 545、5.16 和 99/117 的当前记录。保留历史统计。

逐例台账为 `docs/validation/phase_f_final_gate_20261010/failure_review_current.json`，保留每条原始消息/trace、用例源码位置及摘要、当前复验引用、新路径关联和替代用例。首轮修复检查 **216 passed、1 failed**；剩余失败为账户序列化后实例身份比较，改为类型化值相等，后续用途/门禁检查 **31 passed**。图表复验 **2 passed**。本次发现的 111 个原红项已有 **38 项按相同用例 ID 复验通过**；其余 73 项已按每条原始 trace、用例断言和当前替代门禁核对，排除具体旧 fixture/预期；共享实现仍可达且继续验收，没有按模块名豁免运行时错误。

新入口稳定复验 `current_safety_stable.*` 中独立 Routine/Event 全链与跨进程重放通过；集成模块随后出现 21 条同源 fixture error（32 条已收集用例，11 passed、0 failed、21 errors；运行中断，不是全门禁完成）。实际账本说明账户快照已 78 秒，超过 60 秒守卫；`complete_snapshot` 只是 Dispatcher 的快照就绪标签，不是账户拒绝的真实原因。已修正正向 fixture 在每次模拟券商查询时生成新响应，不修改真实 TTL/审批/重审规则。最终复验使用 `final_current_gate.*`，包括模块内重新生成的 Routine/Event 全链与新进程重放。此前 `current_safety.*` 与脚本修改重叠，实际 capture 以 `implementation drift during capture` 拒绝；该趟中断结果保留，仅作诊断，不算稳定实现的通过证据。指纹 `repair_diagnostic_fingerprint.json` 只用于本轮稳定性检查，**不是 6.1 最终冻结**。

最终广域门禁 `final_current_gate.*` 实际为 **413 passed、1 failed**，414 项均收集；唯一失败是新增历史核验 fixture 以只读模式初始化库而缺表。修正仅发生在隔离测试 setup，核验器仍只读。其后补查 hash 有效的畸形模型 row，修复其未处理异常，并强化原畸形 projection 反例，使其不再先因 hash 不匹配退出。

受影响门禁 `final_affected_gate.*` **82 passed、0 failed/error/skipped**，耗时 1512.82 秒，重新生成 Routine/Event 的完整新入口与独立跨进程重放；正式报告、畸形输入、共享事实、历史保留及用途缺失拒绝通过。两轮预先指定用例按 ID 合并为 **471 个门禁用例全部通过且无遗漏/跳过**，不使用失败后挑选通过项的集合。三个受保护差异仅为 `acceptance_assertions.py`、其测试和 stage-one 历史测试 setup；受影响复验期间指纹未漂移。台账记录 111 个原失败和追加发现，原始失败/中断记录不删；未声称全仓全绿或相对 0.4 零回归。

**13.6 据此勾选完成。** 它不代表 12.2 数据覆盖、6.1 最终冻结、6.4 真实证据、6.6 资格报告或生产部署已完成。最终诊断指纹为 `final_affected_diagnostic_fingerprint.json`，仅作稳定性记录，不发布冻结或资格。

## 原红项的排除对象与替代验证

下面排除的是具体旧 fixture/预期，不排除表内共享实现。逐例原始 trace、断言及通过用例 ID 仍在 `failure_review_current.json`；未声明这些失败在 Phase F 前已存在。

| 旧失败组 | 数量 | 冲突及当前验证 |
|---|---:|---|
| batch CLI / manifest | 22 | 旧 global/product 清单、文字 fallback 及 ready 状态；新 exact scope 的许可、报告、plan/commit 一致性和安全停止验证 |
| read cutover / cutover CLI | 19 | 旧逐边界迁移与返回结构；新联合兼容、当前资格、实际报告、撤销、恢复和不扩大范围验证 |
| Chief / snapshot | 12 | 缺当前研究资格、either-mode/dual-read 预期；新所选模式、其他必需类别、实际引用和缺投影拒绝验证 |
| Chief 写入口扫描 / Phase D | 4 | 隔离探针被字面扫描计作真实出口、tmp 库缺隔离认领；新完整 Routine/Event、模拟交易账本及真实出口禁写验证 |
| Chain regional | 1 | 旧读取触发采集但没有 managed queue lease；保留租约拒绝，新 Layer 消费治理产品 |
| Evidence role / scheduler 扫描 | 2 | 退休消费者名和退休 producer import；活跃角色边界、Layer 产品与受管队列入口验证 |
| FactSet Earnings Insight | 4 | 旧固定十日龄、未满足当前分组发布的 fixture 与 legacy 文本；当前月报、分组准入、实际 PDF/golden、历史版本和 rollback 验证 |
| Research | 4 | 旧文章 fixture 无 admitted version/publication；新 Information 实际发布与文档血缘验证 |
| Sector allocation / HTML | 5 | 未消费的旧 refs、未配置 test_sector；新实际任务引用和已配置 scope 的 CLI 渲染/拒绝验证 |
| **合计** | **73** | **新必需行为与共享安全继续验收，旧差异不成为生产就绪证据** |

## 12.2：用途覆盖与历史引用

从当前 owners、schedules、profiles 和新契约构建九个备选 plan，共 **170 条任务 scope 出现记录**，另列 Chief/Risk/Trader/Clerk 四种条件用途。它们不是必须同时运行的 170 个生产任务；Event 仍需实际 source/version，交易需精确 revision 与 Boss 审批。盘点模板不证明实际执行。

新增只读入口：

```sh
uv run python scripts/prepare_phase_f_stage_one.py cache-audit \
  --inventory "$RUN/required_uses.json" \
  --data-cache var/data.sqlite --internal-cache var/ats.sqlite \
  --out "$RUN/required_cache_history_audit.json"
```

该入口不创建源库、不迁移 schema、不调用 provider、不授予资格；独立读取两个现有数据库的快照。缺库立即拒绝。产品 allowlist 不等于每次必须读取全部产品，候选缓存不能冒充实际用途的准入/完整性或报告适用。

本机实际结果：32 个盘点实体中，财务候选非空 7 个、Consensus 30 个、中立事实 11 个、admitted document 29 个。**不能把这四项合并解释成所有实体都必需这四种输入。** 配置的 Fundamental 11 个目标中，GOOG、SKHY、ASML、COHR、LRCX、LITE、AVGO 共 7 个缺可用财务快照，另有其余缺项须按每个实际用途核验；当前全目标 Fundamental 覆盖不成立。缓存非空也未证明新鲜度、字段完整性和所选 Event 材料适用。

历史文档版本共 460 个：449 个按原路径/正文 hash/字符数重开成功，11 个失败。失败版本路径均来自历史 pytest 临时目录；逐版本核查表明当前 admitted 候选没有选择它们，按版本 ID 与保守的裸 document ID 核对，中立事实、旧 observations 与 structured evidence links 引用均为零（`unresolved_history_data_reference_check.json`）；现有 `var/ats.sqlite` 全部表也没有引用命中。此结论只针对本次现有库快照，不证明库外历史不存在；保留 11 条原记录，不删除、不补假 hash、不把失败版本变成可用。对真正被必需用途引用的不可解析历史仍拒绝。

当前通用 `COMPANY_DATA(kind=financials)` 的 `complete` 表示返回了已接受的财务行（`consumer_api.py` / `DataProducts.company_financial_snapshot`），不等于本次公司研究用途所需字段、报告期间与 Event actuals 均完整。12.2 仍需把实际读取绑定到所选模式和用途核验；本轮不把非空 packet 或完整包 helper 的单一标签当成新需求达标。

数据发布侧 `required_financial_package_check.json` 另对 Fundamental 的 11 个目标检查完整报表包：7 个没有完整包；MRVL、MSFT、NVDA、TSM 有完整包，但超过现有配置的 240 小时时效条件，全部 `ready=false`。这是现有数据契约的结果，不新增 TARGET 的固定时限，也不据此单独判定 Phase F 资格。

在新隔离库实际查询 COHR，DefeatBeta 成功接收 265 行、0 quarantined；Yahoo 财务备选来源接收 117 行；官方 SEC Company Facts 接收 1145 行。最新 2026-06-30 包的 12 个字段在前两个来源均齐备，但均因资产与负债加股东权益相差约 3.34705 亿美元（1.829%）被现有质量门拒绝；官方 SEC 来源未满足现有报表包 helper 的季度口径要求：最新 2026-06-30 期间缺 revenue、gross profit、operating/net income、cash flow、capex 与 EPS 的 quarter 行，见 `real_financial_probe/sec_package_diagnosis.json`。这是该 helper 的完整包诊断，不替代新 Fundamental 本次用途的字段/期间判断，不是 SEC 接口权限失败。没有将多个来源的任意字段拼成可发布包。原始回执在 `real_financial_probe/` 和 `real_financial_probe_{defeatbeta,yfinance,sec}.log`，生产缓存、source mode、资格与路由没有修改。

官方原始数据进一步解释了该差额：同一 2026-08-14 提交的 10-K（accession `0000820318-26-000020`）、同一 2026-06-30 期间，`StockholdersEquity` 为 10,903,495,000，而 `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` 为 11,238,200,000；差额正好是 334,705,000。资产 18,299,852,000 = 负债 7,061,652,000 + 包括非控股权益的总权益 11,238,200,000。证据为 `real_financial_probe/official_balance_fields.json`；三个原始 artifact 的 hash 均重开校验通过，见 `artifact_integrity.json`。不能将归母股东权益改名为总权益，也不能用恒等式残差伪造非控股权益。两个非官方来源的归母股东权益、负债、资产均与官方值一致。

现有发布侧 `financial_quality` 用“资产=负债+归母股东权益”检查本案例，因遗漏非控股权益而误报；不能把该误报当作新用途原值错误。该 helper 和原始失败仍保留，实际新用途按字段语义、实体/期间/币种及血缘独立核验。**是否另行修复发布侧质量检查器，已请求范围选择；官方新证据表明这不是原值修复的前置**。现有 Phase F 12.2 是覆盖核验，不默认扩大成旧检查器修复；新 Fundamental、assurance、资格验证脚本与 workflow 门禁没有调用该 helper。继续已授权的 13.6 与新用途核验。新用途尚缺实际运行/报告绑定的范围继续 pending，12.2 不据局部核验勾选。

本轮实际缓存及引用结果为 `required_cache_history_audit.json`。早期 `required_cache_audit.json` 使用错误的 `doc_type` 属性，已保留为诊断；经 `document_type` 修正的候选结果及可重复入口分别留存，不据错误脚本判数据缺失。

**12.2 暂不勾选。** 170 个模板尚未逐用途绑定实际运行的读取/准入/血缘/完整性、所选范围报告和条件角色引用；现有隔离 COHR 工程全链不能代表全部配置标的。下一步按所选 scope 补真实证据：缺输入保持拒绝，其他已就绪 scope 可以独立推进。实际取证由 6.4 承接，并由 6.6 给出逐 scope 资格；不据本轮 cache audit 自动升级生产资格或替用户签核。

收尾记录一致性检查 `final_record_checks.*` **20 passed**；OpenSpec strict 与 `git diff --check` 通过。这不是 13.7 的最终冻结后验收，13.7 保持待办。

## 工作边界

修复涉及共享保护源码，十消费者旧证据保留，6.1 前须复查闭包并按新指纹补验。当前未执行最终冻结、生产切换、生产报告签核、部署、真实下单或历史数据清理。C3 保留；FakeBroker 工程全链不能代替真实 TWS 资格。TWS 后续必要只读核验仍在本机沙盒外运行。

原始日志、JUnit 与账本留在 gitignored 的 `docs/validation/`；本报告及脚本纳入可审阅工作区，不强制提交验证资产。


## 12.2 本轮限定范围实际核验

用户本轮裁决：只基于已有财务快照的 **MRVL、MSFT、NVDA、TSM** 推进，不等待 GOOG、SKHY、ASML、COHR、LRCX、LITE、AVGO 的财务快照。前文全 11 个标的缺口记录保留为历史及全生产范围盘点，不能继续当作本轮限定范围的前置；九个 plan / 170 条记录是备选模板，不要求全部同时成功。生产 profile 未缩减。

新增 `prepare_phase_f_stage_one.py financial-audit`，运行前文件固定实体、source、期间、币种和逐 metric 的 period basis / unit，再在完整隔离控制面内实际调用 `read_input(fundamental, COMPANY_DATA, financials)`。源库与原始文件只读，不采集、不模拟 API 返回、不调用旧业务算法、不签核或授予资格。`complete` 仅作为入口状态，逐字段覆盖与原始证据重开独立核验。12 个字段是本轮已有包的显式测试范围，不新增 TARGET 的固定字段门槛、240 小时门槛，也不声称这些历史期间适合所有当前 Event。

| 实体 | 本轮固定来源 | 财务期间 | 币种 / EPS 单位 | 匹配财务行 | 可重开历史行 |
| --- | --- | --- | --- | ---: | ---: |
| MRVL | yfinance_financials | 2026-04-30 | USD / USD/share | 12 | 471 |
| MSFT | defeatbeta_stock_statement | 2026-06-30 | USD / USD/share | 34 | 1639 |
| NVDA | defeatbeta_stock_statement | 2026-07-31 | USD / USD/share | 12 | 117 |
| TSM | defeatbeta_stock_statement | 2026-06-30 | TWD / TWD/ADR | 23 | 529 |

**四个财务用途通过**，共 81 行满足预声明字段/期间/单位条件；不同维度的匹配行均保留，不按有利值选一条。13 个去重原始 blob 的 hash / 字节数重开验证；2756 条所选实体的财务历史行（含旧 vintage）可解析。MRVL 未把 SEC 的 2026-05-02 与 Yahoo 的 2026-04-30 混成同一期，TSM 的 ADR EPS 没有冒充普通股 EPS 或 USD EPS。

14 条历史行涉及既有语义修复。对现行内容摘要无法直接重算的行，核对真实 `data_company_financial_semantic_repairs` 账本及 2026-08-28 迁移备份：原行 hash 可重算，原值、期间、raw、artifact、时点与 ID 保持一致，series 变化与账本的 metric/unit/adjustment 对应。仅质量元数据变化的旧行只证明历史可读，明确 `admission_reapproved=false`，不用于批准新必需输入。没有改写 hash、历史记录或删除失败证据；初始失败结果 `selected_financial_audit.json` / `_v2.json` 原样保留，后续 `_v3.json` 及最终报告记录可重复核验的原因。

可重复命令（`--out` 必须为新文件）：

```sh
uv run --no-sync python scripts/prepare_phase_f_stage_one.py financial-audit \
  --requirements docs/validation/phase_f_final_gate_20261010/selected_financial_requirements.json \
  --data-cache var/data.sqlite --artifact-root var/data_artifacts \
  --out /private/tmp/phase_f_selected_financial_review.json
```

最终实际数据证据为 `selected_financial_audit_accepted.json`（逐个必需字段要求 accepted，不能把通用 packet 非空或 conflict 状态当成准入），拒绝检查为 `selected_financial_negative_checks.json`，所选依赖盘点为 `selected_workflow_dependencies.json`（均位于本机 gitignored 的 `docs/validation/phase_f_final_gate_20261010/`）。

**12.2 整项仍不勾选。** 所选 Routine 计划保留 Information → Fundamental，共 8 条任务 scope，缺报告绑定时 `verify_plan_coverage` 实际返回 pending。四个旧 Information brief 原行已保留在 `selected_information_lineage_gaps.json`，虽可被普通读取函数取出，但 `input_refs`、`data_vintage_refs`、`agent_run_id` 均为空；Dispatcher 的 `_select_reusable` 会以缺 agent attempt / data vintage 拒绝复用，不能把旧 brief 的非空或标题里写着新闻来源当作新契约血缘。MRVL、TSM 本实体近 7 天 admitted 文档为零，MSFT 为 1、NVDA 为 2；Information 的 peer/signal-chain 材料仍需合法 scope 和实际读取绑定，不能把 NVDA 文档直接伪装成 MRVL/TSM 自身材料。

后续缺项已经缩到这四个实体所选 workflow 的 Information 输入/投影血缘与实际运行报告，**不再是补齐另外 7 个财务标的**。真实新 workflow 的输出与取证由 6.4 承接、资格由 6.6 判定；本轮财务数据结果不代签报告、不构成生产许可。本轮仅修改审计准备脚本和对应测试/规划记录，没有改生产 profile、业务路由、原始数据或旧财务算法。6.1 前应将此次脚本/测试变更重新纳入指纹；13.6 已完成的原门禁结果保留，不冒称其已经覆盖新增用例。

实际数据的五种负向检查（缺标的、缺必需字段、错期间、错单位、原始历史不可访问）全部按预期拒绝；`selected_financial_negative_checks.json` 记录源库前后 SHA-256 一致。拒绝仅发生在候选审计，不修改正式生产资格。

本轮最终 uv 定向回归 **17 passed，0 skipped**（`selected_financial_tests_accepted.xml/log`），覆盖上述缺项、原始证据损坏、无证据迁移标签及 conflict 字段不能冒充 accepted 的拒绝行为；初次 fixture 错用 RawArtifact 属性的失败日志保留，修正为实际 `.id` 后复验。`selected_financial_current_checks.json` 记录当前实现/测试摘要、全部预定用例及实际审计报告源码摘要一致性；它是诊断记录，不是 6.1 最终冻结或 6.6 资格。OpenSpec strict 与 `git diff --check` 通过。当前依然 100/117 完成、17 项待办。


## 12.2 最终审计结论与任务边界澄清

**12.2 已完成审计实施，当前 101/117，16 项待办；用途覆盖仍 pending，不能退役。** 上一轮将实际 workflow 报告成功作为本项完成前置，混淆了用途审计与 6.4/6.6 后续验收，现予纠正。不是把缺项视作合格：所选每条用途都有真实判定，缺项依然拒绝并保留责任和下一阶段门禁。

最近是否为财报季不是新鲜度的替代判据。财务资料应核对本次用途的**报告期间、口径、单位、准入和血缘**，已有历史财报不能仅因采集较早而强制重采。本轮四个指定期间的财务资料已通过这些检查；没有修改行情 TTL、旧财务算法、生产资格有效期或投影复用新鲜度。Event 特定事件的 cutoff/财报前基线条件仍需按真实事件核验，不能把本轮 Routine 数据盘点外推为 Event 成功。

新增可重复入口（输出用新文件）：

```sh
uv run --no-sync python scripts/prepare_phase_f_stage_one.py selected-use-audit \
  --requirements docs/validation/phase_f_final_gate_20261010/selected_financial_requirements.json \
  --data-cache var/data.sqlite --artifact-root var/data_artifacts \
  --internal-cache var/ats.sqlite --out /private/tmp/phase_f_selected_use_audit.json
```

本轮实际完整输出为 `selected_use_audit_completed_v2.json`，`audit_status=completed`、`coverage_status=pending`、`retirement_allowed=false`，四个实体的 Information → Fundamental 共 8 条必需 task scope 无删减。4 条 Information 数据用途的准入历史文档可读取、文档版本及 hash 可追溯；4 条 Fundamental 依赖用途因为旧 brief 缺输入/版本/Agent 执行血缘而拒绝，并与已通过的四个财务用途分列。MRVL 旧 baseline 没有 fiscal label，另列缺口，不能伪造标签。没有模型调用、数据采集、TWS 访问、生产写入、报告签核或历史删除。初次输出因 baseline 的 set 不能 JSON 序列化而中断，原失败文件保留；将 KPI 词集合排序后实际完整重跑成功。

后续责任保持明确：**6.4** 从新真实输入生成并取证四标的 Information → Fundamental，核实基线及报告引用；**6.6** 以最终账本/证据判资格；**12.8** 在生产清零及观察窗口等条件齐备时验退役。旧投影不补假引用，不把最近无新材料的监控 no-op 冒充新分析输出。全部必需用途缺失及真实历史不可解析继续 fail-closed；未选择的 7 个财务标的不阻塞本轮审计，但生产 profile 仍有原 11 个成员，其全范围资格必须独立检查。

本轮 uv 定向回归 **24 passed**：覆盖所选字段/期间/单位、损坏历史、冲突不能冒充准入；新增审计完成与覆盖 pending 分列、删除/增加/重复必需用途拒绝、无判定/无拒绝原因拒绝、缺源库不创建。此前 17 项及五种真实数据拒绝记录保留。审计准备脚本与测试变更须纳入 6.1 最终指纹；未声称已执行最终冻结或生产门禁。

退出门禁专项 2 项通过（`selected_use_retirement_gate_tests.xml/log`），确认缺必需用途与缺报告仍 pending、不要求无关 workflow；审计完成没有修改 `verify_plan_coverage` / `verify_new_coverage` 的正式验收判据。

Fundamental 不能省略 Information 依赖的专项 **1 passed**（`selected_use_dependency_gate_tests.xml/log`）；本轮共 **27 项有效检查通过，无失败/跳过**。`selected_use_completion_review.json` 复核实际审计对应当前脚本摘要、8 条用途完整、4 条拒绝保留，并对四个真实所选 plan 调用正式 `verify_plan_coverage`，均因缺适用报告保持 pending。OpenSpec apply 复算为 **101/117，16 项待办**，strict 与 diff 检查通过。没有将任务完成偷换为部署或退役许可。
