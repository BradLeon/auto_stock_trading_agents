# D01–D04 产业链证据抽查（每家 10 条）

抽样：2026-10-23，每家从“目录里能找到原文”的证据里取 10 条（有财报会的取 6 条来自最新财报会，其余取新闻/研报；固定随机种子，同一文档最多 3 条，类型尽量分散）。逐字 = 引文在原文里逐字出现（忽略空白和引号样式）。判定：✅ 正确；⚠️ 事实对但标注（类型/立场/方向/期间/单位）有偏差；❌ 引文不逐字或结论标错。原始抽样数据：`var/g0/d01_d04_sample.json`。

## TSM

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 逐字 | 判定 | 说明 |
|---|---|---|---|---|---|---|---|
| 1 | TSM:Q3 FY2026:earnings_transcript | revenue_growth_yoy | reported_actual/incumbent/up/FY2026Q3 / 41.0 % | “Revenue surged 41% year-over-year” | 否 | ❌ | 不逐字出处：该句是 fool.com 页面编辑写的导语（“Revenue surged 41%…”），不是管理层原话，却标成 TSM 自述的已实现事实；且目录里当前最新版本（investing.com）里没有这句 |
| 2 | TSM:Q3 FY2026:earnings_transcript | power_supply_constraint | reported_actual/incumbent/flat/2026-10-15 | “Definitely power supply situation is one of the consideration factor that we are looking into.” | 是 | ⚠️ | “电力供应是考虑因素之一”是态度表述，没有实际数字；标 reported_actual 偏强，标 guidance/无类型更合适 |
| 3 | TSM:Q3 FY2026:earnings_transcript | us capacity expansion / texas evaluation | guidance/incumbent/up/2026-10-15 | “We send the teams to Texas to do all the evaluation.” | 是 | ⚠️ | “派团队去德州做评估”是动作，方向标 up 没有依据（评估不等于扩产） |
| 4 | TSM:Q3 FY2026:earnings_transcript | agentic_ai_cpu_demand | reported_actual/incumbent/up/2026-10-15 | “we did see the demand from agentic AI CPUs continue to increase. Yes, that is a trend today.” | 是 | ✅ | 与原文一致，需求持续增加，类型/方向合理 |
| 5 | TSM:Q3 FY2026:earnings_transcript | wafer capacity growth | guidance/incumbent/up/2026-10-15 | “we put all our strengths to increase the capacity” | 是 | ✅ | 扩产意图，guidance/up 合理（偏笼统） |
| 6 | TSM:Q3 FY2026:earnings_transcript | advanced packaging reticle size roadmap | guidance/incumbent/up/2026-10-15 | “We are working on the 9x reticle size and moving into the 14x reticle size.” | 是 | ✅ | 9x→14x 光罩路线图，guidance/up 合理 |
| 7 | TRENDFORCE:news-ases-spil-breaks-ground-on-n | cowos_monthly_capacity | research/incumbent/up/end of 2026 / 140000.0 wafers/month | “Institutional investors estimate that TSMC's monthly CoWoS capacity could reach 140,000 wafers by the end of 2026 and 220,000 wafers by the end of 202” | 是 | ⚠️ | 原文是“机构投资人估计”，说话人是机构而非 TSM，立场标 incumbent 偏离定义；数值 14 万片/月正确，且同句按 2026/2027 拆成两条是对的 |
| 8 | TRENDFORCE:news-ases-spil-breaks-ground-on-n | cowos_monthly_capacity | research/incumbent/up/end of 2027 / 220000.0 wafers/month | “Institutional investors estimate that TSMC's monthly CoWoS capacity could reach 140,000 wafers by the end of 2026 and 220,000 wafers by the end of 202” | 是 | ⚠️ | 同上句的 2027 年一条，数值 22 万片/月正确；立场同样偏差 |
| 9 | TRENDFORCE:news-samsung-reportedly-eyes-high | high_na_euv_rd_status | counterparty/competitor/flat/- | “TSMC Chairman and CEO C.C. Wei said the company has already purchased High-NA EUV equipment and is actively conducting R&D” | 是 | ⚠️ | 这是 TSM 董事长的话（经 TrendForce 转述），却标成 competitor/counterparty；应是 incumbent 的媒体转述 |
| 10 | TRENDFORCE:news-samsung-reportedly-eyes-high | a13_a12_high_na_euv_not_required | counterparty/competitor/flat/2029 | “the company's upcoming A13 and A12 processes, both targeted for 2029, are not expected to require High-NA EUV” | 是 | ⚠️ | A13/A12 不需要 High-NA 是 TSM 自己的规划，立场标 competitor 偏差；事实本身正确 |

