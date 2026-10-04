# Dataflow 验证证据与 Phase F 读取资格

本 runbook 对应 `complete-target-dataflow` Tasks 4.1–4.6。2026-10-04 Task 4.4 重开后已补齐剩余五角色的正向、故障、只读回退与恢复验证，经用户确认完成；仅验证拒绝不能视为该任务完成。资格账本与 source/consumer 开关分离：查询不切换 read mode、不采集、不触发 Workflow 或交易。

五角色补验已执行，实时日线/TWS 及隔离完整账本/审批链的 9 条输入边完成 API 四步演练，4.4 已确认勾选；实际旧账本断链与期权时间缺失仍未达到完整输入标准，受影响输入和资格仍保持 partial/ineligible。详见 [补验结果](TARGET_DATAFLOW_TASK4_ACCEPTANCE.md) 和 [脱敏证据](TARGET_DATAFLOW_TASK4_RUNTIME_ROLES.json)。Clerk 原生只读回退是 `ats.data.runtime.broker.broker_state`，**不是**会写账本的 `clerk_run`。

## 先确认范围和依赖

权威声明：`config/data/target_dataflow_coverage.yaml` 中的 `consumers` 与 `qualification_policy`。调用必须固定 `domain_id + consumer_id + contract_version + scope`；scope 使用同一实体、产品范围和数据截止时点，不能借一个产品的成功外推整个角色。

每条证据必须包括：

- 明确的证据类型、passed/failed、带时区的验证 as-of、命令和结果摘要；
- 完整的 `required_fingerprint_paths`、该角色的 `consumer_fingerprint_paths`，以及本次实际涉及的 parser、产品、原文 hash/版本引用；
- 前置事件引用、必要的来源状态或逐产品回退证明；
- 环境变量只记录名称，禁止记录值、原文或账户信息。

证据 TTL 是**验证结果的有效期**，不是上游数据的日龄。最新 FactSet 月报、DRAM 免费数据期或最新电话会不会仅因距今天数而被这里降级。代码/配置变化、前置证据撤销、证据过期和最新失败均使资格失效。

## 追加式登记与查询

所有命令用 uv；`--db` 可显式指定隔离账本。生产登记前必须先有真实证据，不可把合成故障探针当作生产来源验收。

```sh
uv run ats data assurance record \
  --consumer sector --domain hierarchy --contract-version target-dataflow-v1 \
  --evidence-type read --outcome passed \
  --scope-json '{"entity":"DRAM_CONTRACT_PRICE","dataset":"industry_dram_contract_price"}' \
  --as-of 2026-10-03T00:00:00Z \
  --command-summary 'fixed cached native product read; source references retained' \
  --summary 'accepted vintage read back; this single event does not grant qualification' \
  --environment-name ATS_DATA_DB_PATH \
  --fingerprint-path src/ats/data/assurance.py \
  --fingerprint-path src/ats/data/consumer_api.py \
  --fingerprint-path config/data/structured.yaml \
  --fingerprint-path config/data/unstructured.yaml \
  --fingerprint-path src/ats/data/products/base.py

uv run ats data assurance query \
  --consumer sector --domain hierarchy --contract-version target-dataflow-v1 \
  --scope-json '{"entity":"DRAM_CONTRACT_PRICE","dataset":"industry_dram_contract_price"}'

uv run ats data assurance history --consumer sector --domain hierarchy \
  --scope-json '{"entity":"DRAM_CONTRACT_PRICE","dataset":"industry_dram_contract_price"}'

uv run ats data assurance revoke --event-id EVENT_ID --reason 'source policy changed'
```

事件存于 Data DB 的 `dataflow_assurance_events`。UPDATE/DELETE 被数据库 trigger 拒绝；撤销追加新事件。旧 schema 只在显式写入时补列，不由只读查询迁移。旧证据没有新详细证明时不得自动升级资格。

`--prerequisite-event` 可重复；前置事件必须属于同一个精确 scope。资格查询递归验证其结果、TTL、指纹、详细证明和撤销状态，不能因为已有较新的成功事件而忽略仍被引用的失效旧事件。

