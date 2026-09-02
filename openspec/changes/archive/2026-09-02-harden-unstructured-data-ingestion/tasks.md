## 1. 读取一致性与类型契约

- [x] 1.1 为正文含独立 `---`、无 frontmatter、损坏 frontmatter 和多版本正文增加失败复现测试
- [x] 1.2 修复 frontmatter 解析与统一读回一致性校验，保证 catalog chars/hash、最新正文和不可变版本一致
- [x] 1.3 运行 source cache、document asset 和 data product 定向测试；本组全部通过后再进入下一组
- [x] 1.4 定义新文档业务语义、载体格式与旧 `article|news|release|deck|transcript|filing` 的双向兼容映射并测试历史读取

## 2. 候选准入与隔离区

- [x] 2.1 增加候选文档、校验结果和 reason-code 契约，支持 identity、period/event、type、completeness 同时报告多个失败
- [x] 2.2 增加 accepted/quarantined 持久化与迁移，使隔离候选保留 provenance 但不进入默认文档查询、版本分块和 Agent 数据产品
- [x] 2.3 实现中央 validator 与来源特有校验扩展点，禁止空值、`TODO`、不可解析期间和未知身份自动放行
- [x] 2.4 用 SKHY→GSK、TSM→非 TSMC、错期间和多重失败 fixtures 验证隔离；运行 store、products、validator 定向测试后再进入下一组

## 3. 财报事件与结构化纪要主源

- [x] 3.1 实现 covered entity 的 earnings event 解析，统一 report date、fiscal year/quarter，并对配置、calendar、filing 冲突显式报错
- [x] 3.2 增加 defeatbeta 配置和远程/本地 Parquet 适配器，按 symbol + fiscal period 查询纪要并记录 `spec.json` 快照时间与延迟
- [x] 3.3 保存原始 speaker/paragraph 结构并生成标准化 Markdown，增加段落连续性、管理层身份、开场/Q&A/结束和前端噪声校验
- [x] 3.4 将 transcript 路由调整为人工/官方覆盖 → defeatbeta 主源 → 其他结构化源 → Tavily 隔离候选，移除“期间未知则放行”
- [x] 3.5 使用本地 fixtures 运行全部 transcript/event 测试，再用隔离数据库只读 smoke test 当前 25 个实体的覆盖、最新日期和正文纯净度；通过后再进入下一组

## 4. 正式公告与 Investor Presentation

- [x] 4.1 将 SEC release 获取改为按目标 earnings event 搜索匹配 filing/exhibit，禁止无 ex99 时回退最大 HTML，并验证 CIK、公司正文和业绩语义
- [x] 4.2 为公司 IR/deck 增加官方域名、公司身份、期间和 presentation 语义校验；Tavily 只返回候选 URL
- [x] 4.3 修正 `Earnings Presentation` 分类优先级并覆盖 release、deck、filing、announcement 的边界测试
- [x] 4.4 用 GOOG XBRL、INTC 股票发行、MRVL warrant、LITE→Teck、CRDO→Criteo 等真实失败 fixtures 运行定向测试；通过后再进入下一组

## 5. IBKR 实时新闻

- [x] 5.1 修复 Historical News UTC/时区日期格式，将 `None`、timeout、provider、合约和单个时间切片失败局部化
- [x] 5.2 增加重叠窗口与稳定 article id 去重，确保同一新闻关联多个 symbol 时正文只存一份
- [x] 5.3 用 fake IB client 覆盖 `None`、单 symbol 超时、零匹配、无 provider 和多切片继续执行测试
- [x] 5.4 在 TWS 开启状态下以 readonly client 做 NVDA 和配置 symbols 的隔离 smoke test，验证标题、正文、provider provenance 与零交易副作用；通过后再进入下一组

## 6. Newsletter 回填、游标与完整性

