## ADDED Requirements

### Requirement: 正式结构化数据只使用一个物理 repository
结构化采集 CLI、主动发现协调器、DataProducts 和 Evidence Observer SHALL 通过同一 repository factory 使用 `ATS_DATA_DB_PATH` 与 `ATS_DATA_ARTIFACT_ROOT`；默认正式路径 SHALL 为 `var/data.sqlite` 与 `var/data_artifacts`。隔离测试 MAY 显式传入临时路径，但生产发布 SHALL NOT 通过在 `ats.sqlite`、临时库与正式库之间复制表完成。旧变量只可作为显式兼容入口，且不得覆盖已经设置的正式变量。

#### Scenario: 同一命令采集后立即运行 Observer
- **WHEN** 操作者未指定测试 repository，依次运行 release-check/ingest 和 L1 Evidence
- **THEN** 两者 SHALL 写入并读取同一个正式 SQLite 与 artifact root
- **AND** SHALL NOT 产生第二份结构化 observations 或要求跨库复制

### Requirement: 来源注册驱动主动发现与增量更新
结构化数据平台 SHALL 允许来源声明 discovery adapter、检查频率、业务 cadence、release identity、可变 metadata 指针、不可变 artifact 身份和条件模块规则。统一调度入口 SHALL 对到期来源执行发现，只有在新 release、new period 或历史修订出现时才拉取与准入内容；同一机制 SHALL 覆盖 Anthropic Economic Index、Census BTOS、RPS/FRED 和 ONS BICS AI。

#### Scenario: 多来源按各自 cadence 到期
- **WHEN** 每周统一 discovery 运行，而 Anthropic 为 release-event、BTOS 为双周、RPS 为季度、ONS AI 为条件模块
- **THEN** 系统 SHALL 分别执行各来源的 due-check 和 source-native discovery
- **AND** SHALL NOT 将低频或未承诺发布日期的来源仅因没有新数据标记为 stale

### Requirement: 主动发现保存可审计的检查状态
每次来源检查 SHALL 保存 `last_checked_at`、候选 release identity、latest upstream identity、latest ingested identity、latest available period、检查结果、请求/文件内容身份和错误上下文。`no_change`、`not_yet_published`、`question_not_fielded`、`methodology_break`、`unreachable`、`validation_failed` 和 `succeeded` SHALL 为不同状态。

#### Scenario: ONS 新 wave 没有 AI 模块
- **WHEN** ONS BICS 发布新 workbook 但受支持 AI 问题未出现
- **THEN** discovery SHALL 记录 `question_not_fielded` 并更新 last checked 状态
- **AND** SHALL NOT 创建空 observations、零值或虚假的 stale 告警

### Requirement: 发现和采集按来源及 slice 隔离失败
统一更新 SHALL 为每个 source 和 source-native slice 保持独立状态、artifact 和发布边界。一个来源不可达、一个 series 修订失败或一个条件模块漂移 SHALL NOT 阻止其他来源的有效更新；整体运行 SHALL 返回逐来源结果和可解释的 partial 状态。

#### Scenario: BTOS 成功而 FRED 暂时不可达
- **WHEN** 同一更新批次发现 BTOS 新 period 并成功入库，但 FRED 请求失败
- **THEN** BTOS observations SHALL 可独立发布，FRED SHALL 保留最近成功版本
- **AND** 总体 SHALL 返回 partial 及两个来源各自状态

### Requirement: 并发检查保持幂等且不重复发布
相同 source/release identity 的并发或重复 discovery SHALL 通过来源级互斥和内容身份收敛为一个有效采集结果。完全相同内容 SHALL 记录 `no_change`，历史值变化 SHALL 追加 vintage；任何路径 SHALL NOT 覆盖旧 artifact 或生成重复 observation。

#### Scenario: 调度与人工更新同时发现同一 BTOS period
- **WHEN** 两个运行同时处理同一官方 period 和响应内容
- **THEN** 系统 SHALL 最多发布一组内容相同的 observations
- **AND** 两次运行记录 SHALL 可审计地指向同一 artifact 或一个成功、一个 no-change 结果

### Requirement: 统一 release check 可独立运行和按来源过滤
运维者 SHALL 能通过同一 discovery/release-check 入口检查全部已启用来源或只检查指定 source/dataset，并能选择仅发现或发现后采集。只读检查 SHALL NOT 改变发布状态；自动采集 SHALL 仍执行来源 schema、质量和 lineage 门。

#### Scenario: 只检查 AI adoption 来源
- **WHEN** 运维者请求仅检查 Anthropic、BTOS、RPS 和 ONS AI 来源
- **THEN** 系统 SHALL 返回 Anthropic、BTOS 和 RPS 三个活跃来源的 due、latest upstream、latest ingested 和可操作状态
- **AND** ONS BICS SHALL 仅能通过显式 source 请求独立检查，不进入 `ai_adoption` group
- **AND** SHALL NOT 运行或改变无关财务、行情或其他 Observer 来源
