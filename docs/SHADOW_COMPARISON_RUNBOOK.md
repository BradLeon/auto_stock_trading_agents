# 影子比较运行手册（Phase F）

> 权威声明：影子运行的结论**只来自可重放的固定输入**（`workflow/shadow_inputs.py`）。
> 同 vintage 不等于同输入；账户状态、运行时行情、逻辑评估时间任一不同，两轮就不可直接比较。

本手册描述的命令即 `ats shadow` 的实际动作，由 `tests/test_shadow_cli.py` 逐条校验。

## 0. 前置：先有输入包，再谈比较

```bash
# 1) 构建影子输入包（新建模块，刻意不改 consumer_api.py —— 它是受指纹文件）
#    必填：logical_eval_time；其余面按适用矩阵固定或显式标注不适用
python - <<'PY'
from ats.workflow.shadow_inputs import build_packet, mark_not_applicable
from ats.workflow.shadow_matrix import expected_not_applicable

packet = build_packet(
    run_id="run-1", consumer_id="macro", batch_class="research_read",
    logical_eval_time="2026-10-05T09:00:00+00:00",
    persistent_refs="<hash>", projection_hash="<hash>")
packet = mark_not_applicable(packet, *expected_not_applicable("research_read"))
packet.assert_usable_as_evidence()          # 逻辑评估时间未固定即拒绝
PY
```

**矩阵的两个方向都是错误**：

| 方向 | 后果 | 谁承担 |
|---|---|---|
| 研究批次被要求提供交易面 | 批次永远跑不起来，比较静默从不发生 | 矩阵写错 |
| 交易批次缺必要面 | 得到因错误原因而干净的结果 | 矩阵写错 |

- `research_read`：只固定持久化 refs + 投影 hash + 逻辑评估时间；运行时面**显式标注不适用**。
- `decision` / `trading`：全量固定（含 `market_runtime`、`account_state`、`history_state`、
  `ruleset_version`、`model_config`）。

## 1. 记录一次比较

```bash
ats shadow compare \
  --report-id rep-2026-10-05-macro \
  --run-id run-1 \
  --consumer-id macro \
  --batch-class research_read \
  --scope-json '{"products": ["MU"], "purpose": "cutover"}' \
  --packet-hash <input-packet-hash> \
  --expected-triggers-json '["trigger-1@09:00", "trigger-2@09:30"]' \
  --left-json '{"_packet": {...}, "schedule_omission": ["trigger-1@09:00"]}' \
  --right-json '{"_packet": {...}, "schedule_omission": ["trigger-1@09:00", "trigger-2@09:30"]}' \
  --db var/shadow/reports.sqlite
```

输出是六面逐面结论，**不是汇总数字**：

| 面 | 判据 | 允许区间 |
|---|---|---|
| `input_snapshot` | 精确 | 无 |
| `schedule_omission` | 对**独立预期集合**判定 | 0（任何遗漏即差异） |
| `analyst_output` | Jaccard 距离 | 0.34 |
| `risk_verdict` | 精确（含 counterproposal） | 无 |
| `approval_chain` | 精确（含**轮次号**） | 无 |
| `trade_attribution` | 精确 | 无 |

### 两条不可交换的规则

1. **`--expected-triggers-json` 不可由两轮并集推导。** 两路径同时漏掉一个触发器时它们
   「一致」；若以互相比较为准，漏掉的理由永远不会被看到。预期集合必须独立提供。
2. **`not-compared` 永不折算为 `matched`。** 缺失必需面时报告不可用作该范围的证据。

### 为什么精确面没有允许区间

风控 verdict、审批结论、订单归因是**事实**不是措辞。给它们允许区间等于发一张
「按未执行规则交易」的执照。若这些面出现差异，唯一出路是**修好并重跑**——没有权限方
可以接受。

## 2. 处置 diverged 面

```bash
# 容忍面可由矩阵声明的权限方接受；权限不符会被拒绝
ats shadow accept \
  --report-id rep-2026-10-05-macro \
  --surface analyst_output \
  --actor <owner> \
  --authority chief_owner \
  --reason "角色重构已按新契约逐条核对" \
  --db var/shadow/reports.sqlite

ats shadow acceptances --report-id rep-2026-10-05-macro --db var/shadow/reports.sqlite
```

