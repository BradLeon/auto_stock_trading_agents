# FactSet Earnings Insight 实施任务（中文阅读版）

> 本文件是 `../tasks.md` 的中文翻译，便于快速阅读。英文原文仍是 OpenSpec 追踪的正式任务清单；下列 checkbox 仅用于对照阅读，不改变正式任务状态。

## 1. Catalog、配置和存储契约

- [x] 1.1 在 `config/data/sources.yaml`/`unstructured.yaml` 中注册 `factset_earnings_insight_doc` 和 `factset_earnings_insight_metrics`，包括稳定 URL、仅限内部使用策略、10 天新鲜度、预期 PDF MIME、超时、重试和 artifact 留存设置；扩展 catalog 测试以断言解析后的契约。
- [x] 1.2 在 `config/data/structured.yaml` 中注册数据集 `sp500_earnings_insight`、V1 metric ID/单位/维度、实体 `SP500`、全部 11 个 `GICS_*` 实体以及 FactSet 标签别名；增加对重复 ID、无效单位和缺失实体别名的 catalog 校验。
- [x] 1.3 为文档采集、`index_core`、`sector_core`、`macro_factset` 和 `sector_factset` 增加独立 rollout 条目，并按需将所有新数据路径默认为 shadow/off；测试 source mode 与 consumer mode 可以独立修改。
- [x] 1.4 在调度配置和有类型 settings 中增加 `factset_refresh_at: "08:10"` 与 `factset_refresh_tz`，并在启动时校验同一时区内刷新早于周六的周度评审。
- [x] 1.5 用通用 document-artifact 关联（`role`、页码、归一化区域、MIME、hash）扩展非结构化 store schema/repository，并增加覆盖现有数据库和幂等 upsert 的迁移测试。
- [x] 1.6 扩展结构化 evidence link，使其支持文字/图片锚点类型、页码、可选字符偏移、chart ID 和 region JSON；验证迁移后仍可读取现有纯文字证据行。
- [x] 1.7 将 `earnings.revision.improved_sector_count` 注册为第 31 个 V1 指标，并增加 FactSet provider mapping、count 单位、SP500 范围、目标季度/估计状态语义，以及必需的 `comparison_date`、`revision_direction` 和 `sector_total` 维度。

## 2. 不可变报告采集和文档投影

- [x] 2.1 在 `src/ats/data/sources/` 下引入 FactSet source module：跟随稳定 URL 的重定向，返回最终 URL、状态、ETag、Last-Modified、MIME、字节、字节数、抓取时间和来源失败分类，同时不写入消费者拥有的文件。
- [x] 2.2 准入前校验 `%PDF`、FactSet Earnings Insight 标题/日期锚点、可解析性和受限页数范围；将失败映射为 `unreachable`、`unauthorized`、`not_pdf` 或 `parse_failed`，并增加响应层单元测试。
- [x] 2.3 通过 structured artifact store 按 SHA-256 持久化 PDF 字节，为 `SP500`/`FACTSET` 创建一个非结构化 `research_article` 文档/版本；关联 `source_pdf` artifact，并保留稳定 URL 和最终 URL。
- [x] 2.4 保证重复抓取和重新处理幂等：相同 PDF hash 不得创建另一个文档版本、结构化 vintage 或处理投影，但新的 extractor 版本可以创建新的 run 和候选集。
- [x] 2.5 提取并持久化全部页面文字及页面偏移/章节标题，盘点页面图片和图表区域，并分别保留 PDF/text hash；增加 fixture，证明文字页和栅格页可共存于同一文档版本。
- [x] 2.6 增加由操作人员控制的本地 PDF 导入路径，复用网络采集相同的校验/投影 pipeline，并将实际导入时间写为 `known_at`；不再把遗留 Obsidian 目录保留为读取依赖。

## 3. 指数文字抽取和准入

