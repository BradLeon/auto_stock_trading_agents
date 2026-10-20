# Information 来源存量标题与内容模式审阅

审阅日期：2026-10-12；Data Products 可见时点：`2026-10-12T06:22:09.699660+00:00`。

后续状态：本报告发现已纳入 [开发与测试计划](INFORMATION_SOURCE_SELECTION_DEVELOPMENT_TEST_PLAN.md) 的字段处理、依赖和验收修订。以下数据仍为首次只读审阅结果，未新增采集或修改源数据。

## 方法与范围

首次审阅只读生产数据平台 var/data.sqlite，经 admitted_documents 读取全部准入、正文可读的当前可见版本；总候选低于 API 上限。当时没有重新采集、调用模型、改元数据或修改开发计划；后续仅按用户要求完善规划文档。不是直接对发行商网站／邮箱的全量调查，也不覆盖隔离、未采集或正文不可读材料。

该产品没有独立摘要字段；下列中文内容概览根据存量标题、可见副标题及正文开头整理，不冒充已保存的模型摘要，不独立核验报道事实，也不推断付费隐藏部分。展示时去除邮件不可见 padding 字符，原正文与哈希未修改。

## 统计与解释

| 项目 | SemiAnalysis | TrendForce |
|---|---:|---:|
| 准入且正文可读文档 | 22 | 49 |
| 标题字段有值 | 18 | 49 |
| 标题／发布时间字段均缺失 | 4 | 0 |
| 库内完整性标签 | 18 partial、4 full | 49 full |
| 标题命中八个采集实体的注册名称／别名 | 1 | 19 |
| 标题出现八个大写 ticker 字面值 | 0 | 0 |
| 正文命中八实体名称／别名（原规则，含误匹配） | 19 | 35 |
| 标题未命中但正文命中（含缺失标题和误匹配） | 18 | 16 |

八实体仅指当前 IBKR 采集集合 NVDA/AMD/AVGO/MRVL/TSM/MU/ORCL/MSFT，不是所有公司。Intel、Anthropic、Huawei、Samsung 等名称也出现在材料中，不能因不在集合就说标题没有公司。原规则可能误命中人名：Poo Mu-ming 中的 Mu 被识别为 MU，因此正文命中数只是候选识别统计，不是股票相关性结论。

TrendForce 标题来自 adapter 对 URL slug 的还原，不是网页原始标题：缺标点、大小写和数值格式。来源类别为 45 条 News、3 条 Insights、1 条其他／breaking。SemiAnalysis 常有正式标题加副标题，副标题更直接列出技术关键词、公司／产品和文章范围。

对四篇库内 full 的 SemiAnalysis 文档，以现有 classify_completeness 只读复算均为 partial，原因是正文存在订阅解锁提示。此处报告标签不一致，不据此宣称已验证获得完整文章；18 篇 partial 也不能仅凭字符数判定实际可见信息价值。四篇标题缺失文档的正文开头包括 NVIDIA／Microsoft 等公司名，不能把缺失字段计为作者故意不写公司。

## 对原验收计划的审阅

1. MRVL XPU Attach 应是实际缺陷回归样本，不是唯一采集验收标的。预算／关联的离线用例应覆盖全部配置采集集合；实际内容清单至少覆盖本轮 MRVL/MSFT/NVDA/TSM，允许每个标的有明确的缺口和无重要消息结果，不要求全都出现同量新闻。
2. T5 的“无 ticker 标题”是应覆盖的情景，不能写成 SemiAnalysis 的普遍属性。正确区分股票代码、公司／别名、产品／技术主题，以及标题字段缺失。
3. T5/T6 应成为两个来源都适用的情景矩阵：标题有实体、仅副标题／导语有实体、仅正文有实体、只有行业主题、完全不相关、元数据缺失。来源特征决定候选预算和解析，不决定只有哪一家可出现哪种情景。
4. 新增反例：URL slug 数值失真、短 ticker／人名歧义、邮件 padding、标题／时间缺失、已有正文的完整性元数据不一致。没有新 Agent／动态图谱需求。
5. 仅做标题规则会漏掉产品／技术主题；直接全文匹配也会误命中。先正确取得和清洗已有标题／副标题／导语，再在预算内使用正文证据和现有 Information 抽取判断。

## SemiAnalysis：全部存量清单

