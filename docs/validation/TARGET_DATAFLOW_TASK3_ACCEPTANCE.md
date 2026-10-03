# Tasks 3.1–3.8 数据路径验收

日期：2026-10-03。Change：`complete-target-dataflow`。

本轮验证的是目标 Data API、发布/质量/版本语义和消费者边界，不是完整 Workflow 推理、生产切流或交易资格。既有来源采集与解析器验收复用 Tasks 2 和已归档 FactSet 专项记录；本轮不重新采集它们。

## 可重放证据

版本化执行器：`scripts/replay_target_dataflow.py`。脱敏结果：`TARGET_DATAFLOW_TASK3_REPLAY.json`。执行器不依赖被 Git 忽略的 `tests/`；通过 socket 守卫禁止网络连接，源数据库以只读事务读取，仅在独立临时目录发布探针数据。输出先标记未完成，失败重跑不能遗留上一轮的成功结果。

```sh
UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync python scripts/replay_target_dataflow.py \
  --cache var/data.sqlite \
  --calendar-cache /var/folders/n1/5cnt70z50cg4_pk9fcd707mm0000gn/T/ats-target-calendar-ojpt7_8l \
  --output /private/tmp/target-task3-replay.json
```

`--calendar-cache` 是此前成功获取政府原文的缓存根目录，包含 `data.sqlite` 与 `artifacts/`；可替换为其他经验证的缓存。缓存缺失会拒绝执行，不自动联网补齐。结构化重放是在 **adapter 输出边界之后** 使用真实已接受记录与原 raw 内容；故障、拒绝、修订数据均明确为合成探针，不声称重新验证了上游网站或全文模型。

## 3.1 结构化域

| 域/数据集 | 固定缓存范围 | 检查 |
|---|---|---|
| 公司财务 `company_financials` | TSM、CapEx、DefeatBeta observation/raw hash | 正常、重复、网络故障、权限失败、stale 来源状态、单位拒绝、修订/as-of、消费者对账 |
| 台湾 `regional_tw_exports` | TW_IC_EXPORT、原 MOF artifact | 同上 |
| 韩国 `regional_kr_exports` | KR_SEMI_EXPORT、原 ECOS artifact；不是韩股报价 | 同上 |
| DRAM `industry_dram_contract_price` | DRAM_CONTRACT_PRICE、免费已发布数据期 | 同上，并由 Layer/Sector/Fundamental 分别读取相同 HIER vintage |
| 预期 `market_consensus` | KLAC、EPS high、YFinance 已持久化 artifact | 同上；不是 runtime 行情持久化 |
| FactSet `sp500_earnings_insight` | GICS_45、rating buy share、已发布 observation/raw | 同上；保留发布 manifest fence，只在隔离库重放选定 observation 子集，不授予整份报告审批 |
| FOMC/BLS/BEA 日历 | 三份原文 hash；38/48/6 个事件 | 原解析器→raw→candidate→版本→产品；重复零新增、失败保留 last-good、刷新超时、未知身份拒绝、改期历史、人工裁决后历史 conflict 不被抹除 |

FactSet/DRAM 的正常读取不因为数据期距今天数而自动降级；结构化 `stale` 探针是来源显式返回的失败状态，不改变已确认的“最新上游释放即可”规则。日历 freshness 检查的是刷新服务健康，而非财报材料距今多久。

## 3.2 非结构化逐源矩阵

所有七个已发布正文来源均独立检查：缓存 hash、不可变版本、重复、合成正文修订、历史正文/标题、正文损坏拒绝、数据包读取，以及引用该版本的中性事实与观点隔离。SEC index 和 Yahoo candidate 不冒充已发布正文。

