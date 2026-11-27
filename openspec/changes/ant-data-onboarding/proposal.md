# Proposal

## Why

ANT（Anthropic PBC）于 2026-11-19 上市，系统已持有 50 股，但系统对它几乎是盲的：它只作为 L1 层的私有实体 `ANTHROPIC` 存在，没有任何任务流为它产出研究，风控把它列为"未分层"，SEC 采集表单也不收 S-1。用户的《ANT 投资观测体系与行动指南》（Claude 文稿）列出约 25 个观测项，其中只有 Ramp 与股价持仓可直接使用。第一批解禁在 2026-12-16 前后，需要在此之前把 ANT 接入为一只正常的持仓股票，并打通观测体系所需的数据通路。

本 change 只负责**数据采集与处理**（计划的 A–C 阶段）：ANT 的身份与任务流接入、公司披露数据通路、行业高频数据补洞。论点监测引擎（`ant-thesis-monitor`）与决策链接入（`ant-decision-integration`）是后续两个 change，依赖本 change 产出的数据。

## What Changes

**A. ANT 接入与通路打通**
- ANT 登记为证券代码，只用于交易直接相关的请求；证据账本、L1 证人、结构化实体与新闻评测继续用 `ANTHROPIC`，两者在证据账本入口换名，同一家公司不拆成两个身份。
- 注册类文件（S-1、S-1/A、424B4）可以入库：用户已下载的首版 S-1 走本地文件入库；S-1/A 与 424B4 从 SEC 补采，以补齐首版留空的发行数字和第三季度数据。
- ANT 纳入 SEC 例行采集（10-Q、10-K、8-K、XBRL 公司财务）。首份 10-Q 前没有内容可采，用已上市并出过财报的 SPCX、NVDA 回放证明通路可用。Finnhub 未给出财季标签时允许人工登记财报日。
- ANT 进入既有研究任务流：PEAD 可交易名单与 `config/pead/ANT.yaml`、L1 层名单、技术面名单（自动随可交易名单）、风控归层到 L1。
- 新闻实体关联支持按实体限定匹配范围：ANT 只有在标题或导语提到 Anthropic/Claude 时才算相关，避免每篇提到 Anthropic 的 AI 新闻都成为 ANT 候选。
- 新上市标的首份财报前，基本面以"未覆盖"状态参与，原因写明"无财报历史"，不阻断主理人周期。

**B. 公司披露指标通路**
- XBRL 映射增加 RPO、RPO 中 12 个月内确认的比例、购买义务及其到期分布。
- 新增公司披露指标：从已入库的注册文件和定期报告中抽取非 GAAP 指标（推理/训练算力费用、未来承诺付款、年付 10 万美元客户数、推理毛利、云渠道占比、NRR/GRR、产品收入、收入结构、前十大客户），每项带原文位置，经"候选 → 自动核验 → 人工确认 → 发布"后成为季度序列。
- 派生指标：边际承诺强度、RPO 覆盖率、宣传口径与 GAAP×4 的差距。
- 前沿实验室收入的趋势序列按来源与口径分开，修复 Sacra 与 TickerTrends 被并成一条序列的问题。

**C. 行业高频数据补洞（免费来源优先）**
- **BREAKING（规格层面）**：OpenRouter 允许用公开模型价格计算"估算花费份额"，明确标注为估算、不是收入；新增模型价格采集。
- 前沿评测：发布日期必须来自官方发布登记，不得用成绩日期或抓取日期代替；保存每任务成本；按可得性逐项扩充 SWE-bench Pro、GDPval-AA、Artificial Analysis 智能指数、Epoch、GDPx、Harvey LAB-AA、APEX-Agents-AA，拿不到的显式标为未覆盖。
- 新增模型发布登记：旗舰模型的官方发布日期，由 OpenRouter 新上架型号提出候选、人工确认后登记。
- 新增 GPU 租赁价格：只接免费可靠来源；没有时显式标为未覆盖，并列出付费选项供用户决定。

## Capabilities

### New Capabilities
- `data/registration-statement-ingestion`: 新上市发行人注册类文件（S-1、S-1/A、424B4）的入库，包括本地文件入库与 SEC 补采，以及各版本之间的取代关系。
- `data/company-disclosure-metrics`: 从公司文件抽取非 GAAP 披露指标并经证据型准入发布为季度序列，以及由这些序列派生的比率指标。
- `data/model-release-registry`: 前沿实验室旗舰模型官方发布日期的登记，供领先窗口和竞品发布后份额回撤计时使用。
- `data/gpu-rental-price`: GPU 租赁价格序列的采集或显式未覆盖状态。

### Modified Capabilities
- `data/openrouter-rankings`: 放开"禁止用价格乘 token 估算"的限制，改为允许带估算标签的花费份额；新增模型价格采集要求。
- `data/frontier-ai-capability-benchmarks`: 新增发布日期来源、每任务成本保存、评测扩充的可得性检查要求。
- `data/frontier-ai-labs-revenue`: 新增趋势序列按来源与口径分开计算的要求。
- `data/unstructured-ingestion`: 新增按实体限定关联匹配范围的要求。
- `sector/chain-layers`: 新增私有实体上市后证人身份统一的要求。
- `agent/fundamental-pead`: 新增新上市 target 首份财报前的基本面状态要求。
- `scheduling/event-calendar`: 新增数据源缺财季标签时人工登记财报日的要求。

## Impact

- **配置**：`config/entities.yaml`、`config/data/structured.yaml`、`config/sectors/ai_hardware.yaml`（L1_app）、`config/risk.yaml`（`instruments.ANT`、L1 上限注释）、`config/pead.yaml` 与新建 `config/pead/ANT.yaml`、`config/data/schedules.yaml`、`config/data/sources.yaml`。
- **代码**：SEC 发现与正文入库、XBRL 映射、实体关联、治理型观察器写入实体、前沿评测与 OpenRouter 适配器、前沿实验室收入趋势、新增公司披露指标抽取与核验 CLI、新增发布登记与 GPU 价格来源。
- **既有行为变化**：可交易名单增加 ANT，占用一个基本面未覆盖名额；L1 层上限 10% 开始约束真实持仓（标配 6%）；L1 三个证据观察器的实体映射变化，需要回归核对；每日信息简报多一个目标。
- **成本**：附注指标抽取每季一轮模型调用；信息简报每日新增一个目标，匹配收窄后候选受控；不接入付费数据源，除非用户逐项批准。
- **不涉及**：交易、审批与风控规则（见 `ant-decision-integration`）；论点监测（见 `ant-thesis-monitor`）。
