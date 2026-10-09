# Phase F 第 4 组实际调度接线

状态：**4.2–4.7 完成，第 4 组 7/7；总进度 72/116，44 项待办**。使用 change `implement-phase-f-shadow-run-and-cutover`，schema spec-driven；起点 66/116。生产切换 10.4 独立，未执行。

## 修改前影响方案

已保存 before.json / before_sources.zip，812 个源文件/测试/配置/规划文件，现有保护面 72 个文件及生产控制/资格只读盘点。保留工作区原修改和历史证据。

拟接入 runtime scheduler 的旧 cron/event、手动 CLI、ownership/Dispatcher；将真实投影发布放入共享认领代次的事务栅栏。独立控制状态采用 workflow + scope，不用单个全局 owner 替代逐范围状态；计划 tick/event-version 为逻辑身份，手动指定相同 tick/event 时归并。认领记录、处置、发布及切换记录可跨进程查询，冻结先于清点，未明处置或缺完成引用保持冻结，旧 worker 迟到发布拒绝。

Scheduler/CLI/Dispatcher/Memory 属于既有受保护面；新增控制实现与实际读取配置纳入指纹并集，manifest 全文件及共享面漂移影响十角色。本轮只做隔离运行/切换/回退验证，生产 owner YAML、schedule enabled、C3 与生产路由不变；最终资格仍待 6.1/6.4。研究移交不操作数据采集 job、queue lease 或 catalog；独立预期集合来自 cron/event 配置而不是双方实际账本。


## 实际达成与证据

| 任务 | 实际接线与验证 | 结论 |
|---|---|---|
| 4.2 | 旧 cron/event、实际手动 CLI、六角色入口、Dispatcher 使用 schedule runtime 的 owner/generation/claim；相同计划 tick/事件版本只完成一次，输入 lineage 改变不能重发旧触发 | 通过 |
| 4.3 | 真实 scheduler startup/reload、保存的旧 callback、新进程读取 SQL；YAML reload 不改变已登记 owner，缺/坏权威停止，旧 callback 无须收到 YAML 变更也会拒绝 | 通过 |
| 4.4 | 隔离 CLI freeze→inventory→handover，事务中复核全部未完成任务；未处置拒绝、并发认领拒绝，carry_over/void 原因与代次可查；旧 worker 实际完成后再交接 | 通过 |
| 4.5 | 实际 Memory 投影、原始评审、score 与报告文件发布有事务栅栏；新代拒绝旧 worker；结果已提交而控制记录未提交的崩溃通过结果库原 claim 绑定阻止重发 | 通过 |
| 4.6 | 独立解析生产 cron/event 配置与已发布事件，记录真实 worker arrival/wake、实际 refs；共同遗漏、misfire、重启、手动重复和回退不重发有否定/正向验证 | 通过 |
| 4.7 | 实际旧/新 scheduler 注册、SQL owner 对照及隔离交接；FactSet 等采集 job 配置/函数不变，研究只消费 calendar，不 enqueue/run collector，既有 queue lease 不变 | 通过 |

新增 `schedule_runtime.py` 为实际权威控制；`schedule_executor.py` 在 APScheduler worker 传递计划时间，保留单 worker。旧 dispatch_claims/schedule_cutover helper 兼容保留，不能作为真实认领证据。SQL 按 workflow + 精确 scope 管理 owner/代次，YAML 仅在首次显式安装提供初始 owner，之后只决定 wake；缺失、损坏或失去原认领记录默认拒绝，不自动重建或降代。

旧 PEAD monitor 的真实输出是 information-brief，财报评分是 fundamental-event。BMO/AMC 窗口消费同一已发布 earnings event ID/version，与 Dispatcher 归并；缺已发布版本停止。评分 final 元数据更新必须绑定原 score claim，并核对现存 owner/generation，交接后旧进程不能继续 promote/stamp。宏观配置事件同样归并已发布 calendar 版本。

冻结先于清点；交接逐项留 carry_over/void 原因。carry_over 仅允许无发布的任务；部分发布保持阻塞，需原 worker 实际完成或显式 void。操作者不能自报 already_run。已完成触发保留实际 refs、历史及结果，再次到达复用合法记录；失败恢复要求新 attempt actor。claim complete 本身不是业务合格，仍核对实际输出 refs/schema/lineage/资格。回退提升代次，禁止还原旧数据库绕过去重。

## 最终验证

全部通过 uv run --offline --no-sync；外部模型、行情及 broker 使用隔离 fixture/FakeBroker，无真实 broker 写入。

| 验证 | 最终结果 | 原始记录 |
|---|---|---|
| 新增实际调度验收及 scheduler/ownership/Dispatcher/业务接线/score/PEAD/chain 回归 | **179 passed，0 skipped**，82.303 秒；含新增调度验收 23 项 | [entries.xml](phase_f_schedule_runtime_20261009/entries.xml) |
| 六角色真实研究入口、Chief/Risk/人工审批/Trader/FakeBroker/Clerk 完整链与恢复 | **40 passed，0 skipped**，292.360 秒 | [business-final.xml](phase_f_schedule_runtime_20261009/business-final.xml) |
| 消费者处置/A/安全读/记录守卫/assurance/隔离回归 | **160 passed，0 skipped**，68.840 秒 | [protection.xml](phase_f_schedule_runtime_20261009/protection.xml) |

