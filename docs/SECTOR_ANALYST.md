# 行业分析师（sector_analyst）

站在**行业视角**、自下而上聚合整条产业链的分析师 Agent。与 PEAD 的**企业级**基本面分析互补：PEAD 盯单个标的的财报预期差，行业分析师看整条链条的景气度、供需、定价权、资金流，产出**层间轮动**和**个股增持/持有/减持**建议。

MVP 覆盖 **AI 硬件产业链**，按 L1→L8 分层（需求沿 L1 向 L8 传导）。

> **2026-08-20 重构**：六层拆成八层，并新增**层级子行业分析师**。此前的输出是「这层景气吗」
> （0-100 分 + bullish/neutral/bearish），**没有仓位含义**；现在每层产出**配置结论**
> （超配/标配/低配/清仓），并直接决定该层的预算使用率。

---

## 一、功能（Feature）

一次运行产出一份**周度行业评审**，包含：

1. **逐层配置结论（L1-L8）**：每层给出
   - **配置**：超配 / 标配 / 低配 / 清仓 —— **它决定本层预算的使用率**
   - **confidence**（0-1）与**周期位置**（依据限定为产业证据）
   - **议题归因**：每条 common 命题一行，说明它对本层配置的含义
   - **反转触发条件**：下一轮能直接核对的观察项
   - **层内选股**：本层每只标的一条 stance + 理由
2. **跨层轮动建议**：利润池正从哪层迁移到哪层，加/减哪层。
3. **行业 regime**：一句话自包含的行业状态判断。

**AI 硬件的 L1-L8 分层**：

| 层 | 含义 | 代表公司 | subgroup |
|---|---|---|---|
| L1 | AI 应用层（Token 经济） | GOOG + OpenAI/Anthropic（非上市） | — |
| L2 | 云服务层（算力租用） | MSFT / AMZN / META / GOOG / CRWV | — |
| L3 | 数据中心电力与冷却 | VRT / ETN / GEV / BE | —（不分组） |
| L4 | 互联与网络 | COHR / LITE / AAOI / CRDO / AXT | 光互联/铜连接/衬底 |
| L5 | 芯片设计 | NVDA / AMD / AVGO / MRVL | — |
| L6 | 存储 | SKHY / MU / 三星 / SNDK / STX / WDC | HBM/常规DRAM/NAND/HDD |
| L7 | 代工与先进封装 | TSM | — |
| L8 | 半导体设备 | ASML / AMAT / LRCX / KLAC | — |

### 为什么这么分层

层同时承担三件事，好的切法要三者**同时**成立：① 需求传导链上的位置 ② 截面 cohort
（z 分的作用域）③ 风险预算 / 相关簇单元。

**拆层的判据是「定价机制」，不是「产业环节」。** 产业环节回答「这东西怎么做出来的」，
而层要回答「这两家放一起比 PEG/毛利率有没有意义」「它们会不会一起跌」。四种机制：

| 机制 | 价格由谁定 | 典型 | 财务特征 |
|---|---|---|---|
| 产能垄断型服务 | 卖方 | TSM 先进制程 | 毛利高且稳 |
| 商品化周期品 | 供需（bit 现货价） | DRAM/NAND/HDD | 毛利大幅摆动 |
| 技术代际替代 | 溢价窗口 | 光模块 1.6T/CPO | 毛利随代际起落 |
| 机电装机量 | 招标/长单 | VRT/ETN/GEV | 毛利平稳靠量 |

反例最能说明问题：**台积电与美光的毛利率差不来自谁经营得更好，来自商业模式**——
而 z 分会把它读成「台积电的质量因子更强」，一个每周都会出现的假信号。

**层是风险预算与景气判断单元；subgroup 只是叙述与比较的分组标签。** z 分在**整层**
统一计算，subgroup 不参与标准化（组内样本太小会退化）。代价是跨 subgroup 的名次先后
可能只是两组的因子分布不同，所以分析师不得仅凭名次断言跨组优劣。

**层键更名后历史靠 `legacy_keys` 解析，旧记录一律不改写**：一行写着 `L5_fab` 的结论是在
「代工+存储」的合并口径下做出的，改写它等于伪造当时的判断范围。拆分产生一对多，
按 basket 里的标的挑对应的那一半。

---

## 二、设计思路（Design）

### 数据流

```
config/sectors/ai_hardware.yaml（分层+成分股，用户可校正的唯一真源）
        ↓
每层并行：
  ① fetch_factors + rank_cohort   纯代码 ────────→ 量化 basket
  ② structure.assess              KB + relative 读数 → tech_tenor/moat_pricing → 混合重排
  ③ layer_review.run              common 结论（该给多少钱）
                                  relative 读数（选谁）
                                  判据笔记 + 混合 basket
                                  上一轮本层 verdict ──→ LayerVerdict
                                                        └→ 使用率 → 重算权重（排名不变）
全部层完成后：
  ④ rotation.run                  8 条 LayerVerdict ──→ 轮动建议 + 一致性检查
        ↓
SectorReview → ① sqlite sector_reviews 表（payload 内含 layer_verdicts）
             → ② Obsidian 行业分析-AI硬件-<日期>.md
             → ③ 注回 PEAD prep/monitor 上下文
```

