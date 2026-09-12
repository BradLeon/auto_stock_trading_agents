## Context

本变更见 `proposal.md`。当前结构化层已经支持 BTOS、RPS/FRED 和 Anthropic Economic Index，并由 `ai_hardware/L1_app` Observer 通过三轴 evidence bundle 读取。Ramp AI Index 是另一种观测：它从 Ramp corporate card、invoice 和 ACH 的匿名聚合交易识别企业是否为 AI 产品/服务发生正向付款，并另外提供 AI spend per employee 与 Token Spend Management 的模型归因 API spend。

本次浏览器验证（2026-09-11）确认：

- 页面 `https://ramp.com/data/ai-index#adoption#overall` 的 `Get the data` 控件是可见按钮，点击后页面提示 `Copied data to clipboard.`，剪贴板内容为制表符分隔文本，而不是标准浏览器下载事件。
- Overall 导出的历史表含 80 行，列为 `Date / Series / Adoption rate (%) / Monthly change (pp) / Yearly change (pp) / Census question version`；当前示例最新月为 2026-08，Ramp Overall=56.13%。
- Sector 导出包含 NAICS 行业分组（例如 Technology and media、Finance and insurance、Manufacturing）；页面也提供 Business size，但企业规模不是首版采集白名单。
- Spend per employee 导出包含 `Median / Top 10% / Top 1% (USD / employee / month)`；Model market share 导出包含 `Provider / Model / Spend type / API spend share (%)`。
- 页面方法说明使用“70,000+ American businesses”；该公开方法文本作为 cohort 说明保存，不能把营销样本数当成精确分母。

## Goals / Non-Goals

**Goals:**

- 将 Ramp 网页官方结构化导出纳入统一 source/artifact/observation/vintage/lineage 体系。
- 提供总体、总体模型分解、行业、支出分位数和模型 API 支出份额的可查询月度数据，并保留各自统计主体和分母；企业规模与地理维度暂不发布。
- 首版只复用已落盘的官方 TSV fixture；没有 fixture 时才使用可见网页导出。API/MCP 和主动 API discovery 不在首版范围内，避免无授权等待、重复下载和公司邮箱注册依赖。
- 为 L1 Observer 提供独立的“付费企业采用与 AI 支出”补充证据、方法卡、Agent context 和嵌入式图表。
- 让网页导出、API（如获授权）和未来稳定下载 URL 使用同一规范化 schema，可进行离线重放和版本对账。

**Non-Goals:**

- 不采集或推断单个 Ramp 客户、交易明细、员工、公司名称、个人账户或未付费工具使用。
- 不将 Ramp adoption 与 BTOS、RPS 或 Anthropic 的比例求平均、相减、加权或合成统一 penetration score。
- 不把 Ramp 公司自身客户数、收入、估值、节省金额作为 AI adoption observation；这些只在来源背景中披露。
- 不把 Geographies 图表纳入首版 L1 snapshot；如未来需要，另开 change 处理地理样本与隐私语义。

## Decisions

### 1. 已落盘官方导出优先，浏览器仅作为补采路径

已落盘的官方 TSV 是默认入口；只有对应 scope 没有 fixture 时，才由受控浏览器点击可见 `Get the data` 并读取剪贴板。采集器不等待下载事件、不使用截图/OCR、不猜测隐藏 URL。

Ramp Developer API/MCP 需要额外企业授权，当前不进入首版运行链路。适配器不会读取 `RAMP_DATA_API_KEY`，不会启动 API 重试，也不会因为 API 不可用而延迟或阻塞网页/fixture slice。未来如有明确授权，另开独立 OpenSpec change。

### 2. 每个 chart 使用独立 source scope 和 artifact

注册以下 source scopes：