Python API：`record_evidence(..., details=...)`、`qualification(...)`、`evidence_history(...)`、`revoke_evidence(...)`。CLI 通过 `--evidence-details-json` 传递相同 details，仅接受白名单字段与有界引用。

## 逐产品回退证明

rollback/fallback 不能只登记一个 passed 标签。details.rollback 必须覆盖该角色的**每个**产品，包含：

```text
product, route, status, read_only, native_packet_equal,
input_refs, payload_hash, scope_hash, as_of, reason
```

route 必须匹配 qualification_policy.rollback_routes；成功结果要求只读、原生值与目标 packet 一致、非空血缘、有效时间及 SHA-256。scope_hash 绑定该产品的精确读取范围；多产品角色使用 scope.products 为各输入声明范围，不得以另一个实体/数据集的回退结果取得资格。缺任一产品、无真实回退或读取不完整，资格保持 ineligible。

本轮在相同缓存与截止时点演练“目标 packet → 目标入口故障 → 原生只读 API → 恢复 packet”，没有改生产开关。该回退绕过新的包装层，**仍共享已准入仓库**，不能修复底层坏数据。不得恢复已退役 Memory 事实表、直连 Provider 补研究材料或调用会写入的 `clerk_run` 冒充只读回退。

## SEC 唯一可选输入例外

政策 `sec-body-optional-v1` 同时登记于 unstructured registry 和资格清单。仅 `sec_edgar_filing_body / sec_filing_documents` 非阻塞；不豁免财务、SEC 索引、电话会、其他输入或交易审批。

Fundamental 的 completeness.details.inputs 必须包含财务、SEC 索引、电话会，以及 SEC 正文的实际状态。每项保留 input_id、source_status、checked_at、stage/reason、task/run 引用及已发布 input_refs。

SEC 失败/缺失时，必须独立验证四项：

```text
empty_input_safe, error_visible, no_risk_inference, invalid_material_rejected
```

其余证据通过后可返回 eligible，同时 `accepted_nonblocking_gaps` 保留原始失败事实、optional=true、blocking=false、policy_version 及 source_verified=false。不能改写来源为成功，不能将空正文理解为无风险或已审阅。财务、电话会、SEC 索引失败仍返回 ineligible；可选正文取得后仍须通过原质量门，错误 URL/身份/正文不得发布。不会自动启用 SEC job。

## 可重放验收及当前结果

```sh
UV_CACHE_DIR=/private/tmp/uv-cache uv run --offline --no-sync \
  python scripts/verify_dataflow_qualification.py \
  --data-cache var/data.sqlite --internal-cache var/ats.sqlite \
  --output /private/tmp/target-task4.json
```

脚本禁止网络连接，复用真实缓存和只读原生 API；资格故障注入、CLI record/revoke 和坏材料发布测试只写独立临时目录。报告分开标注缓存回退证明、合成机制证明与生产资格，不能把隔离 eligible 登记为生产授权。

逐角色结果及阻塞原因见 [TARGET_DATAFLOW_TASK4_ACCEPTANCE.md](TARGET_DATAFLOW_TASK4_ACCEPTANCE.md)。五个研究角色的样本回退可读；Technical、Chief、Risk、Trader、Clerk 有明确未验证或不可用输入，保持 ineligible。未向生产账本签发完整资格，未执行 Phase F 切流。

## 与受管采集的关系

持久化采集统一进入 SQLite queue，worker 以有效 lease 调用原 adapter/raw/gate/publish 链。定时、事件、人工及允许的 cache miss 都不是读取 API 的隐藏副作用；runtime 查询不进入持久化队列。

来源和 launchd 验收已由 Tasks 2 完成，详见 [UNSTRUCTURED_REFRESH_INVENTORY.md](UNSTRUCTURED_REFRESH_INVENTORY.md)；本轮复用这些记录，不重跑采集。旧 scheduler/Agent/Workflow caller 的旁路只登记，不在本 change 修复。注册表为“一装配入口、两领域注册表”；角色权限为十角色/16 条输入边，详见覆盖清单与 Tasks 3 报告。