⚠️ **三个阶段都不吃宏观**，② 不吃 common，③ 的两类证据分开定向 —— 见下。

### 关键设计决策

- **两类议题各自定向**（这条分工本来就在代码里，只是此前没写成设计）：

  | 命题类型 | 去向 | 回答 | 形态 |
  |---|---|---|---|
  | `common` | 层级分析师 | 这一层该给多少钱 | 结论 + 覆盖率 + basis |
  | `relative` | 结构分析师 | 层内怎么排序 | 逐家读数 → 结构因子 |
  | `relative` | 层级分析师的选股段 | **为什么**选它 | 读数原文，不是因子分数 |

  `chain/factor_evidence.py` 一行写死 `kind != "relative"` 就跳过，所以结构因子只吃
  relative。**common 不得进结构因子**：那等于让「行业需求好」改写「谁在赢」，正是
  `CHAIN_EVIDENCE.md` 不变式 2 要挡的。relative 走两条路不是重复计价——一个压成数进
  复合排名，一个读原文写理由，冲突时以读数原文为准。

- **宏观退出整条行业链路**：Chief 已经读宏观 `sector_tilts`，行业这边再吃一遍会让同一个
  判断被计两次；更糟的是**归因污染**——层级结论变差时分不清是产业景气变差还是宏观变差，
  而那两件事对仓位的含义相反（减这一层 vs 减总仓位）。周期位置改用产业证据
  （capex 指引 / 订单交期 / 库存 / 产能投放）。

- **改成两阶段，但轮动仍在同一上下文**：原来的理由是「层间轮动本质是跨层比较，必须在同一
  上下文里完成」——这条**仍然成立**，只是轮动现在比较的是 8 条紧凑结论而不是全部原始素材。
  层级判断反而必须分开：每层只看自己的证据，上下文从单次 128.9k 字符降到每层 5~10k。

- **结构分析师不并入层级分析师**（上下文高度重叠、能省 8 次调用）：`ats sector kbperturb`
  这件仪器专门检验「知识库是否真的在起作用」，它依赖结构打分是一个**可单独消融的阶段**。
  合并会让这个检验失去对照组。代价是结构分析师改吃**上一轮**的 LayerVerdict（周度节奏下
  是合法先验），提示词里明确标注它是上一轮的。

- **「本层无命题」与「证据缺失」必须分开**：前者是**配置缺口**（该建的命题没建），
  后者是**证据缺口**（本季没人发声）。两者都给标配 + confidence ≤0.3，但混为一谈会让
  配置缺口被当成「行业没消息」而永远不被发现。无命题时 confidence 上限**在代码里钳**。
- **限速优先（yfinance 易被限流）**：universe ~22 家 × 多端点很容易 429。方案是 **1 次批量 `yf.download`** 拿全 universe 收盘价（动量/距高全从这算）+ 轻量 `get_info`（限速 0.8s/票，只取估值/毛利）+ **consensus 只拉 PEAD 标的**（4 端点/票太重）。约 45 次调用/周 vs 无脑做法 175 次。全部走 `safe_fetch`，某票限流退化成 `(n/a)` 而非炸掉整跑。
- **校准纪律写进 skill**：多数周是"无变化"就直说；conviction 默认 ≤0.6，多源证据同向才上调；数据缺失的票强制 stance=持有、conviction≤0.3；标 `[PEAD]` 的票必须与其活体档案结论一致或说明分歧。
- **闭环注回 PEAD**：最新评审的 regime + 该票所在层评估 + 个股 call 注入 PEAD prep 的 `industry_context`；monitor 上下文加 1-3 行 regime 提示帮 Flash 校准 materiality（如"L3 光互联已是共识瓶颈"会让又一条光互联利好判低分）。因为 prep 通过 `prior_narrative` 闭环传播，**注入一次即全程可见**。
- **不上 RAG / 不做蒸馏缓存**：8 篇静态小库文件夹直读足够；周度低频，直接注入 Opus 成本可忽略。
- **LLM 失败不落库**：单层失败回退到该层上一次的 LayerVerdict（没有则拒绝猜测，
  confidence=0），不中止其余层；**没有任何层产出结论时整轮不落库**——否则会用「本轮没跑成」
  替换掉「上周的真实判断」，而两者在下游读者眼里长得一样。轮动失败只让周报缺一段。

