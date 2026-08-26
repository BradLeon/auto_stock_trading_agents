# 结构化数据层运维指南

> 适用变更：`build-structured-data-foundation`
> 文档状态：阶段二实现基线（2026-08-25）
> 读者：数据源运维者、发布负责人、故障处理者

## 1. 运维目标

结构化数据层的运维目标不是“定时任务没有报错”，而是持续回答五个问题：

1. Coverage：应该覆盖的实体、指标和期间覆盖了多少？
2. Accuracy / Reconciliation：实体、期间、单位是否正确，多来源差异是否在门槛内？
3. Freshness：最新数据是否在来源发布后按预期进入系统？
4. Completeness：必需字段、期间连续性和证据是否完整？
5. Availability：来源是正常、未发布、无覆盖、不可达还是未授权？

任何报告都不得把缺失写成 0，也不得把 `not_yet_published`、`no_coverage` 和 `unreachable` 合并成一个笼统的 missing。

## 2. 状态标记

| 标记 | 含义 |
|---|---|
| `CURRENT` | 当前代码已经存在 |
| `CURRENT_PARTIAL` | 已实现并完成专项验收，但消费者切换、覆盖或生产观察尚未全部完成 |
| `TARGET` | 本 OpenSpec 变更内接入或补齐 |
| `DEFERRED` | 已知但本变更不接入 |
| `RUNTIME/EXCLUDED` | 仅按需查询，明确禁止写入结构化持久层 |

## 3. 来源覆盖矩阵

### 3.1 持久来源

| 来源 / Dataset | 状态 | 主要内容 | 认证 | 更新节奏 | 保存策略 | 当前消费者 |
|---|---|---|---|---|---|---|
| 台湾财政部 data.gov.tw / `regional_tw_exports` | `CURRENT_PARTIAL` | 台湾电子零组件/IC 出口水平 | 无密钥 | 月度 | 保存响应 artifact、标准化 vintage 与派生结果 | Chain（已完成切换/回滚演练） |
| 韩国银行 ECOS / `regional_kr_exports` | `CURRENT_PARTIAL` | 韩国半导体出口金额指数 | 可用 sample；生产建议 `KR_ECOS_API_KEY` | 月度 | 保存查询 slice、标准化 vintage 与派生结果 | Chain（已完成切换/回滚演练） |
| TrendForce / `industry_dram_contract_price` | `CURRENT_PARTIAL` | DRAM 合约价公开摘要 | 无密钥，页面访问 | 月度/页面更新 | 受页面与条款约束，保存必要响应与血缘 | Chain evidence |
| SEC Company Facts / `company_financials` | `CURRENT_PARTIAL` | 美国发行人官方 XBRL 财务事实 | 描述性 `SEC_EDGAR_USER_AGENT` | 事件/季度 | 保存 Company Facts artifact、filing 与观测版本 | PEAD、Sector（保持 legacy） |
| 等价外国发行人公司披露 / `company_financials` | `PLANNED` | 20-F/6-K 或本地/公司官方财务 | 依来源 | 事件/半年/年度 | 按来源条款 | 尚未切换 |
| defeatbeta / `company_financials` | `CURRENT_PARTIAL` | Yahoo Finance 上游的 `stock_statement` 镜像切片 | 公开数据集或 HF token（以实际访问为准） | 上游快照 | 仅保存目标实体/期间 slice 与双层 provenance | 财务补充、对账 |
| yfinance / `market_consensus` | `CURRENT_PARTIAL` | EPS/收入预测、区间、目标价、评级 | 无正式 API 凭证 | 按抓取时点 | 每次真实 snapshot；不伪造历史 | PEAD、Sector（platform） |
| accepted documents / `private_company_events` | `CURRENT_PARTIAL` | OpenAI/Anthropic 融资、估值、ARR 候选 | 复用文档层 | 事件 | 不复制正文，仅保存 evidence link、候选与核验记录 | Evidence、研究 Agent |

`CURRENT_PARTIAL` 不等于生产默认源。真实专项报告达标后仍需按消费者开关完成 shadow、回滚与完整更新周期观察。

### 3.2 机器目录一致性索引

下表逐行镜像 `config/structured_data.yaml`。来源 ID、状态或数据集发生变化时，配置、本文和一致性测试必须在同一个提交中更新。

