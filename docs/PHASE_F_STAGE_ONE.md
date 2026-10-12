# Phase F 阶段一：独立准备交付（2026-10-10）

后续 13.6 完整工程门禁与 12.2 数据审计实施已完成，当前 101/117；12.2 所选用途覆盖仍 pending，实际 workflow 补证及资格继续由 6.4/6.6 承接。完整本机发现、共享修复及实际缓存/历史检查见 [失败与用途审计](PHASE_F_FAILURE_AND_USE_AUDIT.md)；以下保留阶段一准备时点结果。

本阶段完成 13.6 的失败归因台账、12.2 的必需用途盘点，以及 6.4/6.7 的验证脚本和生产观察方案准备。**这不是四项完整验收通过，也没有生产切换、资格签发、报告签核或真实券商写操作。** Tasks 总数仍为 98/117，19 项待办。

验收依据是 [TARGET_WORKFLOW_DATAFLOW.md](TARGET_WORKFLOW_DATAFLOW.md)，逐章映射见 [验收要求映射](PHASE_F_ACCEPTANCE_REQUIREMENTS.md)。单任务、子流程、完整决策流程分别按实际请求的依赖闭包验收；Routine/Event 是所选模式的替代范围。旧业务成功或新旧一致不作为新入口前置；共享依赖、安全守卫和历史保留仍需证明。

## 13.6：全量发现与归因

原始发现 `docs/validation/phase_f_read_executor_20261009/all_discovery.xml` 为 3560 passed、116 failed、27 errors，共 143 个失败/error。执行期间与受保护源码修改重叠，不能用作最终冻结基线。旧归因文件保留；新脚本重新读取原始 JUnit，检查原失败没有丢失，为每条保存原消息、历史分类、当前源码位置/摘要、受影响的新需求、复验入口和处置状态。

归因依据包括此前已核验的修复记录和按测试模块汇总的原因。**模块级原因仍是待逐例核验的归因，不是每个失败已经修复的证明。** 51 项历史修复仍需在最终基线复验；全部条目保持 `scope_exclusion_proven=false`、`gate_pass_claim=false`。未提供新路径不可达证据的旧专属条目不自动排除；全仓全绿也不成为新入口准入条件。

共享数据定向复验发现的六项后续处置：

| 原因 | 新需求关联与后续工作 |
| --- | --- |
| Anthropic 字面扫描把指纹路径/退役处置识别为活跃配置 | 语义核验实际 Layer/数据边界，并保留活跃旁路反例 |
| 旧 Chain refresh fixture 缺 managed queue lease | 按新 Layer 治理产品读补验，不放松租约守卫 |
| 相同事实 hash 再提取后仍为 superseded | 核验并修复共享中立事实/历史状态语义；当前不能排除 |
| FactSet alias-conflict fixture 缺 core_metrics | 补齐当前策略配置后复验 alias 冲突拒绝 |
| broker fixture 的 SimpleNamespace 不符合运行时 schema | 使用类型化真实形状，并保留坏 payload 不可用反例 |
| scheduler 字面期望退役 research_pipeline import | 在实际受控 CLI/调度入口证明采集与研究边界 |

缺失原始 PDF/图片的提取验证仍记录缺材料；不以重造文件替代真实输入。最终 13.6 待 5.16 完成后，用 `./scripts/run_tests.sh` 执行发现和明确范围的必需门禁；对任何排除给出逐例的新路径可达性证据。

## 12.2：必需用途与 exact-scope 清单

`scripts/prepare_phase_f_stage_one.py inventory` 从当前 owners、schedules、decision profiles、Event 路由及 coverage 契约构建实际 plan，独立导出任务依赖闭包及 read identity。7 个配置 Workflow 共 84 条任务 scope 记录；完整 ai_hardware 决策的 Routine、Event 各 43 条，共 9 个备选组、170 条用途出现记录。**这些不是 170 个同时必需的生产执行。** 配置没有默认时点的 scope 是盘点模板，as_of 是示例时点；Event 的实际 source/version 必须由真实触发绑定。

六类分析用途逐项列出消费者契约、scope、产品允许清单、必需证据、TTL、输入条件和依赖。产品允许清单不意味着每次调用读取所有产品。Chief、Risk、Trader、Clerk 另列条件用途模板：完整所选分析成功才进 Chief；No Action 不强求交易；有行动建议才进 Risk；精确 revision 经 Boss 批准才进 Trader；Clerk 对账覆盖手动及未归因订单。条件用途最终须绑定实际 cycle/approval/broker refs，模板不证明执行成功。SEC optional 例外保留，不能掩盖其他必需输入缺失。