小计：✅ 3　⚠️ 6　❌ 1

## ASML

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 逐字 | 判定 | 说明 |
|---|---|---|---|---|---|---|---|
| 1 | ASML:Q3 FY2026:earnings_transcript | euv_capacity_growth | guidance/incumbent/up/2027 / 30.0 % | “Us mentioning 30% growth in 2027, more than 30% growth in 2028” | 是 | ✅ | 30% 增长（2027）数值与期间正确 |
| 2 | ASML:Q3 FY2026:earnings_transcript | high_na_photomask_pilot_line | guidance/incumbent/up/2031-2033 | “This collaborative effort aims to establish a 12-inch mask pilot line by 2031 and enter production in 2033” | 是 | ✅ | 12 英寸掩模试产线 2031、量产 2033，期间标 2031-2033 合理 |
| 3 | ASML:Q3 FY2026:earnings_transcript | low_na_euv_new_customers_dram | reported_actual/incumbent/up/2026-10-14 | “We continue to see, in fact, new customer on low-NA EUV, especially when it comes to DRAM.” | 是 | ✅ | 低 NA EUV 新增客户（DRAM）为已实现观察，合理 |
| 4 | ASML:Q3 FY2026:earnings_transcript | low_na_euv_output | guidance/incumbent/up/FY2026 / 65.0 systems | “For low-NA EUV, we anticipate output of roughly 65 systems for 2026, which is nearly 50% more than the 44 systems that we shipped last year” | 是 | ✅ | 约 65 台、较去年 44 台增近 50%，数值/单位/期间正确 |
| 5 | ASML:Q3 FY2026:earnings_transcript | capacity_expansion_2028 | guidance/incumbent/up/2028 / 30.0 % YoY (under investigation, may exceed) | “are now looking into expanding our 2028 capacity even above the 30% year-on-year growth target that was under investigation last quarter for both low-” | 是 | ⚠️ | 数值 30% 正确，但 unit 字段写成“% YoY (under investigation, may exceed)”，把限定语塞进单位，下游按数值解析会出问题 |
| 6 | ASML:Q3 FY2026:earnings_transcript | ramp_costs | reported_actual/incumbent/up/FY26Q4 | “we are seeing some ramp costs. We are ramping up for the increases that Christophe was just talking about.” | 是 | ✅ | 斜坡成本为已实现观察，FY26Q4 合理 |
| 7 | TSM:Q3 FY2026:earnings_transcript | 12-inch photomask development with asml | guidance/customer/up/2031 | “we are also working very closely with our ASML to establish a 12 in photomask by 2031” | 否 | ❌ | 引文不逐字：原文是“12-inch photomask”，模型写成“12 in photomask”；其余含义正确。这条来自 TSM 的财报会，被记在 ASML 名下（客户视角），做法合理 |

小计：✅ 5　⚠️ 1　❌ 1