| source_id | catalog_status | persistence | datasets |
|---|---|---|---|
| `tw_mof_exports` | `current_partial` | `persistent` | `regional_tw_exports` |
| `kr_ecos_exports` | `current_partial` | `persistent` | `regional_kr_exports` |
| `trendforce_dram` | `current_partial` | `persistent` | `industry_dram_contract_price` |
| `sec_companyfacts` | `current_partial` | `persistent` | `company_financials` |
| `company_disclosures` | `planned` | `persistent` | `company_financials` |
| `defeatbeta_stock_statement` | `current_partial` | `persistent` | `company_financials` |
| `yfinance_consensus` | `current_partial` | `persistent` | `market_consensus` |
| `accepted_document_evidence` | `current_partial` | `persistent` | `private_company_events` |
| `ibkr_market` | `runtime_excluded` | `runtime` | — |
| `yfinance_market` | `runtime_excluded` | `runtime` | — |
| `yfinance_options` | `runtime_excluded` | `runtime` | — |
| `thetadata_options` | `runtime_excluded` | `runtime` | — |

| dataset_id | catalog_status |
|---|---|
| `regional_tw_exports` | `current_partial` |
| `regional_kr_exports` | `current_partial` |
| `industry_dram_contract_price` | `current_partial` |
| `company_financials` | `current_partial` |
| `market_consensus` | `current_partial` |
| `private_company_events` | `current_partial` |

### 3.3 Runtime / excluded

以下数据不是“未接入”，而是产品决策上明确排除：

| 来源 | 数据 | 路径 | 持久化要求 |
|---|---|---|---|
| IBKR / TWS | 账户、持仓、成交、实时市场数据、model Greeks | `ats.broker` / trader / risk | 不写 structured repository；交易 Journal 按原职责记录决策或成交 |
| yfinance | ticker 股价、OHLCV、即时 market info | `ats.data.market_data` / `sector_snapshot` | 不建立日线或历史副本 |
| yfinance | 期权链 | `ats.data.options` fallback | 不写期权链、Greeks、IV vintage |
| ThetaData | 期权 EOD / IV / skew | `ats.data.options` | 不写 structured repository |

验收必须运行边界测试：调用上述接口前后，结构化数据库行数和 artifact 目录不得新增行情/期权记录。

### 3.4 已知但未接入

| 来源 / 数据 | 状态 | 原因 |
|---|---|---|
| FactSet / Bloomberg / 其他付费财务与 Consensus | `DEFERRED` | 当前无授权接入；未来并列保存，不覆盖历史来源 |
| 其他国家海关、产业月度统计 | `DEFERRED` | 先用台湾/韩国验证平台形状，再逐源提案 |
| 全行业融资、ARR 自动抽取 | `DEFERRED` | 首批仅验证少量带证据和人工核验样本 |
| ticker 日线、期权历史仓库 | `RUNTIME/EXCLUDED` | 不是扩源待办；除非未来另立 OpenSpec 重新论证 |

## 4. 凭证与配置

### 4.1 当前环境变量

```dotenv
ATS_DB_PATH=/absolute/path/to/ats.sqlite
ATS_STRUCTURED_DB_PATH=/absolute/path/to/structured.sqlite
ATS_STRUCTURED_ARTIFACT_ROOT=/absolute/path/to/structured_artifacts
SEC_EDGAR_USER_AGENT=your-app your-email@example.com
KR_ECOS_API_KEY=
FINNHUB_API_KEY=
```

实际字段以 [`.env.example`](../.env.example) 和 `ats.config.Secrets` 为准。密钥只放 `.env` 或运行环境，不进入 YAML、测试 fixture、报告或 Git。

未设置 `ATS_STRUCTURED_DB_PATH` 时沿用 `ATS_DB_PATH`；未设置 artifact 根目录时使用仓库下 `var/structured_artifacts`。上线前应使用绝对路径，避免 scheduler 工作目录变化导致写入另一套存储。

### 4.2 隔离测试环境

任何真实源 smoke、回填或迁移必须使用隔离目录：

