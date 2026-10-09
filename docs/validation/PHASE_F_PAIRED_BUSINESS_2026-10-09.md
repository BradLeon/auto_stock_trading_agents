# Phase F 3.9 同输入实际业务双运行

状态：**3.9 完成；73/116，43 项待办**。3.10 的实际范围影子与签核保持独立。

## 修改前影响确认

已保存 before.json/before_sources.zip 与生产只读盘点。拟新增隔离 pair runner、固定时钟/只读输入绑定与外部边界 tape，旧 role/CLI 链和新 Dispatcher 调用真实业务发布，Risk 调用真实 revision 审查。两侧独立库，运行输入含配置/初始本地历史/治理读/真实行情源时点，缺录制项拒绝、不重新采集。CLI、shadow_replay、Dispatcher、实际 consumer API 等既有保护面必要修改先留影响方案；新增依赖纳入指纹并集，十消费者受共享面漂移影响。原材料不改写，最终资格仍待 6.1/6.4。生产路由、owner、采集与 C3 不变，不签核或启用生产。

## 已实现的业务入口

`ats shadow capture-business` 调用两条真实业务链，在相同初始配置与历史库的独立副本中收集所需输入；同一接口/consumer/scope 请求只获取一次。输出为完整内容的冻结输入库及 input_hash，不接收左右业务结果 JSON。`ats shadow run-pair` 恢复该包，分别调用 `paired_business.legacy_business` 和 `paired_business.dispatcher_business`，产生独立 run ID、实际 task projections 和风控结果。

旧侧为当前代码中保留的手动 role/CLI 入口，按真实任务依赖执行；新侧为真实 Dispatcher，使用隔离 SQL owner/generation/claim 和原发布栅栏。旧 Layer CLI 的额外 cross-section 仍执行，允许两侧查询固定输入包的不同子集，未知查询拒绝。不是 checkout 重构前旧二进制，也不把当前兼容入口解释为历史算法的独立基准。

风控旧侧调用 `pre_trade`，新侧调用 `review_revision`；旧适配器本身已委托 revision 引擎，故本轮验证两个调用契约与结果形态，不宣称存在独立旧风控算法。账户必须有已冻结快照，不借旧侧缺账户降级通过；保留完整的新侧 basis/caps/counterproposal 与旧侧批准/notes 差异供后续比较。

输入包含请求、timezone-aware 逻辑时间、配置、初始业务数据库、已有处理队列及其处理 lease、治理 ConsumerInput 完整 payload/ref/contract、显式 runtime/data 接口响应、模型响应和依赖指纹。使用 JSON 类型白名单恢复 schema/dataclass，不使用 pickle。模型响应固定为输入，实际 prompt hash 单独记录；业务计算、角色入口和发布函数未替换，输出由业务调用生成。它验证录制响应下的双跑，不是随机模型等价性实验。

双运行依赖检查额外固定完整 `src/ats` Python/Markdown 源码，包括 `agents/base.py`、模型 gateway 和全部角色提示文件，不只检查资格 manifest 的 100 路径。提示/适配源码变化会拒绝旧包，配置内容由 seed 的全量 YAML 固定。较早的入口回归材料和补强前重跑材料单独保留，最后 `paired-final.xml` 对应这一完整源码/提示闭包。

逻辑时钟仅在完整隔离上下文启用，并传播到 Dispatcher worker；无上下文时保持系统时间。行情源时点不改写，lease 和执行授权的操作有效期不因此延长。复制已有 queue lease 是处理已接收文档的上下文，不是新启动采集、续租或扩大授权；过期应停止。

## 失败与证据行为

缺输入、scope/contract/time 不符、依赖漂移、离开隔离、重用已有 side 目录均拒绝。重放禁止回到未录制的 Provider/模型接口，网络调用拒绝；通知在隔离上下文抑制。业务内部吞掉未知输入错误也使整个运行失败。PEAD 文件报告被重定向至隔离根，并仍检查发布栅栏。

冻结输入、实际 run 记录和 pair 事件采用追加表。每侧完成记录真实 projection refs；后侧失败保留前侧结果和失败事件，不删除历史或自动新建替代完成。新尝试使用新 pair ID/目录。execution evidence 校验 input/hash/clock、两条真实入口与源码、共同依赖闭包和实际库中 projection ID/hash/payload；旧 helper 的相同读取集合规则保留，不能把导入 JSON 冒充本入口。

## 验证范围和剩余工作

新增场景覆盖 COHR Technical、ai_hardware/L4 的六角色 Routine 依赖链、COHR Event 实际评分/发布、真实风控调用、CLI 跨新进程重放和四类拒绝。外部文档/结构化数据/行情/账户/模型为受控 fixture，实际治理校验、角色计算、Dispatcher、发布和风控仍运行。Event 用 `use_llm=false` 的既有离线评分路径，未证明全量 live earnings package 和生产配置范围覆盖。

此前测试夹具改为显式注册时，一次遗漏 generator 展开导致失败；修正后重跑，原失败 JUnit `entries-fixture-failure.xml` 保留，不归类为业务成功。当前保护面由 91 增至 100 个路径；manifest/共享依赖改变影响十消费者。最小补验和源快照见本报告同名材料目录；旧报告与源快照保留，不重新登记历史资格。

3.10 仍须按选定实际 scope 执行差异比较、报告生成/签核、非空提交拒绝及触发遗漏验收，依赖 11.2。此次受控重放入口通过不代替该任务，也不代替 6.1/6.4 最终冻结/资格。生产 C3、生产 owner/路由/授权状态不变。

## 可复现命令

全部经 uv；具体 CLI 输入示例与现有隔离种子前置见 [runbook](PHASE_F_CUTOVER_RUNBOOK.md#39-实际业务双运行入口2026-10-09)。自动受控场景无需外部服务：

```sh
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync pytest -q tests/test_phase_f_paired_business.py
UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_paired_business_20261009/verify.py
```


## 最终验证结果

三组回归：入口/影子/调度 196 passed，研究与完整执行恢复 40 passed，保护面/A/安全读/隔离/记录守卫 160 passed，共 396 个测试通过。输入合并与完整提示闭包补强后的最终专项复跑 9 passed，属于已包含测试的复跑，不重复计入独立数。仅新增四模块/测试的 Ruff 检查通过；未宣称全仓 Ruff 或全仓测试全绿。OpenSpec strict 和任务依赖 DAG 核验通过。JUnit 的 record_property 提示及既有可视化 escape 警告保留，不影响通过结论。

双运行额外固定 458 个源码/提示/配置路径，资格 manifest 保护并集 100；这两个计数用途不同。各次源码/材料 hash 与生产只读不变检查见 [after.json](phase_f_paired_business_20261009/after.json)，十角色补验影响见 [minimum-reverification.json](phase_f_paired_business_20261009/minimum-reverification.json)。输入包与 side 数据库由 SQLite backup 生成可恢复副本，最后版实际 pair 见 paired-final-materials.zip 与 paired-final.xml 的 properties；较早版本不冒充最后版本运行。

最终只读 verifier 通过：901 个源码/配置/测试文件 hash、100 个资格保护路径、五份 JUnit、2,483 个 SQLite 备份完整性均核对；另外从最后版归档恢复并核对 12 条真实双跑 run 的冻结输入、当前源码/提示指纹、实际读取键和 side 库中的 projection hash/payload。生产盘点与修改前完全一致，未登记生产资格。文档守卫更新后复跑 20 passed（已包含于上述 160 项，不重复计独立数）；任务依赖图 116 节点、73 完成、无未知编号/循环，3.10 保持待办。