### 关键文件

| 文件 | 职责 |
|---|---|
| `config/sectors/ai_hardware.yaml` | 分层+成分股定义（**用户校正的唯一真源**） |
| `src/ats/schemas/sector.py` | SectorConfig / SectorReview 等 schema |
| `src/ats/data/sector_snapshot.py` | 批量价格（1 次 download）+ 动量/距高 |
| `src/ats/agents/sector/assemble.py` | 多源上下文组装（核心） |
| `src/ats/agents/sector/review.py` | 编排：逐层（截面→结构→层级）→ 跨层轮动 |
| `src/ats/agents/sector/layer_review.py` | **层级子行业分析师**：按层组装 → LayerVerdict |
| `src/ats/agents/sector/rotation.py` | 跨层轮动：只消费 8 条 LayerVerdict |
| `src/ats/skills/layer-analyst/SKILL.md` | 层级分析师提示词（四档判据 + 校准纪律） |
| `scripts/verify_layer_migration.py` | 分层重构的不变量校验（改分层时先跑它） |
| `src/ats/agents/sector/report.py` | Obsidian markdown 渲染/写入 |
| `src/ats/agents/sector/context.py` | 注回 PEAD 的 prep_block/monitor_hint |
| `src/ats/agents/sector/cross_section.py` | 截面选股：层内排序 + 权重分配（确定性，无 LLM） |
| `src/ats/agents/sector/structure.py` | 结构层：KB 定性评审 → tech_tenor / moat_pricing |
| `src/ats/skills/sector-analyst/SKILL.md` | 合成提示词（方法论+校准纪律） |

### 截面选股层（谁 / 多少）

周度评审回答"哪层景气"，但不回答"这层里买谁、买多少"。截面层补这一课：在**同层
cohort 内**把几个因子标准化成 z 分（Barra-lite），复合成排名，再按风险预算转成权重。

- **量化因子**：growth 0.25 / quality 0.20 / value 0.25 / momentum 0.10 / revisions 0.20
- **结构因子**（`--structure`，KB 定性）：`tech_tenor` 技术时间朝向、`moat_pricing`
  护城河/份额/定价权，各 0.20；开启时量化整体压到 60%、结构占 40%
- 层预算来自 `config/risk.yaml` 的 `sector_layer_caps`；`cohort_extra` 的票参与排名
  但不占预算

### 配置结论 → 预算使用率（谁决定多少钱）

层级配置结论映射为**本层预算使用率**，截面 basket 的权重之和 =
`weight_cap × clamp(使用率, 0, 1)`：

| 结论 | 使用率 | L6 存储（cap 25%）实际预算 |
|---|---|---|
| 超配 | 100% | 25.0% NAV |
| 标配 | 60% | 15.0% NAV |
| 低配 | 30% | 7.5% NAV |
| 清仓 | 0% | 0 |

**护栏不变式（这条不能松）**：

- `weight_cap` 仍是**永不突破的天花板**。使用率只允许把层**往下调**，钳制在**代码里**做
  ——配置把「超配」误写成 1.5 也不会抬高上限。
- **风控检查读静态 cap，预算分配读调整后的 cap。** 使用率答的是「新增资金投多少」，
  breach 答的是「已有持仓越没越界」。共用一个数会让一条「低配」把满仓但合规的层瞬间
  判成超限、触发不必要的减仓——那是**用建议信号冒充风险事件**。
- 未知档位回落到**标配**那一档，不是满额：「读不出这条结论」不能花得像「高信心买入」。
- 清仓不自动下单，仍走 Chief 提案 + 人工审批。
- 映射表在 `config/risk.yaml` 的 `layer_utilization`（治理归配置），结论由模型给出并
  连同证据归因一起落库（判断归模型且留痕）。
- 回滚开关：`config/pead.yaml` 的 `bind_layer_budget: false` → 使用率恒为 1.0。

### 跨层上限：拆层不得放大总敞口

拆层有一个机械性副作用：`L5_fab ≤30%` 变成两个各自独立的 cap，两层同时满仓就能到 45%
——**那等于在重构的掩护下放松护栏**。做法是**加一层新护栏而不是撤销旧的**：

| group | 成员 | 上限 | 来源 |
|---|---|---|---|
| `dc_infra` | L3 电力冷却 + L4 互联与网络 | 15%（硬顶 20%） | 拆分前的 `L3_dc_infra` |
| `fab_memory` | L6 存储 + L7 代工封装 | 30% | 拆分前的 `L5_fab` |

子层 cap 之和 > group cap 是**有意的**：允许在成员间倾斜，合计仍被卡住。组越限时
**组内每一层都进 `blocked_layers`**——只封「组」封不住新买单，下游按层键判断。