```bash
export ATS_DB_PATH="/private/tmp/ats-structured-smoke/structured.sqlite"
export ATS_DOCS_ROOT="/private/tmp/ats-structured-smoke/documents"
export ATS_STRUCTURED_DB_PATH="/private/tmp/ats-structured-smoke/structured.sqlite"
export ATS_STRUCTURED_ARTIFACT_ROOT="/private/tmp/ats-structured-smoke/artifacts"
```

运行前后都要打印解析后的数据库与 artifact 路径。禁止以默认 `var/ats.sqlite` 运行破坏性回填或首次迁移试验。

### 4.3 从新增来源到发布：可执行 Runbook

运维入口由四个位置共同组成，缺一不可：

| 要改/要看什么 | 唯一位置 | 作用 |
|---|---|---|
| 来源、数据集、指标、QPS/预算、质量门、默认开关 | `config/structured_data.yaml` | 机器目录与 checked-in 发布基线 |
| Provider 适配器 | `src/ats/data/sources/` | 获取 Provider 原生批次，不直接写表 |
| adapter key 到工厂的受控注册 | `src/ats/structured/runtime_registry.py` | 让统一校验和采集命令能够构造适配器 |
| 运行发布覆盖层 | `var/structured_data/releases.yaml` | 不改 Git 配置的 source/consumer mode 与审计历史 |

凭证仍只放 `.env` 或部署环境。新增来源不是只加一段 YAML：还必须实现 Adapter，并在 `_RUNTIMES` 注册稳定 adapter key。`validate-source` 会同时检查 source/dataset 互相引用、请求预算、质量门、验收样本、runtime 边界、运行注册和工厂可构造性。

以下命令均可直接复制。示例以 SEC 为例；替换 source/entity 即可用于其他统一 Adapter。

```bash
# 1. 只读：配置与运行注册校验，不联网、不写库
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data validate-source \
  --source sec_companyfacts

# 2. 联网并写入隔离库；--force 只允许用于隔离验收，不能解除 runtime/excluded
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data ingest \
  --source sec_companyfacts --entity MSFT --force \
  --db /private/tmp/ats-structured-sec/structured.sqlite \
  --artifact-root /private/tmp/ats-structured-sec/artifacts

# 3. 只读：检查隔离库实际覆盖、质量、运行历史与可查询示例
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data catalog --format markdown \
  --db /private/tmp/ats-structured-sec/structured.sqlite \
  --artifact-root /private/tmp/ats-structured-sec/artifacts
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality \
  --dataset company_financials --format markdown \
  --db /private/tmp/ats-structured-sec/structured.sqlite \
  --artifact-root /private/tmp/ats-structured-sec/artifacts
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data examples \
  --dataset company_financials \
  --db /private/tmp/ats-structured-sec/structured.sqlite \
  --artifact-root /private/tmp/ats-structured-sec/artifacts

# 4. 默认仍是只读预检；不带 --apply 不会改变运行状态
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data publish \
  --source sec_companyfacts --mode shadow

# 5. 显式开放生产 shadow 采集，再使用生产库执行一次非 force 采集
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data publish \
  --source sec_companyfacts --mode shadow --apply
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data ingest \
  --source sec_companyfacts --entity MSFT

# 6. 只读 platform 发布门；逐项显示 registration、最新运行和五维质量
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data release-check \
  --source sec_companyfacts --mode platform

# 7. 只有上一步 ready=true，显式 --apply 才会写 release overlay
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data publish \
  --source sec_companyfacts --mode platform --apply

# 8. 查看当前运行覆盖层、动态目录与健康状态
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data releases
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data catalog --format markdown
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data health

# 9. 非破坏回滚；默认预览，--apply 后改回 legacy，历史数据不会删除
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data rollback \
  --source sec_companyfacts --mode legacy --apply
```

`publish --consumer <id> --mode platform` 与 source 发布相互独立。consumer 只有在 `config/structured_data.yaml` 的 checked-in 基线已表明完成 reconciliation 后才能切到 platform；运维覆盖层不能绕过代码审查和消费者对账。

发布判定不是“命令退出码为 0”或“HTTP 200”。最少通过标准如下：

