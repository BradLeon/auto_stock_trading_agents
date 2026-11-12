# R5c/R5d 退役的迁移期命令用法存档

`switch-trade-path-and-retire-r5` 第 8 组（2026-11-10）删除了交易路径切换期间使用的命令。下面是删除前一刻各命令 `--help` 的原样输出，供查历史记录或复读旧证据时参考。

删除后这些命令只保留一个版本的提示桩：运行时打印“已退役”及替代入口，退出码 2。

| 命令 | 删除于 | 替代入口 |
|---|---|---|
| `ats drill` | 8.4 | 实盘路由演练用 `ats live drill` / `ats live rollback-drill` |
| `ats cutover` | 8.4 | 无（切换控制平面已在 R5d 删除） |
| `ats shadow` | 8.4 | 无 |
| `ats batch`、`ats read` | 8.5 | 无 |
| `ats dispatch` 的 `ownership`、`executed`、`record-execution`、`rollback-plan`、`rollback-history` | 8.6 | 无 |
| `ats dispatch` 的 `ledger` 与 `runtime-*` | 8.8 | `runtime-expected` / `runtime-compare` 改名为 `coverage-expected` / `coverage-compare`（参数不变）；其余无。保留 `pause`、`resume`、`owner-log` |
| `ats intake` | 8.7 | 退役消费方核验用 `ats retirement zero/decide` |
| `ats consumer-cutover`（含 `restore`） | 9.3 | 无：研究读路由表已删除；数据产品健康用 `ats data health [--consumer <id>]`、`ats data health-log` |
| `ats data assurance`（query/history/record/revoke） | 9.3 | 无：读取资格账本已删除；历史证据在 `var/data.sqlite` 的 `dataflow_assurance_events` 表，只读 |

## 删除前的 `--help` 输出

