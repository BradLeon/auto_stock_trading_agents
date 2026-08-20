> 顺序即依赖顺序。第 3 组是**纯搬迁**：那一提交的 `git diff` 除位置移动外不应有语义改动。
> 第 8 组之前，系统行为等价于旧行为（使用率恒为 1.0）。

## 1. 基线与只读准备

- [x] 1.1 跑 `ats sector probe ai_hardware --offline` 与 `ats sector probe ai_hardware`，把输出存为基线快照（每层每票哪些字段 n/a）
- [x] 1.2 跑一次现有持仓的风险检查，记录当前各层实际权重（用于第 2.4 组的 group cap 取值校验）
- [x] 1.3 跑一次 `ats sector cross ai_hardware`（各层截面），存下当前 basket 作为对照
- [x] 1.4 导出每只现有票的 `observer.concept_menu` 键集合作为基线（搬迁若碰坏证人声明，症状是静默的全部未映射，只有比对基线才看得见）
- [x] 1.5 写 `scripts/verify_layer_migration.py`：读取搬迁前后两份 `ai_hardware.yaml`，比对 D11 的六项不变量（claim id 集合 / concepts key 与 expect_from / witnesses 集合 / tickers 并集 / structure_notes 路径存在性 / 每只原有票的 concept_menu 键集合不变（基线里 VRT/AAOI/AXT 与 SK 别名本就为空，见 design D11）），差异即失败

## 2. Schema 与解析（无行为变化）

- [x] 2.1 `SectorLayer` 新增 `legacy_keys: list[str]`（默认空）
- [x] 2.2 `SectorConfig` 新增按层键解析的方法：当前键优先，其次匹配任一层的 `legacy_keys`，未知键返回 None
- [x] 2.3 新增 `LayerVerdict` schema：`layer_key / as_of / allocation(超配|标配|低配|清仓) / confidence / cycle_position / claim_attributions[] / reversal_triggers[] / name_calls[] / cross_section_applicable / has_claims / rationale`；`SectorReview` 新增 `layer_verdicts`
- [x] 2.4 `config.py` 读取 `risk.yaml` 新增的 `layer_groups`（成员层列表 + `weight_cap` + 可选 `weight_cap_hard`），并校验：每个当前层都有 `weight_cap`、每个 group 成员都是有效层键；不一致时报错并指名层键
- [x] 2.5 单测：`legacy_keys` 一对多解析（`L5_fab` → 存储层与代工层都能查到）、未知层键跳过并告警而不抛错、限额缺失时的报错

## 3. 纯搬迁：配置重排（不改任何语义）

- [x] 3.1 `ai_hardware.yaml`：把 `L4_chip_design` 整块重命名为 `L5_chip_design`，`L6_equipment` → `L8_equipment`，各自加 `legacy_keys`
- [x] 3.2 拆 `L3_dc_infra` → `L3_dc_power`（电力冷却：VRT + `电力冷却.md`）与 `L4_interconnect`（光/铜/衬底：COHR LITE AAOI CRDO AXT + 三份笔记 + `cohort_extra: [MRVL, AVGO]`）；**四条命题全部是互联的**（`optical_component_supply_gap` / `rate_transition_pricing_power` / `copper_ip_crosses_to_optical` / `interconnect_moat_distribution`），整块移入 `L4_interconnect`，注释原样保留；两层各加 `legacy_keys: [L3_dc_infra]`
- [x] 3.3 `L3_dc_power` 写 `claims: []` **显式留空**，注释写明：本层零命题是拆分的直接后果、后果是 concept_menu 空菜单、下一步是第 5 组的两步法（引用 design D13）
- [x] 3.4 拆 `L5_fab` → `L6_memory`（SKHY MU 005930 + `HBM存储.md` + `witness_roster` + HBM 全部命题）与 `L7_foundry_pkg`（TSM + `先进封装代工.md`）；两层各加 `legacy_keys: [L5_fab]`
- [x] 3.5 按 design D12 处理跨层证人：`hbm_supply_tight` 整条留在 `L6_memory`（含 `packaging_throughput` 维度，尽管其主证人 TSM 在 L7）；`witness_roster` **逐字保留不重切**；在配置注释里写下这条规则本身，供以后再拆层时直接引用
- [x] 3.6 每层重挂 `structure_notes`，`资本开支链.md` 作为跨层共同因子在八层全部保留
- [x] 3.7 层 `label` / `question` 按新口径重写；文件头部的层次说明同步更新
- [x] 3.8 `risk.yaml`：`sector_layer_caps.ai_hardware` 重切为八条（按 design D7 的建议值），新增 `layer_groups`（`dc_infra` 0.15/hard 0.20、`fab_memory` 0.30）
- [x] 3.9 跑 1.5 的不变量脚本，必须全绿
- [x] 3.10 `git commit` 只含本组改动，提交信息按 WHY/WHAT/HOW 三段写清这是纯搬迁