| 阶段 | 必须满足 |
|---|---|
| `validate-source` | `valid=true`，没有未注册 Adapter、缺预算、缺质量门或 runtime 越界 |
| 隔离 `ingest` | 明确终态；accepted/quarantined/unchanged 能和 artifact 对账；实体/期间/单位人工抽查正确 |
| `quality` | 关联数据集 `overall_status=passed`；关键冲突、陈旧、pending mapping 满足 dataset 阈值 |
| `release-check` | `ready=true`；最近运行必须为 `succeeded` 或 `no_change` |
| `publish --apply` | release overlay 产生 history；`data catalog` 显示新 mode |
| 回滚演练 | mode 恢复，既有 artifact/vintage/失败记录仍可查，其他 source/consumer 不受影响 |

发布模式解析优先级是：环境变量 → `releases.yaml` → `config/structured_data.yaml` → `legacy`。环境变量适合一次进程调试；release overlay 适合运维切换；主配置记录经过代码审查的长期基线。

## 5. QPS、限流和内部请求预算

外部限制只在有官方或可验证依据时填写；否则标记 `unknown`。内部预算是系统的保守配置，不代表 Provider 官方承诺。

| 来源 | 外部 QPS / 限制 | 首期内部预算 | 并发 | 超时与重试原则 |
|---|---|---:|---:|---|
| 台湾财政部 dataset API + CSV | `unknown` | 每次任务 1 次 metadata + 1 次 CSV；同一运行复用 | 1 | 30s/60s；最多 2 次指数退避 |
| 韩国 ECOS | sample key 每次最多 10 行是当前代码已知约束；QPS `unknown` | 分页串行；月度任务单数据集 | 1 | 30s；429/5xx 退避，不并发翻页 |
| TrendForce 公开页面 | `unknown` | 每次运行 1 个页面；缓存同一响应 | 1 | 30s；失败不密集重试 |
| SEC EDGAR | 以 SEC 当前 fair-access 政策为准；部署前复核 | 内部上限 5 req/s，单进程并发 1，批量请求复用 company facts | 1 | 429/403 立即降速；指数退避；始终发送描述性 UA |
| HuggingFace / defeatbeta | `unknown`，可能受 CDN、数据集和账号策略影响 | 每实体/期间 predicate slice；禁止下载全量数据集 | 1 | 60s；失败按切片隔离，缓存命中 slice |
| yfinance Consensus | 非正式公共接口，QPS `unknown` | 每 ticker 串行，默认间隔至少 1s；同一 Workflow 共享结果 | 1 | 限流/crumb 失败退避；不得并发扫全 universe |
| 阶段一文档层 | 本地读无外部 QPS | 只读 accepted version/span | 本地 | 无证据不进入核验队列 |

上线负责人必须在每次 Provider 适配升级前复核其服务条款和官方限流页面。若外部政策与本文不同，以 Provider 当前政策为准，并在同一变更中更新配置、测试和本文。

## 6. 调度原则

- 月度官方数据按公布节奏运行，不用高频轮询弥补不确定发布时间。
- 财务官方源围绕 filing / earnings event 触发，并保留低频补漏任务。
- `stock_statement` 用于补充与对账，不得比官方源更高频地重扫全 universe。
- Consensus 只有从首次真实抓取起才有可信历史。抓取节奏由 PEAD 目标事件和新鲜度门决定。
- 文档型融资/ARR 候选按 accepted 文档增量触发，不扫描已处理版本。
- 单来源失败只影响该 source/dataset/entity/slice，不能阻断其他来源。

## 7. 采集运行状态

每次运行必须落在一个明确终态：

| 状态 | 运维解释 | 是否报警 |
|---|---|---|
| `succeeded` | 有新 accepted vintage | 否 |
| `no_change` | 来源正常，内容无变化 | 否 |
| `zero_match` | 查询有效但无匹配记录 | 视目标覆盖而定 |
| `not_yet_published` | 尚未发布 | 否；超过发布窗口才报警 |
| `no_coverage` | Provider 不覆盖 | 否；应进入覆盖矩阵 |
| `stale` | 最新值超过用途门槛 | 是 |
| `unreachable` | 网络、DNS、TLS、服务故障 | 连续失败报警 |
| `unauthorized` | 凭证/订阅/权限问题 | 立即报警 |
| `parse_failed` | 响应结构变化 | 立即报警并保存 artifact |
| `validation_failed` | 候选未通过准入 | 按比例和关键指标报警 |
| `partial` | 部分实体/切片成功 | 是；成功部分保留 |

