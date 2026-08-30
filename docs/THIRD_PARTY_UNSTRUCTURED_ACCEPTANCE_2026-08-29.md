# 第三方非结构化数据来源验收（阶段 10.5）

评估时间：2026-08-29；`yfinance_live_news` 于 2026-08-30 追加隔离验收。范围包括 TrendForce 文章、SemiAnalysis（IMAP/RSS）、IBKR News 及 Yahoo Finance/yfinance 实时新闻。所有运行均不调用 Agent、Workflow、LLM、订单或交易；SemiAnalysis 的采集写入了临时隔离库，其他项目只做只读读取与报告。

## 当前结论

| 来源 | 真实运行结果 | 可发布到 `platform` | 原因 |
| --- | --- | --- | --- |
| TrendForce 文章 | 25 篇发现，25 篇正文合格；其中 1 篇经第二次读取恢复 | 是 | 已写入可逆 source release overlay；只发布来源状态，不切换消费者。 |
| SemiAnalysis IMAP/RSS | IMAP 找到 16 封邮件；30 日窗口有 9 篇；IMAP 与 RSS 传输均成功 | 是（`partial`） | 9 篇均为未订阅预览正文；来源、时间、Message-ID、canonical URL 和正文 hash 已验证。按明确政策发布，但每篇保留 `partial` 完整性标签，不视为全文。 |
| IBKR News | 2026-08-30 只读 TWS 验收：8 个 PEAD 标的、动态 provider、7 日窗口；303 个范围内候选中 199 条标题主体通过，104 条主体拒绝；在 10 篇正文预算内 10/10 正文合格，且无切片失败 | 是 | 已发布为 `platform`。每条保留原生 `(provider, articleId)`、查询标的、标题判定、原始精确时间和 TWS 时区/会话标记；IBKR 健康时不并行采 Yahoo。 |

TrendForce、SemiAnalysis 与 IBKR News 已发布为 `platform`；其中 SemiAnalysis 的可用范围明确是 `partial` 预览资产。IBKR 的发布仅是来源级状态：没有改变任何消费者路由，也没有删除 legacy 路径。

## Yahoo Finance/yfinance 实时新闻：新增隔离验收

`yfinance_live_news` 是对 Yahoo Finance 当前新闻聚合结果的直接、低延迟读取；它与 defeatbeta 的 Yahoo 日级镜像、IBKR/Dow Jones 新闻分别注册、分别验收，不能互为等价替代。Yahoo 的 ticker 推荐只用于召回，**不构成主体正确性证据**：候选标题必须明确命中查询标的的 ticker、公司名或已登记别名，才会请求正文；正文必须来自 URL 的 `article`/`main` 容器且包含标题锚点。

2026-08-30 的两日窗口、11 个当前 PEAD 标的验收结果如下：

| 项目 | 结果 |
| --- | ---: |
| 标的发现成功 | 11 / 11 |
| 唯一候选 | 96 |
| 标题主体拒绝 | 42 |
| 标题主体通过且正文合格 | 17 |
| 正文过短 / 无法读取 | 2 / 5 |
| 因 24 篇正文预算延后 | 13 |

该来源当前为 `registered`，**未发布为 `platform`**。这不是将错误关联混入文档库后的“部分成功”：42 条错误 ticker 推荐已在正文抓取前拦截。未发布原因有两项：一是 2 篇正文过短、5 篇正文不可读、13 篇尚待后续批次；二是标题与 URL 审阅仍须由负责人明确确认。验收报告保留每条候选的查询标的、标题、URL、publisher、精确发布时间、主体判定和缺口原因，运行时写在：

`/private/tmp/YFINANCE_LIVE_NEWS_PEAD_REVIEW.md`

其中的 17 条 `accepted` 行是建议优先审阅的可用样本；`association_rejected` 行用于审计 Yahoo 推荐的错误关联，不会作为任何标的的文档资产发布。

在负责人审阅清单后，仍需在新的验收批次中补齐正文质量门，才能执行发布。复验命令如下；`--approve-title-url-review` 仅确认人工已审阅，**不会绕过**正文质量、时效、血缘或预算门：

```bash
# 只读复验并生成新的标题/URL 审阅清单
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data source-acceptance \
  --source yfinance_live_news \
  --report-path /private/tmp/YFINANCE_LIVE_NEWS_PEAD_REVIEW.md

# 仅当上一条命令的全部数据质量检查通过，且已完成标题/URL 审阅后，才可写发布覆盖层
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data source-publish \
  --source yfinance_live_news --approve-title-url-review --apply
```

## 已实现的验收规则

- 每篇候选记录原生 ID、canonical URL、发布时间、正文 hash、正文状态和来源血缘。
- TrendForce 以站点文章索引而非已过期 RSS 发现；TrendForce 文章与 DRAM 合约价数据集独立验收。
- SemiAnalysis 统一使用共享 IMAP/RSS 管道；以邮件 ID、canonical URL 和正文 hash 去重，IMAP 正文优先；RSS 失败不再静默。未订阅预览可发布，但仅作为带 `partial` 标签的版本，不能冒充或覆盖全文。
- IBKR News 固定为 `readonly=True` TWS 连接；连接、新闻权限、provider/切片失败和真实空结果分别记录。`None` 响应被视为失败，空列表才可表示零新闻。新增诊断会输出 server version、provider、conId、请求 ID、API error 与 `historicalNewsEnd` 状态。
- 只有所有来源级检查通过时，`ats data source-publish <source> --apply` 才能写入可逆发布记录。

