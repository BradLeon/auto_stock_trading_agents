# 行业分析师（sector_analyst）

站在**行业视角**、自下而上聚合整条产业链的分析师 Agent。与 PEAD 的**企业级**基本面分析互补：PEAD 盯单个标的的财报预期差，行业分析师看整条链条的景气度、供需、定价权、资金流，产出**层间轮动**和**个股增持/持有/减持**建议。

MVP 覆盖 **AI 硬件产业链**，按 L1→L6 分层（需求沿 L1 向 L6 传导）。

---

## 一、功能（Feature）

一次运行产出一份**周度行业评审**，包含：

1. **分层评审（L1-L6）**：每层给出
   - **景气度**（0-100 分）
   - **供需**（紧张/平衡/过剩 + 依据）
   - **定价权**（哪个环节集中利润）
   - **资金流**（以相对动量/估值扩张为 proxy）
   - **周期位置**（早/中/晚周期）
   - **信号**（bullish/neutral/bearish）
2. **层间轮动建议**：利润池正从哪层迁移到哪层，加/减哪层。
3. **个股观点**：universe 里每家公司一条 stance（增持/持有/减持）+ conviction + 理由，锚定实际数据。
4. **行业 regime**：一句话自包含的行业状态判断。

**AI 硬件的 L1-L6 分层**（seed 自用户的「半导体产业研究合集」）：

| 层 | 含义 | 代表公司 |
|---|---|---|
| L1 | AI 应用层（Token 经济） | GOOGL + OpenAI/Anthropic/xAI（非上市） |
| L2 | 云服务层（算力租用） | MSFT / AMZN / META / GOOGL / CRWV |
| L3 | 数据中心基建（电力/冷却/网络/光互联） | COHR / LITE / AAOI / VRT |
| L4 | 芯片设计 | NVDA / AMD / AVGO / MRVL |
| L5 | 芯片制造（Foundry+封装+存储） | TSM / MU / SK海力士 / 三星 |
| L6 | 半导体设备 | ASML / AMAT / LRCX / KLA / TEL |

---

## 二、设计思路（Design）

### 数据流

```
config/sectors/ai_hardware.yaml（分层+成分股，用户可校正的唯一真源）
        ↓
assemble.build() 上下文组装（纯代码，无 LLM）：
  · 静态：半导体产业研究合集（36k 字符上限）
  · 动态：1 次批量 yf.download 价格 + 轻量 get_info + consensus(仅 PEAD 标的)
  · PEAD：dossier 叙事尾部 + Scorecard + 近期 insight + 高分 triage 事件
        ↓
run_structured("sector_analyst" / Opus)  单次合成
        ↓
SectorReview → ① sqlite sector_reviews 表
             → ② Obsidian 行业分析-AI硬件-<日期>.md
             → ③ 注回 PEAD prep/monitor 上下文
```

### 关键设计决策

- **单次 Opus 合成，不做两阶段**：层间轮动本质是跨层比较，必须在同一上下文里完成；分层调用会把 36k 静态背景重复发 6 次、成本翻倍且无收益。上下文 ~50k 字符，成本约 **$0.3-0.6/次**。
- **限速优先（yfinance 易被限流）**：universe ~22 家 × 多端点很容易 429。方案是 **1 次批量 `yf.download`** 拿全 universe 收盘价（动量/距高全从这算）+ 轻量 `get_info`（限速 0.8s/票，只取估值/毛利）+ **consensus 只拉 PEAD 标的**（4 端点/票太重）。约 45 次调用/周 vs 无脑做法 175 次。全部走 `safe_fetch`，某票限流退化成 `(n/a)` 而非炸掉整跑。
- **校准纪律写进 skill**：多数周是"无变化"就直说；conviction 默认 ≤0.6，多源证据同向才上调；数据缺失的票强制 stance=持有、conviction≤0.3；标 `[PEAD]` 的票必须与其活体档案结论一致或说明分歧。
- **闭环注回 PEAD**：最新评审的 regime + 该票所在层评估 + 个股 call 注入 PEAD prep 的 `industry_context`；monitor 上下文加 1-3 行 regime 提示帮 Flash 校准 materiality（如"L3 光互联已是共识瓶颈"会让又一条光互联利好判低分）。因为 prep 通过 `prior_narrative` 闭环传播，**注入一次即全程可见**。
- **不上 RAG / 不做蒸馏缓存**：8 篇静态小库文件夹直读足够；周度低频，直接注入 Opus 成本可忽略。
- **LLM 失败不落库**：合成失败返回上一次 review（或 stub），绝不用 stub 覆盖 latest。

### 关键文件

| 文件 | 职责 |
|---|---|
| `config/sectors/ai_hardware.yaml` | 分层+成分股定义（**用户校正的唯一真源**） |
| `src/ats/schemas/sector.py` | SectorConfig / SectorReview 等 schema |
| `src/ats/data/sector_snapshot.py` | 批量价格（1 次 download）+ 动量/距高 |
| `src/ats/agents/sector/assemble.py` | 多源上下文组装（核心） |
| `src/ats/agents/sector/review.py` | 编排 + Opus 合成 + clamp/校验 |
| `src/ats/agents/sector/report.py` | Obsidian markdown 渲染/写入 |
| `src/ats/agents/sector/context.py` | 注回 PEAD 的 prep_block/monitor_hint |
| `src/ats/skills/sector-analyst/SKILL.md` | 合成提示词（方法论+校准纪律） |

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

# 看最新评审 + 历史
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli sector show ai_hardware
```

### 自动调度

`config/pead.yaml` 的 `sector_review` 段控制：`enabled`、`sectors`、`weekday`（0=周一）、`inject_prep`、`inject_monitor`。调度器每周一自动跑（在每日 `_daily` 里检查星期）。行业视角变化是周级的，日级刷新没意义且费 token，所以默认周更。

### 增删关注标的

编辑 `config/sectors/ai_hardware.yaml` 的对应层 `tickers`，一行一个 `{symbol, note}`。**注意**：
- **symbol 必须是 yfinance 能识别的交易代码**（如 Marvell 是 `MRVL` 不是 MVRL；韩股 `000660.KS`、日股 `8035.T`）。
- 这个 yaml 是**唯一真源**，层 key 会被 LLM 逐字回显，你的改动自动传播到报告和注入，无需改代码。
- 标 `TODO 用户确认` 的归层请校正（如 CRWV/VRT/TEL）。

### 普通成分股 vs PEAD 标的

- **PEAD 标的**（有 `config/pead/<SYM>.yaml`）：行业分析会额外读它的活体档案叙事+Scorecard，报告里加粗标 `[PEAD]`，个股 call 会与档案结论对齐。
- **普通成分股**：只吃轻量快照 + 行业评审，不注入 dossier 深度。若想让某成分股进 PEAD 深度框架，照 `config/pead/000660.KS.yaml`（SK Hynix）建一份配置即可。

### 成本

约 **$0.3-0.6/次**（单次 Opus，~50k 输入/3k 输出）。注入 PEAD 的增量 ≤300 tokens。限速设计后每周仅 ~45 次 yfinance 调用。

### 三路输出去向

1. **SQLite** `var/ats.sqlite` 的 `sector_reviews` 表（支持历史对比/回测）。
2. **Obsidian** `<output_dir>/行业分析-AI硬件-<日期>.md`（`output_dir` 在 sector 配置里；永远新建文件、不动你的手写笔记；同日重跑覆盖）。
3. **注回 PEAD**：下一次 `pead prep`/`pead monitor` 自动带上最新行业评审的相关块。