以下四条缺失字段的显示标题只从正文开头定位，不能当作数据平台已经具有该标题元数据；发布时间仍保持缺失。

| 日期 | 标题（或正文开头） | 标签 | 中文内容概览 |
|---|---|---|---|
| 2026-10-08 | Beijing Will Not Pace the Frontier: China’s Speed-First AI Safety Regime | partial | 以中国 AI 安全政策与前沿模型开发速度为主题，讨论政策立场和治理路径。 |
| 2026-10-05 | Anthropic Subscriptions Offer 5x+ More Value Than OpenAI | partial | 比较多家 AI 服务商的订阅套餐和使用限制，强调价值／性能与配额差异。 |
| 2026-09-28 | How GLM5.3 Sparse Attention Affects HBM Memory Usage | partial | 讨论 GLM5.3 稀疏注意力、KV cache 和卸载技术对 HBM 使用的影响。 |
| 2026-09-26 | Intel Panther Lake Teardown | partial | 以 Intel Panther Lake／18A 芯片拆解为主，讨论工艺和架构；涉及 AMD、TSMC、Microsoft。 |
| 2026-09-25 | The Chinese AI Infrastructure Boom: Introducing the SemiAnalysis China Datacenter Model | partial | 介绍中国数据中心研究模型，围绕设施、运营商、租赁、建设速度与地理布局。 |
| 2026-09-23 | ClusterMAX 3.0: The Industry Standard GPU Cloud Rating System Returns | partial | 介绍 GPU 云服务商评级，主题包含可靠性、性能、支持、价格和安全；可见文本提及多家公司。 |
| 2026-09-21 | Computation and Data Movement for Inference | partial | 将 MoE 模型映射到推理硬件，讨论数据流、通信及高效服务。 |
| 2026-09-18 | Engrams Embedding Entendre: Codesign for Efficient DRAM/SSD Offloading | partial | 讨论模型架构与 DRAM／SSD 卸载协同，及其对存储需求的可能影响。 |
| 2026-09-15 | Everyone Says Datacenter Moratoriums Are Killing the US Buildout. We disagree | partial | 讨论数据中心暂停建设政策的实际影响，区分政策覆盖容量与实际推迟容量。 |
| 2026-09-14 | Vera Rubin NVL72 Agentic Inference: 67x better Performance per Dollar | partial | 围绕 Vera Rubin NVL72 的推理性能／成本展开分析；标题使用产品名，不写 Nvidia 公司名。 |
| 2026-09-14 | A Brain Too Big to Carry — On-Device vs Datacenter Inference | partial | 比较机器人端侧和数据中心推理，涉及 Jetson Thor、B300、硅效率与网络约束。 |
| 2026-09-13 | Long Live the Short King: Why 4-hi HBM Wins | partial | 讨论 4-hi HBM 设计、带宽、芯片数量和推理成本／DRAM 使用效率。 |
| 2026-09-11 | Nvidia’s Backstop Universe – Heads I Win, Tails Who Loses? | partial | 讨论 Nvidia 融资支持、AI 基建资本开支与资产负债表约束；标题直接写公司名。 |
| 2026-09-10 | What is So Hard About Behind-The-Meter Power For Datacenters? Part 1 | partial | 讨论数据中心表后供电的工程和经济难点，具有行业背景性质。 |
| 2026-09-09 | Where Does a Robot Think – On-Device vs Datacenter Inference | partial | 讨论机器人推理部署位置及端侧／数据中心成本；主题与端侧产品相关。 |
| 2026-09-07 | TPU Inference Externalization Full Steam Ahead - InferenceX | partial | 讨论 TPU 推理外部化、性能／价格和软件生态竞争，不以公司 ticker 为标题。 |
| 2026-09-01 | Korea’s Trillion-Dollar Sovereign AI Investment | partial | 讨论韩国主权 AI 投资、模型选择与 Nvidia、Hynix、Samsung 的角色。 |
| 2026-08-30 | Most Neoclouds Suck At Security | partial | 讨论新型 GPU 云服务商的安全风险，涉及容器隔离、内核、网络与多租户系统。 |
| 缺失 | [字段缺失；正文标题] $12B of US ratepayers’ money wasted on a modeling mistake and PJM wants to do it again | full | 正文开头讨论美国 PJM 电网建模失误、费用及电网设计；标题和发布日期元数据缺失。 |
| 缺失 | [字段缺失；正文标题] Ultra-High Interactivity on NVIDIA GPUs? - TileRT InferenceX | full | 正文开头讨论 TileRT 在 NVIDIA GPU 上的推理表现，并比较其他推理平台；标题和发布日期元数据缺失。 |
| 缺失 | [字段缺失；正文标题] SpaceX 10GW in 2027 – Why It’s Real, Will Drive $500B ARR for SpaceX, and Why Microsoft Will Be the Largest Offtaker | full | 正文开头讨论 SpaceX 数据中心容量、收入及 Microsoft 采购方角色的作者预测；不是已实现事实；标题和发布日期元数据缺失。 |
| 缺失 | [字段缺失；正文标题] Gemini is Cooked but GCP is Cooking | full | 正文开头讨论 Gemini／DeepMind 与 Google Cloud 的关系及云业务表现；标题和发布日期元数据缺失。 |

