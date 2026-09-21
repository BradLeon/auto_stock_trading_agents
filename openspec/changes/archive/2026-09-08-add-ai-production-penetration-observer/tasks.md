## 1. 契约与依赖

- [x] 1.1 定义不可变的 `core_production_workflow_proxy_v1` 阈值、四条核心序列、职业内生产化任务覆盖下限、职业覆盖率分布、AI 生产化 Observer 专属方法卡、单位、派生版本、grain 枚举、原因码和 JSON-safe 返回契约。
- [x] 1.2 新增兼容性的 `DataProducts.ai_production_penetration(...)` 公开查询，支持 `periods`、`as_of`、`top_n`、DataFrame 和 snapshot 选项，并拒绝非 `1p_api` 来源产品。
- [x] 1.3 将兼容版本的 Seaborn 和 Matplotlib 加入 `data` 可选依赖与锁定环境，采用延迟导入，确保缺少可视化依赖时 JSON/表格查询仍然可用。

## 2. Cell 资格判断

- [x] 2.1 使用稳定 entity ID 和精确层级过滤，从 accepted 或 warning observations 构建同产品、同期间、同 methodology 的职业/任务 cell 指标矩阵。
- [x] 2.2 实现逻辑合取资格判断：Usage `> 0`、Work `>= 80%`、Automation `>= 80%`、Directive `>= 50%`；返回每项输入值、observation ID、阈值比较和失败原因，不进行四舍五入或插补。
- [x] 2.3 对重复冲突、非法单位/范围、混合来源产品、不支持的分类和必要 observation 缺失执行失败关闭，同时保留明确的 cell 级质量诊断。

## 3. 生产化渗透 DataProducts

- [x] 3.1 根据 qualified 数量和公开 Usage cell 数量计算职业与任务的可见单元生产化率，保留分子、分母、完整精度、版本、质量、artifact 和血缘。
- [x] 3.2 以 qualified cell 的 Provider Usage Share 之和计算职业与任务的生产化流量份额，同时提供公开 Usage Share 总量和公开 cell 条件份额；不得将各边际分类比例相乘。
- [x] 3.3 使用 `as_of` 时点可见的 taxonomy version 增加 task taxonomy 映射诊断，包括 mapped/unmapped、qualified mapped/unmapped 数量、relation IDs、代表性 unmapped IDs 和 lower-bound 影响。
- [x] 3.4 仅在期间连续且 product、grain、methodology、threshold 和 derivation version 均相同时计算环比；其他情况返回明确的期间断档或 regime 变化原因。
- [x] 3.5 生成确定性的 qualified 职业/任务 TOPN 表，按 Usage Share、稳定 entity ID 排序；包含当前 Usage/Work/Automation/Directive 及相对上一个可比月的变化或缺失历史原因。
- [x] 3.6 公开规范化 JSON records 和具名 Pandas DataFrames（`summary`、`top_occupations`、`top_tasks`、`coverage`、`occupation_task_coverage`、`occupation_coverage_distribution`），固定列、dtype、排序、单位和 null 语义。
- [x] 3.7 对聚合、资格、TOPN、比较所用输入及 taxonomy relation 去重，并绑定到现有 snapshot manifest，同时记录 threshold、derivation、methodology 和有序记录 hash。
- [x] 3.8 对每个具有有效 taxonomy task denominator 的详细职业计算 `occupation_production_task_coverage_lower_bound`，对关联任务去重，正确处理一项任务关联多个职业、未观察任务和 unmapped qualified tasks，并返回 observation/relation lineage。
- [x] 3.9 基于逐职业覆盖下限生成确定性 CCDF，返回完整曲线点及 10%/25%/50%/75%/100% landmarks、eligible occupation 分母、版本和质量状态，并阻止其进入核心四序列状态机。

## 4. 固定 L1 Observer 命题

