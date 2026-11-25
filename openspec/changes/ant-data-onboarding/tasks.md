# Tasks

> 每个数据源或指标完成后，先请用户抽查验收再进入下一项。验收结果统一写入本 change 的 `verification.md`。

## 1. 身份统一（A1）

- [ ] 1.1 在 `config/entities.yaml` 登记 ANT（别名 ANTHROPIC、Anthropic、Anthropic PBC、Claude；`market: US`，`filings: sec`）。验证：实体加载测试通过，`canonical_entity("ANTHROPIC") == "ANT"`。
- [ ] 1.2 `config/data/structured.yaml` 的 ANTHROPIC 私有实体改为指向 ANT；L1_app 证人改为 ANT。验证：配置加载测试通过；L1 命题的 `expected_witnesses()` 含 ANT、不含 ANTHROPIC。
- [ ] 1.3 治理型观察器写入实体前做规范化（`governed_observer` 与 `chain/sources.to_observations`）。验证：新增测试，ANTHROPIC 的收入观测写入后实体为 ANT；同一证人不被计为两个说话人。
- [ ] 1.4 重跑 L1 三个证据观察器，逐项比对统一前后的状态与读数（规格 `sector/chain-layers`）。验证：差异清单写入 `verification.md`，用户确认。

## 2. 注册类文件入库（A2）

- [ ] 2.1 新增本地注册文件入库命令（D2），解析封面上的注册人名称与申报日期，按原始字节哈希保证幂等。验证：用测试夹具覆盖"身份不符被隔离"和"重复提交幂等"两种情形。
- [ ] 2.2 用该命令入库用户的首版 S-1（`~/Downloads/Anthropic, PBC - S-1 (Q2).html`）。验证：文档可按 ANT 检索，注册人与申报日期（2026-10-26）正确，来源标为本地提供。
- [ ] 2.3 `FORMS` 增加 S-1、S-1/A、424B4，只对配置声明需要补采的发行人生效；新增一次性补采命令。验证：测试确认其他 target 的每日发现不会拉取注册文件。
- [ ] 2.4 对 ANT 补采 S-1/A 与 424B4，并建立版本链。验证："截至今日最新的注册文件"返回 424B4；首版仍可读取；不产生财报事件。

## 3. SEC 例行通路与日历（A3）

- [ ] 3.1 用 SPCX 与 NVDA 的历史财报事件离线回放 发现 → 正文 → XBRL → 实际值抽取。验证：回放记录写入 `verification.md`，每一步都产出预期结果。
- [ ] 3.2 日历支持为缺财季标签的事件人工补充标签（规格 `scheduling/event-calendar`）。验证：新增测试，补充前事件不被标为已发布，补充后生成人工覆盖版本。
- [ ] 3.3 查询 Finnhub 与 Yahoo 是否已有 ANT 首份财报日期与财季标签。验证：结果写入 `verification.md`；没有的话按 3.2 人工登记。

## 4. 接入研究任务流（A4）

- [ ] 4.1 `entity_mentions` 支持按实体声明 `association_scope: headline`，ANT 设为 headline（规格 `data/unstructured-ingestion`）。验证：新增测试，正文顺带提到 Anthropic 时不关联，标题提到 Claude 时关联。
- [ ] 4.2 核对当前基本面未覆盖的 target 数量。验证：数量写入 `verification.md`；加上 ANT 后超过 4 个就停止并报告用户。
- [ ] 4.3 基本面例行任务遇到"无财报历史"时，发布带原因的未覆盖投影（规格 `agent/fundamental-pead`）。验证：新增测试覆盖"首份财报前未覆盖、首份财报入库后自动转为覆盖"。
- [ ] 4.4 与用户确认 `config/pead/ANT.yaml` 的打分维度与权重后写入（D6）。验证：PEAD 配置加载测试通过，权重合计为 1。
- [ ] 4.5 `config/pead.yaml` 的 targets 加入 ANT；`config/sectors/ai_hardware.yaml` 的 L1_app 把 ANT 加入 tickers（备注写明与 GOOG 定价机制不同）、`private` 去掉 Anthropic；`config/risk.yaml` 登记 `instruments.ANT: {layer_key: L1_app}`，并改写 L1 上限注释。验证：配置加载、行业层与风控登记测试通过。
- [ ] 4.6 更新 `docs/ARCHITECTURE.md` 中 L1 的描述，以及 `docs/OPERATIONS.md` 中注册文件入库命令的用法。验证：文档里的命令按写法能运行。

## 5. 试运行与基线核对（A5、A6）

- [ ] 5.1 试运行一天（不下单）：信息简报、基本面、技术面、风控日报、主理人完整运行。验证：ANT 简报候选数和误匹配比例、基本面为"未覆盖：无财报历史"、技术面为历史不足占位、风控日报把 ANT 归入 L1、主理人周期进入且不阻断，全部写入 `verification.md`。
- [ ] 5.2 与用户逐项核对 Claude 文稿的上市时基线表（首版 S-1 + 424B4），回填文稿；以 424B4 为准记录解禁日期，供 `ant-decision-integration` 使用。验证：基线表每项都标为"已核对"，解禁日写入 `verification.md`。

