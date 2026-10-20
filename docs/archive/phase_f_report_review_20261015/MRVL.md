# MRVL Routine 报告审阅稿（actual-MRVL-r2）

> 这是一份**待你签核**的未签核报告的可读版本。内容取自报告里独立回放那次执行的发布结果。

## 一句话

本次 Routine 使用 **1 篇文章**（1 条信息简报，共 **2 条事实变化**），生成 **2 条定性更新**：新增 2。另有 2 条此前已存在的简报、0 条此前已存在的定性更新属于基线，本次没有生成。

## 基本信息

| 项目 | 内容 |
|---|---|
| 标的 / 范围 | MRVL（单个实体） |
| 数据截止时点 | 2026-10-14T02:46:31.536419+00:00 |
| 报告编号 | `actual-MRVL-r2` |
| 内容哈希（前12位） | `40f0d73f3ff4` |
| 你之前签核的版本 | `actual-MRVL`，哈希前12位 `71b9424cfec1` |
| 当前状态 | unsigned（未签核） |

## 与你之前签核版本的对照

| 检查 | 结果 |
|---|---|
| 「new」执行：7 条断言结果 | 全部相同 |
| 「new」执行：11 份已发布投影载荷 | 逐字相同 |
| 「replay」执行：7 条断言结果 | 全部相同 |
| 「replay」执行：11 份已发布投影载荷 | 逐字相同 |
| 仅有的差异 | 运行编号（agent_run_id）；Fundamental 投影记录的输出上限 4096 → 8192（统一标准） |

## 一、信息简报（本次使用的）

### 简报 1：MRVL 信息材料更新

- 发布日期：2026-10-10　相关度：medium　置信度：0.95　独立来源数：未评估
- 来源：ibkr_news
- 摘要：来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：根据提交给美国证券交易委员会的Form 4，Marvell Technology Inc（MRVL）副总裁Justin Scarpulla于2026年10月8日以每股273.59美元卖出1,100股，交易总值300,949美元，交易后直接持股909股。；来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：Form 4历史活动表显示，2026年4月至10月期间，MRVL总裁Koopmans Chris、董事长Murphy Matthew J、高管Bharathi Sandeep、CFO Durn Daniel、CFO Meintjes Willem A、副总裁Casper M
- 事实变化：
  - 来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：根据提交给美国证券交易委员会的Form 4，Marvell Technology Inc（MRVL）副总裁Justin Scarpulla于2026年10月8日以每股273.59美元卖出1,100股，交易总值300,949美元，交易后直接持股909股。
  - 来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：Form 4历史活动表显示，2026年4月至10月期间，MRVL总裁Koopmans Chris、董事长Murphy Matthew J、高管Bharathi Sandeep、CFO Durn Daniel、CFO Meintjes Willem A、副总裁Casper Mark等内部人士多次卖出公司股票，价格从约130美元至299美元不等，部分交易标注为按Rule 10(b)5-1计划执行。

  - 原文 `DOWJONES:managed-ibkr_news-DJ-N-DJ-N-1f878d55-2699cdb0a600:research_article@121a5d7af94a9bb1`（来源 Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4）；发布 2026-10-10；全文完整度 full）支持事实 #0、#1
    - 原文摘录（前 300 字）：SOURCE: Form 4 ISSUER: Marvell Technology Inc SYMBOL: MRVL FILER: Scarpulla Justin TITLE: Vice President DATE TRANSACTION SHARES PRICE VALUE 10/8/26 Sale 1,100 $273.59 $300,949 OWNERSHIP: 909 (Direct) --------------------------------------- HISTORICAL ACTIVITY -------------------------------------- …

- 待核验：来源陈述及标的影响尚需独立核验；多篇材料不等于同事件独立佐证；事件发生时点未独立核验，event_time 使用原始发布时间代理

此前已存在、本次没有使用的简报：

- 2026-10-07　相关度 low　Market Talk 报道 SpaceX 计划投入约 $40B 采购 Nvidia 芯片用于 AI，属计划性市场评论，非确认订单。对 MRVL 无直接关联——SpaceX 非其典…
- 2026-10-08　相关度 medium　TSMC 10月营收受AI需求持续增长驱动表现强劲，为AI产业链提供正面信号；但AMD等芯片股当日反而下跌，属获利了结/仓位因素而非基本面恶化。同时Oracle、Broadcom、…

## 二、定性更新（2 条，Fundamental 例行更新对每条事实变化的归类）

分类含义：确认 = 与现有预期基线一致；否定 = 与基线矛盾；新增 = 基线未跟踪的新主题；待验证 = 相关但暂不能判定。

| # | 分类 | 内容 |
|---|---|---|
| 1 | 新增 | 来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：根据提交给美国证券交易委员会的Form 4，Marvell Technology Inc（MRVL）副总裁Justin Scarpulla于2026年10月8日以每股273.59美元卖出1,100股，交易总值300,949美元，交易后直接持股909股。 |
| 2 | 新增 | 来源信息（Dow Jones Newswires（数据来源：The Washington Service / SEC Form 4））：Form 4历史活动表显示，2026年4月至10月期间，MRVL总裁Koopmans Chris、董事长Murphy Matthew J、高管Bharathi Sandeep、CFO Durn Daniel、CFO Meintjes Willem A、副总裁Casper Mark等内部人士多次卖出公司股票，价格从约130美元至299美元不等，部分交易标注为按Rule 10(b)5-1计划执行。 |

## 三、自动检查结果（独立回放那次执行）

| 维度 | 结果 | 说明 |
|---|---|---|
| 输入固定（回放不重新取数） | 通过 | — |
| 调度（无重复/无遗漏） | 通过 | — |
| 分析产出（时效、来源、结构） · Information 简报 | 通过 | — |
| 分析产出（时效、来源、结构） · Fundamental 更新 | 通过 | — |
| 风控 | 不适用 | selected research/decision scope does not execute this boundary |
| 审批 | 不适用 | selected research/decision scope does not execute this boundary |
| 归因 | 不适用 | selected research/decision scope does not execute this boundary |

风控、审批、归因在本范围内不执行，所以是"不适用"，不是未测试。

## 四、这份报告能证明什么，不能证明什么

- 能证明：按固定数据和固定的模型回答，新入口独立执行与离线回放都得到上面的结果，来源引用、结构、时效和血缘通过检查。
- 不能证明：完整的 TARGET §9.2 业务能力（市场隐含预期、整合叙事、逐 KPI 变化解释、可证伪条件）。这些按你的裁决属于 Phase F 后补（父 change 7.5）。
- 签核不构成部署授权，也不授权任何券商写入。
