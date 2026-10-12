# Phase F / Information 材料审查交接

日期：2026-10-12。用途：从 Information 来源诊断返回当前未完成 OpenSpec，或以后恢复优化开发时定位上下文。

## 当前工作位置

- 实际 Git branch：`feat/rebuild_workflow_dataflow`，记录时 HEAD：`075ed0e`。用户讨论的 change 是 `implement-phase-f-shadow-run-and-cutover`，不要把 change 名称当成当前 Git branch。
- 父 change：`refactor-workflow-dataflow-architecture`。
- 当前权威 [tasks.md](../openspec/changes/implement-phase-f-shadow-run-and-cutover/tasks.md) 统计为 106/117，11 项未勾选；这里只记录交接时点，不代替后续实时状态。
- 工作区已有大量未提交代码、配置、测试和文档。不要 reset、stash、覆盖或切换 branch 来“整理上下文”；本次没有进行 Git 分支切换、提交或推送。

## 本次已经完成什么

1. 原冻结四标的 Information 材料选择已复现，文档版本及正文哈希与隔离快照重查一致。
2. 取消七天过滤的诊断显示每 ticker 3 → 25，主要增加历史电话会和公告，通讯关联问题仍存在。
3. 按用户授权实际刷新 IBKR、SemiAnalysis、TrendForce；新增 18 个准入版本。IBKR 新闻在本机、只读方式查询，所有来源通过现有采集队列执行；运行报告的 LLM/Agent/workflow/orders/trades 副作用计数均为零。
4. 确认正文预算遮蔽了 MRVL 标题候选，及 publisher-only 索引使明显公司主题的行业文章无法进入标的读取。
5. 已写 [开发与测试计划](INFORMATION_SOURCE_SELECTION_DEVELOPMENT_TEST_PLAN.md)，包括范围、E0～E4 依赖、partial 消费、历史索引、测试和粗估工作量。方案未实施，原 tasks 和完成状态未修改。

生产源数据确因 refresh 而改变。原冻结 tape 与报告保留；后来的新输入不能回填旧 as_of、旧 source hash 或已批准模型外发范围。本次规划文档没有改变实现闭包，但未来实现／配置变更需要识别受影响的重冻与重验范围。

## 不能遗忘的审阅结论

[2026-10-11 最终取证报告](PHASE_F_FINAL_EVIDENCE_REVIEW.md) 记录了工程、运行和资格的不同层次。后续用户审阅发现四标的输入高度重叠；当前八份研究报告应保留为共同新闻场景的真实执行／重放证据，不能据此核签完整标的信息覆盖。

当时生产资格为 0/8，源 assurance 账本缺失；隔离证据也仍有必需项缺失。该结果是历史资格记录，本次 refresh 不是资格复算，不代表现已合格。通讯接入优化、生产源证据、人工签核、部署许可分别判断，不能互相替代。

九份供人阅读的报告在本机 `docs/validation/phase_f_final_gate_20261010/human_reports/`（八份研究及 COHR Technical）。它们尚未因本次讨论获得签核；标题清单、共性新闻摘要与机械通过不能替代用户对业务内容的审阅。

## 返回当前 OpenSpec 的执行入口

重新读取权威 tasks、design、delta specs，以及 [验收要求映射](PHASE_F_ACCEPTANCE_REQUIREMENTS.md)，按真实依赖判断下一步，不能直接从历史计数推定可上线。

当前未完成项：

| 范围 | 项目 | 仍需满足的关键条件 |
|---|---|---|
| 切前门禁与观察 | 8.5、8.6 | 最终资格、动态接入、适用且已签核报告、安全恢复、实际边界授权与观察条件 |
| 实际生产读／调度 | 9.4、9.5、10.4 | 先满足切前依赖及适用部署授权，再执行观察与安全停止／已验收版本恢复 |
| 生产退出及退役 | 12.1、12.3、12.7、12.8 | 真实消费者清零、恢复与观察证明、历史保留及退役责任；不能用静态扫描或临时库代替生产结果 |
| 最终回写 | 13.8、13.9 | 对应观察／退出完成后回写；待办和未执行继续明确记录 |

返回后可先核对生产证据缺口、接线／清零库存和安全恢复资料，准备不依赖新 Information 模型输入的工作。生产切换不能跳过最终资格及报告签核；需要完整 Information 内容验收的范围，必须保留本次发现的限制。

本次用户要求先保存开发／测试文档，不代表要求把 E0～E4 全部插为上述 11 项的普遍前置，也不代表允许忽略相关业务缺陷。未来实施优化时，把最小修复及受影响验收范围归入适用 change；扩展研究能力是否另开 change，按当时范围决定，不在本次自行建立新 OpenSpec。

## 运行与授权边界

- 项目环境由 uv 管理，不手工激活 venv；测试遵守 `./scripts/run_tests.sh`。
- TWS API 读／写操作按用户偏好在本机运行。此轮只允许新闻读；真实 broker 写权限没有因 refresh 或可信 IP 设置而开放。
- 本次外部采集不包含模型外发。原 OpenRouter 批准针对已列内容与两模型，不自动覆盖新增通讯、历史正文或扩大后的 scope。
- 上线许可与每次运行 as_of、资格／输入 TTL 分离：用户预期上线批准一次后持续运行直到主动停用；不是日常每 tick 人工续批。
- `docs/validation/`、`docs/assets/`、`tmp/` 按现有规则不入 Git；本文和开发计划放在普通 `docs/` 保存可版本化上下文。未要求此次提交或推送。

## 后续恢复优化时可直接引用的指令

> 阅读 docs/INFORMATION_SOURCE_SELECTION_DEVELOPMENT_TEST_PLAN.md 和 docs/PHASE_F_INFORMATION_REVIEW_HANDOFF.md，重新核对当前 OpenSpec／工作区状态，按 E0 → E1 → E2 → E3a → E3b → E4 推进最小修复。先核定受影响契约和验证范围，不增加 Agent／workflow，不重建平台，不把历史材料当新消息；新模型内容按实际外发授权处理。

仅返回 Phase F 时，读取当前 change 的未完成项及依赖，从可执行的证据／准备工作继续；不要自动执行本优化计划，不自动签核或切换生产。
