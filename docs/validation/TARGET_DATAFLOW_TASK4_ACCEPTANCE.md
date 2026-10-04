# Tasks 4：资格账本与回退验收

日期：2026-10-04。Change：`complete-target-dataflow`。**Task 4.4 重开补验后，经用户确认标记完成。完成范围为只读回退与恢复验证，不代表生产输入齐备或批准切流。**以下 2026-10-03 记录仅为历史结果，剩余五角色的证据以本日补验为准。

## 2026-10-04：剩余五角色补验（4.4 已确认完成）

本轮不是“记录拒绝就通过”：实际执行了 9 条产品边的正向读取、包装层故障、原生只读回退和恢复，另有 10 项负向/恢复检查。脱敏结果见 [五角色补验 JSON](TARGET_DATAFLOW_TASK4_RUNTIME_ROLES.json)。JSON 的 `passed` **仅指这套输入 API 演练通过**，不表示五角色生产输入齐备或生产切流批准；4.4 的完成依据为此前五研究角色与本轮五角色的回退证明及用户复核确认。

| 角色 | 实际执行与通过项 | 实际环境仍有的缺口 |
|---|---|---|
| Technical | Yahoo 实际 NVDA 日线 251 点，最新交易日 2026-10-02；目标与原生值一致，故障拒绝后可恢复。实际期权查询及失败分支也已执行 | ThetaData 连接拒绝；Yahoo 期权可读，但来源时间缺失，保留 partial。未用查询时间冒充报价时间，完整期权输入尚未通过 |
| Chief | 隔离完整账本的账户和历史两条边均完成四步演练；只读重新打开数据库后仍可读；实际账本已不再因无关旧元数据的 null 而崩溃 | 实际账本仍有 9 条 broken_link，账户和历史为 partial；快照为 2026-08-31。没有修旧记录或关闭异常，因此生产完整性仍未通过 |
| Risk | 隔离账户、实际日线和当前风险规则三条边均完成四步演练；账本断链时保持 partial | 实际账户继承上述 9 条断链，不能视作生产完整输入 |
| Trader | 隔离临时账本中通过真正 repository 方法保存 revision、风控批准、人工批准，授权四步演练通过；验证快照超时、缺 cycle、新 revision 无有效批准均拒绝；只读重新打开后授权一致 | 实际生产账本没有 cycle。本轮没有伪造生产 Boss 批准或执行交易；隔离审批链证明 API 正向路径，不是可执行生产授权 |
| Clerk | 只读 TWS 实际账户、已完成订单、成交查询成功，订单/成交均为空列表，属于有效空结果；broker 与隔离审批上下文两条边四步演练通过；订单查询失败返回 partial，健康空列表仍 complete | 实际审批上下文没有 cycle，显式 no_coverage；实际 TWS 没有部分成交样本。部分成交、重放和关联逻辑由隔离 Clerk 回归覆盖，不能称为本轮实际部分成交验收 |

### 新入口最小修正

- Internal State 只校验账户及绩效所需事实列，不再为了读账户/绩效校验旧 decision metadata；账户数字和时间仍严格校验。实际 9 条断链仍暴露为 degraded，原账本字节和旧模型未修改。
- Clerk 回退声明由写服务 `clerk_run` 改为只读 `ats.data.runtime.broker.broker_state`，账户/订单/成交各自失败可见，不会调用 reconcile 或账本写服务。
- Trader 接受 JSON ISO 时间并转换为有时区日期，修复有效授权被 TypeError 拒绝的问题；不接受无时区时间。
- 缺失 decision cycle 由新读取边界返回明确 no_coverage，而不是调用旧 repository 后获得不明确读取错误。

### 可重放与回归

```sh
UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync \
  python scripts/verify_dataflow_runtime_roles.py \
  --internal-cache var/ats.sqlite --output /private/tmp/task44-runtime.json
```

该命令实际联网只读查询行情、期权和本机 TWS。TWS 使用独立 client ID 与 `readonly=True`，不调用下单/撤单方法；同一轮实时结果只采集一次，在内存中供不同角色和故障/回退复用。不把实时行情、期权、账户结果写入持久化研究库或采集队列。只保存状态、数量、时间和 hash；隔离账户样本与批准链位于临时测试数据库，不是生产数据。失败退出不保留旧的 passed 输出。

