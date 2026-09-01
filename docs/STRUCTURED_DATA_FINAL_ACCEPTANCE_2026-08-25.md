# 阶段二：结构化数据沉淀最终验收（2026-08-25）

> OpenSpec change：`build-structured-data-foundation`
> 分支：`codex/data_frame_rebuild`
> 结论：实现与验收完成；有质量缺口的数据源按门禁留在 legacy/planned，不把“变更完成”等同于“所有来源均切生产”。

## 1. 交付结论

阶段二已经形成可独立演进的低频结构化研究数据层：统一来源/数据集/指标目录，内容寻址 artifact，中央准入与隔离候选，不可变 observation vintage，严格 `as_of`，来源选择与冲突，版本化派生，SQL/Pandas/DataProducts，snapshot manifest 和五维质量报告。

ticker 股价、OHLCV、订单簿、期权链、Greeks 和 IV 仍由 IBKR、yfinance、ThetaData 在运行时获取；本变更没有为其建库、回填或保存 artifact。

## 2. 最终测试门

| 测试层 | 结果 |
|---|---|
| 全仓确定性测试 | **1,026 passed**，312.39 秒 |
| PEAD / Sector / Chain 完整回归组合 | **226 项通过** |
| 文档与机器目录一致性 | 通过；来源/数据集状态和总入口链接受测试保护 |
| 数据库迁移、幂等、修订、并发读、`as_of` | 通过 |
| Adapter contract、准入、局部失败 | 通过 |
| SQL / Pandas、派生、横截面、snapshot replay | 通过 |
| runtime 行情/期权零持久写入 | 通过 |

最终全量命令：

```bash
.venv/bin/python -X faulthandler -c \
  'import sys,types; sys.modules["readline"]=types.ModuleType("readline"); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))' -- -q
```

这里注入空 `readline` 模块是本机 Anaconda/readline 组合的测试启动规避，不改变产品代码。

## 3. 真实源与人工抽查

| 数据集 | 真实结果 | 当前决策 |
|---|---|---|
| 台湾 IC 出口 | 20 个连续月，accepted 20 / quarantined 0；同比/环比与旧 Chain 零差异 | Chain `platform` |
| 韩国半导体出口 | 20 个连续月，accepted 20 / quarantined 0；同比/环比与旧 Chain 零差异 | Chain `platform` |
| 官方财务 + stock_statement | 1,064 accepted、7,670 quarantined、228 pending fields、7 个冲突记录；无前视 | fundamentals `legacy` |
| 市场 Consensus | 4 实体每个最新 42 条；60/60 旧标量一致；MSFT 两个真实时点；无前视 | PEAD/Sector `platform` |
| OpenAI/Anthropic 事件数值 | 9 候选、7 accepted evidence、2 rejected、5 observations、3 events；人工抽查准确率 100% | verified observation 可查询 |

人工抽查口径是“标准化值与保存的来源 artifact / 文档 span 一致”，并不把媒体转述自动提升为第一方公司事实。财务多来源差异按原样保留，不覆盖、不平均。

## 4. 五维质量与数量对账

统一报告覆盖 Coverage、Accuracy / Reconciliation、Freshness、Completeness、Availability。六个 dataset 的查询数量均能与 ingestion、candidate 和 observation 语义对账：

- 台湾、韩国与 Consensus：当前报告通过。
- `company_financials`：因核心覆盖、陈旧度和跨源冲突失败，触发 legacy 回退。
- `industry_dram_contract_price`：本轮没有独立真实专项，明确显示 `no_coverage/no_run`。
- `private_company_events`：观测通过，但证据 workbench 不使用普通 ingestion run，Availability 显示 `no_run`，作为后续可观察性缺口保留。

真实专项 artifact 合计 17 个逻辑引用、14 个唯一 blob、3 次库内去重引用、约 17.4 MB 物理内容。各专项采用独立根目录，因此没有虚报跨库全局去重。

## 5. 历史真实性与消费者切换

