# Spec Delta

## ADDED Requirements

### Requirement: 实体关联可以按实体限定匹配范围
实体配置 SHALL 可以为单个实体声明关联匹配范围：全文（默认）或仅标题与导语。声明为仅标题与导语的实体，只有其名称或别名出现在文章标题、副标题或导语中时，文章才 SHALL 关联到该实体；只在正文中出现 SHALL NOT 建立关联。股票代码匹配规则不变。该范围 SHALL 只影响关联，SHALL NOT 改变文章本身的准入。

#### Scenario: 正文顺带提到 Anthropic
- **WHEN** 一篇关于 NVDA 的文章只在正文中提到 Anthropic，ANT 声明为仅标题与导语匹配
- **THEN** 该文章 SHALL 关联到 NVDA，SHALL NOT 关联到 ANT

#### Scenario: 标题提到 Claude
- **WHEN** 一篇文章标题提到 Claude 新模型发布，Claude 是 ANT 的别名
- **THEN** 该文章 SHALL 关联到 ANT
