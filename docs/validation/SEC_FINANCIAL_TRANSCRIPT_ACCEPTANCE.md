# SEC、财务报表与电话会提取复用验证

日期：2026-09-26。Change：complete-target-dataflow。

## 范围与模块

- `src/ats/data/sec.py`：共享 SEC 提取器；旧默认调用行为不变，新增 task-local transport 注入。
- `src/ats/data/pipelines/unstructured/sec.py`：新路径的官方 accession 边界、限速、重试、请求/响应预算，以及调用共享提取器。8-K/6-K 选择财报新闻稿，10-Q/10-K/20-F 选择监管报告。
- `src/ats/data/pipelines/unstructured/transcripts.py`：电话会财季与说话人段落校验，复用旧 DefeatBeta Markdown 渲染。
- `src/ats/data/sources/defeatbeta.py`：HF revision、manifest/hash 固定与 Parquet 查询。
- `src/ats/data/sources/company_financials.py`：保留原有字段映射、币种、ADR/市场调整 EPS 等语义；修正生产 URI 为 US 分区，查询绑定不可变 revision。
- `src/ats/data/pipelines/unstructured/document_ingest.py`：队列后的编排、准入、版本发布与运行审计，替代混装来源职责的 fixed_sources.py。

操作入口为 `uv run ats data document-ingest --source SOURCE [--entity SYMBOL]`。
旧 fixed-ingest 命令仍走同一队列入口，便于已有任务重放；不保留旧 Python 实现副本。
未重命名历史 source ID、快照表或存储目录，未迁移/删除旧资产。

## 真实受管运行

| 来源/范围 | Queue task | Ingestion run | 结果 |
|---|---|---|---|
| 财务数值 NVDA，自 2026-01-01 | 6d9fd9d7-7233-540a-ab92-fa09c8dbf0e4 | e4a67fe412c44072226ea18f | succeeded；新增 13，未变化 39，隔离 0 |
| 财务数值 AMD，同范围 | de756d71-8fec-50ff-8670-36485073e35e | 89ceeb1dc979c80ad0a7c6c8 | succeeded；新增 26，隔离 0 |
| 财务数值 TSM，同范围 | 0cb96533-57f9-56b4-b097-f1ac4a3dc0a6 | 1221efe486d91d62520e4377 | succeeded；新增 13，未变化 13，隔离 0 |
| 电话会 NVDA | 7ae8449d-ad5f-5bcf-ae74-9268a946aead | 35d0ffd60329c8694c5c | 4 条成功发布 |
| 电话会当前注册 US 范围 | 1e510d38-33f3-5c3d-adfb-a8ae62eac6b8 | e27deda7c3deb981d28f | 111 条；107 发布，4 未变化，0 隔离/失败，无缺失标的 |
| SEC AMD 官方正文 | f8c1aeb1-be13-5bb6-9eb0-0f5ebc9d18ac | a482929e2333639d4b74 | failed / queue retry_wait；4 个 accession 未取得正文，0 发布 |

电话会 Data Product `admitted_documents` 实际读回 115 份历史/当前文档，29 个实体，均有非空正文、不可变版本和 full 标记。累计文档数不等于本轮 111 条；历史身份/版本保留，不以删除历史来对齐数量。此次渲染补齐 Prepared Remarks/Q&A，内容变化正常产生版本，不能解释成上游刚发布了 107 场新电话会。

财务数值证据覆盖上述三个标的，不外推全市场，也不等于全部财务消费者切流已验收。

## 换 IP 后真实复验（2026-09-26，当前结论）

以下全部经过新受管队列，不是 mock 或绕过队列写库。未改变代理、预算、调度启用状态或交易路由。

| 来源/范围 | Queue task | Ingestion run | 结果 |
|---|---|---|---|
| 台湾出口全历史 | c3f071e1-171e-5aca-bc59-ec84ca89a814 | c432d2d0bbaeac5572aad297 | succeeded；9 新增/修订、299 未变、0 隔离 |
| 台湾重复采集 | ea9ef8f2-6189-5cbc-9bed-52c09cf31d4b | 3b55c51d8d49dc0cdd75bf39 | no_change；308 未变、0 新增/隔离 |
| SEC 索引全 US 范围重试 | e857bbbd-dbd2-5e38-b0cf-0c0ef3d6b472 | 7b50e38237ef94490d65 | no_change；116 行、无缺失实体、snapshot_stale=false |
| SEC NVDA 正文 | 1ad0fc20-9566-5c3b-b6a6-4c4bfd7d10f7 | 71520e4ea12bb8cda836 | partial；3 发布、1 隔离、0 来源失败 |
| SEC NVDA 重复采集 | 33e2fd9f-1bf1-5ee0-b8b2-437640f1e0a8 | 36577f1c850fb876f3a3 | partial；3 no_change、1 隔离、0 新发布/来源失败 |
| SEC AMD 正文 | 028a0220-dae1-57f3-b08e-0af6d1f67b1c | ed67036671cacc1d1c09 | partial；3 发布、1 隔离、0 来源失败 |

