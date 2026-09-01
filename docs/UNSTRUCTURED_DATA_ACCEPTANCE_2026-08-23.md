# 非结构化数据阶段一验收报告（2026-08-23）

## 结论

本轮发布闸通过，可以把 defeatbeta/Yahoo 日级新闻回填设为生产默认采集源。生产路径为
“调度器每天批量采集一次，Workflow 只读共享资产”，不会由每个 Agent 分别访问远程
Parquet。IBKR 继续负责盘中增量，Finnhub/RSS 继续补充；旧读取接口和历史类型仍兼容。

通过不等于覆盖率 100%。当前缺口已被明确记录，不会以错误正文或错误期间补齐。

## 验收环境与安全边界

- 全新隔离目录：`/private/tmp/ats-stage-one.bHoTNI`
- 隔离 SQLite 与文档目录，不写生产数据库和生产资料库。
- IBKR 使用 readonly client id 190；只调用新闻接口。
- 未调用 LLM、评分、Chief 或交易接口。
- 验收后 `research_insights`、`evidence_facts`、`pead_score_runs`、`decisions`、
  `trades`、`fills` 均为 0 行。

## 真实来源结果

| 类别 | 结果 | 时效/质量判断 |
|---|---:|---|
| defeatbeta 结构化纪要 | 25 个实体中 24 accepted，1 missing | 快照 2026-08-22 05:47 UTC；仅 `005930.KS` 缺失 |
| Yahoo News 日级回填 | 7 天发现 2,897 行，规范去重后 1,757 篇，64 个 publisher | 快照 2026-08-22 05:45 UTC，延迟 24.23 小时，低于 72 小时阈值 |
| SemiAnalysis Newsletter | 30 天 9 篇，截图中窗口内邮件全部覆盖 | 9 篇均有明确 “unlock the rest” 截断证据，保存为 partial，默认不供 Agent 当完整研报使用 |
| IBKR 新闻 | 8 个配置标的发现 676 条标题 | 抽检 5 篇正文全部超过 600 字；readonly，无交易副作用 |
| NVDA 正式公告 | Q1 FY2027 SEC 8-K earnings release accepted，23,091 字 | CIK、事件日期、业绩语义通过；搜索到的非官方 deck 候选被 quarantine |

截图中的 2026-07-23 邮件早于本次精确 30 天窗口起点，因此不计作漏采。其余截图中
处于窗口内的 9 篇全部命中。

## 发布闸指标

- 统一读回一致性：1,772 / 1,772，100%。
- 自动 accepted 候选 identity 正确率：100%。
- 自动 accepted 候选 period 正确率：100%。
- quarantine 候选默认查询不可见，且没有文档版本或分块。
- 正文完整率：1,763 / 1,772，99.49%；9 篇非 full 全部是有明确付费墙证据的 Newsletter。
- 数据处理/决策副作用：LLM、评分、决策、交易、成交均为 0。
- 完整测试：894 passed，4 skipped；4 个 skipped 是显式依赖真实外部条件的测试。

## 已知缺口

1. `005930.KS` 不在 defeatbeta 的纪要与本轮 Yahoo News 覆盖中，继续走公司官方、人工文件或其他结构化源，不能用搜索结果猜补。
2. 当前邮箱收到的 9 篇 SemiAnalysis 都是付费墙截断版本。系统现在能诚实识别，但无法凭现有邮件恢复被截断的付费正文；若账户应有全文，需要检查订阅等级和转发规则。
3. NVDA 自动发现的 deck 因官方 IR 域名未登记且期间不可解析被隔离。补齐实体注册表中的官方 IR 域名后再重试，不应放宽校验。
4. Yahoo News 的 `report_date` 是日级日期，不能替代盘中时间戳；盘中时效仍由 IBKR 提供。
5. defeatbeta 是第三方镜像。当前声明 ODC-BY、研究/教育用途说明；生产使用和再分发应持续复核上游条款与字段变化。

## 生产切换与回滚

- 切换：启用 `config/data/news_sources.yaml` 的 `yahoo_news.enabled`。调度器在 PEAD monitor
  前对完整覆盖名单做一次批量回填，随后各 Workflow 只读共享文档资产。
- 观测：每日检查 `ats data health`；发布或故障排查时运行 `ats data quality`。
- 回滚：关闭 `yahoo_news.enabled` 即停止新 Yahoo 回填；既有资产、来源别名、quarantine
  和历史读取不删除，Finnhub/RSS/IBKR 继续工作。
- 旧逻辑暂不删除：待至少一个完整财报周期确认新来源稳定，再单独提出移除旧抓取路径的变更。