| scope | 统计内容 | 统计主体/分母 | 首版状态 |
|---|---|---|---|
| `adoption_overall` | Ramp Overall adoption | Ramp 相关企业中当月有正向 AI 付款的企业 / 相关企业 cohort | L1 supplemental |
| `adoption_overall_models` | Overall vendor adoption share | 同上，按 vendor breakdown；企业可同时支付多个 vendor | L1 supplemental |
| `adoption_sector` | NAICS 行业 adoption | 同上，按 Ramp 内部 NAICS 分组 | L1 supplemental |
| `spend_per_employee_overall` | Median/Top10/Top1 AI spend | 报告 cohort 的 AI 付款 / matched employee counts | 独立支出段落 |
| `model_market_share_overall` | provider/model API spend share | 使用 Token Spend Management 并连接 provider 的企业 API 成本 | 独立遥测段落 |
| `geographies` | 州等地理截面 | Ramp 地理 cohort | 发现但 out-of-scope |

Vendor adoption（如 Anthropic/OpenAI）不是互斥分类，同一企业可同时支付多个 vendor；禁止强制加总到 100%。Model market share 是 spend share，不是付费企业 adoption share，且 cohort 更窄。

### 3. Release identity 采用页面发布标签加 payload hash

网页没有稳定公开 release ID，因此建立：

```text
source_version = ramp_page:<page_url>:<chart_slug>:<latest_release_label>:<payload_sha256>
artifact_key   = ramp:<scope>:<latest_release_label>:<payload_sha256>
```

artifact metadata 至少保存：URL、chart slug、控件文本、导出方法、payload 字节数和 SHA-256、页面抓取时间、页面显示 latest release、API 文档版本、解析器版本、schema fingerprint、是否 API/网页对账、保存限制。未来若出现稳定 CSV URL，URL 可替换但 scope、payload hash、发布标签和 lineage 语义不变。

### 4. 规范化 schema

每条 observation 统一保存：

```text
source_id = ramp_ai_index
dataset_id = ramp_ai_adoption | ramp_ai_spend
scope = adoption_overall | adoption_overall_models | adoption_sector |
        spend_per_employee_overall | model_market_share_overall
period = YYYY-MM-01
period_basis = calendar_month
entity_id = RAMP_OVERALL | RAMP_VENDOR:<name> |
            NAICS:<group> | MODEL:<provider>:<model>:<spend_type> |
            QUANTILE:<q>
metric_id = ai.ramp.paid_business_adoption_share |
            ai.ramp.vendor_adoption_share |
            ai.ramp.ai_spend_per_employee |
            ai.ramp.api_spend_share
value, unit, raw, dimensions, denominator, methodology_regime,
artifact_id, known_at, fetched_at, quality_status, content_hash
```

原始字段映射：

| 页面列 | 平台字段 | 备注 |
|---|---|---|
| Adoption rate (%) | `paid_business_adoption_share` | 百分比；不是员工采用率 |
| Monthly change (pp) | `provider_monthly_change_pp` | 保留为原始发布列，同时可由水平值重算 |
| Yearly change (pp) | `provider_yearly_change_pp` | 若页面缺失则不补算 |
| Median/Top 10%/Top 1% | `ai_spend_per_employee` + quantile | USD/employee/month |
| API spend share (%) | `ai.ramp.api_spend_share` | Token Spend Management cohort |

### 5. 发现与网页采集流程

每周由 `data release-check --group ai_adoption` 调用 Ramp discovery：

1. 先检查 source artifact/fixture 目录中是否已经存在对应 scope 的官方 TSV。
2. 有 fixture 时直接校验 TSV header、日期、数值、payload hash 并流式解析；不启动浏览器。
3. 没有 fixture 时才打开注册页面，等待 `LATEST RELEASE` 与目标 scope 导航。
4. 对 scope 白名单逐一点击可见 chart/accordion，读取 `Get the data` 剪贴板文本。
5. 计算 payload hash，与已有 artifact 比较；未变化写 `no_change`，新标签/新 hash 进入候选 release。
6. 执行 slice 级质量门。通过的 slice 发布；失败的 slice 隔离，运行总状态为 `partial`。
7. Fixture 与页面导出均不可读时返回 `export_unreadable`；不使用截图、OCR 或 API 回退。

