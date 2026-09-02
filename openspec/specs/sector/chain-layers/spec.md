## Purpose

定义产业链「层」这个分析与风控单元的契约：一个层要同时成立于需求传导位置、截面可比性与
风险相关簇三重职责；层与 subgroup 的分工；层键更名后历史数据仍可解析的规则。

## Requirements

### Requirement: 层的三重职责

一个产业链层 SHALL 同时充当三个单元：① 需求传导链上的一个位置；② 截面排序的 cohort；
③ 风险预算与相关簇的单元。新增或调整层时，配置 SHALL 使这三者指向同一组公司；当某组公司
在 ② 上不可比但在 ③ 上同向时，它们 SHALL 归入同一层并以 subgroup 区分。

#### Scenario: 驱动因素不同的公司不得共用一个 cohort

- **WHEN** 一层内的公司由**互不相同的定价机制**决定盈利（如产能垄断型服务商与商品化周期品）
- **THEN** 系统 SHALL 把它们切分为不同的层，各自拥有独立的 `weight_cap` 与独立的景气结论
- **AND** 拆分后每层的截面 z 分 SHALL 只在该层内部计算

#### Scenario: 同向但不同产线的公司同层分组

- **WHEN** 一组公司股价由同一个周期 beta 驱动（如存储 bit 周期），但产线与工艺不同
- **THEN** 系统 SHALL 把它们归入同一层（风险簇成立）
- **AND** SHALL 以 subgroup 标注其产线归属，作为叙述与比较的分组标签

#### Scenario: 截面 z 分的作用域是层

- **WHEN** 计算某层的截面 z 分
- **THEN** 标准化 SHALL 在**整层样本**上进行，subgroup SHALL NOT 作为标准化的作用域
- **AND** subgroup 的用途 SHALL 限于分组叙述与比较；层级结论 SHALL 按 subgroup 分开讲述
- **AND** 一层是否设置 subgroup SHALL 由配置决定；未设 subgroup 的层，其标的敞口性质与定价机制
  SHALL 写在标的 `note` 中，供分析师在叙述层区分

### Requirement: AI 硬件八层结构

`ai_hardware` 的层结构 SHALL 为八层，按需求传导顺序排列：应用/Token经济、云服务、
数据中心电力与冷却、互联与网络、芯片设计、存储、代工与先进封装、半导体设备。
每层 SHALL 声明自己的 `question`、`tickers`、以及（若适用）`subgroup`、`cohort_extra`、
`structure_notes`、`witness_roster`、`claims`。

#### Scenario: 存储层与代工封装层分离

- **WHEN** 加载 `ai_hardware` 配置
- **THEN** 存储三巨头（SK 海力士 / 美光 / 三星）SHALL 位于存储层
- **AND** 台积电 SHALL 位于代工与先进封装层
- **AND** 两层 SHALL 各自拥有独立的 `weight_cap`

#### Scenario: 互联与网络自成一层

- **WHEN** 加载 `ai_hardware` 配置
- **THEN** 光互联、铜连接、衬底 SHALL 位于同一个互联与网络层，subgroup 保留原有区分
- **AND** 电力与冷却 SHALL 位于独立的一层
- **AND** 因网络/交换或光 DSP 收入而与互联层同业的芯片设计公司 SHALL 以 `cohort_extra`
  参与互联层排序，其风险归属仍留在芯片设计层

#### Scenario: 层内命题随层迁移

- **WHEN** 一条命题（claim）所描述的主体被移入新层
- **THEN** 该命题 SHALL 挂载在新层之下，其 `witness_roster` 与 `expect_from` 保持不变
- **AND** 命题的 `feeds_factor` 声明 SHALL 仍指向该层截面的结构因子

### Requirement: 命题的层归属由被判断的主体决定

一条命题 SHALL 挂在它**所判断的对象**所在的层，而不是其证人所在的层。证人声明
（`witness_roster` / `expect_from` / `witnesses`）SHALL 允许跨层，且 SHALL NOT 因层结构变动
而被重切。

#### Scenario: 主要证人在别层

- **WHEN** 一条命题的某个维度，其主要发声方在另一层（如封装产能维度的主证人是代工厂，
  而命题判断的是存储供给）
- **THEN** 该命题 SHALL 整条留在被判断主体所在的层
- **AND** 该证人 SHALL 保留在证人声明中，SHALL NOT 因不同层而被移除

#### Scenario: 拆层不重切证人表

