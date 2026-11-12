# Spec Delta

## ADDED Requirements

### Requirement: 文章类见证方必须由定时抽取产生观测
被声明为命题见证方的文章来源，其已入库文章 SHALL 由定时任务抽取为中性观测。每篇文章的同一版本 SHALL 只抽取一次。抽取 SHALL 只读取已入库的文章版本，不得为抽取再次访问文章来源。每次运行的模型请求数 SHALL 有上限，超出部分留待下次运行。

#### Scenario: 新一期 SemiAnalysis 入库
- **WHEN** 一篇新的 SemiAnalysis 文章入库，且提及 L5 见证范围内的实体
- **THEN** 下一次抽取运行 SHALL 为其产生说话人为 `SEMIANALYSIS` 的观测
- **AND** 观测 SHALL 指向该文章的版本与原文位置

#### Scenario: 同一文章再次运行
- **WHEN** 已抽取过的文章版本未变化
- **THEN** 抽取运行 SHALL 跳过该文章，不发出模型请求

#### Scenario: 与命题无关的新闻
- **WHEN** 一条 IBKR 新闻不提及任何 L5 见证范围内的实体或命题关键词
- **THEN** 系统 SHALL 不为其调用模型，并记录为已检查、不相关

#### Scenario: 超出单次请求上限
- **WHEN** 待抽取文章所需请求数超过单次上限
- **THEN** 运行 SHALL 只处理上限内的文章，记为成功的 `partial` 并注明剩余数量