| 来源 | 来源特有检查 | 权限/故障、partial/人工审核与预算 |
|---|---|---|
| `trendforce_news` | 真实来源 policy 的正文长度/身份门；与 DRAM 数值分开 | 短文/付费提示、正文故障、source gate 拒绝、partial 拒绝、请求预算延期；无强制人工审核 |
| `semianalysis` | 全文与允许的预览是不同完整性；版本读取保留 partial | 短文/正文故障/source gate 拒绝；policy 允许的长预览可接受但不伪装全文；预算延期 |
| `ibkr_news` | 实体确认和 native ID；正常零新增不是无权限 | 正文故障/权限失败拒绝；独立验证故障激活 Yahoo fallback；partial 拒绝、预算延期 |
| `yfinance_live_news` | 未审批 candidate 不进入 Information 的已发布产品 | 独立验证正常质量门、pending human review、显式审批后的质量判断、短文/故障/实体门及预算；正常质量探针不是生产审批，不虚构该来源有已发布正文 |
| `factset_earnings_insight_doc` | 文档版本和真实派生 observation；复用双 PDF 专项、group/review/product/index 既有签收 | 隔离原生发布 fence、重复、shadow/reject/as-of、版本 pin、空/失败区分；完整注册表中的 alias 冲突被拒绝；HTML 正文请求预算不适用于 PDF transport |
| `defeatbeta_sec_filing_index` | 真实固定 snapshot/raw row 经原 `_filing_row`；不是官方正文 | 重复、非 SEC URL 拒绝、缺实体 partial、权限/网络失败留运行记录、扩容超预算在查询前拒绝；付费墙/全文完整性不适用于索引 |
| `sec_edgar_filing_body` | 缓存官方正文独立于 index；复用官方 accession、EX-99/主文件/6-K 的原生 extractor 检查 | 正文不足/partial/source issue 拒绝，保留 last-good；transport 的官方 URL/预算检查；不冒充 DefeatBeta 索引为原文；可选输入资格例外仍归 4.6 |
| `defeatbeta_earnings_transcript` | 真实 pinned raw row 经原 transcript parser；财年/季度及 speaker 段落检查；错误财季不 scoreable | 重复、空 segments 拒绝、缺实体 partial、权限/网络失败、超预算在查询前拒绝；short/partial/source issue 发布门拒绝 |
| `ai_hardware_knowledge_corpus` | 已登记知识材料；复用固定 manifest membership/file budget 检查 | 本地正文不足、partial/source issue 拒绝；重复和历史版本；无网页订阅权限、无人工月度审批和网络正文预算 |

逐源门禁中使用实际注册 policy 的合成坏材料，这是 **质量门故障注入**，不是上游真实授权/网站故障的证据。原生来源真实采集证据来自既有 Tasks 2。预算现在按实际请求次数计算，重试也扣预算。

## 3.3 / 3.7 十个角色、16 条数据输入边

统一入口：`ats.data.consumer_api.read_input`；schema：`target-consumer-input-v1`；权限与 native API 以 `target_dataflow_coverage.yaml.data_input_contracts` 为准。角色原调用入口另记为 `legacy_read_api`，不是允许旁路。

| 角色 | 可读数据包 | 时间/失败规则 |
|---|---|---|
| Layer | HIER_DATA、DOC_DATA | 中性事实 observed/supersession、结构化 known_at、文档 publication/version；未知历史显式缺口 |
| Information | DOC_DATA | canonical registry source、body hash、partial、不可变正文/元数据历史 |
| Sector | HIER_DATA | 与 Layer 共用固定 vintage，不读 Information/Macro 的观点 |
| Fundamental | COMPANY_DATA、HIER_DATA | 财务/预期/文档/中性事实分别取证；不会搜索补齐 |
| Macro | MACRO_DATA | 结构化 known_at/发布 manifest，不强行把缺失数据变零 |
| Technical | MARKET_DATA | native runtime query time 与 bar/option source time 分开；缺时间/价格不返回 complete |
| Chief | PORTFOLIO_DATA、HISTORY_DATA | Internal State 分区源时间、reconcile/gap；无快照或未对账不标完整 |
| Risk | PORTFOLIO_DATA、MARKET_DATA、RISK_RULES | 账户缺口、行情失败和 ruleset hash 分开；不读取宏观观点 |
| Trader | APPROVED_EXECUTION_AUTHORIZATION | exact decision hash/review/approval；旧 revision、新 revision 无新批准、过期授权拒绝 |
| Clerk | BROKER_STATE、DECISION_APPROVAL_CONTEXT | 只读注入的 broker/audit；None/异常不是正常零订单；账本补偿/部分成交复用既有 Clerk 回归 |

研究数据支持历史 as-of；runtime、当前账本、规则及当前授权 **不声称能重建历史状态**，传入历史 cutoff 会明确拒绝。规则用内容 hash 标版本；行情日期与 query timestamp 分开。未声明角色/产品组合在读取前拒绝。所有 source read 都不隐式启动刷新、LLM、Workflow 或订单。

当前状态边不接受显式 `as_of` 参数（即使调用方传入“当前时间”），只报告实际读取时间与源时间；Trader 的账户快照时间通过 scope 单独传入原生授权验证。新 role API 自行打开的结构化连接使用 SQLite `mode=ro/query_only`，不执行旧 convenience factory 的 schema/catalog bootstrap；artifact handle 也拒绝写入或创建目录。注入仓储仅供受控调用/隔离验收，生产入口默认只读。

## 3.4 / 3.8 隔离与守卫

