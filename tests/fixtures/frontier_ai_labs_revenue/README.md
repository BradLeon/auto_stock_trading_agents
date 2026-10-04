# Frontier AI Labs revenue fixtures

冻结的公开来源快照，仅供离线解析与回归测试使用。

- Sacra fixture 由真实公开页面提取：公司正文 Revenue 段落 + Revenue Metrics 表格。
- 付费墙行在服务端即被脱敏为 `—`，fixture 保留少量 `aria-hidden="true"` 行用于验证「不把缺失解释为 0」。
- TickerTrends 使用公开文章的 body_html，并保留发布时间与内容 hash。

未在任何 fixture 之外接入付费 API、MCP 或登录后导出。