```text
=== ats drill
usage: ats drill [-h] [--drill-id DRILL_ID] [--batch-id BATCH_ID]
                 [--trigger TRIGGER] [--executed-json EXECUTED_JSON]
                 [--unconfirmed UNCONFIRMED] [--authorisation AUTHORISATION]
                 [--fallback-retired] [--fallback-unavailable]
                 [--environment ENVIRONMENT] [--account ACCOUNT]
                 [--live-reference LIVE_REFERENCE] [--run-id RUN_ID]
                 [--cycle-id CYCLE_ID] [--counts-json COUNTS_JSON]
                 [--order-db ORDER_DB] [--report-db REPORT_DB]
                 [--cutover-db CUTOVER_DB] [--batch-db BATCH_DB]
                 [--dispatch-db DISPATCH_DB] [--switch-db SWITCH_DB]
                 [--route-db ROUTE_DB] [--actor ACTOR] [--json]
                 {read-rollback,schedule-rollback,trade-rollback,all-rollbacks,history,traceability,unapproved-orders,acceptance}

positional arguments:
  {read-rollback,schedule-rollback,trade-rollback,all-rollbacks,history,traceability,unapproved-orders,acceptance}

options:
  -h, --help            show this help message and exit
  --drill-id DRILL_ID   演练标识（缺省按 action 派生）
  --batch-id BATCH_ID   read-rollback: 批次 ID
  --trigger TRIGGER     schedule-rollback: 逻辑触发，可重复
  --executed-json EXECUTED_JSON
                        schedule-rollback: 已执行触发映射 {trigger: [refs]}
  --unconfirmed UNCONFIRMED
                        schedule-rollback: 无执行记录的触发，可重复
  --authorisation AUTHORISATION
                        schedule-rollback: 部署授权引用
  --fallback-retired    read-rollback: 声明回退目标已退役（验证保持现状）
  --fallback-unavailable
                        read-rollback/trade-rollback: 声明回退目标不可用
  --environment ENVIRONMENT
                        trade-rollback: environment
  --account ACCOUNT     trade-rollback: account
  --live-reference LIVE_REFERENCE
                        trade-rollback: 实盘授权引用（缺省即被正确拒绝）
  --run-id RUN_ID       traceability/unapproved-orders: 影子运行 ID
  --cycle-id CYCLE_ID   traceability: 决策周期
  --counts-json COUNTS_JSON
                        read-rollback: 影子/审批/账本记录计数 {name: int}
  --order-db ORDER_DB   覆盖影子订单账本路径
  --report-db REPORT_DB
                        覆盖影子报告库路径
  --cutover-db CUTOVER_DB
                        覆盖切流控制库路径
  --batch-db BATCH_DB   read-rollback/schedule-rollback:
                        覆盖批次清单与部署授权库（与控制平面、切换记录是不同文件）
  --dispatch-db DISPATCH_DB
                        覆盖调度状态库路径
  --switch-db SWITCH_DB
                        覆盖调度切换记录库路径
  --route-db ROUTE_DB   覆盖交易路由库路径
  --actor ACTOR         执行人
  --json                输出 JSON 而非 Markdown
=== ats cutover
usage: ats cutover [-h]
                   [--boundary {,projection_read,analyst_output,dispatcher_schedule,approval_lifecycle,clerk_publication,live_trader}]
                   [--route ROUTE] [--scope-json SCOPE_JSON]
                   [--consumer-id CONSUMER_ID] [--report-id REPORT_ID]
                   [--report-db REPORT_DB] [--snapshot-as-of SNAPSHOT_AS_OF]
                   [--actor ACTOR] [--reason REASON]
                   [--fallback-proof {,valid,missing}]
                   [--fallback-retired {,yes,no}]
                   [--fallback-available {,yes,no}] [--db DB] [--bootstrap]
                   {state,wiring,preflight,set-route,activate,release,active,history,fallback,reverify}

positional arguments:
  {state,wiring,preflight,set-route,activate,release,active,history,fallback,reverify}

options:
  -h, --help            show this help message and exit
  --boundary {,projection_read,analyst_output,dispatcher_schedule,approval_lifecycle,clerk_publication,live_trader}
  --route ROUTE         set-route/fallback: target | legacy | disabled
  --scope-json SCOPE_JSON
                        activate/release: scope
  --consumer-id CONSUMER_ID
                        activate/fallback/reverify: consumer
  --report-id REPORT_ID
                        activate: 引用的影子报告 ID
  --report-db REPORT_DB
                        影子报告库路径
  --snapshot-as-of SNAPSHOT_AS_OF
                        reverify: 快照时点
  --actor ACTOR         operator identity
  --reason REASON       变更理由（set-route/release 必填）
  --fallback-proof {,valid,missing}
  --fallback-retired {,yes,no}
  --fallback-available {,yes,no}
  --db DB               覆盖切流控制库路径
  --bootstrap           初始化六条边界并声明本仓库接线
=== ats shadow
usage: ats shadow [-h] [--report-id REPORT_ID] [--proof-file PROOF_FILE]
                  [--matrix-file MATRIX_FILE] [--supersedes SUPERSEDES]
                  [--run-id RUN_ID] [--pair-id PAIR_ID]
                  [--isolation-root ISOLATION_ROOT]
                  [--input-store INPUT_STORE] [--request-file REQUEST_FILE]
                  [--consumer-id CONSUMER_ID]
                  [--batch-class {,research_read,decision,trading}]
                  [--scope-json SCOPE_JSON] [--packet-hash PACKET_HASH]
                  [--expected-triggers-json EXPECTED_TRIGGERS_JSON]
                  [--left-json LEFT_JSON] [--right-json RIGHT_JSON]
                  [--surface SURFACE] [--authority AUTHORITY]
                  [--required-surface REQUIRED_SURFACE] [--actor ACTOR]
                  [--reason REASON] [--cycle-id CYCLE_ID] [--db DB]
                  [--order-db ORDER_DB] [--limit LIMIT]
                  {acceptance-capture,acceptance-run,acceptance-record,acceptance-report,acceptance-check,acceptance-signoff,acceptance-reject,acceptance-revoke,capture-business,run-pair,report,events,acceptances,compare,signoff,reject,revoke,accept,check,intents,attest}

positional arguments:
  {acceptance-capture,acceptance-run,acceptance-record,acceptance-report,acceptance-check,acceptance-signoff,acceptance-reject,acceptance-revoke,capture-business,run-pair,report,events,acceptances,compare,signoff,reject,revoke,accept,check,intents,attest}

options:
  -h, --help            show this help message and exit
  --report-id REPORT_ID
                        report ID
  --proof-file PROOF_FILE
                        acceptance-record: actual new/replay execution proof
                        JSON
  --matrix-file MATRIX_FILE
                        acceptance-run: pre-run frozen requirement matrix JSON
  --supersedes SUPERSEDES
                        acceptance-record: prior immutable report ID
  --run-id RUN_ID       compare/attest: shadow run ID
  --pair-id PAIR_ID     run-pair: unique paired execution ID
  --isolation-root ISOLATION_ROOT
                        business pair: parent isolation root
  --input-store INPUT_STORE
                        business pair: durable frozen input SQLite
  --request-file REQUEST_FILE
                        capture-business: request JSON; acceptance-capture:
                        explicit cycle_id/account/fill_price simulation recipe
  --consumer-id CONSUMER_ID
                        compare: consumer
  --batch-class {,research_read,decision,trading}
  --scope-json SCOPE_JSON
                        compare/check: scope
  --packet-hash PACKET_HASH
                        compare: 输入包 hash
  --expected-triggers-json EXPECTED_TRIGGERS_JSON
                        compare: 独立预期触发集合（不可由两轮并集推导）
  --left-json LEFT_JSON
                        compare: 旧路径各面数据
  --right-json RIGHT_JSON
                        compare: 新路径各面数据
  --surface SURFACE     accept: 面名
  --authority AUTHORITY
                        accept: 权限方
  --required-surface REQUIRED_SURFACE
                        check: 必需面，可重复
  --actor ACTOR         operator identity
  --reason REASON       审计理由
  --cycle-id CYCLE_ID   intents/attest: 决策周期
=== ats batch
usage: ats batch [-h] [--batch-id BATCH_ID]
                 [--batch-class {,collection_publish,research_read,schedule,internal_state_approval,live_trader}]
                 [--owner OWNER] [--old-route OLD_ROUTE]
                 [--new-route NEW_ROUTE] [--scope-json SCOPE_JSON]
                 [--observation-window OBSERVATION_WINDOW]
                 [--success-criteria SUCCESS_CRITERIA]
                 [--stop-reason STOP_REASON] [--fallback-route FALLBACK_ROUTE]
                 [--fallback-proof {,valid,missing}]
                 [--fallback-retired {,yes,no}]
                 [--fallback-available {,yes,no}]
                 [--fallback-drill FALLBACK_DRILL] [--report-id REPORT_ID]
                 [--direct-verification]
                 [--qualification-status QUALIFICATION_STATUS]
                 [--serving {,yes,no}] [--actor ACTOR] [--json] [--db DB]
                 [--cutover-db CUTOVER_DB] [--report-db REPORT_DB]
                 {list,declare,show,dry-run,drift}

positional arguments:
  {list,declare,show,dry-run,drift}

options:
  -h, --help            show this help message and exit
  --batch-id BATCH_ID   declare/show/drift: 批次 ID
  --batch-class {,collection_publish,research_read,schedule,internal_state_approval,live_trader}
  --owner OWNER         declare: 责任方
  --old-route OLD_ROUTE
                        declare: 旧路由
  --new-route NEW_ROUTE
                        declare: 新路由
  --scope-json SCOPE_JSON
                        declare: 批次范围
  --observation-window OBSERVATION_WINDOW
                        declare: 观察窗口
  --success-criteria SUCCESS_CRITERIA
                        declare: 成功判据
  --stop-reason STOP_REASON
                        declare: 停止条件
  --fallback-route FALLBACK_ROUTE
                        declare: 回退目标
  --fallback-proof {,valid,missing}
  --fallback-retired {,yes,no}
  --fallback-available {,yes,no}
  --fallback-drill FALLBACK_DRILL
                        declare: 已演练回退的引用
  --report-id REPORT_ID
                        declare: 引用的影子报告 ID
  --direct-verification
                        declare: 已在生产运行、直接核验接收（不重复切换）
  --qualification-status QUALIFICATION_STATUS
                        drift: 当前资格状态
  --serving {,yes,no}   drift: 该批次是否正在承载流量
  --actor ACTOR         declare: operator identity
  --json                输出 JSON 而非 Markdown
  --db DB               覆盖批次清单库路径
  --cutover-db CUTOVER_DB
                        覆盖切流控制库路径
  --report-db REPORT_DB
                        独立影子报告库路径
=== ats read
usage: ats read [-h] [--batch-id BATCH_ID] [--db DB] [--cutover-db CUTOVER_DB]
                [--report-db REPORT_DB] [--authorisation AUTHORISATION]
                [--authorised-by AUTHORISED_BY] [--issued-by ISSUED_BY]
                [--scope SCOPE] [--valid-until VALID_UNTIL] [--note NOTE]
                [--actor ACTOR] [--apply] [--json]
                {plan,switch,authorize,show-auth,history}

positional arguments:
  {plan,switch,authorize,show-auth,history}

options:
  -h, --help            show this help message and exit
  --batch-id BATCH_ID   switch: 批次 ID
  --db DB               批次清单与运行记录库
  --cutover-db CUTOVER_DB
                        切流控制状态库
  --report-db REPORT_DB
                        独立影子报告库路径
  --authorisation AUTHORISATION
                        已登记的部署授权引用；switch 必填
  --authorised-by AUTHORISED_BY
                        authorize: 授权人
  --issued-by ISSUED_BY
                        authorize: 签发人
  --scope SCOPE         authorize: 逗号分隔的消费者范围
  --valid-until VALID_UNTIL
                        authorize: 授权有效期
  --note NOTE           authorize: 备注
  --actor ACTOR         执行人
  --apply               switch: 真正改路由；缺省只决定不改动
  --json                history: JSON 输出
=== ats intake
usage: ats intake [-h] [--consumer-id CONSUMER_ID] [--run-id RUN_ID]
                  [--opinion-input OPINION_INPUT]
                  [--optional-input OPTIONAL_INPUT]
                  [--stale-input STALE_INPUT]
                  {verify,report,digest,not-tradable,disposition,evidence-index}

positional arguments:
  {verify,report,digest,not-tradable,disposition,evidence-index}

options:
  -h, --help            show this help message and exit
  --consumer-id CONSUMER_ID
                        verify/not-tradable: 单一消费者；缺省为全部十个
  --run-id RUN_ID       not-tradable: 隔离运行 ID
  --opinion-input OPINION_INPUT
                        verify: 该角色实际读取的观点来源，可重复
  --optional-input OPTIONAL_INPUT
                        disposition: 缺失的可选输入，可重复
  --stale-input STALE_INPUT
                        disposition: 按报告年龄被判 stale 的来源，可重复
=== ats dispatch
usage: ats dispatch [-h] [--approval APPROVAL]
                    [--void-unfinished VOID_UNFINISHED] [--trigger TRIGGER]
                    [--execution-ref EXECUTION_REF] [--state STATE]
                    [--switch-db SWITCH_DB] [--actor ACTOR]
                    [--isolation-root ISOLATION_ROOT] [--workflow WORKFLOW]
                    [--business-scope BUSINESS_SCOPE]
                    [--owner {legacy,shadow,dispatcher}] [--reason REASON]
                    [--token TOKEN] [--dispositions DISPOSITIONS]
                    [--from-time FROM_TIME] [--to-time TO_TIME]
                    {runtime-state,runtime-reload,runtime-prepare,runtime-freeze,runtime-inventory,runtime-handover,runtime-cancel-freeze,runtime-expected,runtime-compare,ownership,ledger,executed,record-execution,rollback-plan,rollback-history,pause,resume,owner-switch,owner-rollback,owner-log}

positional arguments:
  {runtime-state,runtime-reload,runtime-prepare,runtime-freeze,runtime-inventory,runtime-handover,runtime-cancel-freeze,runtime-expected,runtime-compare,ownership,ledger,executed,record-execution,rollback-plan,rollback-history,pause,resume,owner-switch,owner-rollback,owner-log}

options:
  -h, --help            show this help message and exit
  --approval APPROVAL   resume: 用户批准原文或其引用（恢复必填，停用不需要）
  --void-unfinished VOID_UNFINISHED
                        pause/resume: 作废未完成认领的理由（有未完成认领时必填，running 不可作废）
  --trigger TRIGGER     限定单一逻辑触发
  --execution-ref EXECUTION_REF
                        record-execution: 执行引用
  --state STATE         第4组的 dispatch 状态库
  --switch-db SWITCH_DB
                        执行记录与回滚记录库
  --actor ACTOR         执行人
  --isolation-root ISOLATION_ROOT
                        runtime-* 隔离演练根目录；不修改生产 owner
  --workflow WORKFLOW   runtime-* workflow ID
  --business-scope BUSINESS_SCOPE
                        runtime-* 精确 scope JSON
  --owner {legacy,shadow,dispatcher}
  --reason REASON
  --token TOKEN         freeze 返回的 token
  --dispositions DISPOSITIONS
                        逐 trigger key 处置 JSON
  --from-time FROM_TIME
                        预期集合窗口 ISO 起点（带时区）
  --to-time TO_TIME     预期集合窗口 ISO 终点（带时区）
```