### 5.1 Ramp 的周期探测与增量发布

Ramp 的 source cadence 仍记录为 `monthly`；实际探测由独立的外部调度意图
`ramp_ai_index_p10d_probe` 驱动，每 10 天尝试一次，而不是把整个 `ai_adoption`
来源组改成相同频率。一次探测在一个受控采集会话中处理五个白名单 scope，报告生成继续只读
统一库，不重复打开网页。

无头调度由浏览器 runner 先把官方 `Get the data` TSV 写入受管入站目录
`var/data/ramp_exports/<scope>.tsv`（或 `ATS_RAMP_OFFICIAL_EXPORT_DIR` 指定的目录）。适配器优先
读取该入站快照；同一轮 discovery 缓存的 payload 直接交给 ingest，避免为入库再次打开浏览器。
入站文件是采集交接物，最终 artifact、observation 和 vintage 仍只在统一结构化库中生效。

探测先比较 source-native 的 `latest_reference_period`、方法/schema 指纹和每个 TSV 的
`payload_sha256`：

- 最新参考期间没有变化且 hash 没有变化：记录 `no_change`，不新增 observation；
- 发现更晚的参考期间：追加新 artifact/observation；
- 期间相同但 hash 变化：追加 revision vintage，保留旧值和 `as_of` 可见性，不覆盖；
- 方法、header 或统计口径变化：标记 `methodology_drift`，留在 shadow 并停止自动发布；
- 仅发现旧期间或缺失单个 scope：不删除旧数据，按 slice 返回 `partial`/`no_change`。

首次受控探测和质量门通过后，显式发布 `ramp_ai_index` source 为 `platform`。后续新月份可
按相同质量门进入 platform；同期间修订或方法漂移不得静默自动提升，仍需人工审阅。

### 6. 质量与方法 regime

- adoption/spend/API share 百分比必须在 `[0,100]`；支出必须非负。
- Median ≤ Top 10% ≤ Top 1%；违反时只阻止 spend slice。
- 同 scope、period、entity、metric 不得有未经解释的重复。
- 页面历史表与 clipboard TSV 的 header、行数和数值必须逐行对账。
- 月份按真实日期排序；缺失月份标记 `period_gap`，不前向填充。
- 不存在 API 与网页 payload 的首版冲突路径；未来授权 API 必须另开变更并定义独立 source conflict 规则。
- vendor adoption 允许重叠，不检查加总 100%；model API spend share 只有在官方明确总和口径时才检查近似 100%。
- 页面方法或样本叙述改变（例如 50,000→70,000、AI 识别规则改变）生成新的 `methodology_regime`，跨 regime 派生默认返回 `methodology_break`。
- “70,000+ businesses”“$1B annualized revenue”等公司自报仅作为文档背景，不能成为样本分母的精确计数；报告使用“Ramp 官方披露的相关企业 cohort”表述。

### 7. 查询与 DataProducts

提供稳定的领域入口（具体 Python 名称可由实现决定）：

```python
ramp_paid_adoption_snapshot(scope="adoption_overall", periods=["2026-08"])
ramp_paid_adoption_snapshot(scope="adoption_sector", periods=["2026-08"])
ramp_spend_per_employee_series(scope="spend_per_employee_overall", quantiles=["median", "top10", "top1"])
ramp_model_market_share_series(scope="model_market_share_overall", period="2026-08")
```

每个结果包括：latest/as-of period、source scope、statistical unit、denominator、technology scope、raw fields、quality/freshness、artifact/observation IDs、methodology/derivation versions、period gap 和 access status。默认 accepted/warning；quarantine 只经审计入口返回。

### 8. L1 Observer 与报告

现有三轴（BTOS enterprise breadth、RPS worker persistence、Anthropic task production）仍决定 L1 主 claim 状态。Ramp 作为 `supplemental_signals.ramp_paid_adoption` 返回：

