# FactSet Earnings Insight 语义采集运行手册

适用 change：`generalize-factset-report-ingestion`。本手册描述新路径；旧日期/页码专用解码器仅保留历史回放。生产 `com.ats.schedule` 已启用语义月末入口；测试审批不得用于生产。

## 范围与停止规则

- P0：报告明示的 S&P 500 Index 已登记适用指标与期间，以及 Information Technology（GICS_45）八类指标的全部已披露期间。任一必需格缺失、单位/期间/实体歧义或数值冲突都阻断 `core_acceptance=passed`。
- P1：原文明示的 AI 硬件、半导体、消费电子及上下游文字证据，只能作为带页码的来源提及，不能由科技行业总体值外推子行业数字。未找到的主题记录 `not_located`。
- P2：其余行业细格。可复用同一解析器，但不为疑难格单独追加大规模人工/模型批次。延期记 `deferred`，不得写成 `not_disclosed`。只有原文确实未披露且独立核实，才写 `not_disclosed`。
- `core_acceptance` 与 `report_coverage` 独立。科技范围通过并不代表 11 行业报告完整；旧 Sector 十一行业接口须留空或只读有明确版本的历史数据。

策略、范围版本和两个已知报告的独立组清单在 `config/data/structured.yaml`；PDF 来源及权限在 `config/data/unstructured.yaml`。未登记日期的新报告先持久化 PDF，再用图表标题与期间生成独立于数值候选的 IT 清单；八类、十二组齐全且无歧义才自动继续，否则保持 blocked 待人工核定。不得用解析成功子集反推清单或从旧月份复制页码、行序、数值。

## 受管运行与审阅

项目使用 `uv run --offline --no-sync`。手动采集示例（去掉 `--offline` 才能访问网络；示例中的 URL 是已核实的 2026-09-18 官方日期版）：

```bash
uv run --no-sync ats data factset-semantic-refresh \
  --source factset_earnings_insight_doc \
  --query-scope '{"url":"https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_091826.pdf"}'
```

普通 CLI 写入先进入持久化队列；`--db --artifact-root --force` 仅用于隔离验收。`task_id`、下载 HTTP 状态、最终 URL、PDF SHA-256、`document_version_id`、发现/Index/组 release ID 和质量原因须保留。网络失败可重试；重试不得改写旧版本。定时入口带 `--query-scope '{"auto_admit":true}'`，规则通过时以 `factset-policy-v1` 写入绑定 PDF、候选及策略 hash 的审批记录；异常保持 shadow 待人工判断。未启用自动准入且无审批 ID 时正常返回 partial/shadow。

异常时人工审阅使用 `factset-index-inventory`、`factset-index-review` 及组 `factset-review` 入口，携带操作者、原文证据和精确的候选/策略/范围绑定。审批只写审计；重跑语义入口时通过 `index_review_id` 和 `review_ids` 指向同一候选，发布前再次验证绑定。测试 fixture/golden 绝不能充当生产操作者审批。数值仅在 Index 和全部科技 P0 分组审核通过、质量校验通过后才是核心通过；未审或被驳回的组保持 shadow。

设置 `ATS_FACTSET_SCHEDULE_SEMANTIC=1` 后，`launchd` 重启的调度器改在纽约时间每月最后一天 23:30 触发，只接受 PDF 报告日期属于目标月份的版本，以 `factset-month-end:YYYY-MM` 去重。月末休眠补跑宽限为 72 小时；若稳定 URL 已指向次月，保持 blocked，需人工使用官方当月归档 URL 补跑。取消变量并重启可恢复旧周调度。2026-09-30 已在本机生产 `com.ats.schedule` 启用；自然月末、misfire 和未来未见版式仍需持续监测。

切流记录：原 plist 备份为 `/private/tmp/com.ats.schedule.pre-factset-monthly.plist`。`launchctl` 环境含 `ATS_FACTSET_SCHEDULE_SEMANTIC=1`，启动日志注册 `factset_monthly_ingest`，不再注册 FactSet 周任务。8 月和 9 月 18 日已知 PDF 的 Index 与 IT 12/12 组均在隔离库规则准入通过。2026-09 月末自然触发因 Mac 休眠延至 10 月 2 日补跑，首次对 9 月 25 日报告产生 `partial`；修复并经隔离回放后，从已持久化的同一官方原件通过受管队列完成生产发布。十一行业 P2 尚未完整。

### 2026-09-25 未见月报回归与生产发布

