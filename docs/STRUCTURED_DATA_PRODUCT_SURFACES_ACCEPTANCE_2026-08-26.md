# 结构化数据可操作产品面补充验收

> 日期：2026-08-26
> OpenSpec change：`build-structured-data-foundation`
> 范围：来源接入—评测—发布闭环、动态数据目录、Agent 消费 Skill 与角色文档

## 1. 结论

上一版交付了结构化底座，但运维者仍缺少统一的来源生命周期，使用者和 Agent 也无法从实际数据库确认“当前能查什么”。本次已把这三项从说明性文档补成可执行产品接口：

1. 来源可以通过统一注册校验、隔离采集、质量预检、显式发布和非破坏回滚完成上线。
2. 数据目录、对象说明、实体/数据集可用性和查询示例由机器目录与当前 repository 动态生成。
3. 自主 Agent 使用仓库内 `structured-data-consumer` Skill；确定性 Workflow 继续依赖 DataProducts/API，不依赖 Prompt。

## 2. 运维生命周期验收

### 2.1 实际入口

| 接口 | 位置/命令 | 验收结果 |
|---|---|---|
| 机器配置 | `config/data/structured.yaml` | source/dataset/metric、预算、质量门、默认 mode 统一登记 |
| 运行注册 | `src/ats/data/adapters/structured/registry.py` | YAML adapter key 只映射到受控工厂，不允许任意动态 import |
| 配置校验 | `ats data validate-source --source <id>` | 返回逐项 checks 与稳定 reason codes；不联网、不写库 |
| 隔离采集 | `ats data ingest --source <id> --force --db <path> --artifact-root <path>` | DB/artifact 均可定向到隔离目录；runtime/excluded 仍不可绕过 |
| 发布门 | `ats data release-check --source <id> --mode platform` | 联合运行注册、最近采集和关联数据集五维质量 |
| 发布/回滚 | `ats data publish|rollback ... --apply` | 默认只预检；`--apply` 才写 release overlay；历史数据不删除 |
| 运行审计 | `ats data releases` | 显示 source/consumer mode 与 history |

运行模式解析顺序为：环境变量 → `var/structured_data/releases.yaml` → checked-in 配置 → `legacy`。来源和消费者独立切换；consumer platform 不能通过 overlay 绕过 checked-in reconciliation 批准。

### 2.2 当前来源运行注册结果

| 来源 | 统一远程采集 | 说明 |
|---|---|---|
| `tw_mof_exports` | 通过 | 官方月度序列 Adapter |
| `kr_ecos_exports` | 通过 | 官方月度序列 Adapter |
| `trendforce_dram` | 通过 | 本次补为统一 batch，并保留原始 HTML artifact |
| `sec_companyfacts` | 通过 | 需要 entity；官方 Company Facts |
| `defeatbeta_stock_statement` | 通过 | 需要 entity；远程 predicate slice |
| `yfinance_consensus` | 通过 | 需要 entity；真实抓取 snapshot |
| `company_disclosures` | 未通过 | 目录状态仍为 planned，尚无运行 Adapter |
| `accepted_document_evidence` | 不适用 | 通过 EvidenceWorkbench 候选—人工核验—发布，不走远程 fetch |
| IBKR/yfinance/ThetaData 行情与期权 | 拒绝 | `runtime_excluded`，不能 ingest/publish 到持久层 |

## 3. 动态数据发现验收

新增接口：

```text
ats data catalog [--format markdown]
ats data describe <source|dataset|metric|entity>
ats data availability --dataset <id> [--entity <id>]
ats data examples --dataset <id>
```

同一能力也由 `DataProducts.catalog()`、`describe()`、`availability()` 和 `examples()` 提供。目录明确分开：

- `catalog_status`：代码和能力成熟度；
- `release_mode`：当前运行选择；
- `accepted_observations`：实际可查询行数；
- `availability`：`queryable`、`registered_no_data` 或 `runtime_excluded`；
- `quality_status`：当前五维质量结论。

在本次验收使用的默认数据库中，动态目录实际发现 84 条 accepted Consensus observation，覆盖 MRVL、NVDA；MSFT 查询明确返回 `no_coverage`，没有套用文档示例伪造覆盖。以早于 MRVL 首次 `known_at` 的时点查询，返回 `not_yet_known`。`ibkr_market` 被描述为 `runtime_excluded`，没有结构化 dataset 或 observation。

`examples` 从 accepted observation 选择真实 dataset/metric/entity/observation ID；空库或无覆盖返回 `no_data`，不会生成不可执行的静态例子。

## 4. Agent Skill 与 Workflow 边界验收

Skill：`.agents/skills/structured-data-consumer/SKILL.md`

- 规定自主 Agent 先 `catalog/describe/availability/examples`，再执行 series/derive/cross-section/lineage。
- 不复制静态指标清单，避免目录扩展后 Skill 漂移。
- 要求保留 source、period、known_at、quality、conflict/fallback、lineage 与 `as_of`。
- 将股价、期权、Greeks、IV 路由到 runtime Adapter，并说明其不进入 structured snapshot replay。
- 明确本 Skill 只负责消费；没有用户授权时不得 ingest、publish、rollback 或修改 mapping。

Skill 的 `agents/openai.yaml` 已启用正常自动发现，并通过 `skill-creator` quick validation。PEAD、Sector、Chain 等确定性 Workflow 仍直接调用 DataProducts/兼容 DTO 和 feature flags，Skill 未加载时其契约也成立。

## 5. 文档交付

- [开发者指南](STRUCTURED_DATA_DEVELOPER.md)：新增运行注册、release overlay、动态目录、Skill/API 分工的组件图和扩源步骤。
- [运维指南](STRUCTURED_DATA_OPERATIONS.md)：新增从配置到隔离采集、质量门、shadow、platform、监控和回滚的完整可复制 Runbook。
- [使用手册](STRUCTURED_DATA_USER_GUIDE.md)：新增动态发现四步法、CLI 派生/横截面、DataProducts discovery API 与 Agent/Workflow 使用边界。
- [总体架构](DATA_ARCHITECTURE.md)：新增上述三类入口与 Skill 链接。

## 6. 测试证据

| 验收项 | 结果 |
|---|---:|
| OpenSpec strict validation | 通过 |
| Skill quick validation | 通过 |
| 结构化专项（首次汇总） | 103 passed |
| 来源生命周期/TrendForce 补充专项 | 25 passed |
| 完整仓库回归（补充前） | 1033 passed |
| 完整仓库回归（最终，含 TrendForce 与文档） | 1034 passed in 65.06s |

TrendForce 统一 Adapter、Skill、角色文档和契约测试全部进入最终回归；OpenSpec strict、`git diff --check` 与完整仓库测试均通过，task 17.4 可以完成。

## 7. 仍然明确存在的边界

- `company_disclosures` 仍是 planned；外国发行人等价官方披露接入需要后续来源实现，不能因生命周期框架存在就宣称已有数据。
- `accepted_document_evidence` 是人工核验工作台，不是远程批量采集 Adapter。
- 动态目录显示的是当前指定数据库；不同 `--db`、环境或时点结果可以不同，静态验收报告不能替代现场运行 `data catalog`。
- 本次没有把 ticker 日线、期权或其他高频行情纳入持久层。
