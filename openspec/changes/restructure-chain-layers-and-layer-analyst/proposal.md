## Why

**投资问题**：现在系统能回答「AI 硬件这条链景气不景气」，但回答不了我真正要做的两个决策——
**这一层该给多少钱**（超配/标配/低配/清仓），以及**这一层内部买谁**。原因不是分析师不够聪明，
是分层本身把两类资产捆在了一起。

这个系统里的「层」同时承担三件事：

| 职责 | 要求 | 消费者 |
|---|---|---|
| ① 需求传导的一环 | 订单流上的位置 | 提示词里的 L1→Ln 叙事 |
| ② 截面 cohort | 同层公司**驱动因素相同**，z 分才可比 | `cross_section.rank_cohort` |
| ③ 风险预算/相关簇单元 | 同层公司**股价同向**，cap 才真的限住风险 | `risk.yaml sector_layer_caps` → basket 权重之和 |

**层只有在三件事同时成立时才是好的切法。现在有两层不成立：**

- **`L5_fab`（TSM + 存储三巨头）在 ②③ 上都不成立。** 代工是产能垄断的服务商，定价由制程节点与
  secular 需求决定；存储是商品化周期品，定价由 bit 供需决定。两者的毛利率/PEG 放进同一批 z 分，
  比较的是两个不同的东西。而 30% 是全表**最松的一档 cap**，恰好盖住了相关性最低的两类资产——
  这不是保守，是护栏开在了错的地方。
- **`L3_dc_infra`（光/铜/衬底 + 电力冷却）同样。** 该层的 `structure_notes` 已经配了**四份**不同的
  判据笔记——它事实上已经是四个 cohort，只是共用一个 `boom_score` 和一个 cap。VRT 与 COHR 唯一的
  共同驱动是云 capex，而那是 `资本开支链.md` 里的**全链共同因子**，不是本层特征。

第二个缺口：`sector_analyst` 站在整个行业做**一次** Opus 合成，每层只得到一条 `boom_score` +
bullish/neutral/bearish。这个输出**没有仓位含义**——「L6 bullish」不告诉我该给 30% 还是 9%。而
`weight_cap × 截面排名` 这条权重路径完全绕开了景气判断：无论本层是周期底部还是顶部，预算都按
`risk.yaml` 的静态上限满额分配。**唯一能表达"这层现在不该重仓"的地方，是人去改 risk.yaml。**

## What Changes

### 一、分层：6 层 → 8 层（**BREAKING**：全部 layer key 更名）

```
L1 应用/Token经济        GOOG（+OpenAI/Anthropic/SPCX 非上市）        ← 不变
L2 云服务/算力租用        MSFT AMZN META GOOG CRWV                    ← 不变
L3 数据中心电力与冷却      VRT ETN GEV BE（不分 subgroup）              ← 从 L3_dc_infra 收窄
L4 互联与网络            COHR LITE AAOI CRDO AXT [extra: MRVL AVGO]   ← 新层
L5 芯片设计              NVDA AMD AVGO MRVL                          ← 原 L4，仅更名
L6 存储                 SKHY MU 005930 + SNDK STX WDC               ← 从 L5_fab 拆出
   subgroup: HBM / 常规DRAM / NAND / HDD
L7 代工与先进封装         TSM                                         ← 从 L5_fab 拆出
L8 半导体设备            ASML AMAT LRCX KLAC TEL                     ← 原 L6，仅更名
```

- **拆出的是「互联与网络」而不只是「光互联」**：光/铜/CPO/交换是同一个**互相替代**的战场，
  同层才有对手可比，relative 命题才立得住。现状是 AVGO 的交换芯片收入、MRVL 的光 DSP 收入
  被 L4 的命题注释**显式排除**、又不属于 L3 的任何命题——两笔收入无家可归。
- **存储层保留 subgroup 分组**：HBM 与 NAND/HDD 同层的理由是**股价吃同一个存储周期 beta**
  （风险簇 ③ 成立），不是「都是存储」；它们的**产能线不同**，所以可比性在 subgroup 级别更强。
  → **层是风险预算与景气判断单元，subgroup 是叙述与比较的分组标签。**
  **z 分仍在整层计算**（subgroup 内样本太小，组内标准化会退化），分组只作用于层级分析师
  写「买谁」时的分开讲述——理由与代价见 design D3。
- **宇宙扩容（共 6 只）**：存储层加 SNDK（NAND）/ STX / WDC（HDD）；电力冷却层加 ETN / GEV / BE。
  不扩的话电力层只剩 VRT 一票——`_zscores` 在样本 <2 时全返 0，截面等于没跑。
  其中 **BE 是该层 AI 纯度最高、也最不同类的一只**（现场发电绕过电网接入排队，但定价机制是技术
  采纳曲线而非机电装机量）；该层不设 subgroup，其可比性限制写在标的 `note` 里，见 design D10。
- **key 迁移方式**：新增 `SectorLayer.legacy_keys`，让 sqlite 里的历史行（`claim_assessments.layer`、
  `sector_reviews` 的 `LayerAssessment.key`）仍能解析到新层，历史不断档。

### 二、证据链随层重排（不改引擎，改归属与作用域）

拆层不是把命题挪个位置就完事，有三处独立工作：

- **命题的层归属定死一条规则**：命题挂**被判断的主体**所在层，不挂**证人**所在层；
  `witness_roster` 天然跨层（`upstream` 从来就不是同层公司），**不随拆层重切**。
  这条不写死，以后每次动层结构都要重吵几十条命题的归属。
