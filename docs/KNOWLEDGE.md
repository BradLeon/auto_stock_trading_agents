# 知识分层：静态知识放在哪、谁读它、怎么知道它该更新了

> 这份文档回答三件事：系统里的"静态知识"有哪些、每个 agent 该读哪一层、以及
> **在没有人定期通读的前提下，怎么发现某份知识已经过时**。
>
> 相关：`docs/DESIGN.md`（角色与决策权）· `docs/CHAIN_EVIDENCE.md`（命题证据链）

## 1. 问题：知识库跨了两个衰减速率

`config/` 在运行时是**只读**的——唯一"agent 参与策展"的环节是命题提议器，它只产出
待确认卡，从不落盘。所以所有静态知识都是人写的，也只能由人改。

但静态知识不是一种东西。按**半衰期**分，它至少是五层：

| 层 | 半衰期 | 载体（字段级） |
|---|---|---|
| **拓扑** 谁卖给谁、瓶颈在哪一环 | 年 | `SignalChainConfig.{symbol, role}` + **YAML 行尾注释** · `knowledge/*.md` §一 |
| **技术方向** 代际演进、替代关系 | 年 | `knowledge/*.md` §二 |
| **结构位** 护城河排序、市占 | **季度** | ~~`knowledge/*.md`~~ → `ClaimAssessment.entity_readings` |
| **本期读数** 谁在赢、ASP、认证进展 | 季度 | `ClaimAssessment` / `FactorEvidence`（自动刷新） |
| **市场** 价格、动量、估值 | 日 | `FactorRow`（运行时现取） |

原来的知识库把第一、二层和第三层写在同一个文件里——`光互联.md` 曾经写着
`moat_pricing：COHR ≳ LITE ≫ AAOI`。这带来两个后果：

1. **过时不可见。** 一次客户认证就能改写子层格局，但文件不会自己变色，也没人被提醒去看。
2. **它是打分器该产出的答案。** 预置答案给打分器，它只会复述——与之前删掉的
   `supports_when`（把"扩产＝供给转松"写死在配置里）是同一类错误。

**原则：知识库给判据，不给排序。**

判据（"自有衬底产能 > 客户分散度 > 模块封装规模"）不会在一个季度里翻篇；排序交给打分器
用本期证据去排。这同时解决了"既重要又静态"的矛盾——**静态的部分本来就该是不会变的部分**。

## 2. 知识库的形状（四节模板）

`config/knowledge/<子层>.md` 一律四节：

```
① 价值链分工      谁在链条哪一环，利润集中在哪几段
② 技术曲线        tech_tenor 的依据：方向确定的演进与替代关系
③ 护城河判据      moat_pricing 的依据：按证据强度从硬到软排列的要素
                  （可含"衰退信号"，但只写**该看什么**，不写**现在亮到哪一档**）
④ 常见误判        这个子层特有的、把读数读反的方式
```

**不写**：谁排第几、市占百分比、估值倍数、以及任何"截至某月的当前状态"。

现有九份：`Token经济`(L1) · `光互联`（含上游衬底）· `铜连接` · `HBM存储` · `云服务`(L2) ·
`电力冷却`(L3) · `芯片设计`(L4) · `先进封装代工`(L5) · `半导体设备`(L6)，
外加跨层的 `资本开支链`。

**L1 原来不写，2026-08-18 改了。** 原来的理由是「只有一票，`cross_section._zscores`
需要 ≥2 个样本」——**这条理由没错，但它答的是另一个问题**。单样本确实让 L1 的截面 z 分
恒为 0，所以这份笔记不改变任何权重；写它是因为另外两件事：

- `SectorContext.kb_criteria` 是**各层 `structure_notes` 的汇总**（`assemble._kb_criteria`），
  sector_analyst 读的是全部九份。而 L2–L6 的景气叙事最终都挂在「L1 能不能把算力卖成
  **链外**收入」这个前提上——**此前没有任何一份笔记写过它**，它只散落在六份研报里。
- L1 的主要玩家未上市，判读只能靠对手方（L2/L4/L5）的公开披露。「**怎么读二手证人**」
  本身就是一条判据，而不是某一期的读数。

**推论：一层该不该有笔记，看的是它有没有别处写不下的判据，不是它有几只票。**

`资本开支链` 与 `Token经济` 接在同一个断面上，边界写死在两份笔记的开头：前者写
**L2 的资本开支怎么变成 L3–L6 的收入**（链上那笔钱怎么流），后者写**那笔钱最终要从
哪里回来**。资金端判据（FCF 覆盖率、融资形态、口径调整）只归前者。