- [x] 4.1 新增 `ai_core_production_workflow_penetration` Observer 入口，仅消费新的受治理 DataProducts 结果，并输出固定命题文本、来源范围、阈值、期间范围、事实、诊断和 manifest。
- [x] 4.2 实现单序列趋势分类器（`directional_up`、`directional_down`、`flat`、`mixed`、`insufficient_history`），要求至少三个连续可比月。
- [x] 4.3 实现基于逻辑合取的广度、深度和总体命题状态，保留职业/任务之间的分歧，不使用多数投票或不透明综合分数。
- [x] 4.4 增加确定性事实与 warning 模板，区分单月比较和趋势，并约束 Usage Share、职业分类、代理口径、隐私缺失、连续性、生产率、ROI 和采用率语义。
- [x] 4.5 保持现有 Usage 型工作采用 Observer 行为，并增加回归测试，证明新命题是兼容性的新增路径。
- [x] 4.6 仅为 `ai_core_production_workflow_penetration` 的成功或部分成功运行生成固定 `methodology_card`，完整展示来源/期间/release、可见与 qualified 样本、隐私缺失、taxonomy 映射、threshold/methodology/derivation versions、可比性、质量、manifest 和不可推断事项。
- [x] 4.7 将逐职业任务覆盖表和职业 CCDF 作为 taxonomy-dependent 补充证据加入 Observer packet，分开陈述其结果与核心 breadth/depth/overall 状态。
- [x] 4.8 将方法卡 builder、类型和调用保留在 AI 工作采用领域模块内，不修改通用 Observer 基类/protocol/schema，也不要求其他 Observer 返回空方法卡。

## 5. 结构化可视化

- [x] 5.1 实现确定性的报告层 renderer：只接收规范化 `summary` DataFrame，使用非交互 backend 和固定视觉配置，不自行查询数据或计算指标。
- [x] 5.2 只有两个可比月时渲染明确标注的 slope/dumbbell 月度比较图，展示 `insufficient_history`，避免趋势措辞。
- [x] 5.3 只有至少三个可比月时才渲染分开的广度/深度时间序列面板，并在 methodology 或 threshold regime 边界断开折线。
- [x] 5.4 每张 PNG 同时写入 JSON sidecar，记录有序图表数据、数据 hash、产品、版本、期间范围、单位、renderer version 和 snapshot manifest ID；验证图表与表格使用相同 hash。
- [x] 5.5 可视化导入或渲染失败时返回非致命 `visualization_warning`，保留表格、事实、状态和 manifest。
- [x] 5.6 使用职业覆盖率 DataFrame 渲染 Figure 4 风格、单调不增的 CCDF，标注 eligible occupation 数、taxonomy version、期间、固定 landmarks、lower-bound 说明和数据 hash。

## 6. 消费接口与文档

- [x] 6.1 新增只读 `ats data ai-production` CLI，支持最新/指定期间、`as_of`、TOPN、JSON/Markdown、snapshot 和可选图表目录参数，并拒绝 Claude.ai 或交易动作模式。
- [x] 6.2 增加 Markdown 渲染，先展示方法卡，再展示四序列期间表、逐职业任务覆盖、职业 CCDF landmarks、职业/任务 TOP10、覆盖诊断、语义限制及可选图表/sidecar 链接。
- [x] 6.3 增加按 AI 生产化 claim ID 显式启用的只读 Evidence/Chain 报告章节以嵌入专属 Observer packet，不修改其他 Observer 的报告路径，也不得将状态传入 corroboration、评分、PEAD、Chief、组合、风险或执行路径。
- [x] 6.4 更新 Evidence Observer、structured-data 用户/开发者、数据源和运维文档，说明核心与补充指标公式、Figure 4 风格分布、方法卡、物理含义、使用示例、失败语义、重放步骤及当前只有两个月数据的限制。

## 7. Hermetic 测试

