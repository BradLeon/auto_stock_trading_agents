# Tasks

> **顺序：**
> - 第 1–4 组只改代码与配置，用夹具和隔离库验证，不写生产库。
> - 第 5 组的安装与生产入库，每一步都需要你确认。
> - 第 6 组等真实定时触发后再勾选。

## 1. FRED 部分（`ramp_ai_index_fred`）

- [ ] 1.1 在 `config/data/structured.yaml` 登记来源 `ramp_ai_index_fred`：
  - 写入 `ramp_ai_adoption`；
  - 14 条 FRED 序列 → scope/segment 映射表（design.md D1）；
  - 发布模式 `platform`；
  - `approved_methodology_regimes: [ramp_ai_index_v2]`；
  - 数据集 `ramp_ai_adoption` 新增 scope `adoption_business_size`。

  验证：目录校验通过（`ats data` 加载 catalog 不报错）；结构化文档一致性测试通过。
- [ ] 1.2 实现 FRED 适配器：
  - 复用 RPS 的 FRED 请求、元数据校验与按序列失败隔离；
  - 频率要求 Monthly、单位 Percent、值在 [0,100]；
  - 行业与供应商按映射表归一 entity id，与网页 v2 一致；
  - 写 `methodology_regime: ramp_ai_index_v2`。

  验证：新增单测用 FRED CSV 夹具覆盖：
  - 14 条序列解析；
  - 频率漂移被拒；
  - 单条序列失败不影响其余；
  - 同 payload 重跑返回 `no_change`。
- [ ] 1.3 在 `config/data/schedules.yaml` 新增 `ramp_ai_index_fred_weekly`（周五 08:25 UTC，`release-check --ingest-new --force-check`，`freshness_slo_days: 10`）。验证：`load_plan` 能加载该任务；refresh/schedule 相关测试通过。

## 2. 网页 v2 解析与增量过滤

- [ ] 2.1 v2 adoption 解析（design.md D4）：
  - Overall + Models 带 Series 列；
  - Sector 列名、NAICS 全称 slug；
  - 缺同比列时不生成同比；
  - 写 v2 regime。

  验证：用 2026-11-12 实测 header 构造的夹具单测通过。
- [ ] 2.2 v2 人均支出解析：列名单位后缀归一，保留分位数顺序门。验证：单测覆盖带单位 header，以及 Median > Top 10% 时被拒。
- [ ] 2.3 token 份额解析：
  - 宽表转长表，生成 `token_spend_share_by_maker` 与 `token_spend_share_by_model`；
  - 周度期间；
  - 每周合计偏离 100±1 时记 warning；
  - 识别到 Volumes 指数时拒绝并报 `methodology_drift`；
  - 在 `structured.yaml` 登记两个新指标。

  验证：单测覆盖 Shares 夹具入库、Volumes 夹具被拒、空值跳过。
- [ ] 2.4 增量过滤与交叉核对（design.md D3）：
  - 按 FRED 映射表剔除已覆盖的 (scope, entity)；
  - 差值超过 0.01pp 时记 `fred_page_mismatch`。

  验证：单测断言：
  - 20 个行业的夹具只入库 13 个；
  - Overall + Models 只入库 Mistral、DeepSeek；
  - 构造的差值触发 warning 且不写 FRED 序列。
- [ ] 2.5 discovery 识别已批准的 regime 换代（design.md D5）：
  - regime 变化且在批准清单内时，记 `approved_regime_transition`，不返回 `METHODOLOGY_DRIFT`；
  - 同一 regime 内的 header 变化仍报漂移。

  验证：单测覆盖两种情况；既有 Ramp 漂移测试保持通过。
- [ ] 2.6 入站文件新鲜度（design.md D7）：
  - 只有导出时间在 10 天内的入站文件才当作本轮结果；
  - 否则调用 exporter；
  - exporter 不可用时报 `export_unreadable`，不返回 `no_change`；
  - `ramp_ai_index` 的 `freshness_slo_days` 改为 35。

  验证：单测覆盖过期文件、不可用 exporter、新鲜文件三种情况。
