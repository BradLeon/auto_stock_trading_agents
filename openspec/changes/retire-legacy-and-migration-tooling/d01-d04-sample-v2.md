# D01–D04 产业链证据抽查（第二轮：换用 deepseek-v4.1-flash-1017 + 新提示词 + 逐字校验）

做法：2026-10-23 对第一轮抽查涉及的文档用新配置重抽一次（**不写库**，原始结果见 `var/g0/ab_all.json`，抽样见 `var/g0/d01_d04_sample_v2.json`），再按第一轮同样的规则抽样判读（有财报会的取 6 条来自财报会、其余来自文章，每个文档至多 3 条）。判定符号同第一轮：✅ 正确；⚠️ 事实对但标注有偏差；❌ 引文不逐字或结论标错。
**两点局限：** ① 这次重抽没有传入期间（period 为空），所以很多行的期间是空的，期间不能与第一轮比；② SKHY 这次只重抽了 7 个文档，其中 2 个返回 0 条，样本池只有 6 条，且 ASML 只抽到 7 条。

## TSM

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 判定 | 说明 |
|---|---|---|---|---|---|---|
| 1 | TSM:Q3 FY2026:earnings_transcript | 与 globalfoundries 的硅中介层（interposer）合作 | reported_actual/incumbent/up/- | “we would like to work with our so-called partner with GlobalFoundries so that both TSMC and GlobalFoundries can support TSMC’s customer” | ⚠️ | “想与 GlobalFoundries 合作”是意愿，标成 reported_actual/up；应为 guidance |
| 2 | TSM:Q3 FY2026:earnings_transcript | n2_annual_capacity_growth | guidance/incumbent/up/2027 | “We expect our N2 family technologies annual capacity to more than double in 2027, supported by strong demand from both smartphone and HPC AI” | ✅ |  |
| 3 | TSM:Q3 FY2026:earnings_transcript | 9x / 14x reticle size 封装开发进度 | guidance/incumbent/up/- | “We are working on the 9X reticle size and moving into the 14X reticle size.” | ✅ |  |
| 4 | TSM:Q3 FY2026:earnings_transcript | high_na_euv_adoption | guidance/incumbent/flat/2030 | “So far, I think our plan is to use it in 2030.” | ✅ |  |
| 5 | TSM:Q3 FY2026:earnings_transcript | capex_usd | reported_actual/incumbent/flat/FY26Q3 / 13.8 USD billion | “In U.S. dollar terms, our third-quarter capital expenditures totaled $13.8 billion.” | ✅ |  |
| 6 | TSM:Q3 FY2026:earnings_transcript | 与 asml 合作建立 12-inch photomask 时点 | guidance/incumbent/flat/- / 2031.0 年 | “we are also working very closely with our ASML to establish a 12-inch photomask by 2031” | ⚠️ | 把年份 2031 当数值存（value=2031、unit=年）；引文已逐字（12-inch） |
| 7 | TRENDFORCE:news-samsung-reportedly-eyes- | high-na euv 设备采购与研发状态 | media/incumbent/flat/- | “TSMC Chairman and CEO C.C. Wei said the company has already purchased High-NA EUV equipment and is actively conducting R&D.” | ✅ | 董事长的话、经 TrendForce 引用：立场 incumbent 正确（旧版标成 competitor） |
| 8 | TRENDFORCE:news-ases-spil-breaks-ground- | cow 工序外包给 osat 的范围扩大 | media/incumbent/up/- | “sources say the company has decided to expand outsourcing of CoW (Chip-on-Wafer), a key step in CoWoS packaging for AI chips, to OSAT provid” | ✅ | “sources say”传闻，类型 media 合理 |
| 9 | TRENDFORCE:news-ases-spil-breaks-ground- | cowos 月产能预估（2026 年底） | research/incumbent/up/- / 140000.0 wafers/month | “Institutional investors estimate that TSMC's monthly CoWoS capacity could reach 140,000 wafers by the end of 2026 and 220,000 wafers by the ” | ✅ | 机构估计，research 合理，数值 14 万片/月正确 |
| 10 | TRENDFORCE:news-ases-spil-breaks-ground- | cowos 月产能预估（2027 年底） | research/incumbent/up/- / 220000.0 wafers/month | “Institutional investors estimate that TSMC's monthly CoWoS capacity could reach 140,000 wafers by the end of 2026 and 220,000 wafers by the ” | ✅ | 同句 2027 年一条，22 万片/月正确 |