- [x] 6.1 增加 mailbox/folder/sender 的 UIDVALIDITY、UID/Message-ID 与成功水位持久化，支持首次 backfill 和后续重叠增量
- [x] 6.2 将采集全部新邮件与下游 `max_articles_per_run` 限流解耦，游标只在本轮资产全部持久化成功后推进
- [x] 6.3 实现 full/partial/teaser 完整性判断并保留 canonical URL、MIME 来源和截断理由
- [x] 6.4 用十封首次回填、两新一重叠、UIDVALIDITY 变化、付费预览和失败不推进游标 fixtures 运行测试
- [x] 6.5 在隔离数据库真实回填至少 30 天 SemiAnalysis 邮件，核对截图所示邮件覆盖、去重和完整性状态；通过后再进入下一组

## 7. Yahoo 新闻回填与质量可观测性

- [x] 7.1 增加 defeatbeta/Yahoo News 日级回填适配器，保留 uuid、publisher、report date、结构化段落和 snapshot lag，并与 IBKR/Finnhub canonical URL 去重
- [x] 7.2 扩展来源健康与阶段一运行报告，区分 accepted、quarantined、unreachable、unauthorized、zero matches、stale 和各 reason-code
- [x] 7.3 为覆盖率、identity/period 正确率、完整率、来源延迟和统一读取一致性增加可查询指标与 DataProducts/CLI 检查入口
- [x] 7.4 运行新闻、健康报告、DataProducts 和兼容性测试，确认旧 workflow 仍能读取 accepted 文档且默认看不到隔离候选

## 8. 全链路隔离验收与文档

- [x] 8.1 更新数据架构、来源优先级、文档类型、quarantine、Newsletter 游标和运维说明，记录第三方数据集许可/更新延迟约束
- [x] 8.2 运行完整单元与集成测试，确认 Evidence、PEAD、Sector、评分和交易路径没有行为回归
- [x] 8.3 在全新隔离目录单独运行阶段一真实采集，逐类检查覆盖、实体、期间、完整性、版本、读取和来源时效
- [x] 8.4 验收统一读取一致性 100%、自动 accepted 抽样 identity/period 正确率 100%、隔离候选不进入默认查询，并确认 LLM/评分/交易记录均为 0
- [x] 8.5 输出最终运行报告、质量缺口和生产切换建议；仅在发布闸通过后才将新来源路由设为生产默认

## 9. 正式披露回归修复（release 与 10-Q/10-K）

- [x] 9.1 为旧 `unknown-release` 精确重校验与事件键迁移增加测试并实现复用，保留 SEC provenance、内容版本和 lineage
- [x] 9.2 为 SEC 传输增加有限重试、filing index/完整 submission 解析回退及阶段化 `unreachable` 结果，禁止静默空成功
- [x] 9.3 按 accession 下载 10-Q/10-K primary document，作为独立 regulatory filing 入库，并与 company release 分离去重、版本和查询
- [x] 9.4 运行 SEC/release/filing/document asset 专项单元与集成测试；本组全部通过后再进行真实源测试
- [x] 9.5 在全新隔离目录只运行 earnings release 与 10-Q/10-K 的真实采集，核对覆盖、实体、期间、form/accession、文件读取与来源失败状态，输出专项报告；不运行其他数据源

## 10. SEC 文档角色、财期与外国发行人完整性

- [x] 10.1 为 AMZN 同比期间、MSFT `quarter ended`、KLAC `fiscal fourth quarter` 增加失败复现测试；实现多候选财期解析与 earnings-event 联合绑定
- [x] 10.2 以 SEC submissions 官方 form/items/primary-document 元数据补强候选发现，保留 filing index description，逐个分类全部 EX-99，删除“最大附件即 release”规则
- [x] 10.3 支持经严格校验的 6-K primary document 作为 foreign issuer release，并按 filing history 判定 domestic/20-F/40-F 报告制度
- [x] 10.4 为 foreign issuer 获取包含中期财务报表/运营回顾的 6-K regulatory filing，并为年度事件获取 20-F/40-F；与 company release 保持独立角色和 provenance
- [x] 10.5 运行 AMZN/MSFT/KLAC/TSM/SKHY/ASML/NBIS 专项单元与集成测试；全部通过后只对这些实体运行官方披露真实隔离验收并输出新报告，不运行其他来源
