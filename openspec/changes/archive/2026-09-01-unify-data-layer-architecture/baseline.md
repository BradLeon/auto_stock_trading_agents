# 数据层统一重构基线

日期：2026-08-26

## 当前目录与职责

| 区域 | 当前职责 | 迁移目标 |
|---|---|---|
| `src/ats/data/` | Provider 适配器、财务、新闻、文档和运行时数据混合 | 按 adapters、pipelines、stores、runtime 拆分 |
| `src/ats/structured/` | 结构化 catalog、采集、质量、repository、查询和发布 | 收敛到 `ats.data` 的结构化子树 |
| `src/ats/data_platform/` | Agent/Workflow 数据产品 facade | 收敛到 `ats.data.products` |
| `src/ats/memory/` | Workflow 记忆，同时拥有文档、证据和 measurement 表 | 保留 Workflow 记忆，数据表由 `ats.data.stores` 接管 |

## 当前公开入口

- `ats.data_platform.DataProducts` / `get_data_products`
- `ats.structured` 的 catalog、repository、ingestion、quality、release、discovery 导出
- `ats.runtime.cli data ...` 的数据产品和结构化运维命令
- `config/structured_data.yaml`、`config/sources.yaml`、`config/news_sources.yaml`

## 当前持久化数据表范围

`memory.store` 同时初始化交易/决策/Workflow 表，以及 `source_documents`、`document_versions`、`document_entities`、`document_chunks`、`evidence_facts`、`data_sources`、`ingestion_runs`、`measurement_series` 和 `measurement_points` 等数据层表。

## 基线测试

执行命令：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from ats.data import DataSource, safe_fetch
from ats.data_platform import DataProducts, get_data_products
from ats.structured import StructuredCatalog, get_repository
from ats.memory import TradingMemory
print('imports=ok')
PY

PYTHONPATH=src .venv/bin/python -c \
  'import sys,types; sys.modules["readline"]=types.ModuleType("readline"); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))' \
  -q tests/test_data_products.py tests/test_structured_foundation.py \
  tests/test_structured_legacy_contracts.py tests/test_structured_runtime_boundary.py
```

结果：公开入口导入成功；29 个相关测试通过。直接运行 pytest 的 readline segmentation fault 是既有本机环境问题，沿用仓库已有的 `readline` stub 方式运行。

## 已知循环依赖信号

- `ats.structured.runtime_registry` 依赖 `ats.data.sources.*`。
- `ats.data.fundamentals`、`ats.data.consensus` 反向依赖 `ats.structured`。
- `ats.data_platform.products` 同时依赖 `ats.memory`、`ats.structured` 和 `ats.data.document_assets`。
- `ats.structured.evidence` 同时依赖 `ats.data.source_cache` 和 `ats.memory`。

这些依赖是后续架构测试和迁移的基线，不在阶段 1 通过动态 import 掩盖。

## 非结构化模块分类

| 目标职责 | 当前模块 | 新入口 |
|---|---|---|
| Provider 适配器 | `data/sec.py`、`data/transcript.py`、`data/articles/*`、`data/news.py`、`data/research.py` | `data.adapters.unstructured.*` |
| 清洗、抽取、准入 | `data/documents.py`、`data/document_assets.py`、`data/admission.py` | `data.pipelines.unstructured.*` |
| 文档、版本、分块、缓存、证据 | `data/document_assets.py`、`data/source_cache.py`、`structured/evidence.py` | `data.stores.unstructured.*` |
| 消费者查询 | `data_platform/products.py` 的文档/证据方法 | `data.products.unstructured` |

本阶段先以统一入口和兼容桥接完成代码所有权声明，不重新抓取或迁移既有文档文件。