- **WHEN** 一个层被拆分为两层
- **THEN** 原命题的证人声明 SHALL 逐字保留
- **AND** 系统 SHALL NOT 以「是否同层」为依据增删证人

### Requirement: 命题缺口必须可见

一个层 SHALL 允许没有任何命题，但该状态 SHALL 是**显式声明**的，并 SHALL 与「有命题但本期
无结论」在下游输出中区分开。当某层的标的不被任何命题声明为证人时，系统 SHALL 能报出这批
标的及其未映射观测的数量。

#### Scenario: 新层暂无命题

- **WHEN** 某层拆分后不携带任何命题
- **THEN** 配置 SHALL 显式留空该层的命题列表并记录原因与下一步
- **AND** 下游输出 SHALL 标注该层为「无命题」，SHALL NOT 表述为「证据不足」

#### Scenario: 标的未被任何命题声明

- **WHEN** 某个标的不出现在任何命题的证人声明中
- **THEN** 系统 SHALL 能列出该标的及其观测中未映射到任何维度的条数
- **AND** 该数量 SHALL 作为判断是否需要新建命题或扩充证人声明的依据

#### Scenario: 声明证人的判据

- **WHEN** 考虑把某个实体加入命题的证人声明
- **THEN** 判据 SHALL 是该实体**是否已在产出可归属的读数**
- **AND** 对取不到文档、或只能给出与既有证人重复表述的实体，SHALL NOT 声明

### Requirement: 层键更名的历史兼容

层 SHALL 支持声明 `legacy_keys`（历史层键列表）。系统在读取历史记录（命题结论、
历史周度评审）时，SHALL 把历史层键解析到声明了该键的当前层；历史记录本身 SHALL NOT 被改写。

#### Scenario: 历史层键解析到新层

- **WHEN** 存储层声明 `legacy_keys: [L5_fab]`，且历史命题结论中存在 `layer = L5_fab` 的行
- **THEN** 按存储层查询历史时 SHALL 返回这些行
- **AND** 原始记录中的层键值 SHALL 保持为 `L5_fab`

#### Scenario: 一个历史键映射到多个新层

- **WHEN** 一个历史层键被两个当前层同时声明（拆分场景）
- **THEN** 两个层查询历史时 SHALL 都能看到该键下的行
- **AND** 系统 SHALL 在展示历史对比时标注该段历史属于拆分前的合并口径

#### Scenario: 未知层键

- **WHEN** 某条历史记录的层键既不是当前层键、也未被任何层声明为 `legacy_keys`
- **THEN** 系统 SHALL 跳过该行并记录一条警告，SHALL NOT 让整次读取失败

### Requirement: 风险层限额与层结构一一对应

`sector_layer_caps` SHALL 为每一个当前层提供 `weight_cap`。加载配置时，若存在层没有对应
限额、或存在限额没有对应层，系统 SHALL 报出该不一致。

#### Scenario: 限额缺失

- **WHEN** 配置中新增了一层但未在风险限额中声明
- **THEN** 加载 SHALL 产生一条明确指出该层键的错误或警告
- **AND** 该层的截面预算 SHALL 退化为保守默认值而非无限额

### Requirement: 报告文件按当前层键产出

每周产出的层报告 SHALL 只覆盖当前存在的层。以历史层键命名的既有文件 SHALL NOT 被删除或
改写——它们是当时口径下的记录；但系统 SHALL NOT 在同一次运行中同时产出新旧层键的文件。

#### Scenario: 拆层后不再产出旧层键的文件

- **WHEN** 一个层被拆分或更名后运行周度评审
- **THEN** 产出的报告 SHALL 只使用当前层键
- **AND** 历史上以旧层键命名的文件 SHALL 保持原样

#### Scenario: 每个当前层都有报告

- **WHEN** 周度评审完成
- **THEN** 每一个产出了结论的当前层 SHALL 有对应的报告文件
- **AND** 未产出结论的层 SHALL 在跨层报告中被列为缺失，而不是静默省略

### Requirement: 单票层的截面降级

当一层的可用样本不足以计算截面 z 分时，该层 SHALL 跳过截面排序，仅产出景气与配置结论，
并在输出中显式标注「截面不适用」。

#### Scenario: 层内只有一只票

- **WHEN** 某层参与排序的样本少于两个
- **THEN** 系统 SHALL NOT 产出该层的排序 basket
- **AND** 该层的层级结论 SHALL 仍然产出，并标注截面不适用及其原因
- **AND** 该层的预算 SHALL 全额落在该唯一标的上，受单票限额约束