台湾原始 CSV SHA-256：`d87d2e7e5e9a25fd335c0f2411034ffe4faa2162dae351cc4165544c55d5deeb`，位于 `var/data_artifacts/d8/`。重算 hash 并解析后，2001-01 至 2026-08 共 308 条与 Data Product 数值全部匹配（误差 < 1e-6）。RegionalProducts 最新 observation `1770d50bea297d331457891f`：2026-08、32,215.598 百万美元、YoY 57.9957%、MoM 16.1534%。实际 CSV 口径为“电子零组件”，历史 `tw_ic_exports` / 产品标签“IC 出口”比来源口径窄；本轮不改变数据定义，不宣称它是纯 HS8542 集成电路。

SEC 六份正文均由 `admitted_documents` 读回，具备官方 URL、不可变 version/hash 和非空全文：NVDA 10-Q 185,600 字符、财报稿 26,197 字符、其他公司稿 10,922 字符；AMD 10-Q 258,147 字符、财报稿 36,881 字符、其他公司稿 3,542 字符。公司稿不全部等同于财报稿；本次正文覆盖仅 NVDA/AMD，不外推 29 个标的全部通过。

仍隔离：NVDA `0001045810-26-000078`、AMD `0001193125-26-354029`，均为 `ValueError:sec_missing:complete_submission:`。共享提取器经过 filing index / complete submission 路径未找到合格目标正文，不能据此断言官方无文件；未用索引/封面冒充正文。按确认政策 SEC 原文缺口可为空、非阻塞，保留隔离记录；运行时资格计算 Task 4.6 仍待独立完成。

索引首轮 task `a236082a-f56f-554b-9f36-a13e25144381` 为 failed / retry_wait，队列仅保留 exit=1 与输出 hash，未取得具体异常，不能臆断为 TLS；上表新手动重试成功，不覆盖首轮失败历史。正文采集使用此前已准入索引，随后全范围索引重验为 116 no_change。本轮无 LLM、Agent、Workflow 或订单动作。

截至 2026-09-26，当时记录尚不代替逐源 launchd cadence、全量 Layer as-of 组合读回、许可/修订及旧 owner 退役验收，故当时 2.2.9、2.2.11、2.2.12 未整体勾选。2.2.12 的最新复核与状态更新见本文件末尾 2026-10-02 专节；其余任务状态不由该专节改变。

## SEC 外部阻塞（换 IP 前历史记录）

本节为历史证据，当前成功结果以上节为准。后续确认 Clash TUN 开启，下面“直连”只是不使用环境代理，并非绕过 TUN 的独立对照；旧 TLS 失败不能证明当前仍不可达。

已用旧路径真实成功的 NVDA 财报稿 URL
`https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm`
进行只读探测，使用配置的 SEC User-Agent。环境代理与直连均报：
`ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]`。

因此本次阻塞发生在 TLS 连接阶段，不是 DefeatBeta 未返回索引，也不是正文解析已经成功。SEC 自动 job 保持关闭，2.2.12 不勾选；未用旧缓存正文冒充此次真实抓取成功。此前沙箱内 NVDA 验证记录 quarantined；随后已修正目标路径错误分类：连接失败/预算耗尽记来源失败，可重试；内容缺失/身份不合格才隔离。

## 本地验证

通过 uv 运行：

```sh
uv run pytest -q tests/test_sec_managed_extractor_local.py tests/test_fixed_sources_target_local.py tests/test_structured_company_financials.py tests/test_managed_cache_miss_local.py
openspec validate complete-target-dataflow --strict
```