- [x] 7.1 测试阈值边界、精确比较、输入缺失、非法值、重复冲突、不支持的 Claude.ai 请求，以及阻止跨产品/跨 methodology 的 cell 连接。
- [x] 7.2 使用合成 fixture 测试可见生产化率、原始流量份额、公开份额、条件份额、完整精度、grain 隔离和隐私过滤分母公式。
- [x] 7.3 测试连续月份比较规则、regime 断点、TOPN 并列值的确定性排序、上期 cell 缺失及完整 observation 血缘。
- [x] 7.4 测试 taxonomy 映射覆盖、滞后/unmapped Task IDs、qualified unmapped 诊断、`as_of` relation 选择，以及职业内覆盖下限不会进入四条核心序列和趋势状态。
- [x] 7.5 测试结构化 JSON/DataFrame 在数值、数量、期间、observation IDs、列顺序、dtype、排序和 null 语义上的一致性。
- [x] 7.6 测试两个月的 `insufficient_history`、四种三个月趋势标签、广度/深度混合状态，以及禁止多数投票得出 `penetration_expanding`。
- [x] 7.7 测试两个月和三个月的图表选择、regime 断点、确定性 sidecar hash、表格/图表数据一致性和非致命 renderer 失败。
- [x] 7.8 增加消费者边界和措辞测试，证明 Observer 不直接导入 Provider/物理 repository，也不会输出员工采用率、已确认持续工作流、岗位替代、生产率、ROI 或交易结论。
- [x] 7.9 测试逐职业任务分子/分母去重、多对多 task relation、未观察任务留在 taxonomy 分母、无分母职业排除、lower-bound warning 和完整 observation/relation lineage。
- [x] 7.10 测试职业覆盖 CCDF 的单调不增性质、完整点集、固定 landmarks、边界比较、eligible occupation 分母、taxonomy regime 断点和表格/图表一致性。
- [x] 7.11 测试 AI 生产化专属方法卡在成功、部分映射、历史不足和 visualization warning 情况下字段完整，并禁止将历史论文验证率误写为当前 methodology 准确率。
- [x] 7.12 对芯片设计、WFE、Macro、Sector 和其他现有 Observer 执行契约回归，证明其 schema、输出字段、渲染、依赖、失败语义和运行路径未因方法卡发生变化。

## 8. 受治理数据验收

- [x] 8.1 在隔离的受治理 2026-04/2026-05 1P API 数据上运行新 DataProducts 查询并对账基准结果：职业可见生产化率 `44.95% → 46.67%`、任务可见生产化率 `32.08% → 31.72%`、职业生产化流量份额 `68.77% → 75.36%`、任务生产化流量份额 `59.40% → 68.66%`；只允许来自已记录源精度的舍入差异。
- [x] 8.2 验证最新职业/任务 TOP10 全部 qualified，四项源指标和月度变化均可独立复算，且每个展示值都能通过血缘解析到正确的 1P API artifacts。
- [x] 8.3 验证当前 Observer 将结果表述为单月比较中的“深度提高、广度混合”，总体状态为 `insufficient_history`，且不使用持续趋势措辞。
- [x] 8.4 在引入较新合成 vintage 后重放已保存 snapshot manifest，证明 JSON、DataFrame、Markdown 表格、命题状态和图表数据 hash 保持不变。
- [x] 8.5 完成隔离的 CLI/报告端到端流程：受治理查询、snapshot、质量/血缘检查、TOP10、Markdown、PNG 和 sidecar；随后运行完整 Anthropic 采集/查询/Observer 回归测试集。
- [x] 8.6 在当前受治理数据上复算抽样职业的生产化任务覆盖下限，并核对 CCDF 的 10%/25%/50%/75%/100% landmarks、eligible occupation 数、taxonomy version 和 mapped/unmapped 诊断。
- [x] 8.7 验证最终 AI 应用层生产化 Observer 输出在所有指标之前展示专属方法卡，并可从方法卡的 manifest/lineage 标识重放四条核心序列、职业内覆盖、职业分布和两类图表；同时验证其他 Observer 输出不出现该字段。