## 3. 谁读什么（字段级）

原则：**每个 agent 读它需要的那一层，且不读会让它确认自己结论的那一层。**

| Agent（skill / role） | 读到的具体字段 | 载体 |
|---|---|---|
| `evidence_observer` | `Concept.key` + `.desc`（`observer.concept_menu()`，只给该公司是声明证人的维度）<br>`SignalChainConfig.symbol/.role` **+ YAML 行尾注释**（`observer.relation_hint()` 重读原文件取注释） | `ClaimDef.concepts`<br>`pead/<SYM>.yaml` |
| `evidence_adjudicator` | `ClaimDef.statement` `.falsifiers` `.entities`<br>`Concept.desc`（逐簇渲染）<br>`EvidenceCluster.{speaker, entity, concept, direction, periods, rows[].evidence_span}` | 命题 + 台账 |
| `structure_analyst` | `knowledge/*.md` 全文 · `FactorEvidence`（逐家读数 + 引用原文）· `FactorRow` · `LayerAssessment` | KB / 台账 / 因子 / 本次周报 |
| `sector_analyst` | `SectorContext.kb_criteria`（**各层 `structure_notes` 汇总**）<br>`.evidence_block`（供需块 + 定价权块）· `.static_notes`（六份研报）<br>`LayerTicker.note` · `SectorLayer.private` · `.macro_block` · `.pead_blocks` | KB / 台账 / 研报 / sector 配置 |
| `pead_analyst` · `pead-framework` | `SignalChainConfig` 的同业名单 | `pead/<SYM>.yaml` |
| `pead_analyst` · `pead-narrative` | 六份研报 **+ 最新 `SectorReview` + `MacroReview`**（附「分歧时以此为准」）<br>`PeadConfig.narrative_seed`（仅当无活体论点） | 研报 + SQLite |
| `pead_analyst` · `pead-expectations` | `ScorecardDim.{key, label, weight}` | `pead/<SYM>.yaml` |
| `industry_analyst` · `pead-signal-chain` | `SignalChainConfig` + 实时价格/财报日 | `pead/<SYM>.yaml` |
| `macro_analyst` | 定量盘 + 外部检索；**不读 KB** | — |
| `context_monitor` · `news_triage` | 活体论点，回退 `narrative_seed` | SQLite / 配置 |
| `claim_proposer` | 已声明命题的 `statement` 列表（"别重复"）+ 未映射观测 | 命题 + 台账 |
| `chief` | **只拿结论**：`SectorReview.{regime, layers, company_calls, baskets}` · risk · technical | SQLite 存档 |

**判读器明确不给知识库**，即使是机制部分。它已经能自己推机制（「扩产是对需求的回应，
且新产能 2027 才到位」是它自己写的），边际收益低而偏置风险实在——一份通用先验会影响
**所有**命题的判读。要补机制，正确的位置是 `Concept.desc`：命题级、人写的。

**顺序是有意义的。** KB 在前、证据在后（`assemble.as_context()` 与 `structure.assess()`
都是这个顺序，沿用 `graph/pead.py` 的既有范式）：判据说的是**怎么权衡一个读数**，
台账说的是**本期读数是什么**；两者冲突时，**后出现的那一块是模型写结论时仍在视野里的**。

## 4. 怎么发现知识库该更新了

更新不只来自冲突，也来自**新知识**。而且不该靠人定期通读——该由数据指出来。
六类信号，全部在现有数据里已经存在，缺的只是收集与呈现（`chain/kb_review.py`）：

| 信号 | 检出方式 | 含义 |
|---|---|---|
| **① 盲区标记** | 扫最近几次 `SectorReview` 每层最新 basket 的 `BasketRow.rationale` 里的「KB 未覆盖」 | 该层缺 KB，或 KB 没覆盖这个名字 |
| **② 未映射聚集** | 未映射观测按 `metric` 词元聚类，要求**跨公司且跨期间**（屏蔽 GAAP 报表词汇） | 反复遇到却无处安放的主题 |
| **③ 未声明关系** | `entity != source_entity` 且该配对不在说话人的**手写** `signal_chain` 里 | 新拓扑——年度级知识的增量 |
| **④ 归因失败** | 某维度/某证人的判读几乎全为 `neutral` | 维度问的问题证据答不了，或 `concepts` 绑错 |
| **⑤ 陌生实体** | `Observation.entity` 不在 `entities.yaml`、`sources.yaml` 或任何层名单里 | 可能有新玩家，**也可能是抓错文档——必须人看** |
| **⑥ 久未复核** | KB 文件 mtime + 该层自那以后新增的观测数，**两个条件都要** | 不是"错了"，是"没人对过账" |

