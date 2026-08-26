# 统一数据层迁移验收报告

日期：2026-08-26  
变更：`unify-data-layer-architecture`  
分支：`codex/data_frame_rebuild`

## 1. 验收范围

本次变更完成的是代码命名空间、配置目录、存储边界和兼容迁移基础设施的统一，不包含删除旧实现、物理拆分 SQLite、重新下载生产数据或切换所有 Agent/Workflow 到新路径。

统一入口为 `ats.data`：

- `ats.data.core`：实体、来源、血缘、质量和 ingestion-run 中立契约；
- `ats.data.catalog`：统一目录加载、legacy overlay 兼容和只读校验；
- `ats.data.adapters`：结构化/非结构化 Provider 适配器边界；
- `ats.data.pipelines`：采集、准入、规范化和质量编排边界；
- `ats.data.stores`：结构化、文档、证据和运行记录存储契约；
- `ats.data.products`：Agent/Workflow 使用的数据产品入口；
- `ats.data.rollout`：source/consumer mode、shadow 对账、发布和回滚入口。

旧的 `ats.data_platform`、`ats.structured`、`config/structured_data.yaml`、`config/sources.yaml` 和 `config/news_sources.yaml` 保留为兼容层；本阶段没有删除或迁移历史数据。

## 2. 配置与发布验收

通过统一目录 `config/data/catalog.yaml` 索引以下文件：

- `config/data/structured.yaml`
- `config/data/unstructured.yaml`
- `config/data/schedules.yaml`
- `config/data/providers/*.yaml`

`ats data config` 为只读检查，验证 catalog 版本、legacy overlay 文件、source/dataset 双向引用、adapter 注册、运行状态和 `runtime/excluded` 边界；不会联网、写数据库或修改发布状态。

本地执行 `ats data config` 的结果为 `validation.valid=true`、`reason_codes=[]`；当前目录中运行时市场/期权来源均被识别为 `runtime_excluded`。

source 与 consumer 支持 `legacy`、`shadow`、`platform`、`fallback` 四种 mode。release overlay 写入前默认预览，只有显式 `--apply` 才改变状态；回滚保留历史 observation、artifact 和 lineage。

## 3. 测试结果

测试使用仓库已有的隔离 fixture；由于当前 macOS Python/libedit 与 pytest capture 存在已知段错误，命令预先注入空 `readline` 模块，不改变业务测试逻辑。

### 3.1 架构、目录、发布和所有权专项

命令：

```bash
PYTHONPATH=src .venv/bin/python -c 'import sys,types; sys.modules["readline"] = types.ModuleType("readline"); import pytest; raise SystemExit(pytest.main(["-q", "tests/test_data_layer_docs.py", "tests/test_data_rollout.py", "tests/test_data_layer_architecture.py", "tests/test_data_catalog.py", "tests/test_data_store_ownership.py"]))'
```

结果：**27 passed**。

### 3.2 数据层与 Agent/Workflow 回归集合

覆盖：统一 catalog、命名空间、结构化财务/Consensus/区域序列/证据、生命周期/as-of/vintage/质量/发布、文档准入/资产/类型、SEC/earnings/transcript/news/research、数据迁移/存储/对账、PEAD、Sector、Chain 和相关 Workflow。

结果：首次运行 **530 passed、1 failed**。唯一失败是已有文档一致性测试要求旧路径 `src/ats/structured/runtime_registry.py` 出现在兼容说明中；补充该说明后专项复测如下：

```bash
PYTHONPATH=src .venv/bin/python -c 'import sys,types; sys.modules["readline"] = types.ModuleType("readline"); import pytest; raise SystemExit(pytest.main(["-q", "tests/test_structured_docs_consistency.py", "tests/test_data_layer_docs.py"]))'
```

结果：**12 passed**。

因此，变更涉及的数据层回归集合在修复兼容文档断言后通过；失败没有涉及业务代码、数据内容或运行行为。

## 4. 当前仍保留的迁移边界

1. 旧模块仍被 Agent/Workflow 使用，因此暂不物理删除；新代码不得再向旧 namespace 添加业务逻辑。
2. 结构化和非结构化仍可共享现有 SQLite/文件资产；新的 repository 契约已定义所有权和对账检查。
3. `IBKR`、`yfinance`、ThetaData 的价格/期权属于 runtime/excluded，不进入持久化结构化库。
4. 真正的生产切换需要逐 source、逐 consumer 完成隔离采集、质量门、shadow 对账和 release-check；本报告不宣称生产数据已经重新采集或所有消费者已经切换。

## 5. 后续切换准入

下一次变更在删除 duplicate implementation 前，必须：

- 为目标 source/consumer 完成真实隔离采集和人工抽样；
- 通过 coverage、accuracy/reconciliation、freshness、completeness、availability 五维质量门；
- 通过 Agent/Workflow 端到端回归和旧/新 repository reconciliation；
- 产生可审计 release history，并演练 rollback；
- 更新本报告或新增对应批次验收报告。
