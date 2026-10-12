# Phase F 最终真实取证审阅（2026-10-11）

本轮完成 6.4 → 6.6 → 6.7，当前 **106/117 完成，11 项待办**。依据仍为 [TARGET_WORKFLOW_DATAFLOW.md](TARGET_WORKFLOW_DATAFLOW.md)，不要求旧业务成功或新旧输出一致。完成的是原始证据接收、逐 scope 资格复算与最小刷新/拒绝验证；不是生产资格通过或上线完成。

## 实际达到的目标

- MRVL、MSFT、NVDA、TSM 各一个独立 Information 和一个包含 Information 依赖的 Routine，八个真实模型 capture 全部成功。各自真实新入口运行和独立子进程重放的必需断言通过，源库在各次运行中未变化。沿用原批准模型、端点、业务上下文与逻辑时点 `2026-10-10T14:40:00.196345+00:00`，没有新模型外发范围。
- 当前 capture 共 8 份 InformationBrief、12 份 FundamentalExpectationUpdate。置信度均显式存在；十二份 Routine 更新均为 `metric=narrative`、previous/new 值为 null。标题金额和日期不再冒充 KPI。新闻并不提供新的公司财务 actuals，本次不声称验证了 Event actuals 或数值 KPI 更新的真实场景。
- MRVL 原始基线仍无 fiscal label；本轮新执行依据已存在且时点有效的财报期间元数据解析 `Q2 FY2027`，投影同时保留原 baseline hash 和 `pead-period` hash。没有修改旧 dossier 或补写旧投影标签。
- 四个既有财务快照按相同 exact-scope、原选期间/币种/单位/指标重新走真实 `read_input(fundamental, COMPANY_DATA)`，全部通过。它们是另行执行的数据读取核验，不能谎称 Routine 本身查询过 COMPANY_DATA。
- COHR 实际公开 Yahoo Technical 输入、新入口和独立重放通过，未调用模型或券商写接口。真实一天 TTL 证据保留原时点，模拟两天后明确 stale；研究证据模拟 31 天后过期。两次独立最小复用各接收 16 条有效事件、零网络调用，不全量重采、不重标时间。过期及缺项均不能放行。

数量/单位条件是输出前确定的有限检查，不能保证所有语义、数字归属或投资判断正确。输出抽查保留了未确认融资、第三方来源与间接影响的限制。Routine 的“确认”标签依分类契约表示符合基线预期，不构成对新闻事件真实性的独立确认。

工程最终冻结为 v6：551 路径、10 消费者，348 个必需用例按同 ID 最新结果全部通过、0 skipped。原完整运行 347 passed/1 failed（进度文档计数）与整个文档模块 7 passed 补验均保留；没有把原运行改写为单次全绿。此后未修改受保护实现/配置/测试闭包。

## 资格结果与缺口

生产源 `var/data.sqlite` 没有 assurance 账本，八个研究 scope **0/8 eligible**，原因均为 `assurance_ledger_missing`。本轮未初始化生产资格账本、签核报告、改变 route/owner、部署服务或下真实订单。

隔离研究账本累计 16 条真实有效事件：四个 Information 各 read/lineage，四个 Fundamental 各 lineage，加另行四个真实财务 read。仍 **0/8 eligible**，尚缺 **40 个必需证据项**：八个 scope 分别缺 write、admission、completeness、reconciliation、rollback。现有数据的准入状态与历史可读，不自动证明本轮完整的生产写入、准入执行、完整性、对账和安全恢复链。

Fundamental 的 company_financials 读取状态有真实补验依据；必需 filing index、transcript、SEC body 状态记录及恢复证明仍需相应证据。SEC body optional 政策及四项检查不变。实际 Routine 消费 Information 和自身基线，不能由其成功倒推这些数据产品全部被读取或所有输入状态均已验证。

COHR 隔离资格只有 runtime_query/timestamp 两条真实事件，仍缺 failure_semantics、no_persistence、fallback，资格未通过。短 TTL 演示不是一条可上线的完整 Technical 批次。

本轮 production inventory 仍有 16 个 pending 退出对象；缺 production_consumer_receipts 和 schedule_scope_owners。缺表不等于生产调用为零，隔离演练不能替代生产观察。12.1/12.3/12.7/12.8 继续待办。

## 分标的中文核签报告

[九份中文报告入口](/Users/liuchao/Code/trading/auto_stock_trading_agents/docs/validation/phase_f_final_gate_20261010/human_reports/README.md)：四标的各自的完整 Information 分析和 Routine 逐条分类理由，以及 COHR 技术指标。阅读版从原始固定输入响应与实际投影整理，不重跑模型、不修改报告正文，不代替签核。报告同时披露 Information 发布截短和标题型 fact_changes 的限制。

## 可审阅的九份未签核报告

以下是隔离原始新入口验收报告；同 report ID 在不同独立数据库中不混用。签核须绑定完整 body hash，不能对表格简称作全局批准。

