# Spec Delta

## ADDED Requirements

### Requirement: 价格历史不足时使用规则包声明的代理 beta 与代理簇
标的的价格历史不足以计算 beta 或相关性时，风控 SHALL 使用规则包中为该标的声明的代理 beta 与代理相关簇，并在审查结果与风险日报中标注"代理值"及其失效条件。历史达到计算所需长度后，SHALL 自动改用计算值。没有声明代理值时，SHALL 保持现有的默认处理并标注"无覆盖"。代理值属于规则包，SHALL 计入规则集版本。

#### Scenario: 上市不足 20 个交易日
- **WHEN** ANT 只有 6 个交易日的价格，规则包为 ANT 声明了代理 beta 与"AI 簇"
- **THEN** 风控 SHALL 用代理 beta 计算组合 beta，并把 ANT 计入 AI 簇的簇上限与压力情景
- **AND** 审查结果 SHALL 标注 ANT 使用代理值

#### Scenario: 历史足够后切换
- **WHEN** ANT 的价格历史达到相关性计算所需长度
- **THEN** 风控 SHALL 改用计算出的相关性，并在当日风险日报中注明切换
