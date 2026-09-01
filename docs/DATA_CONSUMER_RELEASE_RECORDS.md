# 数据消费者发布记录

> 记录日期：2026-09-01。数据源/数据集发布与消费者读取切换是两件事；本文件记录后者。
> 机器可读的完整证据保存在 `var/data.sqlite:data_consumer_release_records`，其中每条记录都含输入、输出、
> 血缘、失败处理和回滚字段。本文件提供可审阅索引，不复制原始文档正文或 Workflow memory。

## 查询方法

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data consumer-release-records \
  --consumer sector_agent --target-db var/data.sqlite
```

该命令只读。发布资格仍须先通过 `data release-assessment --consumer <ID>`；发布或回滚覆盖层才是写操作。

## 本轮结论

| 消费者 | 决定 | 当前 mode | 发布记录 ID | 依据 / 后续动作 |
| --- | --- | --- | --- | --- |
| `pead_consensus` | published | platform | `d00e87a494ef455d80db15d849162e0d` | Yahoo Finance 权威 Provider snapshot；修订或分析师范围差异带 `known_at` 保存。 |
| `sector_consensus` | published | platform | `1462f068a3ea49be832c15ae89ec2786` | 当前 sector cross-section 的逐实体 snapshot 对账等价。 |
| `sector_agent` | published | platform | `67751c6287c644228a4a6911547f92b4` | 区域序列等价；TrendForce 只通过 Chain evidence 进入，`unknown` 命题状态保留为显式降级。 |
| `macro_agent` | published | platform | `d68280478b0d4cf3b28fef5047583e03` | legacy `ConnectError` 经过独立官方 artifact、2026-07 期间/单位、freshness 及 no-LLM offline 输出复核，归类 `governed_upgrade`。 |
| `pead_fundamentals` | published | platform | `08999e928fa74a8e8d36b510f1c92ea9` | MSFT legacy DTO 为空，平台 FY2026 Q4 报表包经 12/12 lineage、期间/单位、时效和完整 8 行 PEAD DTO 独立复核，归类为 `governed_upgrade`。 |
| `pead_graph` | published | platform | `23a61fdb83e2471898684c6f508ae3d2` | NVDA Q2 FY2027 隔离 legacy/platform prep/score 对账：相同 deterministic score/decision，platform 将 legacy 的错误日历日 `2026-11-17` 修正为受管事件包 `2026-08-26`；release、10-Q、transcript 版本均可追溯。 |
| `pead_monitor` | published | platform | `702241e2ca5d48fc8543de80ee66df61` | NVDA 七日隔离 no-LLM 实测。IBKR News 三个受管文档均有不可变 version 血缘与正确 ticker 关联；与 legacy 聚合新闻的召回差异属于已批准的“IBKR 优先、仅不可达时 Yahoo 兜底”受控升级，并已独立复核输出。 |
| `chain_regional` | published | platform | `d59c735c68b641d589e526d39b273f26` | 台湾财政部、韩国 ECOS 的 2026-06/07 月度点位、单位、同比/环比及确定性 Chain observations 均与 legacy 等价；每条平台点均保留 artifact/observation 血缘。 |
| `sector_constituent_financials` | published | platform | `aceb48a6ff2a49c1bdce1dfcaab65f83` | 原 `sector_fundamentals` 更名。MSFT 实测复用 PEAD 的 DefeatBeta 完整报表包（2026-06-30）；AMD 无包时三项财报指标明确为 `no_coverage`，未回退为 Provider 财务字段；市值、估值与 Beta 继续为 runtime。 |
| `pead_research` | published | platform | `f4fde0e465b94245942c7744b2ef2200` | 30 天 platform-only 隔离验收选中 29 篇第三方研究文章，全部具 immutable document/version 血缘；临时 memory no-LLM 处理循环按每轮 8 篇上限完成。SemiAnalysis `partial` 预览允许进入但保持完整性/截断血缘，其他不完整资产、官方披露与新闻被排除；legacy 等价不作为门槛。 |
| `evidence_chain` | published | platform | `1cdc59f3f4c14d11b35d4a0867b275c6` | 实际 platform no-LLM Chain 报告（77,957 字、26 条 claim assessment）已验收；1,401 条引用均可重放，其中 130 条为完整 document/version、1,271 条为明确标记的历史 evidence snapshot，0 条不可重放。`structured_observations` 不在该消费者合同内。 |
| `chief_graph` | orchestration boundary | shadow | `244283b9cc904122af97e936727d1b93` | 仅汇总上游并写 Workflow memory；不得发布为 data-consumer platform。 |
| `runtime_scheduler` | orchestration boundary | shadow | `ef5964295e1743a388cdff6283a2e902` | 仅触发 products/runtime/pipelines；阶段隔离和 dry-run 已验收，不存在可替换数据读模型。 |

## 回滚结果

十一个 `published` 直接消费者均已在生产 release overlay 上完成独立演练：
`platform → legacy → platform`，每一步均成功，且其他消费者未被修改。对应 history 位于
`var/structured_data/releases.yaml`。未发布消费者没有提升 mode；它们保留既有 `shadow` 或 `legacy`，
因而当前读取路径本身就是其安全回退状态。

`chief_graph` 与 `runtime_scheduler` 的回滚仅是编排边界的 overlay 演练，不能也不会使其成为 platform
数据消费者。它们的 report、score、decision、trade 与 run record 始终位于 `ats.memory`。
