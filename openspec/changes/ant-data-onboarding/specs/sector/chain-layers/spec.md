# Spec Delta

## ADDED Requirements

### Requirement: 上市公司的证券代码与证据身份分离
命题证人上市后，其证券代码 SHALL 只用于交易直接相关的请求（行情、持仓、下单、风控、财报与监管文件、目标名单）；命题证人、证据账本、新闻、评测与结构化实体 SHALL 继续使用公司名称身份。登记表声明了证据身份的公司，按证券代码取得的文件抽出的证据 SHALL 记在该证据身份下，按证券代码查询证据 SHALL 读到同一批记录；同一经济主体 SHALL NOT 同时以两个身份出现在命题的证人计数或证据簇中。与普通词或其他主体同形的裸代码出现在他人文件中时，SHALL NOT 自动归到该公司。

#### Scenario: 公司自己的注册文件与第三方报道
- **WHEN** 按 ANT 入库的注册文件与 Sacra 的收入读数各自产生关于 Anthropic 的证据
- **THEN** 两者 SHALL 记在 `ANTHROPIC` 下
- **AND** L1 商业化命题 SHALL NOT 出现第二个叫 ANT 的证人

#### Scenario: 蚂蚁集团的报道
- **WHEN** 其他公司或媒体文件里的抽取结果以 "Ant" 指代一个主体
- **THEN** 该记录 SHALL NOT 归到 `ANTHROPIC`

#### Scenario: 改动前后的回归核对
- **WHEN** 登记 ANT 之后用同一份数据重跑 L1 三个证据观察器
- **THEN** 观察器读数与命题证据簇 SHALL 与登记之前一致，差异 SHALL 逐项列出
