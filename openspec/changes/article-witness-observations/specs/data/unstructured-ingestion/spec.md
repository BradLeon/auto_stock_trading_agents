# Spec Delta

## ADDED Requirements

### Requirement: 预算内完成的部分采集必须按成功记录且超时原因如实保留
文章类采集因正文请求预算上限而只取得部分正文时，运行 SHALL 记为成功并注明预算使用情况，调度 SHALL NOT 将其当作失败重试。采集任务超时 SHALL 以超时原因和已等待时长记录，SHALL NOT 被其他异常掩盖。

#### Scenario: ibkr_news 正文达到预算
- **WHEN** 一个窗口选中 350 篇新闻，正文预算 10 篇且已取满
- **THEN** 运行状态 SHALL 为成功的 `partial`，原因写明“预算内 10/350”
- **AND** 控制器 SHALL 不再为该窗口重试

#### Scenario: 采集超时
- **WHEN** 采集任务超过运行时限
- **THEN** 队列 SHALL 记录 `timeout` 与已等待时长
- **AND** SHALL 不出现与超时无关的异常类型