接受记录**可审计**（操作者、权限方、理由、时间），且**可撤销**——撤销在事件日志里追加
一条，接受行保留。撤销后该差异回到未接受状态。

## 3. 签核 / 驳回 / 撤销

```bash
ats shadow signoff  --report-id <id> --actor <owner> --reason <理由> \
                    --required-surface risk_verdict --db <db>
ats shadow reject   --report-id <id> --actor <reviewer> --reason <理由> --db <db>
ats shadow revoke   --report-id <id> --actor <owner>  --reason <理由> --db <db>
```

- **签核即校验**：存在未接受差异或必需面 `not-compared` 时**拒绝签核**。这不是把问题推给
  引用环节——那时操作者刚断言报告可用，而当时没有任何东西这么说。
- **驳回不改写原结论**（追加式，SQLite 触发器强制）。改写会让「曾经签核过」无从查证，
  而驳回本身也可能被质疑。
- **某面补齐后此前的 `not-compared` 仍在**：重跑是新事件。"当时没看、后来看了" 正是审计
  需要的。

## 4. 引用校验（四项，一次列全）

```bash
ats shadow check --report-id <id> --scope-json '{...}' \
                 --required-surface risk_verdict --db <db>
# 可引用 → exit 0；不可引用 → exit 1（可直接作 shell 门禁）
```

四项：**签核有效且未被驳回/撤销** · **必需面无 not-compared** · **无未接受差异** ·
**报告仍适用于当前 scope 与当前代码配置**。

第四项靠指纹：报告记录了比较时的代码/配置指纹。指纹变了（`config/data/` 下任何
consumer 契约或 manifest 改动）则该报告不再为新代码作证。这是「改完代码要重跑影子」
从建议变成可强制的原因。

查看历史：

```bash
ats shadow report      --report-id <id> --db <db>
ats shadow events      --report-id <id> --db <db>
```

## 5. 影子账本（3.7）

```bash
ats shadow intents --cycle-id c1 --order-db var/shadow/orders.sqlite
ats shadow attest  --run-id run-1 --order-db var/shadow/orders.sqlite
```

- 影子订单意图写入**独立账本** `var/shadow/orders.sqlite`（自己的 env
  `ATS_SHADOW_ORDER_DB`，刻意不与 Phase E 的 `ATS_SHADOW_DB_PATH` 混用——那是工作流
  运行与触发的库，把订单意图放进去会让它和调度状态共用同一条访问规则）。
- **真实 `trades` 写入经仲裁拒绝，不静默改道。** 改道是更诱人的方案且更糟：误写看起来
  成功了，于是同一个错误在影子窗口之外复发并真的写进生产。
- `attest` 的 `prohibition_exercised=false` 意味着本次运行**没有到达下单调用点**，
  因此**不能**据此声称禁令有效。没有尝试不等于通过。

## 6. 常见拒绝原因

| 拒绝信息 | 含义 | 处置 |
|---|---|---|
| `no downstream difference can be attributed` | 两轮输入包不同 | 重建输入包后重跑 |
| `not-compared` | 该面无可比数据 | 补数据后重跑（不可跳过） |
| `no acceptance authority` | 精确面不接受 | 修好并重跑 |
| `requires acceptance by X` | 权限方不符 | 换正确权限方或修差异 |
| `scope mismatch` | 报告覆盖的范围不同 | 为该 scope 单独比较 |
| `predates a change` | 报告早于代码/配置变更 | 重跑影子比较 |
| `unaccepted divergence(s)` | 存在未处置差异 | 接受或修正后重跑 |

## 7. 与后续步骤的关系

本手册产出的报告是**切流的唯一证据来源**（`workflow/cutover-control` 在切流时调
`check_citable`）。切流请求本身不需要「规划批准」以外的东西，但**live 真实切换另需
可审计的实盘授权**，二者不可互相替代。
