## Purpose

信息分析师处理新闻、公告、研报、文章与电话会材料，输出中性可核验的 `InformationBrief`：发生了什么事实变化、可能影响谁、置信度多高、时效如何、还有什么待核验。它不判断买什么、买多少，也不做组合建议——投资判断属于基本面与主理人。

公开角色名不使用 News 或 Intelligence，因为它处理的是多类信息资产，而非单一新闻流。

## Requirements

### Requirement: 输入只能是已通过准入的文档与中性证据

信息分析师 SHALL 只消费已通过数据平台准入的文档、文档版本、chunk 或中性证据事实。它 SHALL NOT 在执行过程中直接调用新闻、研报、公告或检索 Provider，SHALL NOT 自行抓取未入库正文。

未通过准入或报告期 / 身份不明确的材料 SHALL NOT 进入信息分析师的上下文。

#### Scenario: 不直连 Provider

- **WHEN** 信息分析师需要某篇文档的正文
- **THEN** SHALL 从已入库的文档资产或数据产品读取
- **AND** SHALL NOT 在角色模块内发起取回请求（该行为 SHALL 被架构守卫判为违规）

#### Scenario: 未准入材料不进入

- **WHEN** 某批候选文档尚未通过非结构化质量门
- **THEN** 它们 SHALL NOT 出现在本轮信息简报的来源中
- **AND** 本轮 SHALL 记录这段缺口，SHALL NOT 以「无信息」呈现

### Requirement: InformationBrief 必须携带六类要素

每条信息简报 SHALL 至少包含：来源文档（含文档版本与片段位置）、事实变化、影响候选、关联实体、置信度与时效，以及待核验项。缺任一项 SHALL 判定该次产出失败，SHALL NOT 以自由文本降级写入。

#### Scenario: 字段齐备才写入

- **WHEN** 一条简报缺少事实变化或待核验项
- **THEN** 该次产出 SHALL 被判为失败并指出缺失字段
- **AND** SHALL NOT 被写成一条字段残缺的投影

#### Scenario: 结论可追溯到文档位置

- **WHEN** 复核简报中的一条事实变化
- **THEN** SHALL 能取回来源文档标识、版本与片段位置
- **AND** SHALL 能取回抽取时间与置信度

### Requirement: 信息分析师必须可独立运行并有独立入口

信息分析师 SHALL 具备不依赖基本面或主理人即可完整终结的运行路径：手动入口、定时入口与文档准入事件入口三者各自可发起一次独立运行。运行时 SHALL NOT 要求其他分析师的投影已存在，SHALL NOT 触发基本面或主理人流程。

#### Scenario: 只更新信息快报

- **WHEN** 以手动或定时入口发起一次信息分析运行
- **THEN** SHALL 只产出信息简报投影并正常终结
- **AND** SHALL NOT 触发基本面例行模式、事件模式或主理人决策周期

#### Scenario: 文档准入事件触发

- **WHEN** 一批新文档通过数据平台准入
- **THEN** SHALL 可据此发起一次信息分析运行
- **AND** 该运行 SHALL 以稳定幂等键去重，重复事件 SHALL NOT 产生第二条等价简报

### Requirement: 简报必须携带三类时间并做同源聚类

每条事实变化 SHALL 携带**事件时间**（事情发生的时刻）、**发布时间**（文档对外可见的时刻）与**抽取时间**（系统入库并抽取的时刻）。三者 SHALL 分别记录，SHALL NOT 用单一时间字段代替。

同一事件被多份文档报道时，简报 SHALL 将这些文档归入同一事件簇，并记录簇内文档数与独立信源数。它 SHALL NOT 把同源重复计为多条相互独立的事实变化，也 SHALL NOT 因簇内文档数量增加而抬高置信度。

#### Scenario: 三类时间分别可查

- **WHEN** 复核简报中的一条事实变化
- **THEN** SHALL 能分别取回事件时间、发布时间与抽取时间
- **AND** 时效判定与事件模式 cutoff 判定 SHALL 以事件时间或发布时间为准，SHALL NOT 以抽取时间为准

#### Scenario: 同一事件被多篇文档报道

