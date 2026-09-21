## ADDED Requirements

### Requirement: 结构化适配器支持官方网页图表导出作为可治理输入

结构化来源适配器 SHALL 能把可见官方图表的机器可读导出（包括剪贴板 TSV）作为 source-native artifact，保存导出方法、页面 URL、图表/查询身份、原始 payload 哈希和读取权限状态。网页渲染成功但导出不可读 SHALL 是独立的 `export_unreadable`，SHALL NOT 通过截图或 OCR 绕过结构化准入。

#### Scenario: 剪贴板 TSV 进入共享层

- **WHEN** 浏览器从官方 `Get the data` 控件读取 TSV
- **THEN** 适配器 SHALL 以字节级 payload 建立 artifact identity 并交给统一 pipeline
- **AND** SHALL 保留页面控件与图表 slug 作为 provenance

#### Scenario: 导出权限失败

- **WHEN** 页面可见但浏览器无权读取剪贴板，且本地没有已经保存的官方 fixture
- **THEN** 对应 slice SHALL 进入明确的 `export_unreadable` 状态
- **AND** SHALL 不把旧值复制成新 release 或伪造 no_change

### Requirement: 结构化运行支持同一来源的多 chart slice 独立发布

一个来源运行中不同 chart/dataset slice SHALL 拥有独立 artifact key、quality result 和 publish status。某一图表失败 SHALL 不阻止不依赖它的 slice 发布；跨 slice 派生 SHALL 只有在显式声明相同统计主体、分母和 methodology regime 后才允许。

#### Scenario: Spend slice 失败而 adoption 成功

- **WHEN** spend per employee 导出损坏但 Overall adoption 导出质量通过
- **THEN** adoption SHALL 可发布，运行总状态 SHALL 为 `partial`
- **AND** spend SHALL 保留失败 artifact/diagnostic 而不被空值替换