“没有新增行”不能自动判定为 unreachable；“HTTP 200”也不能自动判定为 succeeded。

## 8. 质量门与首批通过标准

阈值最终由 machine-readable dataset 配置控制。以下是首批验收基线，真实样本暴露合理差异时可以通过 OpenSpec 更新，但不得为让测试变绿而临时放宽。

### 8.1 区域月度序列

- 目标回填窗口月份覆盖率：100%，已明确 `not_yet_published` 的月份不计缺失。
- 重复运行新增重复 vintage：0。
- 月份连续性：100%；来源确有断档时必须有 reason code。
- 原始水平与官方 fixture：精确一致，浮点只允许解析精度误差。
- yoy/mom 与平台公式：容差 `1e-9`；缺历史返回 missing。
- 真实源 smoke：最新期间、单位和发布时间/可见时间人工核对。

### 8.2 财务与 stock_statement

- 首批核心指标映射率：按实体和期间报告；未映射字段必须全部出现在 pending pool。
- 官方值与原始 XBRL/公司披露抽样：100% 一致。
- 官方与镜像差异：逐项展示，不覆盖、不平均；超容差即 conflict。
- 财期连续性、单季/累计、币种和单位缩放必须通过。
- 基本 statement 恒等检查给出 pass/warn/fail，不因缺行制造伪失败。
- 真实样本：AMZN、MSFT、KLAC、TSM 加至少一个镜像缺失实体。
- 财务质量门达标前，PEAD fundamentals 保持 legacy；可以 shadow 对账但不得切 platform。

### 8.3 Consensus

- 每个 snapshot 的目标财期必须解析为具体公司期间或目标事件。
- EPS/收入预测、区间、目标价、评级等按字段报告 completeness，不以默认值填缺失。
- NaN 转为 missing，不得保存为数字或 0。
- `as_of` 早期查询不得使用后期 snapshot。
- 超过用途配置的 snapshot age 标记 stale。
- 旧 PEAD DTO 对账覆盖值、缺失语义、期间和来源。

### 8.4 证据型融资、估值、ARR

- 首批发布样本人工抽查率：100%。
- 实体、数值、单位、事件/期间、原文位置正确率：100%，否则不进入默认查询。
- 每个 accepted 观测必须有 document/version/span 和核验记录。
- 模型置信度不能单独使候选 accepted。
- 多篇转述同一融资事件不得形成多个融资事件。

## 9. 当前运维命令

```bash
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data health
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data sources
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data datasets
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data metrics
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series --source tw_ic_exports --entity TW_IC_EXPORT
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series --source tw_ic_exports --vintages
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data series --source tw_ic_exports --as-of 2026-08-01T00:00:00+00:00
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data coverage --dataset company_financials
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data quality --dataset market_consensus --format markdown
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data lineage <observation-or-snapshot-id>
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data conflicts --dataset company_financials
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data pending-mappings
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data ingestion-history --source sec_companyfacts
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli data artifacts --source sec_companyfacts
```

不带 `--dataset` 的 `data quality` 同时返回既有文档质量和 structured 五维报告；带 `--dataset` 时只返回该结构化数据集。Markdown 与 JSON 使用同一份机器结果。

## 10. 日常检查清单

### 每次采集后

1. 确认运行终态和持续时间。
2. 对账 discovered、accepted、quarantined 和 reason codes。
3. 检查最新 period、published_at、known_at 和 snapshot lag。
4. 检查 pending mapping 是否新增。
5. 检查多来源 conflict 和回退原因。
6. 检查 artifact 哈希去重率和新增磁盘用量。
7. 对关键数据集抽查一条 raw → normalized → query 血缘。

### 每周

1. 生成 structured 五维质量 Markdown 报告。
2. 检查连续失败、陈旧来源和未授权来源。
3. 检查隔离候选增长是否异常。
4. 检查 SQLite 文件大小、写锁等待和查询延迟。
5. 检查来源状态是否与本矩阵一致。

### 每个完整更新周期

1. 运行旧路径 shadow reconciliation。
2. 比较关键消费者的输入和输出差异。
3. 复核 feature flag 和回滚仍可用。
4. 保存验收报告，不只保留终端截图。