- [x] 3.1 定义有类型的 FactSet candidate、report period、estimate state、metric group、evidence anchor 和 extraction run 模型，包含原始 token/value 及 extractor 版本。
- [x] 3.2 使用标题、目录和章节锚点实现文档分类及报告阶段判断（`pre_reporting`、`in_progress`、`substantially_complete`），对无法识别的布局产生 `unknown_template` 隔离结果。
- [x] 3.3 用按章节划分的文字 extractor 替换单体式遗留正则，覆盖披露进度；EPS/营收 Scorecard 与 surprise；增长；利润率；guidance；bottom-up EPS；forward/trailing P/E 参照；地域；评级；目标价上行空间。
- [x] 3.4 将每个候选标准化为标准实体、目标季度/年度或快照日期、`estimated|blended|actual|not_applicable`、单位和来源元数据；把 “Ten of eleven sectors” 及等价数字/单词变体解析为 `earnings.revision.improved_sector_count=10`，并保留原始 token、文字证据、`comparison_date`、`revision_direction` 与 `sector_total=11`。
- [x] 3.5 实现确定性的范围、单位、期间/状态和组成校验，包括分布合计 `1.0 ± 0.01`、非负整数 guidance 计数，以及 `0 <= earnings.revision.improved_sector_count <= sector_total`；缺失值必须保留为带原因的 null，不能变为零。
- [x] 3.6 对共享同一 observation identity 的不同数值实现重复证据合并与冲突隔离；为 7.5%/7.7% 营收增长差异增加回归 fixture，并断言系统不会静默选择其中任一个。
- [x] 3.7 通过现有 structured ingestion/release pipeline 发布准入的指数候选，携带 document/version/text-span evidence link，并按适用指标组生成 `index_core` quality manifest。

## 4. 行业图表抽取和质量门禁

- [x] 4.1 增加按标准化标题、坐标轴/图例锚点、预期列、适用报告阶段和 extractor 版本索引的 chart registry；不能把页码作为唯一分类器。
- [x] 4.2 增加可选的确定性本地 OCR adapter，显式发现依赖并支持 `extractor_unavailable` 状态；确保 OCR 缺失时跳过行业抽取，但不阻塞文档或指数处理。
- [x] 4.3 渲染/提取图表图片，应用带版本的布局裁剪，并输出逐单元格候选，包含 chart ID、从 1 开始的页码、归一化 bounding box、原始 token、解析值和图片区域证据。
- [x] 4.4 将所有行业标签标准化为 11 个标准 GICS 实体，并拒绝存在缺失、重复或未知行的表格；增加标签别名及恰好 11 行完整性的测试。
- [x] 4.5 实现图表表格校验器，覆盖预期列、数值范围、Scorecard 计数勾稽和百分比组成；隔离整张受影响表格，而不是发布不完整的行业排名。
- [x] 4.6 将通过校验的行业候选作为独立 `sector_core` release 分区发布并生成自己的 manifest；任何标注单元格、行或表格检查失败时保持 shadow。

## 5. 验收语料和来源验证

- [x] 5.1 为当前报告 `082826` 创建可提交的 acceptance manifest，记录 PDF hash、报告元数据、选定文字 span、图表 identity、预期适用性以及全部 V1 行业单元格，但不提交授权 PDF 字节；历史 PDF 仅作为可选受控重处理输入。
- [x] 5.2 增加用于 CI 的合成 PDF fixture 和内部语料 runner；授权 artifact 不可用时输出明确的跳过原因。
- [x] 5.3 对当前报告 `082826` 执行文档验收，要求报告日期正确、hash 不可变、导入幂等、页面文字/图表 inventory 完整，且不存在非预期的模板分类。
- [x] 5.4 对当前报告 `082826` 执行指数验收，要求预期适用字段 100% 完整、期间/状态/单位正确、证据全覆盖，并显式隔离已标注的来源冲突。
- [x] 5.5 对当前报告 `082826` 执行行业验收，要求 231 个适用单元格逐格 100% 一致、行列完全完整、没有未解决的 GICS 标签，然后才能将 `sector_core` 从 shadow 切换出去。

## 6. 有类型 DataProducts 和历史查询

- [x] 6.1 新增 `src/ats/data/products/earnings_insight.py`，包含有类型的 report、index、sector、status、warning 和 lineage 记录；通过公共 data-products namespace 导出。
- [x] 6.2 实现 `DataProducts.earnings_insight_snapshot(as_of=None)`，选择内部一致的已发布报告快照，关联受治理的文档引用，并确保消费者不会访问网络或物理表。
- [x] 6.3 不存在 release 时返回有类型的 `registered_no_data`/`unavailable`；超过新鲜度边界后，返回上一期 release，并包含报告年龄、最近刷新失败和 `stale`。
- [x] 6.4 增加 latest、`as_of` 和全部 vintage 的查询/CLI 覆盖；证明相同目标期间的连续报告仍各自独立，并且回填 PDF 在真实摄取 `known_at` 之前不可见。
- [x] 6.5 增加 observation 到来源的追溯，同时解析文字 span 和图表区域；确保内部图表视图附带来源/授权元数据。
- [x] 6.6 实现从 `EarningsInsightSnapshot.index` 到现有 `EarningsBackdrop` DTO 的单向兼容 mapper，并对缺失字段、冲突、stale 数据和 estimated/blended/actual 标签进行回归测试。

