## ADDED Requirements

### Requirement: 中性证据事实的全部写入路径必须归数据层所有

系统 SHALL 将**全部**中性证据事实的写入路径纳入数据层的证据存储与准入流程，与其它来源的证据事实使用同一套实体、来源、期间、血缘与质量契约。该要求覆盖所有产生中性证据事实的路径，不限于观察名单：观察名单（不持有、仅观察的标的）、产业链来源抽取、产业链文章抽取及命令行入口 SHALL 均以数据层证据存储为唯一写入目标。Workflow memory SHALL NOT 承载中性证据事实的存储，也 SHALL NOT 依赖已被退役的证据表。

#### Scenario: 任一路径抽取到中性事实

- **WHEN** 任一产生中性证据事实的路径从披露正文中抽取出一条可核验的事实
- **THEN** 该事实 SHALL 经数据层证据准入后写入数据层证据存储
- **AND** 该写入 SHALL NOT 依赖仅存在于 Workflow memory 中的证据表

#### Scenario: 观察名单标的发生财报发布

- **WHEN** 调度流程为观察名单中的标的发现一次已确认的财报发布并抽取其纪要或披露正文
- **THEN** 抽取所得的中性证据事实 SHALL 经数据层证据准入后写入数据层证据存储
- **AND** 该写入 SHALL NOT 依赖仅存在于 Workflow memory 中的证据表

#### Scenario: 观察名单仅扩大覆盖而不进入交易路径

- **WHEN** 观察名单中的标的通过准入并产生证据事实
- **THEN** 该标的 SHALL NOT 因此被纳入评分、决策周期或下单路径
- **AND** 观察 SHALL 仅作为只读的覆盖扩大

### Requirement: 数据层的写入侧归属必须与读侧一致

数据层 SHALL 为其证据存储提供写入侧的存储结构与写入接口，使读侧与写侧对同一批事实的归属一致。系统 SHALL NOT 出现「读侧已迁移而写侧仍指向旧存储」的中间状态长期存在；若迁移确需分阶段完成，未迁移的一侧 SHALL 被显式登记，SHALL NOT 以静默方式继续写入旧存储。

#### Scenario: 写入侧与读侧同源

- **WHEN** 一条中性证据事实被写入数据层
- **THEN** 该事实 SHALL 能被数据层的读入口按同一标识与血缘读回
- **AND** 读入口 SHALL NOT 需要回退到 Workflow memory 获取该事实

#### Scenario: 迁移分阶段进行

- **WHEN** 数据层搬迁处于读侧已完成、写侧未完成的中间状态
- **THEN** 未完成的一侧 SHALL 被显式登记并给出完成条件
- **AND** 系统 SHALL NOT 让这一中间状态被当作已完成

### Requirement: 证据写入路径在存储初始化后必须仍可执行

Workflow memory 初始化其存储并执行数据层边界归类时，SHALL NOT 使任何被写入路径依赖的表变为缺失。初始化之后，各证据写入路径 SHALL 仍可正常执行。

#### Scenario: Workflow memory 初始化

- **WHEN** Workflow memory 初始化其存储并执行数据层边界归类
- **THEN** 初始化 SHALL NOT 使任何被写入路径依赖的表变为缺失
- **AND** 各证据写入路径 SHALL 在初始化后仍可正常执行

#### Scenario: 边界归类与写入目标不一致

- **WHEN** 某张表被边界归类判为数据层所有，而仍有写入路径指向该表
- **THEN** 该不一致 SHALL 在初始化阶段即被发现
- **AND** 系统 SHALL NOT 以运行期缺表错误的形式暴露该不一致

### Requirement: 分析结论与中性事实的写入边界必须各自明确

系统 SHALL 明确区分两类写入：数据层可以发布确定性抽取的中性事实；含观点的分析结论 SHALL 只写入 Workflow memory。系统 SHALL NOT 允许含观点的结论以中性事实的形式进入共享事实层，也 SHALL NOT 要求 Workflow memory 承载中性事实的权威存储。

#### Scenario: 观察到中性事实

- **WHEN** 一个观察路径从披露正文中抽取出一条可核验的中性事实
- **THEN** 该事实 SHALL 按数据层的准入与质量契约发布
- **AND** 其血缘 SHALL 可追溯至原始文档版本

#### Scenario: 结论不得冒充事实

- **WHEN** 某个分析路径产出一条含判断或观点的结论
- **THEN** 该结论 SHALL 只写入 Workflow memory
- **AND** 共享事实层 SHALL NOT 新增与该结论对应的记录