- 官方 PDF SHA-256：`da6bfc99301d5a908705757612ee534018ab72504d14b79396903399b7534958`；36 页，文档版本 `SP500:2026-09-25:research_article@ae4759ca66f21519`。首次自然月末任务 `33e82af8-d433-5b1c-a354-cebe180a905a` 为 `partial`，未把错误核心值发布。
- 评级正文前段将 `59.9%` 错断成 `5 9.9%`；后段同报告完整句子与原图证实 S&P 500 Buy/Hold/Sell 为 `59.9%/35.5%/4.7%`。解析只选数值组成自洽的完整原文句子，不补写缺失数字。
- 同一评级和目标价图在正文与附录重复嵌入；按相同图像 hash、组和期间去重。不同图像的冲突仍阻断。IT 的 `target_ratings` 原图为 Buy/Hold/Sell `70%/27%/4%`、Target/Close `22.8%`；四格通过原文及组校验。
- Index Q3 盈利增长图的 `29.1%` 在单一 OCR 缩放被读成 `29. 1%`；仅在两种独立缩放均读为 `29.1%` 且与正文一致时接纳，分歧继续阻断。
- 隔离库完整回放 `core_acceptance=passed`，随后生产受管任务 `b2cc72af-3a4d-5478-8ba7-940184d1d2a7` 成功。生产 Index release `93cb64ba5ca96d150b2fc31b` 为 `platform`；消费者读回 43 个 Index 指标/期间观测，IT `12/12` 组发布、无核心缺口。`report_coverage=partial` 只代表 P2 其余行业未完整发布，不能对外表述为十一行业完整。
- 固定官方 URL 的再次下载任务 `312eb1d2-b3ec-53bd-8fcf-7d1e48468f00` 遇网络异常进入 `retry_wait`；改用此前受管采集、hash 相同的本地不可变 PDF 重处理，保留下载任务记录，不把网络失败归为解析失败。
- 生产任务结束后以 `launchctl kickstart -k gui/501/com.ats.schedule` 重启调度器加载通用修复；新 PID `36684` 正常注册 `factset_monthly_ingest`。本地三项 9 月 25 日回归测试通过；正式生产库的 Index 与 IT 产品读回亦通过。

TODO（非核心、不阻塞此次发布）：其余十个行业的详细指标格仍需按 P2 停止规则逐源核验和补齐；不得将未完成格标为 `not_disclosed` 或使旧十一行业 Sector 接口显示本期完整横截面。

## 消费与回退

`ats data earnings-insight` / `factset-status` 读取发布状态及历史；Macro 分析包保留全部已发布期间和最多十条绑定原文的主题提及。科技部分通过不使旧 Sector 十一行业接口显示完整。最新已发布月报不因距今天数而标为 stale 或向下游发年龄警告；采集失败保留在运维状态字段。

回退有三个互不替代的层次：

1. 解析器：撤销调度的 `ATS_FACTSET_SCHEDULE_SEMANTIC=1`，下次回到旧入口；已写入的新候选/版本不删除。
2. 产品发布选择：`ats data factset-product-pin VERSION_ID --confirm` 只接受已发布且质量通过的 Index 版本；`ats data factset-product-unpin --confirm` 恢复自动选择最新可用版本。可用 `--release-file` 指定隔离覆盖层。显式查询 `version_id` 用于审计历史，不受 pin 改写。若 pin 指向当下不可见或已撤销版本，读取失败关闭，不静默跳到别版。
3. 数据来源/消费者：`ats data rollback SOURCE_ID --kind source` 或 `ats data rollback CONSUMER_ID --kind consumer` 独立恢复路由；环境变量覆盖优先于 release overlay，应先核查。回退不物理删除 PDF、候选、证据、审阅和 vintage，也不触发交易。

## 后续报告核验与事故处理

对新报告先保存官方固定日期 URL、hash、报告日期及源证据；规则自动核查 Index/科技清单、指标、期间、单位和明确允许的未披露项；只有异常才由人工判断，再做发布和产品读回。将 P0 精度/覆盖与 P1/P2 缺口、解析耗时分别记录，不以 `report_coverage=partial` 否定已通过的核心，也不能以核心通过宣称全报告完成。遇到页序/图序/分辨率/行业顺序变动，依标题、表头和原始数字证据定位；冲突则保留失败阶段并隔离，不猜测小数点、负号或柱高。发生误发布时先停消费者、pin 前一已发布版本并停止新解析入口，再核对 hash/审批/known_at；保留事故版本用于追溯。

本 change 的双报告准确率、队列、故障/回滚证据由 `docs/validation/FACTSET_CROSS_REPORT_BASELINE.md` 汇总，并映射回父 change `complete-target-dataflow` 的 FactSet 数据源验收；不自动勾选父 change 的复合任务或替父 change 切流。
