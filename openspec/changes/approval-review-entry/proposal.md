# Proposal

## Why

首席决策进入待审批后，审批人目前看不到请求内容，也无法在手机上完成批准：

- 飞书群机器人推送的只有提醒。它是单向的自定义机器人，不能接收原生按钮回调。
- 卡片上的 URL 按钮依赖公网隧道。在“墙内 + Clash”环境下，cloudflared quick tunnel 长期不稳。
- 签名链接只绑定“周期 + 裁决”，不绑定所批准的提案修订版本，与“人工审批只能作用于被风控批准的同一版提案”的要求不一致。
- 终端里的 `ats approval show/approve` 和 `ats approval export` 是目前仅有的可用入口，审批人必须坐在 Mac 前。

（来源：switch-trade-path-and-retire-r5 tasks 11.11）

## What Changes

- **飞书卡片带完整请求摘要：** 每笔订单的标的、方向、数量、限价、金额；风控结论；首席理由摘要；修订 hash 前缀；过期时间。审批人在手机上就能看清要批的是什么。
- **新增审批页：** 由 `ats serve` 提供只读详情页，显示完整请求、研究快照引用和风控明细。
  - 打开页面不产生任何动作。批准或拒绝必须在页面上二次确认，以 POST 提交，避免链接预取误触发。
- **签名绑定修订版本：** 签名链接绑定周期 id、修订 hash、裁决和过期时间。修订变化或过期后旧链接失效，页面提示重新审批。
- **公网入口改为固定域名的 named tunnel：** 用 launchd 托管，替代 quick tunnel；页面访问需带签名，不暴露其他接口。隧道不可用时，本机浏览器访问 `127.0.0.1:8000` 的同一页面仍可完成审批（与现有兜底一致）。
- **不在范围：** 用户对首席决策内容本身的不认可。需要用户说明具体问题后另行处理。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `decision/approval-lifecycle`：新增“审批入口必须展示完整请求内容”与“审批链接必须绑定修订版本且需页面内确认”两条要求。

## Impact

- **代码：**
  - `src/ats/channel/feishu_bot.py`：卡片内容与签名；
  - `src/ats/runtime/server.py`：审批详情页与确认提交；
  - `ats approval` 命令共用同一份请求摘要。
- **运维：** named tunnel 需要用户的 Cloudflare 账号与一个域名，由用户完成授权；launchd 新增 `com.ats.tunnel`。
- **安全：** 签名密钥沿用 `FEISHU_APPROVE_SECRET`；链接有过期时间；隧道只转发审批相关路径。