- 总体 adoption 的最新值、月变化、历史序列；
- 行业截面；
- AI spend per employee 的 median/top10/top1；
- model API spend share 的 provider/model 截面。

报告顺序为：命题判断 → 三轴总览 → Ramp 付费企业采用补充 → 跨源口径说明 → BTOS/RPS/Anthropic 原有内容。Ramp 图表直接使用同一 rows hash 的 CSV/JSON，正文内嵌 PNG 并附 sidecar。Ramp 与 BTOS/RPS/Anthropic 的方向一致只写“方向性印证”，不改变三轴整体状态。

本变更将 Ramp 固定为 L1 的第四个追踪命题，而不是一个只供调试的附表：
“AI 是否从自报使用和试验，转向真实的企业付费采购，并在行业、企业规模和模型供应商之间扩散？”
该命题拥有独立的 `claim_id=ai_paid_business_adoption_diffusion`、方法卡、状态和文字结论，
但不进入三轴 `overall_status`。五个已确认 scope 必须各自拥有历史折线/截面图、CSV/JSON 和 sidecar；
企业规模虽然是命题的目标维度，但因首版白名单未包含 `business_size`，报告必须把它列为
`not_published` 缺口，不能用 Overall 或 NAICS 行业值替代。

## Risks / Trade-offs

- **[剪贴板依赖]** 浏览器权限或用户会话可能导致导出不可读 → 使用明确 `export_unreadable` 状态，保留页面 URL/截图仅作诊断，不作为事实；API 只作获授权回退。
- **[样本选择偏差]** Ramp 客户偏向高增长、技术导向企业，且未观察免费工具/个人账户 → 方法卡固定披露，禁止外推全美企业或员工采用率。
- **[口径漂移]** 网页与 API 文档样本数和方法文本可能不同 → 保存两套文档 fingerprint，跨 regime 停止趋势派生，需显式批准后恢复。
- **[公司/产品混淆]** Ramp 客户/收入等营销披露可能被误当作 adoption 分母 → 业务背景与观测 schema 分离，质量规则拒绝将公司 KPI 写入 adoption dataset。
- **[多 slice 不一致]** adoption、spend、model API 的更新时间和 cohort 不同 → 每个 scope 独立 freshness/partial 状态，Agent context 展示异步期间。
- **[API 限速]** 200 requests/10 秒/IP 和 60 秒超时 → 本地持久化、单并发、指数退避、months 批量查询；网页路径不依赖 API。
- **[网页 UI 改版]** 按钮文本/布局变化会破坏浏览器采集 → 使用可见 aria/name 选择器、DOM contract fixture 和人工验收；若控件消失即阻断该 slice。

## Migration Plan

1. 注册 source/datasets/metrics 和 `ai_adoption` group，不改变已有三来源 artifact。
2. 在隔离 SQLite/artifact/output 目录运行网页 discovery，保存当前 Overall、Overall + Models、Sector、Spend、Model share 的 payload fixture；Business size 与 Geographies 仅验证为发现项，不发布。
3. 实现 parser、质量门、vintage/as-of、DataProducts 和 rows hash 对账；网页与历史表至少抽样逐行一致。
4. 生成 Ramp supplement 的 Markdown、CSV/JSON、PNG/sidecar 和 Agent context；三轴主结论做 shadow 对账，确认未变化。
5. 通过 `validate-source → probe/ingest → quality → availability → bundle → evidence layer → lineage → manifest replay` 后，显式发布 `ramp_ai_index` source 为 `platform`；后续按 `P10D` 探测，只追加新期间或 revision vintage。
6. 若后续撤回 Ramp，停用该 source scope 即可；不得删除已保存 artifact/observation，三轴 Observer 继续正常运行。

## Open Questions

无会改变当前规格或架构的阻塞性问题。以下事项可在实施验收时确定：网页导出是否长期保持同一列名，以及是否出现稳定 CSV URL；API/MCP 授权不属于本首版。