②的词元聚类是**语义的代理，不是语义**：故意做得很粗，这样它标出来的东西可以用眼睛核。
③读的是**原始 YAML**而不是 `load_pead_config()`——后者在文件没声明时会用同层同业兜底
（`config._derive_signal_chain`），那份兜底没有 `role` 也没有注释，而注释正是
`relation_hint` 区分「上游 HBM 主供」与「上游 EUV」的唯一依据。把兜底当成声明，
这个检测器会永远沉默。

### 落点：周报里的一节，不是自动更新

```
每周（跟随周度作业，截面跑完之后）
  ①盲区  ②未映射聚集  ③未声明关系  ④归因失败  ⑤陌生实体  ⑥久未复核
        ↓ 确定性汇总（无 LLM）
  产业链证据周报 新增「知识库复核」小节
        ↓
  你决定改不改 —— 系统不自动改，也不静默调和
```

与既有治理一致：**agent 提议，只有人写进配置**。这里连提议都不产生新配置，
只产生一张待办清单。**无发现是合法结果**——一个每周都有内容的小节，是一个没人相信的小节。

## 5. 怎么验收「被正确使用」

知识库本身对不对，只有人能判（§2 的模板 + §5 的清单）。但「其他 agent 有没有用对」
是可以测的，按**可验证性**分四层：

| 层 | 问题 | 手段 | 现状 |
|---|---|---|---|
| 0 | 读到了吗 | `SectorContext.stats()["kb_chars"]` · `LayerBasket.structural` | ✅ 已有 |
| 1 | 用的是判据，还是编的 | `chain/kb_audit.py` → 周报「知识库使用审计」 | ✅ 已建 |
| 2 | 判据是 load-bearing 还是装饰 | `ats sector kbperturb`（消融/投毒/对照） | ✅ 已建 |
| 3 | 判读方向对不对 | 人工标注的黄金回归集 | ⬜ 未建 |
| 4 | 认知是否正确（有没有 alpha） | 结构分落 `Prediction`，四周期结算 | ⬜ 未建 |

### 层 1 的锐利之处

**知识库里零股票代码、零公司名**——这是四节模板的构造性质，不是巧合。所以把一条
**关于某公司**的说法归给知识库，就是编造，不需要判断内容对错。否定式（「KB 未覆盖
MRVL」）是唯一合法的共现，用否定词排除。

审计同时统计 `moat_pricing` 的**出处分布**（台账读数／行业评审／仅判据／无说明）。
一层里没有一个分数建立在台账读数上，不是知识库的问题——**是缺命题和证人**。

### 层 2 的一条纪律：先测噪声底

两臂是两次独立的模型调用，本来就会飘。**实测各层差很多：L6 的噪声底 0.05，L3 是
0.33。**所以任何 |Δmoat| 都必须先跟 `--mode control` 的结果比，否则同一个 0.4
在两层里意思完全相反。

而且**推断是单向的**：分数动了，证明判据在起作用；分数不动，什么也证明不了
（可能没在用，可能模型先验本来就一致，可能它识破了自相矛盾的投毒文本）。

三个已经踩过的坑，都写进了工具里：
- `structure.assess` 失败时返回 `{}`，渲染出来就是"全 0"——在投毒臂里看起来正好像
  一次剧烈的崩塌。空结果必须**中止比较**，不能变成结论。
- 整臂弃权（非空但全 0）同理，也会被误读成最强的 load-bearing 证据。
- 平均值的分母只能算**两臂中至少有一臂给了非零分**的标的；否则五个弃权能把两个
  标的上干净的 1.2 崩塌稀释成 0.36。

## 6. 改知识库时的检查清单

1. 要写的这句话，**一个季度后还成立吗**？不成立 → 它是读数，属于证据台账，不属于这里。
2. 它是**判据**还是**结论**？"自有衬底产能是最硬的定价权来源"是判据；
   "COHR 的衬底自供最强"是结论。
3. 写的是**该看什么**，还是**现在是什么**？衰退信号只写前者。
4. 加了新子层？记得在 `config/sectors/<name>.yaml` 的对应层挂 `structure_notes`，
   否则没有任何 agent 会读到它。
5. 改完之后，**下一次周报的「知识库复核」会不会因此少一条**？如果不会，
   说明改的可能不是被指出的那个问题。