## 6. XBRL 映射（B1）

- [ ] 6.1 `XBRL_CONCEPTS` 增加 RPO、RPO 中 12 个月内确认的比例、购买义务及到期分布的标签。验证：新增解析测试；对一家已披露 RPO 的 target 做回放，结果与 10-Q 原文一致。

## 7. 披露指标管道（B2）

- [ ] 7.1 确认结构化存储中可复用的候选与隔离状态；没有的话设计候选表。验证：结论写入 design 的 D7 补注。
- [ ] 7.2 新增 `config/data/disclosure_metrics.yaml`，声明 ANT 的指标清单（规格 `data/company-disclosure-metrics`）。验证：配置加载测试；清单中的每项都有单位与期间类型。
- [ ] 7.3 实现章节定位 → 模型抽取 → 代码核验片段与数值 → 写入候选。同一文件版本与指标的结果缓存。验证：用测试夹具覆盖"片段不含数值则标为证据不完整"和"未披露不生成零值"。
- [ ] 7.4 实现确认、驳回、更正的 CLI 与发布。验证：测试覆盖确认人留痕、更正保留历史、多版文件冲突时取较新值。
- [ ] 7.5 报告首轮回放的请求数，经用户确认后用首版 S-1 与 424B4 回放。验证：每个候选都与原文逐项比对一致，经用户确认后发布为第一期数据。

## 8. 派生指标与收入序列（B3、B4）

- [ ] 8.1 实现边际承诺强度、RPO 覆盖率、宣传口径与 GAAP×4 的差距；输入缺失时返回"不可计算"。验证：新增测试覆盖正常计算与缺输入两种情形。
- [ ] 8.2 前沿实验室收入的趋势按来源与口径分开计算，公司自报单独成一条序列（规格 `data/frontier-ai-labs-revenue`）。验证：回归测试中 6 到 7 月的假下滑消失；商业化观察器报告与修复前对比，差异由用户确认。

## 9. OpenRouter 价格与估算花费份额（C1）

- [ ] 9.1 新增模型价格采集（公开模型列表），按 vintage 保存。验证：采集测试；接入一次真实数据，与 OpenRouter 网页抽查 3 个模型的价格一致。
- [ ] 9.2 实现估算花费份额：声明输入输出比例、无价格桶、质量警告，以及价格采集前的期间返回"无价格数据"（规格 `data/openrouter-rankings`）。验证：新增测试；用户确认比例假设。
- [ ] 9.3 更新 `docs/data/` 中 OpenRouter 的方法说明。验证：文档写明估算口径和不能作为收入的原因。

## 10. 前沿评测（C2）

- [ ] 10.1 发布日期改为查询发布登记，查不到就留空并标注；去掉写死的旗舰模型 ID。验证：新增测试，库中的 Anthropic 记录发布日期不再等于成绩日期。
- [ ] 10.2 恢复解析 Artificial Analysis 公开页面上的成本字段。验证：解析测试；与网页抽查一致。
- [ ] 10.3 对 SWE-bench Pro、GDPval-AA、AA 智能指数、Epoch、GDPx、Harvey LAB-AA、APEX-Agents-AA 逐项做可得性检查。验证：检查表写入 `verification.md`，由用户决定接入哪些。
- [ ] 10.4 为通过检查的评测逐个实现适配器。验证：每个适配器有解析测试；接入后与网页抽查一致，用户验收。

## 11. 模型发布登记（C3）

- [ ] 11.1 新增 `config/data/model_releases.yaml` 与查询接口，支持按时点回放与更正留痕（规格 `data/model-release-registry`）。验证：新增测试覆盖"候选不能用于计时"和"更正后按旧时点回放"。
- [ ] 11.2 信息简报的模型发布成分生成候选，并提供确认命令。验证：测试；用当日真实简报做一次演练。
- [ ] 11.3 与用户一起登记近 12 个月各实验室的通用旗舰发布。验证：每条都附官方公告链接，用户确认。

## 12. GPU 租赁价格（C4）

- [ ] 12.1 免费来源可得性调研。验证：调研表（来源、型号、形态、历史深度、可追溯性、未采用原因）与付费选项清单写入 `verification.md`，用户决定。
- [ ] 12.2 有合格免费来源时实现适配器，否则只发布"未覆盖"状态（规格 `data/gpu-rental-price`）。验证：对应的测试；下游能读到状态。

## 13. 集成核对

- [ ] 13.1 阶段关口：在后台跑数据层与研究任务流的回归测试。验证：结果与 `docs/TEST_BASELINE.md` 的基线对比，新增失败为 0。
- [ ] 13.2 汇总 Claude 文稿约 25 个观测项的最新覆盖状态（已覆盖、部分、未覆盖及原因）。验证：状态表写入 `verification.md`，供 `ant-thesis-monitor` 使用。