## MU

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 逐字 | 判定 | 说明 |
|---|---|---|---|---|---|---|---|
| 1 | MU:Q4 FY2026:earnings_transcript | supply_demand_balance_visibility | guidance/incumbent/up/2026-09-30 | “we do not have a line of sight to when supply and demand will get in balance. Customers want assurance now even beyond 2030 timeframe.” | 是 | ✅ | 看不到供需平衡时点，原文一致；期间写 2026-09-30（财报期末）、方向 up 含义模糊，但可接受 |
| 2 | MU:Q4 FY2026:earnings_transcript | tax_rate_guidance | guidance/incumbent/flat/FY27Q1/FY2027 / 15.5 % | “We expect a fiscal Q1 and fiscal 2027 tax rate of around 15.5%.” | 是 | ✅ | 税率 15.5%，guidance/flat 合理 |
| 3 | MU:Q4 FY2026:earnings_transcript | full_year_revenue | reported_actual/incumbent/up/FY2026 / 133.2 USD billion | “For the full year, we achieved record revenue of $133.2 billion, up 256% year-over-year.” | 是 | ✅ | 全年收入 133.2B、同比 +256%，数值正确 |
| 4 | MU:Q4 FY2026:earnings_transcript | gross_margin_guidance | guidance/incumbent/flat/FY27Q1 / 86.25 % | “gross margin to be approximately 86.25%” | 是 | ✅ | 毛利率约 86.25%，guidance 合理 |
| 5 | MU:Q4 FY2026:earnings_transcript | nand_asp | reported_actual/incumbent/up/FY2026Q4 / 30.0 percent | “Prices increased approximately 30%, driven by tight NAND industry conditions.” | 是 | ✅ | NAND 价格约 +30%，合理 |
| 6 | MU:Q4 FY2026:earnings_transcript | sca_count_and_revenue_coverage | reported_actual/incumbent/up/through 2030 / 26.0 agreements | “To date, we have signed 26 SCAs, which we currently estimate to be over 35% of our revenue through 2030.” | 是 | ✅ | 26 份长期协议、占 2030 年前收入 35% 以上，只记了 26，没记 35% 覆盖率 |
| 7 | DOWJONES:DJ-N-1f202c99:article | nand_revenue_rank | research/competitor/flat/Q2 | “YMTC ranked third in shipments this quarter but closed fifth in terms of revenue, behind Micron and Kioxia” | 是 | ✅ | YMTC 排名句，归到 MU 名下为竞争对手视角，合理 |
| 8 | DOWJONES:DJ-N-1f35c1f9:article | stock_price_move | market/incumbent/up/- | “Micron shares climbed more than 2% Tuesday morning, recovering a part of Monday's near-6% tumble to close back below their 50-day line.” | 是 | ✅ | 股价异动，market 类型合理 |
| 9 | DOWJONES:DJ-N-1f213312:article | stock_price | market/incumbent/flat/2026-08-13 / 949.83 USD | “Micron shares closed up 4.2% at $949.83” | 是 | ❌ | 方向标错：原文“收盘上涨 4.2%”，却标 flat（应为 up）；数值 949.83 美元正确 |
| 10 | DOWJONES:DJ-N-1f1ff945:article | stock_price_ytd_change | market/incumbent/up/- / 190.0 % | “the stock remains up some 190% so far this year” | 是 | ✅ | 年内涨约 190%，数值正确 |

小计：✅ 9　⚠️ 0　❌ 1

## SKHY