相关回归：27 项定向测试通过；扩展至 Clerk compensation/linkage/orchestration 后为 **55 passed、1 failed**。失败为 `test_broker_unavailable_registers_missed_window_gaps`：它使用当前日期减 10 天生成填单（本轮为 2026-09-24），却固定验证截止窗口 2026-09-23；该填单不在窗口内，故返回 0，断言预期 >=1。未修改旧 Clerk 逻辑或该测试，也不把这批回归称作全绿。既有 sector SyntaxWarning 保留。实际 TWS 查询出现 Error 300 取消 ticker 警告，但账户/订单/成交结果成功；不据此宣称期权 Greeks 完整。

纯缓存资格机制脚本再次通过 24 项检查，不联网；它仍显示运行时角色未在该**纯缓存**执行器中验证，应与本节实时补验一起阅读，不能将两份结果混为一份生产批准。OpenSpec strict、diff check 和新增脚本/只读 broker 的 Ruff E/F（忽略 E501 长行）通过。

### 当前结论与未完成范围

4.4 补验后经用户确认标记 `[x]`：十角色的只读回退/恢复验证完成，其中新增五角色的 9 条输入边证明已补齐。**实际完整输入仍有期权来源时间缺失和内部账本 9 条断链**，受影响输入仍 partial，生产资格未签发，不能以任务勾选覆盖缺口。生产缺 cycle 为“尚未产生批准决策”的正常状态，不要求为测试制造真实交易或 Boss 批准；隔离授权验证不替代后续真实运行的审批链证据。剩余缺口在 Tasks 5 逐域/消费者清单中继续保留；路由和定时任务没有改动，提交订单数为 0。

## 2026-10-03 历史记录（非当前完成结论）

## 逐任务复核

| Task | 本轮需要验证的内容 | 结果 |
|---|---|---|
| 4.1 | 追加式事件、schema 兼容、脱敏字段、代码/config 指纹、前置引用 | UPDATE/DELETE 拒绝；旧 schema 显式写入时补列、不升级旧证据；拒绝环境变量值、raw payload、无效 TTL；版本化摘要带指纹 |
| 4.2 | 精确 domain/consumer/contract/scope；必需证据及逐产品回退齐备 | 缺账本、缺证据、只覆盖一个产品、错误 product scope、只有 passed 标签均拒绝；仅隔离故障探针获得 eligible |
| 4.3 | TTL、manifest/依赖漂移、最新失败、撤销及消费者隔离 | 递归校验前置事件，不能以较新成功掩盖仍被引用的旧撤销；一个 consumer 的撤销不污染另一个已独立验证的 consumer |
| 4.4 | 十角色/16 产品边的真实缓存原生回退读取与恢复 | 五个研究角色样本可读、值与包装层一致；其余缺口如实拒绝，未恢复 Memory 事实表、未连接券商 |
| 4.5 | Python 与 CLI record/history/query/revoke；运维指引、无隐式切流 | CLI 使用独立账本实跑通过，details 正确传入并读回；runbook 更新；生产路由和定时任务不变 |
| 4.6 | SEC 单独缺失可选，其他必需输入不可豁免，坏材料不得发布 | SEC 失败保留错误/时间/task/run/政策版本；安全空值验证齐备时隔离 eligible；财务、电话会、SEC 索引失败仍 ineligible；错误 URL/短正文拒绝 |

## 逐角色回退结果

同一缓存和固定截止时点依次读取目标 packet、在隔离调用上下文注入包装层故障、读取原生 API，再恢复 packet。未改变生产开关；原生 API 与目标包共享已准入仓库，这不是存储灾难恢复或完整 Workflow 回滚。