- 财务首次抓取之前的 `as_of` 查询不可见本次回填；future leakage 为 0。
- Consensus 的早期 `as_of` 只返回第一次 MSFT snapshot，不读取第二次可见时点。
- 消费者 manifest 创建后追加新 vintage、改变来源优先级、注册公式 `v99`，旧 manifest 的 observation ID 与重放值不变。
- 持久财务/Consensus 与模拟股价/期权组合后，manifest 只包含持久 observation。
- `FundamentalData`、Consensus 固定键 dict 和 Chain `SeriesPoint` 兼容契约均保留。

默认消费者模式：

| 消费者 | 模式 |
|---|---|
| Chain 台湾/韩国 | `platform` |
| PEAD Consensus | `platform` |
| Sector Consensus | `platform` |
| PEAD fundamentals | `legacy` |
| Sector fundamentals light | `legacy/runtime` |

所有旧路径继续保留。删除旧取数逻辑不属于本变更，必须另立 OpenSpec，并在删除前再次完成真实更新周期与 Workflow 回归。

## 6. 三类角色文档走查

- 开发者：组件架构图、采集/准入/查询数据流、领域对象、Adapter 契约、映射、扩源、测试分层、迁移和禁止绕过规则均已与实现校准。
- 运维者：机器目录镜像、已接入/planned/runtime-excluded、认证、QPS unknown 与内部预算、调度、五维质量、故障、回滚、退役和验收命令均已覆盖。
- 使用者：数据发现、CLI、Python、SQL、Pandas、latest/vintages/`as_of`、横截面、派生、血缘，以及 persistent/runtime 组合均给出当前可运行示例。

总入口为 [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md)，状态事实源为 `config/data/structured.yaml`。

## 7. 未覆盖与后续小变更输入

以下是明确保留的产品缺口，不影响本次底座完成，但阻止相应来源切换：

1. `company_disclosures` 仍为 planned。TSM 等外国发行人需要 20-F/6-K、公司 IR 或本地官方披露适配器，才能补足 SEC Company Facts 的季度时效。
2. 财务有 228 个待映射镜像字段和 7 个跨源 conflict records；应按真实消费者优先级分小批映射，不应追求把所有 Provider 字段一次性纳入核心指标。
3. 财务 freshness 当前按 240 小时统一门过严；事件型季度数据应另立变更，基于发行人公布日历定义 deadline，而不是在本验收中放宽。
4. TrendForce DRAM 没有本轮独立真实专项；需要固定页面 fixture、真实 smoke 和条款复核后再提高状态。
5. 外部 QPS 无权威值的来源继续标记 `unknown`，只使用保守内部预算；yfinance 仍是非正式公共接口。
6. Evidence workbench 应补普通 ingestion run 映射，使 Availability 不再显示 `no_run`。
7. PostgreSQL/湖仓暂不需要。12,000 条合成负载点查询 P95 约 1 ms、竞争写 P95 约 0.26 ms、锁错误 0、SQLite 约 7.4 MB；超过既定阈值时先复跑基准再提物理迁移。

## 8. 验收证据索引

- [现状基线](STRUCTURED_DATA_BASELINE_2026-08-25.md)
- [SQLite 基准](STRUCTURED_DATA_BENCHMARK_2026-08-25.md)
- [区域序列专项](STRUCTURED_DATA_REGIONAL_VALIDATION_2026-08-25.md)
- [财务专项](STRUCTURED_DATA_FINANCIAL_VALIDATION_2026-08-25.md)
- [Consensus 专项](STRUCTURED_DATA_CONSENSUS_VALIDATION_2026-08-25.md)
- [文档证据专项](STRUCTURED_DATA_EVIDENCE_VALIDATION_2026-08-25.md)
- [五维质量与数量对账](STRUCTURED_DATA_QUALITY_VALIDATION_2026-08-25.md)
- [消费者迁移与重放](STRUCTURED_DATA_CONSUMER_VALIDATION_2026-08-25.md)