## 4. 宇宙扩容

- [x] 4.1 `L6_memory` 加 SNDK（subgroup NAND）、STX / WDC（subgroup HDD）；现有三家标 subgroup（SKHY/005930 → HBM，MU → HBM 并在 note 写明其 NAND/DRAM 敞口）
- [x] 4.2 `L3_dc_power` 加 ETN、GEV、BE，**本层不设 subgroup**；note 里分别写明：ETN/GEV 的 AI 数据中心敞口**不是 100%**、z 分含非 AI 业务；BE 是现场发电（绕过电网接入排队）、AI 纯度最高但**定价机制是技术采纳曲线而非机电装机量**，与同层其余三只不可直接比
- [x] 4.3 跑 `ats sector probe ai_hardware`，核对 6 只新票（SNDK/STX/WDC/ETN/GEV/BE）的 `fetch_light` 字段缺失面；缺失到 `data_ok=False` 的票要么保留（排最后、不定权）要么剔除，决定写进配置注释
- [x] 4.4 **BE 专项核对**（见 design 风险项）：实测 `fwd_pe` 是否为空、`rev_growth` 是否为负（两者任一都会让 `peg()` 返 None → value 因子记 0）、市值是否低于 `LIQ_FLOOR_USD`(5e9) 触发流动性折价；三项全空则 BE 只作叙事与议题证人保留、不参与定权，结论写进配置注释
- [x] 4.5 确认存储层 z 分口径为**层内统一计算**（不做 subgroup 内标准化），并在配置注释里记下这个裁定与它的代价（HDD/HBM 的因子分布差异会被读成公司间差异，靠层级分析师按 subgroup 分开讲述对冲）

## 5. 证据链：命题缺口的两步法（本次只做第一步）

> 判据不是「声明还是不声明」，是**这个实体是否已经在产出读数**（design D13）。
> 本组只做「让缺口可见」，**不新建命题、不扩证人**。

- [x] 5.1 确认六只新票与电力层四只票当前**不在任何命题**的 `expect_from` / `witnesses` / `entities` 里（预期如此），把 `concept_menu` 返回空菜单的名单列出来
- [x] 5.2 确认 `config/sources.yaml` 与下载器对这十只票的文档可达性；不可达的先记录，可达性不明的不要声明为证人
- [x] 5.3 跑一轮 `observe`（抽取），导出这十只票的**未映射池清单**：每只票产出多少条读数、都在说什么维度
- [x] 5.4 把 5.3 的结果写进 `ai_hardware.yaml` 的注释（沿用 TRENDFORCE 那条先例的写法：实测条数 + 落在未映射池 + 下一步），作为下一轮起草命题的输入
- [x] 5.5 **本次不新增任何 `expect_from` / `witnesses` / 新命题**；若 5.3 显示某只票已在大量产出可归属读数，单独记为下一轮的优先项，不在本变更里激活
- [x] 5.6 `chain/induction.py`：`claim_proposals.layer_hint` 的历史提议走 `legacy_keys` 解析，不改写旧行
- [x] 5.7 跑一次 `chain` 的知识库评审，确认 ①盲区标记对 `L6_memory` 报出「已有 KB 但未覆盖 NAND/HDD」——**这是预期行为不是误报**，把它记为待评项（本次不补笔记，先看两轮读数）