## 9. 发布与运维准备

- [x] 9.1 运行格式化、lint、目标测试、全量测试和 OpenSpec 校验；单独记录无关的既有失败。
- [x] 9.2 以 shadow/report-only 模式运行 Observer，在默认启用报告章节前，对同一输入比较方法卡、JSON、全部 DataFrames、Markdown、四序列图、职业 CCDF、PNG sidecars 和 manifest hashes。
- [x] 9.3 记录仅禁用/撤销代码的操作步骤，并确认无需迁移或删除原始 observations、taxonomy relations、现有消费者或历史 manifests。
- [x] 9.4 验收证据已记录于 `validation.md`；审阅已同意。未来任何阈值、工作流连续性信号或交易集成都必须使用独立 OpenSpec change。

## 10. 审阅反馈：输出与正式 L1 workflow

- [x] 10.1 调整 AI 生产化 Markdown 审阅顺序：方法卡、四条核心指标、职业任务组合已确认覆盖分布、分布尾部完整披露、TOP10、文末指标注释与限制。
- [x] 10.2 将四个核心指标的物理含义、分子/分母或求和公式、职业/任务不可相加的限制写入 Markdown 文末注释和用户文档。
- [x] 10.3 将面向用户的“职业内任务覆盖下限”改名为“职业任务组合的已确认生产化覆盖”，保留兼容字段；先展示分布，后将覆盖率至少 50% 的职业作为分布尾部完整披露，且不得称为典型职业。
- [x] 10.4 使两张图的标题和坐标使用与报告一致的中文指标名称；CCDF 解释已确认覆盖口径，不使用未解释的 lower bound 术语。
- [x] 10.5 新增通用 `ats evidence layer --sector <sector> --layer <layer>` 只读 workflow：只运行注册在该层的 Observer；`ai-production` 作为 claim 快捷入口经过同一注册表；默认写出层级审阅文档与图表路径。
- [x] 10.6 增加覆盖输出顺序、指标注释、分布尾部清单、中文图表标签、按层运行、未注册层、既有 Evidence 路径兼容性和正式 workflow 可重放的回归测试。
- [x] 10.7 使用正式按层 Evidence workflow 生成 2026-04/05 审阅结果，保存报告/manifest/图表证据，更新 `validation.md` 后等待最终审阅。

## 11. 审阅反馈：按层运行与读者语义

- [x] 11.1 建立按 `(sector, layer, claim_id)` 注册的只读 Evidence 分发器；运行一个 layer 不得触发同一 sector 的其他 layer。
- [x] 11.2 默认将按层 Markdown 输出写入 sector `output_dir`，支持 `--output`/`--chart-dir` 覆盖并打印路径；JSON 保持机器接口。
- [x] 11.3 将任务组合覆盖的 CCDF/landmarks 移到 50% 清单之前；将清单明确改为分布尾部完整披露，并将 TOP10 标为高流量典型使用单元。
- [x] 11.4 为 layer 分发、claim 快捷入口、无注册 Observer、默认/覆盖输出、报告顺序与旧 Evidence action 不变增加回归测试。

## 12. 层配置驱动的 Evidence 注册

- [x] 12.1 在 sector layer schema 增加与 Chain `claims` 分离的 `evidence_observers` 声明，并在 `ai_hardware.yaml` 的 `L1_app` 注册 AI 生产化 Observer。
- [x] 12.2 将按层分发器改为从已加载 layer 的声明解析 runner，移除 `(sector, layer)` 静态注册真源；未知 runner、重复 claim 或 disabled 声明须返回明确状态。
- [x] 12.3 增加配置加载、声明启停、claim 快捷入口、Chain `claims` 隔离和未注册 layer 的回归测试，并更新用户/开发文档与 validation。
