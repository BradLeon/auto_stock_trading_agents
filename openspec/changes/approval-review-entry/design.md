# Design

## Context

- **现有链路：** 首席进入 `pending` → 飞书群机器人推卡（URL 按钮，HMAC 签名 `thread_id:verdict`）→ `GET /feishu/approve` 验签后直接 `resume_cycle`。
- **审批语义：** `ats approval approve` 要求至少 12 位修订 hash 前缀；飞书链接没有这一约束。
- **网络环境：** quick tunnel 在 Clash 环境下多次失效，已知可行的稳定方案是 named tunnel（固定域名）加 launchd 托管。Clash TUN 栈需为 System。

## Goals / Non-Goals

**Goals：**
- 手机上能看清请求并完成批准或拒绝。
- 批准严格绑定所见的修订版本。
- 打开链接不产生副作用。

**Non-Goals：**
- 不改审批状态机。
- 不做多审批人。
- 不评价首席决策质量。

## Decisions

### D1. 方案选择：飞书卡片摘要 + 自托管审批页（建议）

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A（建议） | 群机器人卡片带摘要；按钮打开 `ats serve` 审批页，页内确认 | 不需要飞书企业应用；页面能展示完整内容；与终端审批共用逻辑 | 需要稳定的公网入口（named tunnel） |
| B | 飞书企业自建应用 + 原生卡片回调 | 在飞书内完成，无需打开网页 | 需建企业应用并配回调地址，回调仍需公网入口；卡片里放不下完整明细 |
| C | 只做本机网页 | 无网络依赖 | 不在 Mac 前就无法审批 |

建议 A。B 同样需要公网入口，却多了企业应用的审批与维护成本。C 解决不了“人不在电脑前”的问题，作为隧道不可用时的兜底保留。

### D2. 链接与签名

- 签名内容为 `thread_id : revision_hash : verdict : expires_at`；有效期默认与审批过期时间一致。
- `GET /approvals/<thread_id>?rev=…&sig=…` 只渲染详情页。
- 页面上的“批准”“拒绝”以 POST 提交，携带同一签名与一个一次性表单 token。
- 服务端比对当前待审批修订：
  - 不一致或已过期：返回“已失效，请以最新请求为准”；
  - 一致：调用与 `ats approval approve` 相同的函数。
- 旧的 `/feishu/approve` GET 直接恢复的方式停用，旧链接改为跳转到详情页。

### D3. 请求摘要单一来源

由 `ats approval export` 生成的摘要结构作为唯一来源，飞书卡片、审批页和终端 `show` 都从它渲染，避免三处内容不一致。

### D4. named tunnel

- 用户在 Cloudflare 创建 named tunnel 并绑定子域名；`cloudflared` 由 launchd 托管，使用 http2 协议。
- ingress 只放行 `/approvals/*`，其余路径返回 404。
- `FEISHU_APPROVE_BASE` 改为固定域名。

## Risks / Trade-offs

- [隧道在审批窗口内不可用] → 卡片同时给出本机链接；守护检查在待审批出现时探测隧道可达性，不可达则在飞书提醒“请在 Mac 上审批”。
- [链接被转发给他人] → 签名有效期短，且每个 POST 需一次性 token；群仅审批人可见。
- [卡片摘要过长被截断] → 订单超过 8 笔时卡片只列前 8 笔和合计，完整内容在页面中查看。

## Migration Plan

1. 先上 D2、D3：本机页面立即可用。
2. 用户创建 named tunnel 后切换 `FEISHU_APPROVE_BASE`。
3. 用一个演练周期（dry-run）走通手机审批。
4. 停用旧 GET 恢复。

回滚：保留终端审批；卡片退回只提醒。