小计：✅ 8　⚠️ 2　❌ 0

## ASML

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 判定 | 说明 |
|---|---|---|---|---|---|---|
| 1 | ASML:Q3 FY2026:earnings_transcript | logic 业务同比增幅 | reported_actual/incumbent/flat/- | “logic went up around 10%, but the advanced logic piece, included in there is far better.” | ❌ | 原文“logic 增长约 10%”，方向标成 flat（应为 up），数值 10% 没提取 |
| 2 | ASML:Q3 FY2026:earnings_transcript | zeiss 供应瓶颈判断 | guidance/supplier/flat/- | “We do not see ZEISS today as a bottleneck for the capacity expansion we are discussing, even beyond the 30% we mentioned for 2028.” | ⚠️ | ASML 自己在谈供应商蔡司，立场标 supplier（应按说话人记 incumbent） |
| 3 | ASML:Q3 FY2026:earnings_transcript | 价值定价（关键产品涨价）生效节奏 | guidance/incumbent/up/- | “you will start to see the effect on that in the course of 2027, but the full effect you will start to see from 2028 and onwards” | ✅ |  |
| 4 | ASML:Q3 FY2026:earnings_transcript | immersion 工具产能提升至 330 wafers per hour | guidance/incumbent/flat/- | “We are going to bring the different immersion product to 330 wafers per hour.” | ⚠️ | “把浸没式产品提到 330 片/小时”是能力提升，方向标 flat 偏弱 |
| 5 | ASML:Q3 FY2026:earnings_transcript | 中国本土光刻竞争进展 | guidance/incumbent/flat/- | “I think the fact this year are not different from the fact a year ago or even five years ago. So I think that the progress is not very stron” | ⚠️ | “进展不强”是对现状的判断，标 guidance 不妥，应为 reported_actual 类的观察 |
| 6 | ASML:Q3 FY2026:earnings_transcript | advanced logic 业务同比增幅 | reported_actual/incumbent/flat/- | “So that grew over 25%, is expected to grow over 25% in this year.” | ❌ | 原文“增长超过 25%，预计今年增长超过 25%”，方向标成 flat（应为 up），且已实现与预期混在一条 |
| 7 | TRENDFORCE:news-samsung-reportedly-eyes- | high-na euv 首次用于量产逻辑器件 | media/supplier/up/- | “ASML said this marks the semiconductor industry's first use of High-NA EUV in mass-produced logic devices.” | ⚠️ | ASML 自己说的话（经 TrendForce 引用），立场标 supplier，应为 incumbent |

小计：✅ 1　⚠️ 4　❌ 2

## MU

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 判定 | 说明 |
|---|---|---|---|---|---|---|
| 1 | MU:Q4 FY2026:earnings_transcript | hbm 需求增速 vs 行业需求 | guidance/incumbent/up/- | “we actually see the overall HBM demand outpacing the industry demand in 2027 as well as in 2028.” | ✅ |  |
| 2 | MU:Q4 FY2026:earnings_transcript | new_york_fab_initial_wafer_output | guidance/incumbent/flat/CY2030 | “we celebrated a concrete pour milestone for our first New York fab with initial wafer output expected in calendar 2030” | ✅ |  |
| 3 | MU:Q4 FY2026:earnings_transcript | nand 相关 capex | guidance/incumbent/up/- | “Our investments related to NAND as part of our overall CapEx certainly are increasing as well.” | ✅ |  |
| 4 | MU:Q4 FY2026:earnings_transcript | data_center_ssd_revenue | reported_actual/incumbent/up/FY2026Q4 / 10.0 USD billion | “Data center SSD revenue in fiscal Q4 was nearly $10 billion, more than 10 times the year ago quarter, and was over two-thirds of total compa” | ✅ |  |
| 5 | MU:Q4 FY2026:earnings_transcript | 存储供需紧张程度（2026-2028） | guidance/incumbent/up/- | “Again, we see 2027 and 2028 tighter than 2026.” | ✅ |  |
| 6 | MU:Q4 FY2026:earnings_transcript | dio_outlook | guidance/incumbent/down/- | “Our inventory levels and supply remain extremely tight, and we expect DIO to decline in the coming quarters.” | ✅ |  |
| 7 | DOWJONES:DJ-N-1f20c725:article | 年初至今股价涨幅 | market/incumbent/up/so far this year / 200.0 % | “The stock remains up more than 200% so far this year” | ✅ |  |
| 8 | DOWJONES:DJ-N-1f1ed0d1:article | 股价单日涨幅 | market/incumbent/up/- / 4.92 % | “Micron stock closed 4.92% higher Wednesday” | ✅ |  |
| 9 | DOWJONES:DJ-N-1f1ed0d1:article | 股价近一月跌幅 | market/incumbent/down/- / -10.0 % | “shed almost 10% in a month” | ✅ |  |
| 10 | DOWJONES:DJ-N-1f20c725:article | nand 占收入比重 | media/incumbent/flat/- / 25.0 % | “NAND is a critical sector for Micron, making up around a quarter of the company's revenue.” | ✅ |  |