| 消费者/标的 | 报告 ID | 内容 hash（前 12 位） | 状态 |
|---|---|---|---|
| information/MRVL | actual-MRVL | 600054be0e51 | unsigned |
| fundamental/MRVL | actual-MRVL | 3586f03eb488 | unsigned |
| information/MSFT | actual-MSFT | 2d61dc4d675e | unsigned |
| fundamental/MSFT | actual-MSFT | 724b203398cb | unsigned |
| information/NVDA | actual-NVDA | 791e381ba2cd | unsigned |
| fundamental/NVDA | actual-NVDA | 29aa80bea6a3 | unsigned |
| information/TSM | actual-TSM | 87fccb651680 | unsigned |
| fundamental/TSM | actual-TSM | a0ef77b7d82e | unsigned |
| technical/COHR | actual-technical | 57a8a686c5b2 | unsigned |

完整 scope、body hash、独立报告数据库路径在本地 `docs/validation/phase_f_final_gate_20261010/v6_reports_for_human_review.json`。报告签核仅接收对应隔离输入/场景的新入口验收结论，不授予生产数据资格、持续部署许可或券商写权限。历史失败及报告仍原样保留。

## 后续依赖与当前阻塞

1. 补齐所选上线范围的真实数据平台/运行时证据并登记可接受原始事件，明确输入状态及恢复证明；在正式源账本复算资格。只补受影响范围，不重做无关标的采集，不把隔离 fixture 改名为生产证明。
2. 审阅并明确签核适用的新入口报告。当前九份均 unsigned；源库当次 preflight 为 pending、exit 1，报告未签核与资格缺失分别记录。
3. 资格、适用签核和安全恢复齐备后推进 8.5，再完成 8.6 的具体首批 scope、观察起止、停止阈值、探针、负责人及部署许可覆盖。批准一次上线，之后按许可持续运行；每次 as_of/TTL 检查仍独立。
4. 在明确生产部署授权下执行 9.4/10.4，完成真实观察及 9.5 停止/已验收版本恢复，再推进 12.1/12.3/12.7/12.8 → 13.8 → 13.9。缺生产观察不受控退役，不强制恢复旧业务。

因此当前不能写成“只等部署批准”：尚有上述实际证据缺口，以及报告签核和生产部署授权两个不同的审阅动作。用户此前批准的是具体内容向 OpenRouter 外发，没有包含报告签核或生产切换。

## 复算入口与本地证据

所有命令用 uv；输出使用新路径，保留原记录。`docs/validation/`、`docs/assets/` 依用户规则不提交 Git。

- 原始运行选择及审阅：`v6_actual_capture_selection.json`、`final_actual_research_reopened_v6.json`、`v6_actual_output_business_review.json`。
- 同 scope 财务补验：`selected_financial_requirements_v6_exact_scope.json`、`selected_financial_audit_v6_exact_scope.json`、`final_actual_scope_financial_supplement_v6.json`。
- 资格初次接收：`final_actual_scope_qualification_v6.json`（12 条/缺 44），补验后以上 supplement（16 条/缺 40）；不覆盖初次结果。
- 两次复用：`final_actual_financial_refresh_v6_one.json`、`final_actual_financial_refresh_v6_two.json`；原时点逐事件保留。
- 短 TTL：`actual_short_ttl_v6.json`；源当次拒绝：`final_actual_preflight_v6.json`；退出库存：`production_inventory_after_v6_actual_capture.json`。

可重复最小接收命令：

```sh
uv run --no-sync python scripts/refresh_phase_f_evidence.py --scopes docs/validation/phase_f_final_gate_20261010/selected_exact_scopes.json --source-ledger tmp/phase_f_actual_partial_assurance_v6/data.sqlite --freeze docs/validation/phase_f_final_gate_20261010/final_implementation_freeze_information_confidence_v6.json --root /private/tmp/phase-f-new-reuse-root --output /private/tmp/phase-f-new-reuse.json --reuse-current
uv run --no-sync python scripts/prepare_phase_f_stage_one.py preflight --scopes docs/validation/phase_f_final_gate_20261010/selected_exact_scopes.json --freeze docs/validation/phase_f_final_gate_20261010/final_implementation_freeze_information_confidence_v6.json --bindings docs/validation/phase_f_final_gate_20261010/final_actual_report_bindings_v6.json --source-ledger var/data.sqlite --out /private/tmp/phase-f-new-preflight.json
```

示例复用源为隔离候选账本，不是生产资格源；第二条命令读生产源，当前应返回 pending。输出文件/隔离根须每次选新路径。过期原始事件不复用；重新运行真实受影响探针生成新证据，不能仅把旧日期改成现在。

本轮回写后的文档一致性检查 7 passed，OpenSpec strict 与 `git diff --check` 通过；`final_v6_after_intake_integrity.json` 确认冻结仍稳定，源库与 v6 明确只读内容核对时一致。此前目录 CLI 的元数据初始化事件仍保留在 [冻结过程记录](PHASE_F_FREEZE_AND_EVIDENCE.md)，不将整个长程会话描述为源库始终未变。