12.2 完整验收仍需每个选定用途的实际读取、准入/血缘/完整性、数据覆盖与历史引用重开结果。未绑定报告、未查资格、缺历史 refs 明确保持未验证。

## 6.4/6.7：已准备的验证入口

| 入口 | 现在可证明的范围 | 完整验收还需 |
| --- | --- | --- |
| `prepare_phase_f_stage_one.py inventory/triage` | 可复算用途清单、原始失败未丢失 | 实际用途验证、最终基线逐例处置 |
| `verify_dataflow_qualification.py --business-scopes --as-of` | 按所选消费者 exact-scope 做本地 cache/机制探针；拒绝用窗口终点代替独立运行时点 | 每个用途分别绑定真实数据/报告；代表性 cache 读不证明全部用途覆盖 |
| `refresh_phase_f_evidence.py` | 复用可重开的原始证据，保留原时点与前置，在隔离账本追加核验 | 6.1 最终冻结后按 6.4 接收；不升级历史签核或资格 |
| `prepare_phase_f_stage_one.py preflight` | 只读核验当前 exact-scope 资格、适用且签核有效的新报告；缺库、缺项、过期/漂移保持 pending | 正式执行器继续检查许可、代次、依赖、停止/恢复与其他部署门禁 |
| `capture_phase_f_technical.py` | 最小真实 Yahoo Technical 取证和隔离重放 | 当前需求/签核/最终资格，不能证明券商可用 |
| `probe_phase_f_tws_readonly.py` | 本机真实 portfolio、完成订单、成交及开放订单读响应；先禁止券商写 | 完整实际账户输入与生产用途证明；读回执不授予资格或部署许可 |

验证输出均使用新路径，不覆盖旧证据。preflight 的 `passed` 仅是所列资格/报告检查通过；缓存脚本运行完成也不是生产资格签发。预检不负责自动放行部署。未来短 TTL 重验只刷新过期的必需输入，复用其余仍适用的原证据；旧证据不能通过改时间变新。

### 可重复命令

以下全部用 uv。`RUN` 指本次新建输出目录；`AS_OF` 为独立、带时区的实际时点。示例盘点时点不用于生产 freshness 判断。

```sh
uv run python scripts/prepare_phase_f_stage_one.py inventory --as-of "$AS_OF" --out "$RUN/required_uses.json"
uv run python scripts/prepare_phase_f_stage_one.py triage --junit docs/validation/phase_f_read_executor_20261009/all_discovery.xml --previous docs/validation/phase_f_read_executor_20261009/failure_attribution_20261010.json --out "$RUN/failure_attribution.json"
uv run python scripts/verify_dataflow_qualification.py --business-scopes "$RUN/consumer_scopes.json" --as-of "$AS_OF" --data-cache var/data.sqlite --internal-cache var/ats.sqlite --output "$RUN/cache_probe.json"
uv run python scripts/refresh_phase_f_evidence.py --scopes "$RUN/scopes.json" --source-ledger "$SOURCE_LEDGER" --root "$RUN/intake" --output "$RUN/intake.json" --freeze "$FINAL_FREEZE" --reuse-current
uv run python scripts/prepare_phase_f_stage_one.py preflight --scopes "$RUN/scopes.json" --freeze "$FINAL_FREEZE" --bindings "$RUN/report_bindings.json" --source-ledger "$SOURCE_LEDGER" --out "$RUN/preflight.json"
uv run python scripts/capture_phase_f_technical.py --root "$RUN/technical" --entity COHR
```

`scopes.json` 是完整 RouteIdentity 对象数组；`consumer_scopes.json` 是 consumer→business_scope 对象，不得把同一消费者的多个业务范围合并，需逐范围执行。`report_bindings.json` 按 RouteIdentity.key 绑定 `report_id/report_db/batch_class/required_surfaces`；不得塞入 scope 外绑定。当前 inventory 中的 read_identity 是盘点时点身份，正式部署身份须在 5.16 持久许可契约完成后明确绑定，不能自动转换。

TWS 命令仅在本机沙盒外、有效只读会话下运行；本阶段没有连接 TWS：

```sh
uv run python scripts/probe_phase_f_tws_readonly.py --out "$RUN/tws_readonly.json"
```