小计：✅ 10　⚠️ 0　❌ 0

## SKHY

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 判定 | 说明 |
|---|---|---|---|---|---|---|
| 1 | DOWJONES:DJ-N-1f1dc18d:article | adr 涨幅 | market/incumbent/up/- / 3.4 % | “the company's American depositary receipts (ADRs), essentially US-listed stock, were up 3.4%” | ✅ |  |
| 2 | DOWJONES:DJ-N-1f1ed0d1:article | 韩股单日涨幅 | market/competitor/up/- / 5.5 % | “while the Korean listing gained 5.5%” | ⚠️ | 立场标 competitor，说的是 SK 海力士自己的股价 |
| 3 | DOWJONES:DJ-N-1f1dc18d:article | 韩股涨幅 | market/incumbent/up/- / 5.5 % | “with the Korean listing gaining 5.5%” | ✅ |  |
| 4 | DOWJONES:DJ-N-1f20c725:article | nand 出货份额 | research/competitor/flat/second quarter / 22.0 % | “Samsung maintained its lead with a 25% shipment share, followed by SK Hynix at 22%.” | ⚠️ | 引的是 SK 海力士的份额 22%，立场标 competitor，应为 incumbent |
| 5 | TRENDFORCE:news-samsung-reportedly-eyes- | high-na euv 首台量产级设备导入时点 | media/competitor/up/- | “the company introduced its first mass-production-grade High-NA EUV system in the second half of 2025 and began R&D with the equipment this y” | ⚠️ | “the company”指代 SK 海力士，立场标 competitor，与上一轮的 incumbent 不一致 |
| 6 | DOWJONES:DJ-N-1f1ed0d1:article | adr 单日涨幅 | market/competitor/up/- / 9.0 % | “the company's American depositary receipts (ADRs), essentially U.S.-listed stock, rose 9%” | ⚠️ | SK 海力士 ADR 涨 9%，立场标 competitor |

小计：✅ 2　⚠️ 4　❌ 0

## 与第一轮对比

| 对象 | 第一轮（旧模型） | 第二轮（新模型） |
|---|---|---|
| TSM | ✅3 ⚠️6 ❌1 | ✅8 ⚠️2 ❌0 |
| ASML | ✅5 ⚠️1 ❌1（7 条） | ✅1 ⚠️4 ❌2（7 条） |
| MU | ✅9 ⚠️0 ❌1 | ✅10 ⚠️0 ❌0 |
| SKHY | ✅9 ⚠️1 ❌0 | ✅2 ⚠️4 ❌0（样本池仅 6 条） |

- **改善：** 不逐字引文 2→0（逐字校验已强制，TSM 一份文档里还因此丢了 1 行）；TSM 的立场错位基本消失（董事长的话现在是 incumbent）；`unit` 里不再塞限定语；MU 的股价方向对了。
- **没改善甚至变差：** ① 方向判断仍不稳（ASML 两条“增长”标成 flat）；② SKHY 的 competitor 立场系统性出现（4/6），是新的偏差；③ 年份当数值存（2031）。
- **召回不稳定：** 14 个文档合计旧 224 条、新 242 条，但逐文档差异很大：ASML 财报会 67→35，TSM 财报会 28→62，道琼斯文章有 2 篇从 5、7 条变成 0 条。只跑了一次，不能判断这是模型随机性还是新提示词/校验造成的。

> 样本小（10 条/家）且两轮抽到的行不同，结论只作方向参考，不是统计意义上的优劣。