- **WHEN** 同一事件存在财报稿、通讯稿与多家媒体报道
- **THEN** 这些文档 SHALL 被归入同一事件簇
- **AND** 简报 SHALL 给出独立信源数，置信度 SHALL NOT 因簇内文档数量增加而上调

#### Scenario: 早发布晚入库的材料

- **WHEN** 一份文档的发布时间早于其抽取时间数日
- **THEN** 简报 SHALL 按发布时间判定其时效
- **AND** SHALL NOT 因抽取时间较新而把它判为本期新增信息

### Requirement: 信息简报不得包含投资建议

信息简报 SHALL NOT 输出买入 / 卖出 / 增持 / 减持、目标仓位、目标权重或组合层面的建议。它可以指出影响候选与关联实体，SHALL NOT 给出该影响应当如何交易的结论。

#### Scenario: 模型输出了买卖建议

- **WHEN** 抽取结果出现买卖方向、仓位或组合建议
- **THEN** 该产出 SHALL 被判为不合法并拒绝写入
- **AND** SHALL NOT 以「仅供参考」的措辞保留该建议

#### Scenario: 影响候选与建议分离

- **WHEN** 简报指出某公司毛利率指引上修
- **THEN** 输出 SHALL 只描述事实变化与可能影响的对象
- **AND** SHALL NOT 给出该公司的增减持结论

### Requirement: 复用既有抽取、triage 与 digest 能力并移除越界副作用

信息分析师 SHALL 复用既有的文章 / 通讯抽取能力（`pead/research.py`）、材料性评估（`pead/triage.py`）与摘要能力（`runtime/digest.py` 的 intel digest）。迁移时 SHALL 移除其中的越界副作用：直接改写 dossier 的写入、以及角色内部的取数调用。

#### Scenario: 抽取能力迁入并保持只写简报

- **WHEN** 复用文章 / 通讯抽取链路
- **THEN** 其产出 SHALL 写为信息简报投影
- **AND** SHALL NOT 直接改写基本面 dossier 或预期基线

#### Scenario: 监控链路的 dossier 直写被移除

- **WHEN** 复用文档识别与更新逻辑
- **THEN** 其产出 SHALL 为信息简报，SHALL NOT 直接追加 dossier 叙事或改预期
- **AND** 由此产生的事实变化 SHALL 由基本面在例行模式下自行消费

#### Scenario: triage 与摘要不再直连取数

- **WHEN** 复用材料性评估与摘要能力
- **THEN** 正文获取 SHALL 经数据产品入口
- **AND** 角色模块 SHALL NOT 保留任何直连 Provider 的调用路径

### Requirement: 同一文档不重复产出简报

信息分析师的产出 SHALL 以「来源文档版本 + 抽取逻辑版本」为幂等键。同一文档版本被重复处理时 SHALL 复用已有简报，SHALL NOT 产生第二条语义等价的投影。

#### Scenario: 同一文档被重复处理

- **WHEN** 同一文档版本再次进入处理流程
- **THEN** 系统 SHALL 返回已有简报投影
- **AND** SHALL NOT 新增第二条投影或重复计入下游消费

#### Scenario: 文档产生新版本

- **WHEN** 同一来源文档出现新版本
- **THEN** SHALL 产出一条新的简报投影并引用前一版本
- **AND** 旧版本简报 SHALL 保留，SHALL NOT 被覆盖

### Requirement: 简报以投影发布且可被基本面消费

信息分析的唯一对外产出 SHALL 是写入 `task_projection_envelopes` 的 `InformationBrief` 投影。基本面分析师 SHALL 通过投影读取简报，该读取 SHALL 是架构守卫显式允许的两条跨角色依赖之一。

#### Scenario: 产出一条信息简报投影

- **WHEN** 一次信息处理成功
- **THEN** 系统 SHALL 写入一条 `agent_role=information_brief` 的投影
- **AND** 其 `data_vintage_refs` SHALL 记录所消费文档版本与数据 as-of

#### Scenario: 基本面按实体复用简报

- **WHEN** 基本面分析师需要某标的的近期信息
- **THEN** SHALL 按实体作用域读取未过期的简报投影
- **AND** 复用 SHALL 记录所依赖的简报投影标识