## IBKR 已发布的验收证据与故障兜底

2026-08-30 的复验使用独立只读 client ID `191`。动态枚举到 `BRFG`、`BRFUPDN`、`DJ-N`、`DJ-RT`、`DJ-RTA`、`DJ-RTE`、`DJ-RTG`、`DJNL`；8 个已配置 PEAD 标的在全部时间切片中完成，无 `failed_slices`。候选账本显示：303 条在 7 日窗口内、199 条标题明确命中查询标的、104 条被拒绝且未请求正文；最新 10 条主体通过的候选均读取到至少 600 字正文。发布时的完整可审计报告在：

`/private/tmp/IBKR_NEWS_RELEASE_ACCEPTANCE_2026-08-30.md`

时间字段使用 TWS 返回的原始 `datetime`：若 API 返回无时区值，报告将它精确保留为 ISO 时间并标记 `tws_session_timezone_unreported`，绝不擅自标为 UTC。每个候选同时保留动态 provider、provider/article ID、查询与通过/拒绝的实体列表、标准化标题和正文 hash。去重分三层：相同 `(provider, articleId)` 的重放、跨 provider 的相同“标准化标题 + 精确时间”、以及已读取正文的“标准化标题 + 正文 hash”。

IBKR 是新闻新路径的唯一主来源。只有 TWS 不可达、权限/订阅不足、provider 不可用、请求被拒绝/限流，或指定历史切片失败时，验收会生成 `yfinance_live_news` 的 fallback 决策；健康的 IBKR 即使返回零新闻也**不**调用 Yahoo。fallback 仍必须走 Yahoo 自己的标题实体、正文标题锚点、正文质量与血缘门，因此它只补故障范围，不会把不同编辑流混为同一数据集。

旧路径对照后的兼容修复仍有效：历史接口采用 `reqHistoricalNews`（而不是实时 tick `292`）补齐时间窗口，并在扩展超时的低层请求中使用 `YYYYMMDD HH:MM:SS UTC` wire 格式；日常采集只使用本次 `reqNewsProviders()` 的动态枚举，诊断才可显式探测 provider。`reqNewsArticle` 始终使用历史头条原样返回的 `(providerCode, articleId)`；空、二进制/PDF 或最终读取失败的正文会被记录为缺口而不是伪造成功。

## defeatbeta `stock_news` 的替代性结论

已对 PEAD 的 8 个标的做只读实测。镜像快照为 `2026-08-29T04:58:24Z`，检查时约滞后 8.5 小时，正文保留段落和 UUID，时效与技术血缘合格；但其 ticker 关联不等同于“文章主体正确”。例如 NVDA 7 天内返回 1,175 条，标题直接命中 NVDA/NVIDIA 的只有 263 条，75 条在标题和正文中都没有 NVDA/NVIDIA。其它标的也有 3–40 条标题和正文均未命中的错误关联。

因此它**不能替代** IBKR/Dow Jones 作为高精度、独立证据新闻源；可作为 Yahoo Finance 广覆盖新闻补充，前提是另行增加发行人别名、标题/正文相关性和 publisher 分级的准入规则。当前不把它静默降格为 IBKR fallback，也不改变既有 `yahoo_news` 的运行路径。

## 复验命令

```bash
# TrendForce：只读网络检查
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data source-acceptance \
  --source trendforce_news --report-path /private/tmp/TRENDFORCE_ACCEPTANCE.md

# SemiAnalysis：必须使用隔离数据库和文档根目录；不写生产资产。
# 通过时可发布 partial 预览版本，正文完整性仍保留在报告与文档版本中。
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data source-acceptance \
  --source semianalysis --acquire --db /private/tmp/semianalysis.sqlite \
  --artifact-root /private/tmp/semianalysis-documents \
  --report-path /private/tmp/SEMIANALYSIS_ACCEPTANCE.md

# IBKR：先执行无持久化的诊断；使用独立只读 client ID，避免与交易/API 会话冲突
ATS_IBKR_NEWS_CLIENT_ID=191 PYTHONPATH=src .venv/bin/python -m ats.runtime.cli \
  data ibkr-news-diagnostics NVDA --provider DJ-N --provider BRFG

# IBKR：仅在诊断可收到 historicalNewsEnd 后，再执行来源覆盖验收
ATS_IBKR_NEWS_CLIENT_ID=191 PYTHONPATH=src .venv/bin/python -m ats.runtime.cli \
  data source-acceptance --source ibkr_news \
  --report-path /private/tmp/IBKR_NEWS_ACCEPTANCE.md
```

通过后才执行：

```bash
ATS_IBKR_NEWS_CLIENT_ID=191 PYTHONPATH=src .venv/bin/python -m ats.runtime.cli \
  data source-publish --source ibkr_news --apply \
  --report-path /private/tmp/IBKR_NEWS_RELEASE_ACCEPTANCE.md
```

该命令只更新来源发布记录；不会切换 Agent/Workflow，也不会删除旧采集实现。
