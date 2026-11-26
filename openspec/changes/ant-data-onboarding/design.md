# Design

## Context

动机见 proposal.md。与实现相关的现状：

- **身份**：Anthropic 只以私有实体出现在 `config/sectors/ai_hardware.yaml` 的 L1_app `private`、L1 商业化命题证人 `ANTHROPIC`，以及 `config/data/structured.yaml` 的私有实体。治理型观察器（`data/sources/governed_observer.py` → `chain/sources.to_observations`）写入的实体是字面量 `ANTHROPIC`，不做规范化。`evidence/observer.concept_menu` 按证人字面量匹配。
- **SEC 通路**：`data/sources/sec_submissions.py` 的 `FORMS` 只有 10-K、10-Q、8-K、20-F、6-K。发现窗口由财报事件决定（`due_entities`，事件前 3 天到后 1 天）。正文入库在 `pipelines/unstructured/sec.py::fetch_filing`。目前没有本地文件入库入口。
- **XBRL**：`data/sources/company_financials.py` 的 `XBRL_CONCEPTS` 约 14 个 GAAP 指标，不含 RPO 和购买义务。
- **新上市标的的基本面**：`agents/fundamental/inputs.py::governed_company_inputs` 要求公司财务完整，否则抛出 `ReadUnavailable`；`workflow/decision_requirements.py` 允许最多 4 个基本面未覆盖的 target（`planning.MAX_UNCOVERED_FUNDAMENTAL`），未覆盖的标的只能持有或减仓。
- **新闻关联**：`data/entity_association.py::entity_mentions` 对标题、导语和全文统一匹配，没有按实体限定范围的能力。
- **前沿评测**：`sources/frontier_ai_capability.py::_public_model` 把发布日期设成成绩日期；`_parse_aa_dataset_documents` 有意跳过成本；`DEFAULT_FLAGSHIP_MODEL_IDS` 写死。
- **前沿实验室收入**：趋势把 Sacra 与 TickerTrends 混成一条序列（见 `docs/data/L1_OBSERVER_DATA_SOURCES.md` 第 3.5 节）。
- **用户的首版 S-1**：`~/Downloads/Anthropic, PBC - S-1 (Q2).html` 是 SEC 完整提交文本，约 14 MB，正文可以完整抽出（约 157 万字符），没有 inline XBRL；发行相关数字留空。

## Goals / Non-Goals

**Goals:**
- 12/16 第一批解禁前完成 A 组：ANT 接入各研究任务流，S-1 与 424B4 入库，主理人每日运行不被阻断。
- 首份财报前完成 B 组：首份 10-Q 发布后，披露指标能自动产出候选，由用户确认后成为序列。
- C 组按数据源逐项接入，每项都经用户抽查验收。

**Non-Goals:**
- 不新增 L1 命题。ANT 独有的维度（留存、单位经济）留给 `ant-thesis-monitor`，因为层命题要求至少两个证人。
- 不改风控规则、代理 beta 和代理簇（见 `ant-decision-integration`）。
- 不做通用的"任意发行人"披露抽取框架，只支持配置里声明的发行人；目前只有 ANT。
- 不接入任何付费数据源。

## Decisions

### D1 证券代码 ANT 与证据身份 ANTHROPIC 分离（2026-11-26 用户确认，取代原"规范身份用 ANT"）
- `config/entities.yaml` 登记 ANT（别名 `ANTHROPIC`、`Anthropic PBC`、`Claude`），用于交易直接相关的请求；加 `code_ambiguous: true`（裸 "ANT" 与普通词、蚂蚁集团同形，文章提及须带股票语境，名字折叠不收裸代码）和 `evidence_entity: ANTHROPIC`。
- L1 证人、`sources.yaml`、治理型观察器、结构化实体保持 `ANTHROPIC` 不变。
- 换名只发生在证据账本的入口：抽取写入（说话人；以及模型用名称别名或说话人自己指代的主体）、按实体读取与作废、命题维度菜单（`config.ledger_entity`）。他人文件里的裸 "ANT" 保持原样。
- 理由：证据、命题、新闻、评测用公司名更不易误认；账本历史行与既有序列本来就以 `ANTHROPIC` 为键，不需要迁移。
- 原方案（证据账本折叠为 ANT）在实施中撤回；撤回前定时任务写入生产的两行已于 2026-11-26 清理。

### D2 注册类文件：本地入库命令 + 按发行人补采
- 新增运维命令（暂定 `ats data registration-ingest --entity ANT --form S-1 --file <path>`）。它把本地文件包装成与 SEC 正文入库相同的行结构，复用 `fetch_filing` 之后的准入与存储，注册人名称和申报日期从封面解析。
- SEC 补采：`FORMS` 增加 S-1、S-1/A、424B4，但**只对配置里声明需要补采注册文件的发行人生效**（ANT），并且走一次性补采命令，不进入每日发现，避免把所有 target 多年前的 S-1 都拉进来。
- 注册类文件的文档类型与财报材料分开，不触发财报事件路由。

### D3 SEC 例行通路不写 ANT 专用代码
ANT 加入 `config/pead.yaml` 的 targets 后，现有的发现、正文、XBRL 任务会自动覆盖它。验收用回放：用 SPCX（2026-05 递 S-1 上市、已发布定期报告）和 NVDA 的一个历史财报事件，离线回放发现 → 正文 → XBRL → 实际值抽取的全过程，证明通路可用。ANT 首份 10-Q 发布后再做一次真实验收。

