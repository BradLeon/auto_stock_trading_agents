# 结构化数据层改造前基线报告

> 日期：2026-08-25
> 分支：`codex/data_frame_rebuild`
> OpenSpec change：`build-structured-data-foundation`

## 1. 基线范围

本次基线覆盖改造直接影响的现有能力：

- measurement series / point vintage 与基础 `as_of`。
- DataProducts 当前指标、质量、健康和 CLI 产品。
- Chain 第三方序列与 evidence 转换。
- PEAD 数据源的缺失降级语义。
- Sector 基本面/Consensus 消费契约。
- 公司财期解析。
- defeatbeta 现有远程 Parquet 适配逻辑。
- SQLite 加法迁移。
- yfinance market data 运行时接口。
- 新增的财务、Consensus 和 catalog characterization tests。

## 2. 盘点交付物

- 迁移矩阵：`docs/STRUCTURED_DATA_MIGRATION_MATRIX.md`
- 机器可读范围、指标和质量门：`config/structured_data.yaml`
- 旧路径特征测试：`tests/test_structured_legacy_contracts.py`

盘点确认：财务与 Consensus 当前由 PEAD、Sector 运行时调用；Chain 区域序列已写 measurement；`stock_statement` 尚无生产 Adapter/消费者；IBKR/yfinance 行情与期权均登记为 runtime/excluded。

## 3. 测试命令与结果

测试文件：

```text
tests/test_structured_legacy_contracts.py
tests/test_measurement_store.py
tests/test_data_products.py
tests/test_chain_sources.py
tests/test_pead_data.py
tests/test_sector.py
tests/test_fiscal.py
tests/test_defeatbeta_adapter.py
tests/test_data_migrations.py
tests/test_market_data.py
```

由于第 4 节记录的本机 Python 环境缺陷，本次使用以下等价 pytest 入口，仅在启动前为 pytest 的终端兼容代码提供空 `readline` 模块：

```bash
.venv/bin/python -X faulthandler -c \
  'import sys,types; sys.modules["readline"]=types.ModuleType("readline"); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))' \
  -- -q \
  tests/test_structured_legacy_contracts.py \
  tests/test_measurement_store.py \
  tests/test_data_products.py \
  tests/test_chain_sources.py \
  tests/test_pead_data.py \
  tests/test_sector.py \
  tests/test_fiscal.py \
  tests/test_defeatbeta_adapter.py \
  tests/test_data_migrations.py \
  tests/test_market_data.py
```

结果：

```text
........................................................................ [ 92%]
......                                                                   [100%]
78 passed in 8.91s
```

结论：业务基线通过，可以进入加法式平台底座改造。

## 4. 已知环境问题：pytest 启动时 exit 139

直接运行 `.venv/bin/pytest` 会在加载测试前发生 segmentation fault。`-X faulthandler` 定位到 `_pytest.capture._readline_workaround()` 导入 `readline`：

```text
Fatal Python error: Segmentation fault
...
File "_pytest/capture.py", line 95 in _readline_workaround
```

进一步确认：

- `.venv/bin/python` 链接到 `/Users/liuchao/anaconda3/bin/python3`。
- Python 版本为 3.11.5 arm64。
- 单独执行 `import readline` 同样 exit 139。
- 崩溃扩展来自 `/Users/liuchao/anaconda3/lib/python3.11/lib-dynload/readline.cpython-311-darwin.so`。
- 导入 `ats`、导入 pytest 及执行非 pytest CLI 均正常。

因此这是本机 Anaconda `readline` 二进制/动态库环境问题，不是某个 ATS 测试或结构化改造导致。当前用进程级 stub 绕开 pytest 无关的 readline 终端兼容导入；没有修改应用代码、测试语义或仓库依赖。后续应单独重建干净虚拟环境或修复 Anaconda readline，但该外部环境缺陷不阻塞本变更的确定性测试。

## 5. 固定的兼容契约

### 财务

- `fundamentals.fetch()` 返回 `FundamentalData`，Provider 失败不向 Workflow 抛错。
- info、statements、SEC 分别失败时保持 `None`/空列表并增加明确 note。
- statement 行缺失不补 0。
- `fetch_light()` 固定返回 None-filled 七字段 dict。

### Consensus

- `fetch()` 固定返回完整键集合。
- 标量缺失为 `None`，集合缺失为 `[]`。
- NaN 归一为 missing，不作为数值返回。
- estimates 和 analyst 两个子源可以局部成功。

### 区域序列

- measurement point 保存原始 value vintage，yoy/mom 不写入 raw payload。
- 同内容重复运行幂等。
- 修订追加且历史 `as_of` 不使用未来修订。
- 当前 `collect()` 的 0 表示无新增，-1 表示不可达；更细状态将在统一状态机中加法扩展。

### Runtime 边界

- market data 与 options 继续由既有运行时路径返回。
- 本变更不得为这些接口增加 structured 写入、artifact 或回填。

## 6. 进入下一阶段的门

- [x] 消费者、返回契约、覆盖与 runtime 边界已盘点。
- [x] 首批核心指标与 pending mapping 政策已配置。
- [x] 数据集质量门、真实样本和回滚条件已配置。
- [x] 旧路径特征测试已补齐。
- [x] 结构化相关基线测试全部通过。