- [ ] 2.7 更新 `docs/data/RAMP_AI_INDEX.md`：v2 数据字典、FRED 与网页分工、失败原因码。验证：文档一致性测试通过，文中命令与配置任务名一致。

## 3. 浏览器 runner

- [ ] 3.1 在 `pyproject.toml` 增加可选依赖组 `ramp-browser`（`playwright`）；代码在未安装时能正常导入，并报 `browser_not_installed`。验证：未安装环境下 Ramp 来源的 release-check 返回 `export_unreadable` 和该原因码，FRED 来源测试不受影响。
- [ ] 3.2 实现 runner（design.md D2）：
  - 注入 `writeText` 截获脚本；
  - 按可见按钮切换 5 个图表，Token spend 切到 Shares；
  - 逐个点击 “Get the data”；
  - header 校验后原子写入 `<scope>.tsv` 与 `<scope>.json`；
  - 总超时 300 秒、单图表 45 秒；
  - 失败原因码按 D2 细分；
  - 由注册表在 Playwright 可用时注入为 exporter。

  验证：用本地静态 HTML 夹具模拟按钮与剪贴板写入的单测通过，覆盖成功、控件缺失、未截获剪贴板、超时。
- [ ] 3.3 把 runner 写进 `docs/validation/RAMP_AI_INDEX_ACCEPTANCE.md` 与 `docs/data/L1_OBSERVER_DATA_SOURCES.md`：采集方式、两个任务、失败处理。验证：文档一致性测试通过。

## 4. 读取侧与 L1 观察器

- [ ] 4.1 `products/ramp_ai_index.py` 各查询增加 `methodology_regime`，默认 v2：
  - v2 下查询 `model_market_share_overall` 返回空并带 `scope_discontinued`；
  - 新增 `ramp_token_spend_share_series`；
  - `adoption_business_size` 纳入 `ramp_paid_adoption_snapshot` 可用 scope。

  验证：单测：
  - 同库中同时有 v1、v2 时，默认只返回 v2；
  - 显式 v1 返回 v1；
  - v2 序列不混入 v1 期间。
- [ ] 4.2 `work_adoption.py` 与 `ramp_visualization.py` 改为 v2 七个 scope：
  - token 份额以周为横轴；
  - 只有网页部分失败时，adoption 照常展示，支出与份额标不可用原因；
  - 方法卡更新为规模分档、v2 口径、地理未纳入。

  验证：L1 观察器的 Ramp 相关测试更新后通过，新增“仅网页失败”的场景测试。
- [ ] 4.3 同步 `config/sectors/ai_hardware.yaml` 中 L1 数据清单注释，以及 `docs/data/L1_OBSERVER_DATA_SOURCES.md` 的数据集与 scope 表。验证：文档一致性测试通过。

## 5. 安装与生产首采（每步需你确认）

- [ ] 5.1 经你确认后安装 `playwright` 与 Chromium（约 150 MB）。验证：`playwright --version` 正常，`.venv` 中可导入。
- [ ] 5.2 生产执行一次 `ats data release-check --source ramp_ai_index_fred --ingest-new --force-check`。验证：14 条序列入库，`RAMPAIALL` 最新期间为 2026-10，值与网页一致；记录写入本 change 的 `verification.md`。
- [ ] 5.3 生产执行一次 `ats data release-check --source ramp_ai_index --ingest-new --force-check`（无头 runner）。验证：
  - 5 个图表导出成功或给出明确原因码；
  - 增量入库数量与 2.4 的预期一致；
  - 交叉核对没有 mismatch，或差异已记录；
  - 结果写入 `verification.md`。
- [ ] 5.4 生产执行一次 L1 观察器。验证：Ramp 段落显示 v2 的 2026-10 数据和七个 scope，三轴 judgement 与本次变更前一致。

## 6. 真实定时观察

- [ ] 6.1 观察下一次 `ramp_ai_index_fred_weekly` 的真实定时触发。验证：刷新账本有成功或 `no_change` 记录，且 `no_change` 基于本次实际取得的 payload。
- [ ] 6.2 观察下一次 `ramp_ai_index_p10d_probe` 的真实定时触发。验证：成功入库，或给出明确的 `export_unreadable` 原因码；同期 FRED 部分状态不受影响。
