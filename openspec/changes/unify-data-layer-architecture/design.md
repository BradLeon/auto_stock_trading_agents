## Context

当前代码的职责边界是按历史演进形成的：`ats.data` 同时包含 Provider 适配器和业务数据模块，`ats.structured` 聚合了结构化目录、采集、质量、存储和查询，`ats.data_platform` 提供消费者 facade，`ats.memory` 又同时管理 Workflow 记忆、文档资产、证据和测量表。配置也分散在 `structured_data.yaml`、`sources.yaml`、`news_sources.yaml` 和 Workflow 配置中。

本设计承接 `proposal.md` 和 `specs/data/data-layer-architecture/spec.md`。目标是先建立稳定的命名空间和责任契约，再逐步移动实现；不要求一次性重写已有 Provider 或数据库。

## Goals / Non-Goals

**Goals:**

- 让结构化和非结构化能力拥有同一个 `ats.data` 根命名空间，并按生命周期划分组件。
- 建立适配器、采集管道、存储、数据产品和 runtime 的单向依赖。
- 用统一 catalog 管理来源、数据集、状态、调度和质量门，同时保留结构化/非结构化配置的领域差异。
- 让 `ats.data_platform` 和 `ats.structured` 在迁移期成为兼容转发层。
- 拆分数据层 repository 的代码所有权，降低 `ats.memory` 的混合职责。
- 为开发者、运维者、使用者提供可执行且与代码同步的文档。

**Non-Goals:**

- 本变更不引入新的外部数据 Provider，不扩大数据覆盖范围。
- 本变更不把文档、数值观测和 Workflow 决策强行合并为同一数据模型或同一查询表。
- 本变更不持久化 ticker 股价、OHLCV、订单簿、期权链、Greeks 或隐含波动率。
- 本变更不在第一阶段删除旧表、旧配置或旧导入路径。
- 本变更不决定向量数据库、知识图谱或独立时序数据库的选型。

## Decisions

### 1. 以数据生命周期组织目录，而不是以信息类型建立两个平台

目标目录如下：

```text
src/ats/data/
├── core/                 # entity/source/lineage/quality/run 等共享契约
├── catalog/              # 配置模型、加载、引用校验
├── adapters/
│   ├── structured/       # SEC、Yahoo、defeatbeta、Consensus 等数值 Provider
│   └── unstructured/     # SEC 文档、release、transcript、RSS、研报等
├── pipelines/
│   ├── common/           # ingestion、admission、去重、发布
│   ├── structured/       # 数值标准化、准入、vintage
│   └── unstructured/     # 文档清洗、抽取、分块、证据准入
├── stores/
│   ├── structured/       # observations、artifacts、derived views
│   └── unstructured/     # documents、versions、chunks、evidence
├── products/             # structured、unstructured、combined、discovery
├── runtime/              # 即时行情和期权，不进入持久层
└── compat/               # 旧入口的转发和弃用标记
```

选择该方案是因为采集、存储、查询和运行时边界比“文章/表格”更能决定依赖方向。若按 `structured/` 和 `unstructured/` 各自复制一套完整平台，会重新产生两套 source registry、运行记录和质量机制。

### 2. `data_platform` 归入 products，`structured` 归入 data 实现

`DataProducts` 是消费者读模型，不是独立的数据域，因此正式实现迁移到 `ats.data.products`。`ats.structured` 当前包含多个生命周期职责，按职责拆到 `catalog`、`pipelines.structured`、`stores.structured` 和 `products.structured`。

迁移期保留：

```python
from ats.data.products import get_data_products       # 新入口
from ats.data_platform import get_data_products       # 兼容入口
from ats.data.structured import get_repository         # 新结构化入口
from ats.structured import get_repository              # 兼容入口
```

兼容模块只允许 re-export 或调用新实现，不允许继续添加业务逻辑。

### 3. 依赖方向由架构测试固定

依赖规则固定为：

```text
agents/workflows
        ↓
data.products / data.runtime
        ↓
data.pipelines
        ↓
data.adapters + data.stores
        ↓
external providers / filesystem / database
```

`data.core` 和 `data.catalog` 是跨结构化/非结构化共享边界。适配器不得导入 products 或 memory；stores 不得发起网络请求；products 不得导入具体 Provider；`data.structured` 与 `data.unstructured` 不得互相导入实现模块。通过 import-linter 风格的静态检查或等价单元测试固定这些规则。

### 4. 配置采用“总目录 + 领域配置 + Provider 配置”