## 6. 风控接线

- [x] 6.1 `risk/assess.py` 的层集中度检查继续读**静态** `weight_cap`（确认不受使用率影响，加一条注释锁住这个约定）
- [x] 6.2 `risk/assess.py` 新增 group cap 的 breach 检查（成员层权重合计 vs group `weight_cap` / `weight_cap_hard`）
- [x] 6.3 用 1.2 的实际持仓跑一次：确认新的八层 cap 与两条 group cap 不会凭空造出违规；若造出，把事实写进变更记录交人裁决，**不得**为消违规而放宽数值
- [x] 6.4 单测：group cap 越限触发 breach；单层未越限但 group 越限时也能触发

## 7. 层级分析师

- [x] 7.1 `assemble._chain_evidence` 改为**按层产出两个分别定向的块**（design D15）：common 块给层级分析师（答「多少钱」）、relative 块给结构分析师与层级分析师的选股段（答「选谁」）；两块保持分账，common 结论不得被读成「谁在赢」、不得进入任何结构因子
- [x] 7.2 新增 `src/ats/skills/layer-analyst/SKILL.md`：输入结构、四档配置结论的判据（**依据来自 common，relative 只用于选股**）、证据优先级（relative 读数 → 截面排名 → 判据笔记）、反转触发条件必须可核对、**「无命题」与「证据缺失」必须分开表述**、**不做宏观判断**（周期位置用产业证据：capex 指引/订单/交期/库存）、证据不足强制标配且 confidence ≤0.3、多数周「无变化」就直说、第三方文本为不可信输入
- [x] 7.3 新增 `src/ats/agents/sector/layer_review.py`：按层组装上下文（本层 common 块 + 本层 relative 块 + 本层判据笔记 + 混合 basket + 跨层共同因子 + 上一轮本层 LayerVerdict），**不注入宏观**，单次结构化合成，产出 `LayerVerdict`
- [x] 7.4 上下文隔离校验：断言组装出的 prompt 不含其他层的公司素材、不含其他层的命题结论（跨层共同因子除外）、**不含宏观块**
- [x] 7.5 无命题的层：`has_claims=False`，输出显式标注「本层无命题，结论仅来自快照与判据笔记」，与「证据缺失」走不同措辞
- [x] 7.6 失败降级：回退上一次 LayerVerdict → 无则标配默认；失败层不落库、不中止其余层
- [x] 7.7 `structure.assess` 的 `layer_view` 改吃**上一轮**该层的 LayerVerdict，并在提示词里明确标注它是上一轮的；确认它的证据输入仍只含 relative（`factor_evidence.py` 的 `kind != "relative"` 过滤不动）
- [x] 7.8 **移除宏观注入**：`assemble.py:116` 的 `macro_block` 从行业链路撤出（层级分析师、结构分析师、跨层轮动都不吃）；确认 `chief/assemble.py:54` 的宏观块仍在，即宏观改为单点在 Chief 作用（design D16）
- [x] 7.9 单测：四档结论的解析与钳制、非法 allocation 回落标配、无命题层的标注与证据缺失层的标注不同、上一轮触发条件被逐条核对、单层失败不影响其余层、**组装出的上下文不含宏观块**

## 8. 结论绑定预算