## R5d（第 9 组，2026-11-11）删除前的用法

```text
=== ats consumer-cutover
usage: ats consumer-cutover [-h] [--consumer-id CONSUMER_ID] [--actor ACTOR]
                            [--approval APPROVAL] [--reason REASON] [--db DB]
                            {status,health,log,restore}

positional arguments:
  {status,health,log,restore}

options:
  -h, --help            show this help message and exit
  --consumer-id CONSUMER_ID
                        information | fundamental | ...
  --actor ACTOR         restore: 操作者
  --approval APPROVAL   restore: 用户批准原文（必填）
  --reason REASON       restore: 原因（必填）
  --db DB               覆盖切流控制库路径
```

`ats data assurance` 是 `ats data` 的一个动作，删除前的专属参数（取自 `runtime/cli.py` 的参数定义）：

```text
        help="purge-source: 声明数据已在别处留存，写入清除记录供审计",
    )
    data.add_argument("--purge-note", default="", help="purge-source: 写入清除记录的备注")
    data.add_argument("--consumer", dest="assurance_consumer", default="",
                      help="assurance: target coverage consumer ID")
    data.add_argument("--domain", dest="assurance_domain", default="",
                      help="assurance: consumer data domain")
    data.add_argument("--contract-version", dest="assurance_contract_version", default="",
                      help="assurance: pinned consumer contract version")
    data.add_argument("--evidence-type", default="", help="assurance record: required evidence kind")
    data.add_argument("--outcome", dest="evidence_outcome", choices=["passed", "failed"], default="",
                      help="assurance record: evidence outcome")
    data.add_argument("--scope-json", dest="evidence_scope_json", default="{}",
                      help="assurance: exact JSON scope shared by all evidence")
    data.add_argument("--evidence-details-json", default="{}",
                      help="assurance record: source statuses and per-product rollback proof; no raw text")
    data.add_argument("--summary", dest="evidence_summary", default="",
                      help="assurance record: sanitized result summary")
    data.add_argument("--command-summary", dest="evidence_command_summary", default="",
                      help="assurance record: sanitized command description (no secrets)")
    data.add_argument("--environment-name", dest="evidence_environment_names", action="append", default=[],
                      help="assurance record: environment variable name only; never its value")
    data.add_argument("--prerequisite-event", dest="evidence_prerequisites", action="append", default=[],
                      help="assurance record: prerequisite evidence ID; repeatable")
    data.add_argument("--fingerprint-path", dest="evidence_fingerprint_paths", action="append", default=[],
                      help="assurance record: dependency file to hash; repeatable")
```