目标配置结构：

```text
config/
├── data/
│   ├── catalog.yaml          # source/dataset/entity/status 的总索引
│   ├── structured.yaml       # 指标、映射、vintage、质量门
│   ├── unstructured.yaml     # 文档类型、正文策略、分块和保留策略
│   ├── schedules.yaml        # 采集触发与预算
│   └── providers/             # Provider 认证、endpoint、限流、保存约束
├── workflows/                 # PEAD、Evidence、Sector 等消费者配置
└── runtime/                  # broker、market 等即时查询配置
```

`catalog.yaml` 只负责索引和引用，不复制全部 Provider 参数。统一 loader 返回经过 schema 校验的配置对象，并检查 source、dataset、adapter、entity 和 fallback 引用。现有配置在兼容期通过 loader 的 legacy overlay 读取，禁止同时出现两套独立状态。

### 5. 先拆代码所有权，再拆数据库物理边界

第一阶段不改变 SQLite 文件位置。把 `memory.store` 中的数据层表按 repository 接口迁移到 `data.stores`，由数据层 repository 通过兼容访问旧表；Workflow 记忆继续由 `ats.memory` 管理。这样可以先验证行为和职责，再决定是否拆库或迁移表，降低一次性数据迁移风险。

数据层 repository 的边界为：结构化观测、原始 artifact、文档元数据/版本/分块、证据、ingestion runs。交易、决策、性能和 agent run 仍属于 memory。

### 6. 按阶段迁移，每阶段设置回滚点

每个阶段都先引入新入口，再迁移调用方，最后删除重复实现。每阶段必须通过导入兼容、配置校验、数据产品回归、受影响来源 smoke test 和受影响 Workflow 回归；失败时只回退当前阶段的 feature flag 或兼容转发，不删除已有数据。

## Risks / Trade-offs

- [循环依赖在移动期间短暂增加] → 先建立 core/interfaces 和架构 import test，再移动实现；禁止通过局部动态 import 掩盖长期循环。
- [旧入口与新入口出现双写差异] → 兼容层只转发到单一实现，增加新旧返回值对账测试，禁止复制业务逻辑。
- [配置迁移导致来源状态分裂] → 统一 loader 采用明确优先级和 legacy overlay，并输出配置来源；旧配置在过渡期只读。
- [memory.store 拆分影响旧 Workflow] → 先按 repository 接口隔离代码所有权，保留旧表和回退路径，完成消费者迁移后再做物理迁移。
- [目录重排导致导入路径破坏] → 为所有公开旧路径保留兼容模块，加入全仓库 import smoke test，并设置弃用日志而非立即删除。
- [新目录过度设计] → 第一阶段只建立边界和 facade，只有在现有职责明确时才移动文件；不为未来的向量库或知识图谱预建无用抽象。

## Migration Plan

1. **基线与契约**：冻结当前公开导入、CLI、配置和数据库表清单，建立目标目录、依赖规则和兼容测试。
2. **命名空间与产品 facade**：新增 `ats.data.core/catalog/products`，将 `ats.data_platform` 改为转发层，保持所有现有消费者运行。
3. **结构化迁移**：逐步迁移 catalog、structured pipeline、repository 和 discovery，保留 `ats.structured` 转发；对结构化来源和消费者逐个回归。
4. **非结构化迁移**：按 adapters、pipelines、stores 拆分 SEC、release、transcript、RSS、研报和证据路径；对文档完整性和血缘做回归。
5. **配置统一**：引入 `config/data/` 与统一 loader，先 shadow-load 和对账，再切换默认配置入口；旧配置保持只读兼容。
6. **memory 所有权迁移**：将数据层 repository/schema 迁出 `ats.memory`，先共用 SQLite，再按实际需要决定物理拆库。
7. **退役旧逻辑**：所有来源和消费者完成对账、发布和稳定运行后，逐个删除旧实现；最后移除兼容层和旧配置别名。

回滚策略：任一阶段只回退新入口的 feature flag 或兼容转发，恢复旧读取路径；不删除已经保存的 artifact、文档版本、观测 vintage 或运行记录。

## Deferred Decisions

- SQLite 是否最终拆为结构化库、非结构化库和 Workflow memory 三个物理文件：不属于本变更，先完成代码所有权隔离并继续共用现有 SQLite 文件。
- 是否引入专门的依赖检查工具：不作为本变更新依赖，先使用现有测试框架和静态导入检查；只有现有检查不足时再单独提案。