定向集合 51 passed，覆盖共享提取器复用、EX-99/完整 submission 回退、按 declared type 选择主文件、6-K 封面拒绝、task-local transport 异常恢复、官方 URL/请求预算、电话会 ordinal 别名/断裂、发布/重复/修订、财务原映射及队列边界。四个新拆分模块 Ruff 通过。

额外旧 SEC 集合与财务集合合跑：37 passed、3 failed。三个失败均为旧 documents.gather/Memory 调用直接写生产平台而无 queue lease；按用户范围只登记，不修旧调用方。

测试保留在 Git 忽略的 tests/ 中。本文件是可版本化的脱敏验证摘要；不包含凭据或电话会原文。未提交、推送或切换交易/Workflow 路由。

## 2.2.12 固定来源链最终复核（2026-10-02）

- **定时检查**：生产 `com.ats.data-refresh` 账本记录 SEC 索引 job `defeatbeta_sec_index_daily` 于 2026-10-01、10-02 经受管队列成功；电话会 `defeatbeta_transcript_daily` 于 2026-10-01 定时运行成功。对应 task 分别为 `23141072-a09d-5372-b602-7761455a14fa`、`0772ba13-afa9-50f6-90fc-9f857931d342`、`6f1d9ea5-a040-55a3-9234-16b2c99073c5`。固定上游未改变时，`no_change` 是有效刷新结论。
- **最新完整覆盖验收**：固定 US revision `a46d68650c1f90b7331608350dced8364047b3f7` 下，SEC 索引 116 行、29/29 目标美股；电话会 111 条、29/29 美股。`admitted_documents` 读回 115 份历史/当前完整文档，均有实体、财季、非空 speaker/segments、不可变版本与 hash。US-only 是来源范围，韩股不属于这两个固定源的目标集合。
- **SEC 正文缺口**：NVDA/AMD 已发布并读回 6 份 SEC 官方原文；2 份 `sec_missing` 保留隔离、错误与 task/run lineage。SEC 正文按用户确认是 optional/non-blocking，不要求每个索引项都成功取得正文；索引元数据不冒充正文，正文 job 不因该例外自动启用。
- **更新与修订语义**：定时检查固定 revision、manifest/spec 与文件 hash；上游未变化时报告 `no_change`。相同身份修订 append-only 与重复无变化有受管路径/隔离重放验证。没有真实新上游 revision 可观察时，不等待上游人为改变；发现新 revision/hash 时必须生成新候选并保留旧版本。
- **许可与使用边界**：Hugging Face 数据集卡片标注 `ODC-By`，并说明用途面向研究和教育、数据来源包括 Yahoo Finance、Nasdaq 与美国财政部。注册表继续使用 `odc_by_research_subject_to_upstream_terms`；本项目限内部投资研究，保留来源归属、revision 与 hash，不对外再分发原始 transcript 或索引文件。数据集卡片标签不证明底层来源内容可不受限制地再利用；这记录的是当前系统使用边界，不构成法律意见。若用途扩展至公开传播、转售、对外提供全文或训练/发布衍生数据，必须重新审查数据集与上游条款。[数据集卡片](https://huggingface.co/datasets/defeatbeta/yahoo-finance-data)
- **缺失和 fallback 用例**：自动持久化只接受固定 DefeatBeta source ID 与 SEC 官方正文来源；搜索引擎/新闻网页没有写入 fallback。受管测试覆盖 revision/hash、身份/财季、官方 host/path、speaker/segment、来源缺失、重复与同身份修订。旧 caller 无 queue lease 的失败仍作为 legacy 缺口记录，不属于新固定来源链。

本次以 `UV_CACHE_DIR=/private/tmp/ats-uv-cache uv run --offline --no-sync pytest -q tests/test_fixed_sources_target_local.py tests/test_sec_managed_extractor_local.py` 重跑，**20 passed**；`openspec validate complete-target-dataflow --strict` 通过。一次包含旧 SEC/旧 DefeatBeta 直写用例的扩展集合为 66 passed、4 failed；四项均因 legacy caller 没有 managed queue lease，和既有“只登记旧路径问题、不修复旧路径”的范围一致，不纳入本固定来源链验收。

结论：按当前限定的内部研究用途，**2.2.12 固定来源技术链、真实定时刷新、覆盖、缺失/修订语义和许可边界记录已完成**。SEC 原文可选缺失保持非阻塞；此结论不表示 SEC 原文来源本身已通过、不授予对外分发权，也不代表 Phase F 或全域 Dataflow 切流资格。
