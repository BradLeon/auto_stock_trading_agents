## ADDED Requirements

### Requirement: 结构化采集必须支持事件驱动网页指标的定期发现
结构化数据平台 SHALL 支持对没有固定 release 文件、但会在公开公司页面更新的低频指标执行配置化周期探测。探测 SHALL 比较 source-native 参考期间、内容指纹、解析 schema 和方法文本；无变化 SHALL 记录 `no_change`，新期间 SHALL 增量追加，同期间变更 SHALL 保存 revision vintage，方法漂移 SHALL 阻止新版本自动进入正式数据。

#### Scenario: 定期检查未发现新收入数据
- **WHEN** Sacra 页面收入观察、引用和内容指纹与最近成功探测一致
- **THEN** 运行 SHALL 返回 `no_change` 且不新增 artifact 或 observation
- **AND** 已发布数据和报告重放 SHALL 保持不变

### Requirement: 首次正式发布可以由完整验收直接完成
对于配置为 `publish_after_acceptance` 的新结构化来源，平台 SHALL 在首次端到端采集、质量、查询、lineage 与 replay 验收全部通过后直接将通过质量门的数据发布为 `platform`，而不要求预先存在 shadow 数据集。后续失败或方法漂移 SHALL 只隔离候选 revision，并保留最近有效 platform vintage。

#### Scenario: 新收入数据集完成验收
- **WHEN** Frontier AI Labs 收入数据的规定验收全部通过
- **THEN** source 与 dataset SHALL 可直接成为 platform 可查询数据
- **AND** 后续报告 SHALL 只读统一存储而不在渲染时重新采集