> **当前局限**：单票层的预算不是层上限的全额，而是 `single_name_cap_frac`（40%）那一档
> ——单票拿不到超过层上限 40% 的仓位，而它是唯一的票，溢出无处可去，**剩下 60% 不分配**。
> 这是既有行为但它是静默的，别把 `weight_cap` 当成单票层会用满的数。

---

## 三、使用指南（Usage）

### 命令

```bash
# 免费检查数据组装（不打 LLM，看每票哪些字段 n/a + 完整 prompt）
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector probe ai_hardware
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector probe ai_hardware --offline  # 连 yfinance 都不打

# 真跑一次评审（Opus 合成 + 写 Obsidian 报告 + 落库）
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector review ai_hardware
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector review ai_hardware --no-llm      # 只组装+stub，不花钱
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector review ai_hardware --no-report    # 不写 Obsidian

# 只跑某一层（或全部层）的层级评审：配置结论 + 层内选股
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector layer ai_hardware --layer L6_memory
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector layer ai_hardware --layer all

# 看最新评审 + 历史（含各层配置结论与换算出的预算）
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector show ai_hardware
```

> 改分层结构时**先跑不变量校验**，别靠肉眼读 2000 行 diff：
> ```bash
> .venv/bin/python scripts/verify_layer_migration.py <改前.yaml> config/sectors/ai_hardware.yaml \
>     --new-tickers SNDK,STX,WDC
> ```
> 它比对命题 id / concepts+expect_from / witnesses / 标的并集 / 笔记路径 /
> **每票的 concept_menu 键集合**。最后一项抓的是最阴的那种错：碰坏一条命题的证人声明
> **不会报错**，症状是那家公司的观测静默地全部未映射。

### 自动调度

`config/pead.yaml` 的 `sector_review` 段控制：`enabled`、`sectors`、`weekday`（5=周六）、`inject_prep`、`inject_monitor`。调度器的独立 `weekly_review` job 按 `config/settings.yaml` 的 `weekly_review_at` / `weekly_review_tz`（当前为周六 08:50，Asia/Shanghai）先跑宏观、再跑行业和截面重排；它不属于每日 `_daily` 级联，也不依赖 NYSE 交易日。若机器睡眠或 daemon 未运行而错过该时点，依次执行：

```bash
ats macro review macro
ats sector review ai_hardware
ats chief run --channel feishu_bot  # 可选：让 Chief 读取两份新报告；默认 dry-run，仍须人工审批
```

行业视角变化是周级的，日级刷新没意义且费 token，所以默认周更。

### 增删关注标的

编辑 `config/sectors/ai_hardware.yaml` 的对应层 `tickers`，一行一个 `{symbol, note}`。**注意**：
- **symbol 必须是 yfinance 能识别的交易代码**（如 Marvell 是 `MRVL` 不是 MVRL；韩股 `000660.KS`、日股 `8035.T`）。
- 这个 yaml 是**唯一真源**，层 key 会被 LLM 逐字回显，你的改动自动传播到报告和注入，无需改代码。
- 标 `TODO 用户确认` 的归层请校正（如 CRWV/TEL）。
- **新增标的默认没有可归属维度**：它不在任何命题的证人声明里 → `concept_menu` 返回空菜单
  → 它的观测 100% 未映射。补声明前走**两步法**（先观测再声明），见
  [`docs/CHAIN_EVIDENCE.md`](CHAIN_EVIDENCE.md)。

### 普通成分股 vs PEAD 标的

- **PEAD 标的**（有 `config/pead/<SYM>.yaml`）：行业分析会额外读它的活体档案叙事+Scorecard，报告里加粗标 `[PEAD]`，个股 call 会与档案结论对齐。
- **普通成分股**：只吃轻量快照 + 行业评审，不注入 dossier 深度。若想让某成分股进 PEAD 深度框架，照 `config/pead/000660.KS.yaml`（SK Hynix）建一份配置即可。

### 成本

调用数从 ~9 增至 ~17（8 次结构 + 8 次层级 + 1 次轮动），但每层上下文比原来的全行业
上下文小得多：证据块每层 common 4.3k~9.6k 字符 + relative 0.8k~1.5k，对比原来单次
128.9k。总 token 量与改造前同量级。注入 PEAD 的增量 ≤300 tokens。
每周 yfinance 调用随宇宙从 25 只增至 31 只。

### 三路输出去向

1. **SQLite** `var/ats.sqlite` 的 `sector_reviews` 表（支持历史对比/回测）。
2. **Obsidian** `<output_dir>/行业分析-AI硬件-<日期>.md`（`output_dir` 在 sector 配置里；永远新建文件、不动你的手写笔记；同日重跑覆盖）。
3. **注回 PEAD**：下一次 `pead prep`/`pead monitor` 自动带上最新行业评审的相关块。