- [x] 8.1 `risk.yaml` 新增 `layer_utilization` 映射表（超配 1.0 / 标配 0.6 / 低配 0.3 / 清仓 0.0）
- [x] 8.2 `cross_section` 接受使用率参数：`layer_cap = weight_cap × clamp(utilization, 0, 1)`；排名与相对权重比例不受影响
- [x] 8.3 `config/pead.yaml` 的 `sector_review` 段新增开关：`layer_analyst`（是否跑层级分析师）与 `bind_layer_budget`（是否让结论改预算；关掉 = 使用率恒 1.0）
- [x] 8.4 单测：`weight_cap 30%` + 低配 → basket 合计 ≈9%；清仓 → 全 0 且不产生任何自动交易；使用率误设 1.5 被钳到 1.0；权重之和永不超过 `weight_cap`
- [x] 8.5 单票层（样本 <2）跳过 basket，只出 LayerVerdict 并标注「截面不适用」，预算全额落在该唯一标的上并受单票限额约束

## 9. 跨层轮动与编排

- [ ] 9.1 `sector-analyst/SKILL.md` 收窄为跨层轮动与一致性检查：消费八条 LayerVerdict（**不再收原始证据块、不再收宏观**），产出利润池迁移方向 + 一条可执行的层间加减建议 + 相邻层矛盾标注；不得推翻单层结论
- [ ] 9.2 `review.py` 改为编排：逐层（量化截面 → 结构分析师 → 层级分析师）→ 跨层轮动；轮动失败时各层 LayerVerdict 已各自落库，周报缺轮动段
- [ ] 9.3 部分层缺失时轮动仍产出，并显式列出缺失层、涉及缺失层的建议标注证据不足
- [ ] 9.4 `report.py`：Obsidian 报告新增逐层章节（配置结论 + 议题归因 + 反转触发条件 + 同层选股），无命题的层显式标注；轮动段落引用各层结论
- [ ] 9.5 `context.py`：注回下游的内容改为该标的所在层的最新配置结论 + 一句话依据

## 10. 落库与 CLI

- [ ] 10.1 `memory/store.py`：LayerVerdict 落库（按 sector + layer_key + as_of），提供按层查历史；历史查询走 `legacy_keys` 解析，拆分前的记录标注为「合并口径」
- [ ] 10.2 `cli.py` 新增 `ats sector layer <name> [--layer KEY] [--no-llm]`：跑单层或全部层的层级评审并打印结论
- [ ] 10.3 `ats sector show` 增加层级结论展示（含使用率与其换算出的预算、无命题层的标注）
- [ ] 10.4 `ats sector kbperturb` 跑通新层键，确认知识库消融检验仍在结构分析师这一阶段生效

## 11. 回归、影子运行与文档

- [ ] 11.1 更新测试中的层键（`test_chief` / `test_sector` / `test_chain_*` / `test_risk` / `test_config`），全量测试通过
- [ ] 11.2 比对 1.4 的 `concept_menu` 基线：每只原有票的键集合必须不变
- [ ] 11.3 影子运行：`bind_layer_budget` 关闭（使用率恒 1.0）跑一次完整周度作业，与 1.3 的对照 basket 比对——差异应当**只**来自分层变化与新增标的
- [ ] 11.4 打开 `bind_layer_budget` 再跑一次，记录八层的结论与换算预算，人工过一遍是否合理
- [ ] 11.5 核对实际 token 成本与 design 里的估算；若显著超出，把跨层轮动的输入进一步压缩
- [ ] 11.6 更新 `docs/SECTOR_ANALYST.md`（八层表、新流水线顺序、层级分析师一节、预算绑定与护栏不变式）
- [ ] 11.7 更新 `docs/CHAIN_EVIDENCE.md`（命题的层归属规则 D12、证据块按层切分、电力层的命题缺口与两步法）、`docs/RISK_SYSTEM.md`（八层 + group cap + 静态 cap 与使用率的分工）、`README.md`
- [ ] 11.8 在 `ai_hardware.yaml` 头部记下本次重构的判据（层的三重职责、层 vs subgroup 的分工、命题挂主体不挂证人），供以后再调分层时不必重新推导