## 7. Macro 和 Sector workflow 集成

- [x] 7.1 重构 Macro assembly/review，使其在 `macro_factset` 控制下只通过 DataProducts 产品取得 FactSet 上下文；platform 分支不得访问网络、PDF 或 Obsidian。
- [x] 7.2 增加 Macro 双读/shadow 对比输出，覆盖数值、期间/状态、报告日期、新鲜度、warning 和渲染后的评审文字；切换到 platform 前至少验证两个报告 vintage。
- [x] 7.3 在 `sector_factset` 控制下将 GICS 行业矩阵加入 Sector assembly，并明确标记为 top-down 市场背景，区别于 AI-hardware layers 和公司证据。
- [x] 7.4 增加消费者测试，证明 Chief、Risk、PEAD、Technical 和公司证据 workflow 不会直接查询或独立加权 FactSet 产品。
- [x] 7.5 增加降级测试，证明 FactSet unavailable 时省略相应结论但不导致周度评审失败，而 stale 数据会连同原始报告日期一起暴露。

## 8. 调度、运维和可观察性

- [x] 8.1 为采集、文档投影、候选抽取、校验和独立的条件发布实现单一 pipeline 入口；输出 run/report/partition 计数器和脱敏 provenance，不包含 PDF 正文或带签名参数的 URL。
- [x] 8.2 在 `src/ats/runtime/scheduler.py` 中注册 `factset_weekly_ingest`，按配置的刷新时间在周六执行，并设置有限 misfire grace 和单次运行合并；验证它早于周度 Macro→Sector 评审运行。
- [x] 8.3 增加调度测试，覆盖时区顺序、节假日/报告未变化时的 no-op、刷新失败但存在上一期 release，以及刷新失败且没有 release。
- [x] 8.4 增加运维命令/报告，用于查看来源状态、列出报告 hash/版本、展示分区质量、追溯证据、查询 vintages，以及使用指定 extractor 版本重新处理 artifact。
- [x] 8.5 扩展 source validation、release-check、health、lineage 和 snapshot-manifest 覆盖，使 `index_core`、`sector_core` 结果及最近一次尝试失败可独立查看。

## 9. 切换、回滚和退役

- [x] 9.1 应用增量迁移，在所有 source/consumer mode 均非 platform 的情况下，将当前报告 `082826` 导入隔离环境；归档 acceptance 和 quality 报告。历史 PDF 可由操作人员单独受控重处理。
- [ ] 9.2 提升 `index_core`，在双读门禁通过后切换 `macro_factset`，并观察一次成功定时刷新和一次周度评审；在下一个周六窗口前，获批准的操作人员等价演练可改为按有效生产 mode 运行已注册 FactSet import pipeline，并紧接着运行 Macro 与 Sector review。记录 run ID、选中报告版本、effective mode、结果及 consumer-flag 回滚演练。`sector_factset` 保持 shadow，其提升仍属于 9.3。
- [ ] 9.3 只有通过 100% 单元格门禁后才提升 `sector_core`；Sector smoke/regression 测试通过后切换 `sector_factset`，并观察一次成功定时刷新和 Sector 评审。
- [x] 9.4 验证回滚只改变路由，并保留 PDF artifact、document version、candidate、evidence link、structured observation 和 vintage。
- [ ] 9.5 观察期结束后，删除 Macro 对 FactSet 的直接下载/解析器调用，移除对 `config/macro.yaml` FactSet 文件夹设置的运行时依赖，只保留受治理的 import/ingest 路径。
- [ ] 9.6 更新数据层架构、来源 inventory、操作 runbook、版权/内部使用指南、metric dictionary 和消费者 ownership 文档；在宣布迁移完成前运行聚焦测试和完整测试套件。