最终三组共 **379 passed**。不是全仓全量测试结论，既有全量失败及原报告保留。`business.xml`、`runtime.xml` 等早期记录仅为历史；当前结果以表内三份 XML 为准。最终验证版本固定后，记录文档/任务守卫补验 **20 passed**（首次检查指出进度行格式不匹配，修正文档后通过，未放宽守卫），结果见 docs-checks.xml。OpenSpec strict 通过，116 项依赖无循环、4.2–4.7 直接前置全部闭合。只读校验通过：897 源文件、91 保护路径、归档 1878 个 SQLite 一致备份完整性及生产盘点不变。

实际命令（统一前缀 `UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync`）：

```sh
pytest -q tests/test_phase_f_schedule_runtime.py tests/test_scheduler.py tests/test_scheduler_jobs.py tests/test_workflow_ownership.py tests/test_phase_e_dispatcher.py tests/test_phase_f_business_wiring.py tests/test_score_state.py tests/test_pead_graph.py tests/test_chain_evidence.py --maxfail=2 --junitxml=docs/validation/phase_f_schedule_runtime_20261009/entries.xml --basetemp=/private/tmp/phase-f-schedule-complete-entries -o junit_family=legacy
pytest -q tests/test_phase_f_research_gate.py tests/test_phase_f_opinion_execution.py --maxfail=2 --junitxml=docs/validation/phase_f_schedule_runtime_20261009/business-final.xml --basetemp=/private/tmp/phase-f-schedule-complete-business -o junit_family=legacy
pytest -q tests/test_phase_f_disposition_a_regression.py tests/test_phase_f_trader_a.py tests/test_phase_f_safe_reads.py tests/test_phase_f_record_guardian.py tests/test_assurance_surface.py tests/test_phase_f_progress_doc.py tests/test_isolation.py --maxfail=2 --junitxml=docs/validation/phase_f_schedule_runtime_20261009/protection.xml --basetemp=/private/tmp/phase-f-schedule-complete-protection -o junit_family=legacy
```

调度测试调用真实注册 callback 与 APScheduler run_job/计划时间执行器；scheduler 注册和时钟受控，不是长驻 daemon 连续生产观察。实际 role/Memory/CLI/Dispatcher 发布保留，模型/feed/broker 边界使用 fixture；主要业务实体 COHR、layer L4_interconnect，不代表生产全 universe/全部事件覆盖。首次实际验收发现报告尝试使用 Vault 输出路径，文件权限拒绝；已将隔离报告输出限定 root/reports 并重跑通过。未成功写入外部 Vault。

## 材料、影响与边界

[before.json](phase_f_schedule_runtime_20261009/before.json) / before_sources.zip 保留修改前 812 个捕获文件及生产只读盘点；[final_sources.json](phase_f_schedule_runtime_20261009/final_sources.json) / final_sources.zip 捕获最终 897 个实现/测试/配置文件，包括原始未纳入前快照的 JSON fixtures，差额不等于本轮新增文件。保护面从 72 扩展至 **91 个路径**；manifest 全文件、实际读取配置、调度/发布/隔离闭包纳入。影响十消费者，最低重验明细见 [minimum-reverification.json](phase_f_schedule_runtime_20261009/minimum-reverification.json)。保留历史证据，不改旧基线、不登记生产资格；此快照是本轮验证版本，**不是 6.1 最终资格冻结**。

[test-evidence.json](phase_f_schedule_runtime_20261009/test-evidence.json) 保存 JUnit case 属性中的真实 refs/claim/history/source；entries/business/protection-materials.zip 及对应 JSON 索引保存各最终隔离运行的配置、输入、报告和 SQLite backup（合并 WAL 的一致备份），可按原 basetemp 相对路径定位。材料反映当时运行，不要求重新访问数据源。[after.json](phase_f_schedule_runtime_20261009/after.json) 固定源码、材料 hash、最终结果和生产前后盘点；[verify.py](phase_f_schedule_runtime_20261009/verify.py) 只读复算，不创建/修改生产库。运行：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_schedule_runtime_20261009/verify.py
```

生产 owner YAML、schedule enabled、既有授权/资格/路由及控制库只读盘点前后相等；C3 停用，FakeBroker 隔离许可不构成 Paper/live 许可。研究交接不重新启动采集、不重建 calendar/catalog；缺已发布财报事件须由既有 managed data 流程提供。

**后续注意/暂缓原因**：生产旧驻留程序必须先受控部署/重启到带栅栏版本，再移交 SQL owner；旧二进制没有本轮逻辑，不宣称其自动受保护。生产交接协调/观察/激活继续由 10.2/10.3/10.4 承接，联合多边界恢复由 5.15 承接，混合 owner 聚合调用默认拒绝，需精确 scope 操作。runbook 实际隔离 CLI state/freeze/inventory/handover/reload/expected/compare 顺序执行成功，空账本 compare clean=false；原始输出见 runbook-cli.json、真实权威备份 runbook-dispatch.sqlite。完整控制平面 runbook 5.13 仍依赖 5.10。这些任务不是本轮隔离验收的生产动作，不提前勾选。下一项可按依赖推进 3.9（4.6 已闭合）或 10.3；3.10 仍须 11.2，资格仍等待 6.1/6.4。