回执只保留本地，权限 0600。脚本禁止 submit/cancel；缺返回/连接错误不能算通过。Risk/Trader 报价及账户的新鲜度按实际运行时契约（如 60 秒）检查；资格证据的 1/7/30/90 天 TTL 与此分离。Technical 历史收盘序列按相应市场日期/产品规则核验，不强加 60 秒实时报价要求。部署一次批准直到主动停用，与每次运行的 as_of、查询窗口和 TTL 分离。

## 生产观察方案（8.6 的提前准备，尚未批准或执行）

正式前置顺序：**5.16 → 6.1 → 6.4 → 6.6 → 6.7**；13.6 最终门禁依赖 5.16，13.7 依赖最终门禁与冻结。首批实际 read/schedule 部署仍依赖 8.5/8.6、13.6/13.7 和对应 9.4/10.4 前置。提前写脚本不是跳过这些依赖。

首批选择一个当次就绪的 exact-scope，候选可以是 Technical/COHR；是否含调度、依赖闭包和边界组合须由就绪清单明确。单读不自动授权调度，联合动作逐边界验收。真实券商写仍禁止；FakeBroker 全链可在物理隔离下验证 Clerk，不能作为真实网关证明。

| 预先登记项 | 登记与检查办法 |
| --- | --- |
| 窗口与责任 | 在动作前填实际 start/end（带时区）、负责人、回退负责人、部署许可 refs 和 scope。建议首轮两个交易日并覆盖所选真实触发；具体长度按触发频率定，不作为 TARGET 新增固定门槛。现在值待登记，不能激活 |
| 独立预期集合 | 从固定版 schedule 配置、真实已发布 Event/version、预登记手动 trigger IDs 导出。记录预期触发、选择/未选择原因；不能从成功执行倒推。无激活或空窗口是未测，不能用零记录证明覆盖 |
| 每次运行 | 保存独立 as_of/查询窗口、Event/source/version、run/输入 hash、需求与实现指纹、精确资格及报告 refs、owner/claim/generation、输出与 publish refs。运行前自动重查许可未撤销、资格/报告适用、输入 TTL 和停止状态 |
| 决策/交易范围 | 按实际所选用途检查完整分析、Chief No Action 或 Risk→Boss 精确 revision→Trader A→Clerk refs；研究用途不强求下单。保存真实券商写拒绝审计，既有合法成交与新违规写区分 |
| 覆盖/历史/采集 | 比较预期/实际，核验重复 claim/publish 和遗漏；只读重开历史 refs、订单归因；核对采集队列 owner 未被研究迁移改写 |
| 停止阈值 | 任一必需投影/输入缺失、资格/报告过期撤销或漂移、输入超 TTL、许可撤销/旧代、重复认领/提交/发布、未知或无归因订单、意外券商写、联合部分切换、历史引用不可解析，均停止对应边界并留审计 |
| 恢复 | 保持停止，保留在途授权/订单与原始证据，迟到成交只读对账。资格刷新本身不自动恢复；须当前代次、适用许可和已验收目标/恢复证明，旧 worker 不复活 |
| 结束评判 | 窗口后只读重开权威记录，逐项 passed/failed/untested/not-applicable。真实零使用/零重复须有非空独立预期和实际记录支持。失败、缺运行或缺证据均不宣布生产成功 |

观察不是日常人工续批；上线许可持续有效，但每次运行自动守卫，输入或资格失效时保持停止。实际窗口、负责人、许可和边界范围将作为 8.6 的具体审阅对象，此次准备不代填批准。

## 本地证据与验证

本轮产物在 `docs/validation/phase_f_stage_one_20261010/`：用途清单、逐例失败台账、测试 JUnit/日志以及缺账本 preflight 拒绝回执。原始材料保留在原路径，不覆盖。按用户规则 `docs/validation/`、`docs/assets/` 不提交 Git；本报告及脚本提供可追踪的结论和复算入口。

通过 `./scripts/run_tests.sh` 的准备脚本、原证据复用与文档一致性定向检查 **23 passed**（`preparation_ready.*`），现有资格 assurance 回归 **36 passed**（`assurance_ready.*`），共 **59 passed**。OpenSpec strict 与 `git diff --check` 通过；缺账本/缺报告 CLI 返回 pending、exit 1，且未创建源账本。实际缓存脚本只选择 Technical/COHR，输出一个消费者、零网络调用，Fundamental/SEC 不适用；无可复用运行时行情如实 ineligible，并未签发资格。TWS 只做帮助入口及隔离拒绝反例检查，没有真实连接。

定向检查不替代 13.6 最终全量发现，也不代表生产验收。诊断指纹文件 `diagnostic_freeze.json` 仅用于本轮预检反例，不是 6.1 最终冻结。