- 中性 fact 不含 stance/direction；这些留在 profile/Task Projection 或 Workflow Memory。Information 的 directional insights 是 Memory 中间观点，不称为中性共享事实。
- 新 fact 必须引用已发布文档版本，不接受 future version。新提取追加 factual vintage，不覆盖旧 fact；文档元数据 publication 同样追加。未知历史不补造。
- 当前 Agent 树静态扫描通过；版本化执行器保存 10 个负例：relative/HTTP/dynamic provider import、底层表、Memory 材料与绑定别名、非法投影、投影 alias/dynamic read、事实回写。
- 只允许 Layer→Sector、Information→Fundamental；Chief 汇总六类研究观点。声明校验检查全部产品边、允许角色、schema、mode、实际可解析的 native API 及 Observer tombstone。
- 这是静态代码/数据包边界验证，不是任意 Python 代码的安全沙箱，也不替代之后的跨进程/恢复/完整 Workflow 验收。

## 3.5 旧新读取对账与差异处置

固定实体和 as-of，对现有只读/native facade 与新 role packet 比较：Layer/Sector/Fundamental 的 HIER 行、Information/Layer 的正文、Macro 的数值、Technical/Risk 的 close series、Chief/Risk 的账户、Chief 历史、Trader exact authorization、Clerk broker/audit context。值和引用一致；新包增加 schema、owner、时间、完整性和权限字段。这不是重跑旧采集器或 Agent 推理。

| 差异 | 分类/处置 |
|---|---|
| 未登记 legacy `sec`/`defeatbeta`/搜索来源不再进入 canonical DOC 包 | 预期来源治理变化；保留旧来源/历史，不作为新来源成功证据 |
| 旧 convenience reader 只读 latest，新包可以读修订前正文、元数据和事实 | 预期历史语义变化；as-of 不能读到未来 revision |
| 无快照/无 reconcile 的旧 Internal State 空集合，新包显式 unavailable/partial | 预期安全变化；不把空集当已签收完整账户 |
| 财务 7/29、Layer no_coverage、SEC 原文可空 | 已确认覆盖差异；不填零、不伪造覆盖、不授予未完成资格 |
| 缺文档版本的历史 facts 共 5880 条 | 5783 文档型、68 搜索型、29 数值型。数值型也缺少 vintage link，不能靠类别补证；target 文档事实 API 不签收这些行，旧 owner 为 Data Platform legacy evidence migration，只记录不修 |
| 旧 Chain/PEAD/helper 无 queue lease，旧日历历史无审阅日志 | 旧调用链问题，只登记。新生产路径要求受管 owner；旧历史标未知，不伪造审阅 |

本轮固定样本没有未解释的值差异；旧覆盖缺口不因此消失。整个 Workflow 的观点内容、因果时序、重启和跨进程一致性仍由独立集成验收处理；回滚可用性/最终 eligible 判定仍归 Tasks 4。

## 3.6 最小修复与回归

补齐文档/元数据/fact 的历史读取与 hash 验证；修复 transcript 财季匹配、按已知时间而非 hash 字符串选版本；补 runtime/broker/Internal State 时间与失败包装；Information 不再通过 Memory 查文档身份；source acceptance 改读权威 unstructured registry；重试按真实请求扣预算；固定文档 partial 不得冒充 full；加强契约/架构守卫。未重建已验证 parser，未修旧采集路径或 Phase D 推理，未恢复退役 Memory 事实表。

结构化 candidate 身份现包含完整记录（单位、币种和维度），避免拒绝材料与已接受材料撞 ID、覆盖候选血缘；不重写历史记录。只读连接和 artifact 禁写也已在隔离回放中验证。

回归记录：

- 结构化/区域/共识/宏观/日历定向基线：190 passed、1 deselected；非结构化定向基线：59 passed。
- 最终综合 Data/Runtime/Internal/授权/Clerk/日历/固定来源/FactSet 产品集合：181 passed、1 deselected。
- Information/文档选定集合：52 passed、1 deselected；最后受影响 store/products/guard/source-gate 集合：36 passed。
- 最后只读仓储、候选身份、结构化 ingestion、consumer identity、products/source acceptance 定向回归：53 passed。
- 零网络执行器：6 结构化数据集、7 正文来源、3 政府日历、2 fixed raw parser 链、3 fixed publication gate、16 consumer edges、10 架构负例通过。
- OpenSpec strict validation 与 `git diff --check` 通过。

本地旧测试未强行改成通过：Information 固定 2026-09-23 时钟落在当前 lookback 之外；FactSet 旧断言期待已取消的 stale；自定义 FactSet policy fixture 缺 `core_metrics`。完整权威注册表中的 alias 冲突负例已独立通过。额外 PDF/OCR 补充批次 40 passed、1 fixture failed 后主动停止耗时解析，不把该批次声称为全绿，也不重复网络采集；双 PDF 来源质量沿用已归档专项。生产切流、资格账本、回滚和整体 change 归档均未由本轮执行。
