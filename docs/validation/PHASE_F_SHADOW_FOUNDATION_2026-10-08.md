# Phase F 可恢复输入与强制报告门禁

日期：2026-10-08。范围：3.1、3.6。结论：输入持久化/跨进程恢复基础和正式入口报告门禁已实现；实际业务双跑、生产资格与切流尚未验收。使用 uv 项目环境；没有真实券商网络写入。

## 实现与边界

新增 `shadow_replay.py`：内容寻址的完整输入包、逐面内容/hash、治理读取记录、逻辑时间及规则/模型/提示内容独立保存。持久化为隔离目录内 SQLite 追加记录。缺内容的旧 hash-only 包不能作为恢复输入；旧比较 CLI 继续用于工具级比较。

CaptureReads 保存治理 API 的完整输入，包括 payload、input_refs、source_as_of、queried_at、contract_version、消费者和查询范围。审批前规范化与批准后执行检查分别按 stage/purpose 保存报价。ReplayReads 向两侧提供相同 read_input 和逻辑时钟，只查捕获记录，检查当前消费契约；未捕获的阶段、范围、时点拒绝，不回查 Provider。所有重放读与运行要求完整隔离和进程 broker 禁写；恢复内容的只读校验不修改数据库。

正式 batch dry-run、read 执行器和 cutover CLI 的 target set-route/preflight/activate 均使用强制报告门禁；最终 record_activation 再检查。空 ID、缺 checker、异常/非法 checker 返回、不可读报告、撤销、scope/代码/config 漂移、必需面未比较和未接受差异均拒绝。collection_publish 的 direct_verification 不切换路由，仍是原地验证。

原 check_citable 继续作为比较工具的签核/适用性接口。正式入口另使用 check_batch_report：要求可恢复包和追加的两侧运行记录，检查相同包/时钟/消费请求、不同新旧业务入口、当前业务源文件，以及输出与输入包绑定，并从持久输出重新比较核对报告。仅左右 JSON 导入的报告不可切流。重比较撤销原签核状态，必须重新签核。报告指纹增加报告/重放/门禁源文件与配置文件；资格清单及既有资格记录不修改。

## 验证

测试使用合成行情和临时隔离数据库。跨 Python 进程恢复时将 consumer_api 的 Provider 读取替换为抛错，仍可恢复同一 hash、逻辑时间及两个阶段的完整报价。负向覆盖 hash-only、篡改、缺账户、阶段错误、无时区、未捕获 scope/time、越界保存、非隔离读取、追加记录修改、测试 helper 冒充业务证据，以及实际 CLI 拒绝后路由/activation 零变化。

相关控制流旧测试改为显式提供报告 ID 和 unit checker；这些测试只证明决策/序列化，不作为业务报告证明。真实门禁负向测试不替换 checker。所有 JUnit 保存在 [本轮目录](phase_f_shadow_foundation_20261008/)，包括初始失败与修复后结果；最终去重为 **477 passed、0 failed**：核心 170、batch/CLI 111、输入恢复 28、兼容性 175、文档守卫 20（输入恢复与 batch 集有重叠）。计数及受保护源文件/生产库存前后核对见 [verification.json](phase_f_shadow_foundation_20261008/verification.json)。OpenSpec strict、ruff F/I 和 git diff --check 通过。

最初扩展测试曾暴露 scheduler 的 AST import 断言失败（`test_workflow_data_cutover.py::test_scheduler_data_accesses_use_unified_runtime_products_and_pipelines`）；本轮没有修改 scheduler。该失败保留在 initial.junit.xml，尚未修复，不计入本轮最终相关通过集。本轮没有运行全仓测试或建立可信的前 Phase F 对照，不能据此宣告全仓零回归。

## 后续工作与依赖

3.1、3.6 完成后可继续 7.1，再做 3.7。3.9 仍依赖 3.7、7.2、7.4、7.5、4.6：需要实际新旧分析/风控入口使用上述注入接口，产生同输入的业务输出；3.10 再执行实际范围双跑、跨进程重放和差异签核。当前不存在由本轮产生的可用于生产切流的业务报告。

**后续注意/待修复项**：本轮未完成完整角色接入、实际调度遗漏基准、非空影子订单/Clerk 链和生产部署验收。报告代码指纹变更使旧报告不能复用；Trader A 受保护实现本轮没有改写，既有资格/生产库存的前后只读核对见 verification.json。

**为何现在不修**：用户本次授权范围为 3.1、3.6，业务双跑/影子账本及集成验收按 tasks 的独立依赖实施，不以合成 adapter 测试替代 3.9/3.10 的业务证据。历史报告及初始失败保留。