## 11. 故障处理

### 11.1 来源不可达

1. 先区分本机网络/代理、DNS/TLS、Provider 故障和限流。
2. 保留错误类型、HTTP 状态和运行时间；日志不得包含密钥。
3. 对 SEC 等来源检查代理节点和 User-Agent，再判断是否限流。
4. 不用空数组覆盖之前的健康数据。
5. 只重跑失败 source/entity/slice，不重跑全部来源。

### 11.2 解析失败

1. 冻结失败响应 artifact。
2. 用该响应增加 fixture，再修改解析器。
3. 验证旧 fixture 仍通过，防止只适配新页面。
4. 重跑 normalization/admission，不必重新下载相同 artifact。

### 11.3 单位或期间冲突

1. 将候选保持 quarantined。
2. 检查 Provider 原字段、公司财年结束日和累计/单季口径。
3. 修复版本化 mapping，重做标准化。
4. 不直接修改 accepted 数值，不删除旧审计记录。

### 11.4 SQLite 锁或性能异常

1. 记录数据库大小、并发写数、锁等待和慢查询。
2. 确认采集是否错误并发，尤其是同一 dataset 分页。
3. 优先批量事务、索引和 repository 优化。
4. 只有有可复现基准证明 SQLite 不满足首期阈值时，才提出物理迁移变更。

## 12. 新增来源的上线流程

| 步骤 | 运维交付物 | 通过标准 |
|---|---|---|
| 注册 | 来源矩阵、owner、用途、认证、保存政策、预算 | 信息完整；未知限制显式标记 |
| Fixture | 成功、空、未发布、限流、权限、字段变化样本 | 离线 contract tests 全通过 |
| 隔离采集 | 独立 DB/artifact 的端到端报告 | 不写生产；总数可对账 |
| 真实 smoke | 少量代表实体/期间 | 实体、期间、单位、最新性人工核对 |
| 质量门 | 五维报告、pending/conflict 清单 | 达到数据集阈值 |
| Shadow | 新旧值与缺失语义差异报告 | 容差内或差异有解释 |
| 切换 | 单消费者 feature flag、回滚演练 | 回滚成功，其他消费者不受影响 |
| 观察 | 至少一个完整更新周期 | 无质量门失败和不可解释漂移 |

未完成任何一步，都不能将来源标记为“已接入默认查询”。

## 13. 备份、回滚与退役

- 生产迁移前备份 SQLite 和 artifact manifest；不只备份数据库而漏掉内容文件。
- 回滚使用 source/consumer feature flag 恢复 legacy 读路径。
- 新表、新 vintage 和 artifact 不因回滚删除，保留只读审计价值。
- 原始 artifact 保留期在首次真实存储用量报告后确定；此前禁止自动清理未知血缘文件。
- 退役来源前确认没有 snapshot manifest、消费者或派生定义依赖它。
- 删除旧持久取数逻辑不属于本变更，必须另立 OpenSpec 并重新做 Workflow 验收。

## 14. 发布验收清单

- 确定性单元、迁移、adapter contract、查询一致性和文档检查全部通过。
- 隔离真实源采集没有写入生产路径。
- 质量报告能与 ingestion runs、accepted、quarantined、pending mappings 逐项对账。
- 至少一组财务修订和两期 Consensus snapshot 通过严格 `as_of` 无前视测试。
- 台湾/韩国水平值与平台 yoy/mom 和旧 Chain 输出完成对账。
- 股价与期权边界测试证明没有持久写入。
- PEAD、Sector、Chain 完整 Workflow 运行和关键输入/输出差异已记录。
- 开发者、运维者、使用者文档的状态、命令和来源矩阵与实现一致。

## 15. 相关文档

- [总体数据架构](DATA_ARCHITECTURE.md)
- [结构化数据开发者指南](STRUCTURED_DATA_DEVELOPER.md)
- [结构化数据使用手册](STRUCTURED_DATA_USER_GUIDE.md)
- [五维质量验收报告](STRUCTURED_DATA_QUALITY_VALIDATION_2026-08-25.md)
- [消费者迁移与重放验收](STRUCTURED_DATA_CONSUMER_VALIDATION_2026-08-25.md)
- [开发与测试约定](DEVELOPMENT.md)
