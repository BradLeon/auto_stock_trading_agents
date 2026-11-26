# 验收记录

> 每项由用户抽查后签字。未签字的标"待确认"。

## 1. 身份统一

### 1.4 登记 ANT 前后的 L1 对比（2026-11-26，按分离方案重做）

**签字：用户 2026-11-26 确认**（1.1–1.4）。

方法：同一份生产数据副本，旧代码旧配置（HEAD）对新代码新配置各跑一次，比较观察器读数与命题证据簇（确定性部分，不调模型）。证据文件：`var/audits/ant-data-onboarding/1.4/cl_old.json`、`cl_new.json`、`clusters.py`。

**差异清单：无。** 两份输出逐字节相同：

| 项 | 结果 |
|---|---|
| 观察器读数（生产化 3、商业化 3、原始能力 2） | 相同 |
| ai_core_production_workflow_penetration：读入 6 行、3 个证据簇 | 相同 |
| ai_frontier_labs_commercialization：证人 ANTHROPIC / OPENAI / OPENROUTER，读入 182 行、4 个簇（含 SACRA→ANTHROPIC 收入簇） | 相同 |
| ai_frontier_raw_capability：读入 4 行、2 个簇 | 相同 |

证据簇相同则命题判读的输入相同。

生产账本清理（用户批准）：撤回方案前定时任务写入的两行——`revenue_ant` 读数标记失效（原 `revenue_anthropic` 行仍有效）；一条新闻观测实体改回 `ANTHROPIC`（观测表与事实表各一行）。清理后账本里没有有效的 ANT 行。清理前备份：`var/backups/data-before-ant-ledger-cleanup-20261126.sqlite`。

模型调用：2026-11-25 首次比对未限定 L1，发出 39 次 `evidence_adjudicator` 请求；2026-11-26 针对已撤回方案的重跑发出 5 次；本次对比 0 次。

附带改动的测试：`tests/test_chain_kb_review.py` 原用 ANTHROPIC 作"未登记的合作方"示例，ANTHROPIC 现在是 ANT 的名称别名，示例改为仍私有的 OPENAI。回归时另有两条原有失败（不带本次改动同样失败）：`test_anthropic_consumer_boundaries::test_anthropic_job_adoption_has_no_default_trading_weight_or_route`、`test_collection_boundary::test_technical_review_publishes_per_entity_projections`。

### 1.x 身份分工（2026-11-25 至 11-26，用户确认）

ANT 只是证券标识，适合交易直接相关的请求；证据、命题、新闻、评测用 Anthropic / Claude。ANT 同时是普通英文词、也与蚂蚁集团同形。落法见 design.md D1：
- `config/entities.yaml` 的 ANT 加 `code_ambiguous: true`。
- 文章里单独的 "ANT" 须带 `$ANT`、`NYSE/NASDAQ: ANT` 或邻近 shares/stock 等语境才算提及；`Ant Group`、大写 `ANT GROUP`、无语境的 `ANT` 都不关联。
- `canonical_entity` 不把 "Ant" 折叠成 ANT；Anthropic、Claude 等名字在证券侧照常折叠到 ANT。
- `config/entities.yaml` 的 ANT 加 `evidence_entity: ANTHROPIC`，证据账本入口换名。
- 测试：`tests/test_config.py` 与 `tests/test_chain_evidence.py` 新增用例。