| # | 来源文档 | 指标 | 类型/立场/方向/期间 | 引文 | 逐字 | 判定 | 说明 |
|---|---|---|---|---|---|---|---|
| 1 | TRENDFORCE:news-micron-flags-hbm4e-as-start- | hbm4_supply_to_nvidia | reported_actual/incumbent/flat/- | “SK hynix, Samsung and Micron have all begun supplying HBM4 to NVIDIA's Vera Rubin.” | 是 | ✅ | 三家都开始向英伟达供应 HBM4，合理 |
| 2 | TRENDFORCE:news-sk-hynix-linked-bcpe-pangea- | kioxia_investment_amount | reported_actual/incumbent/flat/2018 / 4.0 trillion won | “SK hynix invested roughly 4 trillion won in Kioxia in 2018 through two SPCs” | 是 | ✅ | 2018 年投资约 4 万亿韩元，数值正确 |
| 3 | TRENDFORCE:news-micron-flags-hbm4e-as-start- | one_team_model | media/incumbent/flat/- | “SK hynix's approach brings together itself, NVIDIA, and TSMC, with NVIDIA defining the HBM requirements for its GPUs, SK hynix handling memory stackin” | 是 | ✅ | “一个团队”合作模式，media 类型合理 |
| 4 | TRENDFORCE:news-samsung-reportedly-eyes-high | high_na_euv_system_introduced | reported_actual/incumbent/flat/2H2025 | “the company introduced its first mass-production-grade High-NA EUV system in the second half of 2025 and began R&D with the equipment this year” | 是 | ✅ | 2025 下半年引入高 NA EUV，期间合理 |
| 5 | TRENDFORCE:news-ai-agents-enter-chipmaking-s | ai_gpu_count | media/incumbent/up/- / 2000.0 GPUs | “the company will install 250 AI servers equipped with 2,000 NVIDIA Blackwell GPUs at its Cheongju campus” | 是 | ✅ | 部署 2,000 块 Blackwell GPU，数值正确 |
| 6 | TRENDFORCE:news-samsung-sk-hynixs-hbm4-push- | dram_bit_growth | research/incumbent/up/2026 / 25.0 % | “SK hynix's DRAM bit growth is expected to reach the mid-20% range in 2026, above its previous 20% forecast, driven in part by rising shipments of 1c-b” | 是 | ✅ | DRAM 位增长中 20% 区间，数值 25 是“中 20%”的近似，已按研究类型处理 |
| 7 | TRENDFORCE:news-nvidia-vera-rubin-spillover- | qlc_ssd_shipping | reported_actual/incumbent/up/2025-04 | “started shipping 321-layer QLC-based SSDs in April” | 是 | ⚠️ | 原文只说“4 月”，期间被写成 2025-04，年份没有依据（该文是 2026 年） |
| 8 | TRENDFORCE:news-micron-can-meet-less-than-ha | us_investment_scope | counterparty/competitor/flat/- | “SK Hynix's Indiana project is limited to back-end manufacturing, assembly, and packaging” | 是 | ✅ | 印第安纳项目仅后道，对手方视角合理 |
| 9 | DOWJONES:DJ-RTE-1f1f060c:article | adr_change | market/incumbent/up/- / 9.0 percent | “SK Hynix SKHY, another major beneficiary of memory-chip demand, saw its American depositary receipts rise 9%” | 是 | ✅ | ADR 涨 9%，market 合理 |
| 10 | TRENDFORCE:news-samsung-sk-hynixs-hbm4-push- | hbm3e_selling_price | reported_actual/incumbent/flat/- | “The company, cited by the report, pushed back, saying it is not selling HBM3e at particularly low prices, while declining to disclose specific contrac” | 是 | ✅ | 公司否认低价销售 HBM3e，合理 |

小计：✅ 9　⚠️ 1　❌ 0

## 抽样之外的系统性发现

- **溯源缺口：** 这四家共 709 条证据里有 424 条（TSM 80/130、ASML 240/308、MU 57/178、SKHY 47/93）的来源文档编号是旧体系的（如 `TSM:20260716`、`ASML:release:2026-07-13`），在现行文档目录里查不到，因此无法回查引文原文。本次抽样只覆盖了能回查的 285 条，这 424 条没有验证。
- **同一文档多个版本、来源不同：** TSM 的 Q3 财报会有 3 个版本（investing.com 两版、fool.com 一版），内容不完全相同（例如 fool.com 版有编辑导语，investing.com 版没有）。证据行没有记录它出自哪个版本。
- **“逐字摘录”约束并非总被遵守：** 40 条里有 2 条引文不逐字（TSM 的编辑导语、ASML 条的 `12 in`/`12-inch`），另有 1 条方向标错（MU 股价上涨标成 flat），都已列在上表。