### D4 新闻关联范围按实体声明
在 `config/entities.yaml` 为实体增加可选字段 `association_scope: headline`（标题、副标题、导语），默认 `full`。`entity_mentions` 按此字段决定读取哪些字段。ANT 声明为 `headline`。
- 不选"降低 ANT 的候选上限"：上限只截断数量，挡不住无关文章占用名额和模型调用。
- `Claude` 作为别名在 headline 范围内误匹配的风险较小，试运行时统计候选数和误匹配比例，再决定是否保留。

### D5 新上市标的走既有"未覆盖"机制
不新增状态机制。基本面例行任务在 `ReadUnavailable` 且原因为"无财报历史"时，发布带原因的未覆盖投影，主理人周期按既有规则继续，ANT 只允许持有或减仓。加入可交易名单前，先核对当前未覆盖的数量：加上 ANT 后如果超过 4，就停止接入并报告，不擅自调整上限。

### D6 `config/pead/ANT.yaml` 的打分维度对齐文稿
`scorecard_dims` 用文稿的问题替换通用的七个维度：新增年化收入、推理毛利、算力费用率、未来承诺付款、NRR、年付 10 万美元客户数、非 coding 产品收入，权重合计为 1，在用户确认后写入。`fiscal_label` 不手填，来自日历。这样首份财报的实际值抽取会直接产出文稿指标，监测和 PEAD 使用同一套口径。

### D7 披露指标走"候选 → 核验 → 发布"的独立管道
- 新增数据源 `company_disclosure_metrics`：
  1. 按 `config/data/disclosure_metrics.yaml` 中声明的指标清单，先用确定性的关键词和章节定位，从文件中切出相关段落（MD&A、财务报表附注、关键指标定义）。
  2. 再交给模型抽取数值和原文片段。
  3. 由代码核验片段里确实包含该数字，并检查单位和期间。
  4. 写入候选状态，经 CLI 确认后发布为结构化观测。
- 不选扩展 PEAD 的 `extract_actuals`：它产出的是打分卡读数，不是可追溯的时间序列，也没有人工确认环节。
- 不选证据观察器：它服务于命题判读，不产出数值序列。
- 实现前先查结构化存储是否已有可复用的候选、隔离状态（`structured_observations` 的状态字段）；有就复用，没有再新增候选表。

### D8 成本控制：不把整份文件交给模型
S-1 约 157 万字符。抽取只把定位出的段落交给模型，每个指标一次短上下文调用，同一文件版本和指标的结果缓存，重跑不重复请求。首轮回放前先报告请求数，由用户确认。

### D9 OpenRouter 价格与估算花费份额
- 新增 `sources/openrouter_pricing.py`，每天读取公开模型列表，保存输入和输出单价，按 vintage 记录。
- 估算份额在 `products/openrouter_rankings.py` 中计算：周 token × 当周有效单价 × 声明的输入输出比例，比例写在配置里并在结果中披露。

### D10 前沿评测的三处修改
- 发布日期改为查询发布登记（D11），查不到就留空。
- 恢复解析 Artificial Analysis 公开页面上的成本字段，不调用付费 API。
- 旗舰模型改由规则和发布登记决定，去掉写死的 `DEFAULT_FLAGSHIP_MODEL_IDS`。
- 新评测先逐个做可得性检查，结果写进本 change 的 `verification.md`，通过的才写适配器。

### D11 模型发布登记用配置文件 + 简报候选
- 已确认的记录放在 `config/data/model_releases.yaml`，由人维护，git 留痕，更正也在这里记录。
- 信息简报的事件成分里出现模型发布时，生成候选写入候选队列；用户确认后由运维命令写入配置。
- 不选全自动抓取官方博客：各实验室公告格式不一，误判发布层级的代价高，而频率只有每月一两次。

### D12 GPU 租赁价格先调研后实现
先做可得性调研：公开按需价格页、市场型平台的公开接口等。结果写入 `verification.md`。有合格的免费来源才实现适配器；没有就只发布"未覆盖"状态和付费选项清单。

## Risks / Trade-offs

- [ANT 占用未覆盖名额后，再有其他标的缺基本面，可能超过 4 个上限，阻断主理人] → 接入前核对数量；超过时停止接入并报告。首份 10-Q 入库后名额自动释放。
- [身份统一改变 L1 证人计数，影响命题判读] → 回归核对逐项列差异，用户确认后才上线。
- [只看标题和导语会漏掉正文里关于 Anthropic 的重要信息] → 重大事件通常出现在标题。试运行一周后与全文匹配的结果对比漏报，再决定。
- [披露指标口径在不同季度变化，例如一项费用被拆成两项] → 规格要求记录口径变化而不静默合并，由监测的模型判读给出口径提示。
- [OpenRouter 的输入输出比例假设影响估算份额] → 披露假设，只用于看趋势和拐点，不作为绝对份额。
- [Artificial Analysis 页面结构变化导致成本解析失败] → 按来源故障状态处理，成本留空，不影响成绩发布。

## Migration Plan

1. 先完成身份统一与回归核对，再加入可交易名单。
2. 加入可交易名单后试运行一天：信息简报、基本面（未覆盖）、技术面（占位）、风控日报、主理人完整运行（不下单）。
3. 回滚：从可交易名单移除 ANT 即可停止各任务流；身份别名可以撤回；新增的数据源可以按结构化来源的生命周期停用，不影响既有来源。

## Open Questions

- OpenRouter 估算份额的默认输入输出比例。实现时给出依据后由用户确认；不影响规格。
- `Claude` 是否保留为 ANT 别名。由试运行的误匹配统计决定；不影响规格。