| 角色 | 回退样本 | 结果与影响 |
|---|---|---|
| Layer | DRAM 已发布指标、NVDA 已准入文档 | HIER/DOC 均可读；固定 vintage/正文与 packet 一致 |
| Information | NVDA 已准入文档 | 文档版本与正文一致，不经 Memory/搜索 |
| Sector | DRAM 已发布指标 | 数值、observation 引用一致 |
| Fundamental | 已发布公司财务样本与 DRAM 指标 | 两条产品边均可读；精确实体/数据集见 JSON；不能外推为全部标的财务覆盖 |
| Macro | 台湾已发布数值 | 数值和引用一致 |
| Technical | 当前行情 | 无可复用的 runtime quote；本轮不重新联网或以合成报价签发生产资格，保持未验证 |
| Chief | 实际内部账户/历史账本 | 原生读取报 `ValidationError: invalidation_source`：历史 performance payload 该字段为 null，不符合当前模型的字符串要求；只记录旧数据兼容缺口，不修旧账本，保持 ineligible |
| Risk | 实际账户、行情、规则 | 修复**新**规则入口 `get_config().app.risk`，规则读回通过；账户同上述旧 payload 缺口，行情无复用 quote，整角色未通过 |
| Trader | 当前 exact revision 授权 | 本地无本次可执行的当前批准链，不能拿历史批准替代，也不为了验收造生产决策；拒绝为安全终态，未提交订单 |
| Clerk | 券商状态、决策审批上下文 | 原声明 `clerk_run` 是写服务，不能充当只读 broker fallback；未调用它。实际账本无持久化 decision cycle，审批上下文返回明确缺失；未伪造 broker/decision 状态，保持 ineligible |

**生产资格**：本轮没有向生产账本填入完整证据集，以上任何角色都未被本轮自动签发生产资格。五个研究角色的回退通过仅为各自固定样本证据，仍需 Tasks 5 整合来源/写入/准入/血缘等全部前置证据。此前将剩余五角色未验证或拒绝等同于完成 4.4 的判断已撤回，必须补充正向、故障、原生回退与恢复验证。

## 最小修复

- 资格事件新增有界 `details_json`，保存来源状态和逐产品回退证明；旧记录不补造证明。
- 前置证据从“只存引用”改为递归校验结果、TTL、指纹、详细证明和撤销；拒绝未知/跨 scope 引用。
- fallback/rollback 绑定声明的 route、每个 product、scope hash、payload hash、血缘、只读属性和原生一致性；Trader 也必须具备 fallback 证据。
- SEC 政策同步至权威注册表和覆盖清单，原失败状态与是否阻塞分离；Fundamental 必须记录财务、SEC 索引、电话会和 SEC 正文状态。只允许 SEC 正文例外，不接受其他来源的可选扩展。
- 资格查询只读连接启用 query_only；证据有效期与材料日龄分开。指纹包含共用入口/注册表及角色特有依赖。
- 新 Risk 入口配置层级修正为 `get_config().app.risk`；未修改旧 performance payload、旧 scheduler 或 Clerk 写流程。

## 可重放命令与证据

```sh
UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync \
  python scripts/verify_dataflow_qualification.py \
  --data-cache var/data.sqlite --internal-cache var/ats.sqlite \
  --output /private/tmp/target-task4.json
```

版本化执行器不依赖 `tests/`。来源数据库用 mode=ro/query_only 和固定事务读取，正文只读；socket 守卫禁止网络采集。故障、拒绝、资格与 CLI 写入均在独立临时目录；公开摘要没有原文、账户值或凭据。单独运行的 CLI 子进程仅执行 assurance 命令，不调用数据获取接口。

结果：十角色/16 产品边、24 个资格故障/状态检查、SEC 空值/官方身份/发布质量门和四类 CLI 操作通过；网络请求 0。脱敏摘要见 [TARGET_DATAFLOW_TASK4_REPLAY.json](TARGET_DATAFLOW_TASK4_REPLAY.json)，代码/config 指纹可复核。

定向回归：52 passed（consumer identity、authorization、Internal State、architecture guards、固定来源）；Tasks 3 完整缓存回放再次通过，未重新采集。新增 assurance/验收脚本 Ruff E/F/I、OpenSpec strict 与 diff check 通过。

包含旧 cutover 测试的首轮为 58 passed、1 failed：该断言要求 Scheduler 仍包含旧 `research_pipeline` 导入，而现有 schedule 已通过受管 SemiAnalysis 队列触发。Scheduler 本轮未修改，不恢复该旧路径，也不把该批次称为全绿。既有 sector viz 的 `\d` SyntaxWarning 单独保留。

## 后续边界

Tasks 5 负责汇总逐域/逐消费者最终证据和通过/拒绝清单；完整 Workflow、跨进程/重启、实际路由开关切换仍是后续集成/Phase F 的工作。Technical/内部账本/Trader/Clerk 的上述缺口可交给其 owner 后续处理，但不得先签发资格再补证。不在本轮归档、提交、推送或切流。