## TrendForce：全部存量清单

标题按平台存储的 slug 还原值原样展示；内容概览尽量避免从被拆散的标题数字还原金额／百分比。库内标签均为 full，本文不将该标签等同于全源全文覆盖。

| 日期 | 平台标题 | 中文内容概览 |
|---|---|---|
| 2026-10-12 | [News] ai interposers grow larger raising hbm4e cooling challenges how samsung sk hynix are responding | 讨论 AI 封装扩大后的 HBM4E 散热挑战及 Samsung、SK hynix 的应对。 |
| 2026-10-09 | [News] samsung reportedly plans exynos auto exit prioritizes mobile aps image sensors for 2027 system lsi turnaround | 转述 Samsung 调整汽车 AP 业务、转向移动 AP 与图像传感器的报道。 |
| 2026-10-09 | [News] nvidias nvhbm reshapes memory makers base die strategies as micron samsung turn to external foundries | 讨论 Nvidia NVHBM 与 HBM base die 外包策略，涉及 Micron、Samsung 和 TSMC。 |
| 2026-10-08 | [News] taiwan osat sigurd says customers lock in 1 year plus deals as ai chip testing capacity tightens | 报道 AI 芯片测试需求、Sigurd 客户长约及测试产能紧张。 |
| 2026-10-08 | [News] microsoft expands local ai push with nvidia rtx spark surface ultra priced up to 5899 as memory costs rise | 转述 Microsoft 本地 AI 产品与 Nvidia RTX Spark 平台，讨论硬件价格及内存成本。 |
| 2026-10-08 | [News] samsung sees 3q26 operating profit soaring 783 to record krw 107 4 trillion on hbm4 memory price gains | 报道 Samsung 初步季度业绩与 HBM／内存价格变化。 |
| 2026-10-08 | [News] tsmc q3 revenue beats forecasts at record nt1 49 trillion net profit seen surging 64 | 讨论 TSMC 月度和季度营收、市场预期与财报前盈利预测；实际数据与预测需区分。 |
| 2026-10-08 | [News] ai chip spending fuels debt financing wave broadcom reportedly eyes tens of billions to fund openai | 转述 AI 芯片采购融资及 Broadcom／OpenAI 等公司的融资框架。 |
| 2026-10-08 | [News] micron taiwans taoyuan union secures right to strike with 88 3 support taichung mediation set for oct 22 | 报道 Micron 台湾工会罢工投票及后续调解。 |
| 2026-10-07 | [News] intel ceo lip bu tan says it remains in musks terafab as tsmc speculation grows | 讨论 Intel 与 Terafab 项目，以及可能的 TSMC 合作。 |
| 2026-10-07 | [News] huawei says ascend market share has surpassed nvidia in china backs npo over cpo for optical interconnects | 转述 Huawei Ascend 供应和市场份额观点，以及 NPO 与 CPO 光互连选择。 |
| 2026-10-07 | [News] microns expansion push lifts korean chip suppliers with hanmis hbm tc bonder reportedly in focus | 讨论 Micron 扩产对韩国设备供应商、HBM 键合设备需求的影响。 |
| 2026-10-07 | [Insights] memory spot price update dram spot largely sideways as holidays month end effects weigh | 内存现货市场综述，讨论假期、月末因素对 DRAM／NAND 询价和价格的影响。 |
| 2026-10-07 | [News] samsung 3q preliminary results preview market sees historic krw 100t profit memory margin could top 80 | Samsung 财报前预览，讨论券商盈利预测及内存业务利润率。 |
| 2026-10-06 | [News] amds lisa su visits tsmc ai supply chain partners as company locks in capacity 3 5 years ahead | 报道 AMD 管理层访问 TSMC／台湾供应链及长期产能安排。 |
| 2026-10-06 | [News] nanya dram asp seen rising 10 in 4q26 winbond also sees further price gains as memory supply stays tight | 讨论 Nanya、Winbond 的价格预期及内存供应紧张。 |
| 2026-10-05 | [News] tsmc earnings preview terafab talks price hikes and a14 intel race lead five key themes | TSMC 财报前主题综述，包括潜在合作、涨价及先进制程竞争；含预期内容。 |
| 2026-10-05 | [News] china based cs microelectronics develops 90w addressable vcsel chip | 报道 CS Microelectronics 的高功率可寻址 VCSEL 产品。 |
| 2026-10-05 | [News] ic designers reportedly eye 5 to double digit price hikes in late 2026 early 2027 as foundry costs rise | 转述晶圆代工和后端成本上升可能推动 IC 设计公司涨价。 |
| 2026-10-05 | [News] china reportedly spent 13b stockpiling asml duv tools in 2024 25 as advanced chip push continues | 转述中国采购／储备 ASML DUV 设备的报道及其先进芯片背景。 |
| 2026-10-05 | [News] ai hbm drive memory testing demand how chinas suppliers are challenging an 80 import market | 讨论 AI／HBM 测试设备需求与中国供应商的市场机会。 |
| 2026-10-02 | [News] amoled display driver leader viewtrix expands into optical communications | 报道 Viewtrix 从显示驱动拓展到光通信及相关子公司。 |
| 2026-10-02 | [News] tesla halves ai5 ai chips lpddr5 to 72gb cuts ai6 lpddr6 by one third as memory supply tightens | 讨论 Tesla AI 芯片内存容量调整及 Optimus 供应／成本约束；正文提及 TSMC、Micron。 |
| 2026-10-02 | [News] toshiba to double ai data center hdd capacity by fy2027 with 380m expansion as ai storage demand surges | 报道 Toshiba 因 AI 存储需求扩大 HDD 产能。 |
| 2026-10-02 | [News] micron taiwan investment tops nt1 6t as four site expansion accelerates nanya adds pingtung project | 报道 Micron 在台湾的 DRAM／HBM 扩产，以及 Nanya 投资。 |
| 2026-10-01 | [News] micron fy2026 revenue surges 256 to 133 2b locks in 35 of revenue through 2030 | 转述 Micron 财年／季度收入、利润表现及长期需求安排。 |
| 2026-09-30 | [News] nvidia reportedly explores glass substrates 2028 adoption possible as taiwan suppliers step up | 转述 Nvidia 评估玻璃基板及供应商准备情况；商业化时间仍为预期。 |
| 2026-09-30 | [News] samsung foundry loss reportedly seen shrinking 42 yoy in 2026 as hbm4 base die 4nm demand rises | 讨论 Samsung Foundry 与 HBM base die 的业务联动及亏损预期。 |
| 2026-09-30 | [Insights] memory spot price update dram market quiet amid holidays nand buying stays weak | DRAM／NAND 现货价格综述，区分产品价格、节假日影响及购买需求。 |
| 2026-09-30 | [News] anthropic eyes 2t ipo valuation 518b ai buildout spans amazon google broadcom and more | 转述 Anthropic 融资／IPO 与云、芯片合作关系，涉及多家技术公司。 |
| 2026-09-30 | [News] wuhan xinrongguang founded with registered capital of cny 13 2 billion targeting silicon photonics | 报道硅光公司武汉芯融光的注册与经营范围。 |
| 2026-09-29 | [News] samsung sees hbm taking nearly 30 of industry dram capacity in 2027 up from 20 currently | 讨论 HBM 对 DRAM 晶圆产能的占用和传统内存供应影响。 |
| 2026-09-28 | [News] micro led optical interconnects take center stage at cioe 2026 | 讨论 CIOE 展会中的 Micro LED 光互连技术及产业链参与者。 |
| 2026-09-25 | [News] 12 inch wafer price hikes loom for 2027 as ai demand tightens supply with ltas reportedly up 15 40 | 转述 AI 需求下的十二英寸硅片长约价格、供应与扩产背景。 |
| 2026-09-24 | [News] cxmt ymtc ramp memory capacity but chinas ai cloud boom could soak up new supply through 2027 | 讨论 CXMT／YMTC 扩产与中国 AI 云需求之间的供需关系。 |
| 2026-09-24 | [News] micron reportedly ends 2gb gddr7 narrowing supply options for nvidias rtx 50 series | 转述 Micron GDDR7 产品退出及 Nvidia 显卡供应选择。 |
| 2026-09-24 | [News] ligent technologies goes public on hkex raising 723m to scale optical transceiver and chip capacity | 报道 Ligent Technologies 上市与光模块／芯片产能用途。 |
| 2026-09-24 | [News] qualcomm says announced 2nm chips will use tsmc keeps door open to samsung foundry | 转述 Qualcomm 对 TSMC／Samsung 代工合作及 2nm 产品的表态。 |
| 2026-09-23 | [News] german court reportedly finds micron infringed two ymtc nand patents grants injunctions | 转述德国法院的 Micron／YMTC NAND 专利诉讼裁定。 |
| 2026-09-23 | breaking intel emib substrate yields reportedly hit 45 suppliers eye 60 by 1q27 | 转述 Intel EMIB 基板良率与后续目标；标题数字不能直接作为已验证事实。 |
| 2026-09-23 | [News] samsungs lpddr6 passes industry first snapdragon validation at 10 7 gbps galaxy s27 may split memory tiers | 报道 Samsung LPDDR6 的 Snapdragon 验证与带宽／功耗指标。 |
| 2026-09-23 | [Insights] memory spot price update dram spot prices hold cautious tone ddr4 2gx8 drops 3 6 | DRAM／NAND 现货价格综述，讨论不同产品规格的价格和供需。 |
| 2026-09-23 | [News] boe joins glass substrate race as samsung electro mechanics skc lg eye 2027 2028 ramp | 讨论 BOE 及韩国供应商的玻璃基板竞争，正文提及 Nvidia。 |
| 2026-09-22 | [News] koreas hbm related exports to malaysia soar 5 7 fold in one year as ai packaging demand rises | 讨论韩国 HBM 相关出口、马来西亚封装需求与供应链流向。 |
| 2026-09-22 | [News] metas muse highlights ai agents higher cpu to gpu needs could benefit intel amd and arm | 讨论 Meta AI agents 对 CPU／GPU 配比的潜在影响及 Intel、AMD、Arm 机会。 |
| 2026-09-22 | [News] intel unveils micro led based glass substrate packaging technology patent | 报道 Intel Micro LED 玻璃基板封装专利及结构。 |
| 2026-09-22 | [News] micron earnings preview can the margin boom last hbm4 ramp and long term contracts in focus | Micron 财报前预览，讨论 HBM4、利润率、长约与云厂商资本开支预期。 |
| 2026-09-21 | [News] tsmc plans mini loop at kaohsiung to speed cowos tool validation reportedly eyes auo site for copos | 报道 TSMC 为 CoWoS 设备验证建立 Mini-Loop，以及潜在 CoPoS 场地。 |
| 2026-09-21 | [News] cxmt g5 dram enters mass production with 50 higher dies per wafer saqp enables 11 95nm half pitch | 报道 CXMT G5 DRAM 工艺、量产及晶圆产出效率。 |

## 定位与原始证据

- 开发计划：[INFORMATION_SOURCE_SELECTION_DEVELOPMENT_TEST_PLAN.md](INFORMATION_SOURCE_SELECTION_DEVELOPMENT_TEST_PLAN.md)（已按本报告发现完善，尚未实施）。
- 当前列表的文档 ID、版本、正文、哈希及原规则命中信息：`tmp/information_publisher_review_20261012/inventory.json`，按既有规则仅本机保存。
- TrendForce 标题生成：`src/ats/data/articles/trendforce.py::_slug_title`。
- 实体别名识别：`src/ats/data/articles/entity_association.py::title_mentions_entity`。
- 通讯完整性规则：`src/ats/data/research.py::classify_completeness`。

本报告是来源模式和验收设计审阅，不是优化实施、准入元数据修复、正式模型结果或生产资格报告。
