## REMOVED Requirements

### Requirement: 层级评审的输入契约

**Reason**: 该需求随 `sector/layer-analyst` 一并迁入 `agent/layer-analyst`（角色契约归 `agent/` 域，`sector/` 只留产业链领域知识）。输入契约本身继续有效，但它是角色契约而非产业链领域知识，留在本能力会让「角色契约在 `agent/`、领域知识在 `sector/`」的归域约定出现第二个落点。

**Migration**: 内容原样承接为 `agent/layer-analyst` 的「层级评审的输入契约」，表述中「层级配置结论」改为「层级状态判断」。无需迁移消费方：层级评审的调用方不变。

### Requirement: 层级配置结论

**Reason**: 目标架构（§7.2、§14.4）把配置权收口到行业分析师，层级分析师不得给出超配 / 标配 / 低配 / 清仓结论。本需求是冲突的根源——它要求层级分析师直接产出配置结论并附带 confidence 与逐条议题归因，当前实现即 `schemas/sector.py:277` 的 `LayerVerdict.allocation` 与 `agents/sector/layer_review.py:213-220`。

**Migration**: 配置结论改由 `agent/sector-allocation` 的「行业分析师独占三级配置权」产出；层级侧改为产出 `expanding | steady | contracting | unclear` 的状态判断（见 `agent/layer-analyst` 的「层级状态判断必须锚定议题证据并区分证据缺失与无命题」）。历史 `allocation` 字段保留为遗留数据但不再被任何消费方计入分配，退出登记进 `config/workflow/legacy_retirement.yaml` 的 Phase D 段。

### Requirement: 反转触发条件

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。该需求约束的是层级判断的可证伪性，属于角色契约。

**Migration**: 原样承接为 `agent/layer-analyst` 的「反转触发条件」，仅把「层级配置结论」改为「层级状态判断」。

### Requirement: 同层选股

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。同时其语义被收窄：原需求允许逐票取舍直接流向配置，而配置权已归行业分析师。

**Migration**: 承接为 `agent/layer-analyst` 的「层内排序是相对证据排序，不是资金分配」，明确排序只表达相对强弱、不携带权重或金额；权重由 `agent/sector-allocation` 决定。

### Requirement: 配置结论绑定预算使用率

**Reason**: 预算使用率是资金分配动作，随配置权一并移出层级分析师。层级分析师不具备也不应获得「该给多少钱」的映射能力。

**Migration**: 承接为 `agent/sector-allocation` 的「配置结论绑定预算且护栏只降不升」，映射关系（超配 100% / 标配 60% / 低配 30% / 清仓 0%）与 `risk.yaml` 的 `layer_utilization` 配置保持不变，仅执行主体由层级评审改为行业评审。

### Requirement: 护栏不变式

**Reason**: 同上——护栏约束的是配置结果，随配置权移出层级分析师。

**Migration**: 承接为 `agent/sector-allocation` 的同一护栏要求（只能下调、不越 `weight_cap`、使用率钳制到 100%）；其「层级评审失败」场景拆为两处：失败留痕归 `agent/layer-analyst` 的「层级评审失败不得降级为配置结论」，降级默认归 `agent/sector-allocation` 的「层级分析缺失时降级并留痕」。

### Requirement: 跨层轮动消费层级结论

**Reason**: 轮动的产出主体是行业分析师而非层级分析师，该需求描述的是行业分析师的职责，放在层级能力里会造成「谁负责轮动」的二义。

**Migration**: 承接为 `agent/sector-allocation` 的「跨层轮动消费层级结论而不推翻它」，并把「上下文 SHALL NOT 包含宏观判断」并入该能力的「行业分析师只消费层级投影与共享事实」。

### Requirement: 每层一份报告，结论先行

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。报告结构约束属于角色产出契约。

**Migration**: 原样承接为 `agent/layer-analyst` 的同名需求，报告中「层配置结论（含预算）」改为「层状态判断」，预算明细改由行业报告承载。

### Requirement: 议题结论必须附证据链

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。

**Migration**: 原样承接，无语义变化。

### Requirement: 截面明细须含相对命题读数

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。

**Migration**: 原样承接，无语义变化。

### Requirement: 候选追踪议题

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。

**Migration**: 原样承接；因层级不再产出配置，原「不影响本期配置结论、截面排序或权重计算」改为「不影响本期的层级状态判断、截面排序或投影 payload」。

### Requirement: 层级结论的留痕与注回

**Reason**: 随层级分析师迁入 `agent/layer-analyst`。原需求允许「最新结论注入下游智能体上下文」这种自由文本注回，与目标架构「跨角色读取必须经投影」不一致。

**Migration**: 承接为 `agent/layer-analyst` 的「层级结论的留痕与投影注回」，注回方式由自由文本改为经 `task_projection_envelopes` 读取，并要求留下投影引用（内容哈希与 as-of）以便追溯。
