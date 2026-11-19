# Spec Delta

## ADDED Requirements

### Requirement: 文章类见证方必须由定时抽取产生观测
被声明为命题见证方的文章来源，其已入库文章 SHALL 由定时任务抽取为中性观测，适用于所有登记了该来源为证人的层。每篇文章的同一版本 SHALL 只抽取一次。抽取 SHALL 只读取已入库的文章版本，不得为抽取再次访问文章来源。抽取前过滤的范围 SHALL 由该来源被登记为证人的命题推导，SHALL NOT 写死某一层。每次运行的模型请求数 SHALL 有上限，超出部分留待下次运行。

#### Scenario: 新一期 SemiAnalysis 入库
- **WHEN** 一篇新的 SemiAnalysis 文章入库，且提及 SemiAnalysis 被登记为证人的命题所涉实体
- **THEN** 下一次抽取运行 SHALL 为其产生说话人为 `SEMIANALYSIS` 的观测
- **AND** 观测 SHALL 指向该文章的版本与原文位置

#### Scenario: TrendForce 文章涉及非 L5 的层
- **WHEN** 一篇 TrendForce 文章只涉及 L6 存储命题的实体或关键词
- **THEN** 该文章 SHALL 通过预抽取过滤并产生说话人为 `TRENDFORCE` 的观测
- **AND** 过滤 SHALL NOT 因为文章与 L5 无关而跳过它

#### Scenario: 同一文章再次运行
- **WHEN** 已抽取过的文章版本未变化
- **THEN** 抽取运行 SHALL 跳过该文章，不发出模型请求

#### Scenario: 与命题无关的新闻
- **WHEN** 一条 IBKR 新闻不提及 DOWJONES 被登记为证人的任何命题所涉实体，也不命中这些命题的关键词
- **THEN** 系统 SHALL 不为其调用模型，并记录为已检查、不相关

#### Scenario: 超出单次请求上限
- **WHEN** 待抽取文章所需请求数超过单次上限
- **THEN** 运行 SHALL 只处理上限内的文章，记为成功的 `partial` 并注明剩余数量