- **两个新层的命题缺口**：拆完之后 **L3 电力与冷却层零命题**——现有四条命题全是互联的，
  整块去 L4。后果是 `concept_menu` 对该层四只票返回空菜单，它们的观测 **100% 未映射**。
  本仓 2026-08-14 踩过这个坑（COHR 216 条、LITE 52 条全部落在未映射池）。本次采用**两步法**：
  电力层 `claims: []` 显式留空并记录原因 → 跑一轮 observe 导出未映射池 → 下一轮拿真实读数
  起草命题。六只新票同理，**先看它们实际说了什么再决定声明谁**。
- **证据块按层切分并各自定向**：`assess_layer` 本就按层跑，但 `assemble._chain_evidence` 把全
  sector 汇成两个大块广播给单一分析师。改为每层产出**两个分别定向的块**：
  **common → 层级分析师**（答「这层该给多少钱」）、**relative → 结构分析师喂因子 + 层级分析师
  写选股理由**（答「选谁」）。这条分工本就写在代码里（`factor_evidence.py:115` 只收 relative），
  此前没被写成设计。分账规则不变：common 结论不得被读成「谁在赢」，也不得进入任何结构因子。

**同时撤销一处重复计价**：宏观退出整条 sector 链路（层级 / 结构 / 轮动都不吃），
只在 Chief 作用——`chief/assemble.py:54` 本来就注入了宏观 `sector_tilts`，行业链路再吃一遍
会让同一个判断被计两次，且层级结论变差时分不清是产业景气还是宏观。层级分析师判周期位置
改用产业证据（capex 指引 / 订单交期 / 库存）。

### 三、新增层级子行业分析师（`layer_analyst`）

每层跑一次，读**本层自己的**共同议题结论 + 相对命题读数 + 判据笔记 + 截面 basket（不含宏观），产出：

- **配置结论**：超配 / 标配 / 低配 / 清仓 + confidence + 每条议题一行的归因
- **周期位置**与**反转触发条件**（写成可证伪的观察项，下一轮直接核对）
- **同层选谁**：以 relative 命题读数 + 截面排名为依据的逐票取舍理由

`sector_analyst` 保留，但职责收窄为**跨层轮动与一致性**：它消费 8 条层级结论（紧凑）而不是
全部原始上下文，跨层比较仍在同一个上下文里完成。

### 四、配置结论绑定仓位（只能在 cap 内下调）

层级结论映射为**本层预算使用率**，`cross_section` 用 `weight_cap × 使用率` 作为 basket 的
`layer_cap`：

```
超配 → 100% × weight_cap      标配 → ~60%      低配 → ~30%      清仓 → 0%
```

`risk.yaml` 的 `weight_cap` 仍是**永不突破的天花板**——分析师只能决定在护栏内用多少，
不能抬高护栏。清仓路径不自动下单，仍走既有的 Chief 提案 + 人工审批。

## Capabilities

### New Capabilities
- `sector/chain-layers`: 产业链分层的定义契约——层的三重职责、层与 subgroup 的分工、
  layer key 与历史别名的解析规则、单票层的截面降级行为。
- `sector/layer-analyst`: 层级子行业分析师——输入契约、配置结论的取值与含义、
  同层选股的证据优先级、结论到预算使用率的映射与护栏不变式。

### Modified Capabilities
（无：`openspec/specs/` 当前为空，本项目尚未建立主 spec。）

## Impact

**配置（真源）**
- `config/sectors/ai_hardware.yaml`（1980 行）：层结构重排、claims 按「被判断主体」重挂、
  witness_roster 逐字保留（跨层不重切）、structure_notes 重挂、新票入表、电力层 `claims: []`
  显式留空并记录原因
- `config/risk.yaml`：`sector_layer_caps.ai_hardware` 由 6 条重切为 8 条
- `config/knowledge/`：`HBM存储.md` / `先进封装代工.md` 的归属层变更；光互联/铜连接/衬底三份
  笔记合并挂到新的互联层
- `config/pead.yaml`：`sector_review` 段新增层级分析师开关

**代码**
- `src/ats/schemas/sector.py`：`SectorLayer` 加 `legacy_keys`；新增 `LayerVerdict` 与
  `SectorReview.layer_verdicts`
- `src/ats/agents/sector/`：新增 `layer_review.py`（编排）；`cross_section.py` 接受预算使用率；
  `assemble.py` 的 `_chain_evidence` 拆成按层产出；`review.py` 改为消费层级结论
- `src/ats/chain/induction.py`：`claim_proposals.layer_hint` 的历史提议走 `legacy_keys` 解析
- **证据引擎本体不改**：`corroborate.py` 的三道闸/去重/立场加权、`observer.py` 的
  `concept_menu` 与抽取逻辑一行不动——本次动的是**命题的层归属**与**证据块的作用域**
- `src/ats/skills/`：新增 `layer-analyst/SKILL.md`；`sector-analyst/SKILL.md` 收窄为轮动
- `src/ats/runtime/cli.py`：`ats sector layer <name> [--layer KEY]`
- `src/ats/memory/store.py`：层级结论落库 + 历史 layer key 的别名解析

**数据/历史**
- sqlite `claim_assessments.layer`、`sector_reviews` 的历史层键通过 `legacy_keys` 解析，不改写旧行
- 每周 yfinance/consensus 调用量增加 6 票（SNDK/STX/WDC/ETN/GEV/BE）

**成本**
- 8 次层级合成（上下文各自小得多）+ 1 次轮动合成，替代现在的单次大合成

**文档**
- `docs/SECTOR_ANALYST.md`、`docs/CHAIN_EVIDENCE.md`、`docs/RISK_SYSTEM.md`、`README.md`
  中的 L1-L6 表述全部过期
