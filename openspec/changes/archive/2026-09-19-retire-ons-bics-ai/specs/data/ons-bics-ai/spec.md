## REMOVED Requirements

### Requirement: ONS discovery 区分 BICS wave 与 AI 模块发布

**Reason**: `ons_bics_ai` 正式退役。该 requirement 定义的发现语义只服务于 BICS 条件模块：BICS 发布新 wave 时确认是否实际存在受支持的 AI 问题，缺失时返回 `question_not_fielded`。上游在 wave 163 已停止提出 AI 问题，来源事实断流；该来源从引入起就不是任何 L1 Observer 的输入，消费者数为 0，持续探测只产生运维成本。条件模块发现这一通用能力本身不随本要求退役——`question_not_fielded` 状态语义保留在 `data/structured-ingestion`，并已改写为与来源无关的表述，供未来其他条件模块来源复用。

**Migration**: 无需迁移消费方，因为没有 Observer、报告、图表、Chain、PEAD 或其他路径读取该来源。运维上停止对 `ons_bics_ai` 的发布与探测：`feature_flags.sources.ons_bics_ai`、`sources.ons_bics_ai`、数据集 `ai_enterprise_adoption_uk`、7 个 `ai.uk_enterprise_adoption.*` 指标、`provider_mappings.ons_bics_ai` 与 `adapters/structured/registry.py` 中的运行期适配器键全部删除；退役事实登记进 `retired_sources` 墓碑。库内 5,081 obs / 5,081 series / 3 artifacts / 2 source checks 按本次 change 的显式 purge 决策清除，不保留导出。若未来需要英国或欧洲企业 AI 采用证据，应针对 Eurostat `isoc_eb_ai` 等具备固定年度 cadence 的来源另开 change，而不是复活 `ons_bics_ai`。

### Requirement: ONS 以问题身份和 universe 建立 methodology regime

**Reason**: 随 `ons_bics_ai` 一同退役。该 requirement 要求每条 observation 保存 wave、question identifier、问题文本哈希、答案 bucket、routing、universe/route、统计单位、企业规模、SIC industry、estimate、standard error 与 release identity，并在问题文字、答案 buckets、routing、规模范围或技术列表变化时形成新 methodology regime。全库只有 wave 159 出现过一次深度题，regime 断点逻辑从未在第二条可比 wave 上被真正使用，其价值停留在设计层面。

**Migration**: 无需迁移。该来源的 methodology regime 概念没有下游消费者：没有序列依赖跨 regime 趋势判定，也没有报告展示 `methodology_break`。通用的「口径变化必须切断序列而非静默拼接」原则已由 `data/structured-ingestion` 的方法论变化检测要求覆盖，不随本要求消失。

### Requirement: ONS 保存采用广度和组织嵌入深度但不合成单值

**Reason**: 随 `ons_bics_ai` 一同退役。该 requirement 要求保存 AI 技术采用率、平均使用技术数量、extensive/limited/pilot 分布、每日使用 AI 的员工占比、改善运营用途、采用方式、培训/技能整合和工作岗位影响等白名单指标，并要求采用广度与嵌入深度保持独立、条件题显式标明分母。这是本来源全库唯一具备「非美国企业采用」维度的部分，但深度题只在 wave 159 存在一次，产品层只能永久返回 `insufficient_history`，无法支撑任何趋势判断。信息量与时效性均不达标，用户判定退役。

**Migration**: **不可逆**。该形态的英国企业 AI 采用与组织嵌入深度数据随 purge 永久丢失，不保留 CSV/JSON 导出（用户已知并接受的决策）。L1 三条轴中的「企业采用广度」继续由 `us_census_btos` 承担、「员工持续使用」由 `rps_genai_adoption` 承担、「任务生产化」由 `anthropic_economic_index` 承担，三者均不受影响；「美国之外的企业采用切片」在本次退役后**不再有来源覆盖**，属已知能力空缺，任何报告都不得再用文字回填这一空缺。

### Requirement: ONS 只作为地域补充且披露覆盖限制

**Reason**: 随 `ons_bics_ai` 一同退役。「ONS 结果标记为 UK supplement、不与 BTOS 美国值默认排名/相减/合并、跨地域并列只可在显式可比性矩阵中展示」这一约束在来源退役后失去指称对象，且它从未在 L1 输出中出现过——该来源不在任何 Observer、报告或图表里。跨来源禁止数值融合的通用原则由 `data/structured-query` 的可比性矩阵要求独立承载。

**Migration**: 无需迁移。退役后不存在第二个地域的企业采用序列，因此不存在需要跨地域可比性矩阵的场景；若未来重新引入非美国企业采用来源，应在新 change 中按当时的可比性要求重新声明地域补充与分母语义，而不是沿用本条。

### Requirement: ONS 质量门保留低响应、抑制和口径差异

**Reason**: 随 `ons_bics_ai` 一同退役。该 requirement 定义的百分比/均值值域约束、缺失与抑制与低样本与高 standard error 与未提问与 schema drift 的分状态表达，以及 chart CSV 与 workbook 同 cell 不一致时的 reconciliation 逻辑，全部绑定 BICS workbook 结构。解析适配器随本次 change 删除，质量门失去执行主体。

**Migration**: 无需迁移。通用的「抑制值不等于零」、「来源内部不一致需保留双 artifact 并阻止该 cell 进入默认趋势」原则由 `data/structured-ingestion` 的质量报告要求继续覆盖；BICS 专属的 `[c]` suppression code 与 workbook schema drift 处理随适配器删除，若未来接入类似 workbook 来源需在新 change 中重新实现。

### Requirement: ONS 查询返回可审阅的深度快照

**Reason**: 随 `ons_bics_ai` 一同退役。该 requirement 定义的按 wave、as-of 和 question regime 返回采用广度、嵌入深度、行业/规模切片、coverage、质量、freshness、问卷引用与 lineage 的 UK 深度快照查询能力，其唯一实现是 `products/ons_bics_ai.py` 与本 change 同时删除的 `products/base.py::ons_bics_ai_snapshot`。没有消费者调用它。

**Migration**: 无需迁移。该 DataProduct 方法从 `DataProducts` 契约中移除，`structured_artifacts`/`structured_observations` 中该来源的行随 purge 清除，历史 `as_of` 重放对该来源不再可解析。所有现存调用点为零，经全仓检索确认。
