# 上线 Checklist（Paper Trading）

逐步把系统跑通到「定时自动分析 → 飞书手机审批 → IBKR 模拟盘下单」。每一步都带**验证命令**，验证通过再进入下一步。

> 全程默认 **paper（模拟盘）**。先 `dry-run`（不下单）跑顺，再 `--live`（IBKR paper 真实挂单）。

---

## 0. 前置准备

- [ ] Python 3.11+，已装 [uv](https://docs.astral.sh/uv/)
- [ ] 克隆仓库并建虚拟环境 + 装依赖：

```bash
cd auto_stock_trading_agents
uv venv --python 3.11 .venv
uv pip install --python .venv -e ".[data,broker,memory_persist,channel,schedule,dev]"
# 若本机走 SOCKS 代理（出现 "Using SOCKS proxy" 报错时）：
uv pip install --python .venv socksio
```

- [ ] 拷贝环境文件：`cp .env.example .env`
- [ ] 验证：`PYTHONPATH=src .venv/bin/python -m pytest -q` → **全绿**

> 下文命令统一用 `PYTHONPATH=src .venv/bin/python -m ats.runtime.cli <cmd>`。可在 `.venv` 激活后直接用 `ats <cmd>`。

---

## 1. 配置密钥（`.env`，已 gitignore，切勿提交）

> 安全：不要把 key 贴进聊天/代码。直接 `! echo 'KEY=value' >> .env` 写入。明文暴露过的 key 立即轮换。

- [ ] **LLM（必填）** — OpenRouter 中转，默认模型 `anthropic/claude-opus-4.8`
  ```
  OPENAI_API_KEY=sk-or-v1-...           # OpenRouter key
  OPENAI_BASE_URL=https://openrouter.ai/api/v1
  ```
- [ ] **FRED（宏观，可选但推荐）** — 免费申请 https://fred.stlouisfed.org/docs/api/api_key.html
  ```
  FRED_API_KEY=...
  ```
- [ ] **IBKR** — 见第 2 步；先确认默认值（paper 端口 7497）
  ```
  IBKR_HOST=127.0.0.1
  IBKR_PORT=7497
  IBKR_CLIENT_ID=11
  ```
- [ ] **飞书** — 见第 4 步
- [ ] 验证 LLM 通：
  ```bash
  PYTHONPATH=src .venv/bin/python -c "from ats.llm import get_model; print(get_model('manager').invoke('say PONG').content)"
  ```
  → 输出 `PONG`（若 400 余额不足 → OpenRouter 充值）

---

## 2. IBKR TWS（模拟盘）

- [ ] 安装并登录 **TWS**（或 IB Gateway），用 **Paper Trading** 账户登录
- [ ] 开启 API：`File ▸ Global Configuration ▸ API ▸ Settings`
  - [ ] 勾选 **Enable ActiveX and Socket Clients**
  - [ ] **Socket port** = `7497`（paper）
  - [ ] **Trusted IPs** 加入 `127.0.0.1`
  - [ ] 建议勾选 **Read-Only API** 关闭（否则无法下单）
- [ ] 验证连通：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli ibkr
  ```
  → `✅ Connected. account=... NetLiq=$...`（列出持仓）

> ⚠️ TWS **每 24 小时自动登出一次**。每天首次 `--live` 前先 `ats ibkr` 自检。

---

## 3. 第一次 dry-run（不下单，CLI 审批）

- [ ] 跑一轮，终端里审批：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli run
  ```
  - 看到分析师报告 + Manager 决策 + 风控调整
  - 在 `boss>` 提示符输入 `a` 批准 / `r` 驳回 / `report NVDA` 查历史
- [ ] 验证落库：
  ```bash
  PYTHONPATH=src .venv/bin/python -c "from ats.memory import get_store; print(get_store().last_performance())"
  ```
  → 有一条 performance 记录

> 单周期约 1–3 分钟（10 次 Opus 调用）属正常。

---

## 4. 飞书应用（异步手机审批）

- [ ] 在 [飞书开放平台](https://open.feishu.cn/) 创建 **企业自建应用**，拿到 **App ID / App Secret**
- [ ] **权限**：开通 `im:message`（发送消息）等，发布版本并通过审核
- [ ] **目标会话**：把机器人加入一个群，拿到该群的 **chat_id**
  （可用飞书 API `im/v1/chats` 查，或给机器人发消息从回调里取）
- [ ] **事件/卡片回调**：开启「事件订阅」或「卡片回调」，**请求网址**填第 6 步的公网地址 `https://<域名>/feishu/callback`，复制 **Verification Token**
- [ ] 写入 `.env`：
  ```
  FEISHU_APP_ID=cli_...
  FEISHU_APP_SECRET=...
  FEISHU_CHAT_ID=oc_...
  FEISHU_VERIFICATION_TOKEN=...
  # 飞书国际版 Lark 用 https://open.larksuite.com
  FEISHU_BASE_URL=https://open.feishu.cn
  ```
- [ ] 在 `config/settings.yaml` 设 `channel.kind: feishu`
- [ ] 验证推送：
  ```bash
  PYTHONPATH=src .venv/bin/python -c "from ats.channel import get_channel; from ats.schemas.channel import Notification; get_channel('feishu').push(Notification(kind='info', title='ats test', body='hello'))"
  ```
  → 群里收到一条消息

---

## 5. 公网隧道（让飞书能回调到本机）

飞书需要从公网 POST 到你的 webhook。本地开发用隧道：

- [ ] 安装 ngrok 或 cloudflared
- [ ] 起隧道指向 webhook 端口（第 6 步的 8000）：
  ```bash
  ngrok http 8000        # 或: cloudflared tunnel --url http://localhost:8000
  ```
- [ ] 把隧道域名 `https://xxxx.ngrok.app/feishu/callback` 回填到第 4 步的回调网址

> 生产环境请用固定域名 + HTTPS，并保留 `FEISHU_VERIFICATION_TOKEN` 校验。

---

## 6. 启动 webhook 服务

- [ ] 起 serve（**单独一个常驻进程**）：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli serve --port 8000
  ```
- [ ] 飞书后台保存回调网址时会发 `url_verification` 握手 → 看到日志返回 `challenge` 即通过
- [ ] 健康检查：`curl http://localhost:8000/health` → `{"ok":true}`

---

## 7. 第一次飞书审批 + 真实 paper 下单

> 确保第 2 步 `ats ibkr` 当天已自检通过、第 6 步 serve 在跑。

- [ ] 先 dry-run 走飞书（不下单，验证全链路）：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli run --channel feishu
  ```
  → 终端打印 `⏸ awaiting Boss approval via feishu`；手机收到审批卡片
- [ ] 手机点 **✅ Approve** → serve 日志出现 `resuming ... -> approved` → 卡片回 toast
- [ ] 确认无误后，真实 paper 挂单：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli run --live --channel feishu
  ```
- [ ] 在 TWS 里看到挂单/成交；`ats ibkr` 持仓更新；`report <SYM>` 能查到这笔交易

---

## 8. 定时调度（每个交易日自动）

- [ ] `config/settings.yaml` 设：
  ```yaml
  schedule:
    run_at: "16:15"           # 美东收盘后
    timezone: America/New_York
  channel:
    kind: feishu
  ```
- [ ] 先单次验证（非交易日会自动跳过）：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli schedule --now
  ```
- [ ] 起常驻调度（另一个进程，与 serve 并存）：
  ```bash
  PYTHONPATH=src .venv/bin/python -m ats.runtime.cli schedule --live
  ```
  → 每个交易日 16:15 自动分析 → 推飞书 → 你手机审批 → serve 执行

> 三个常驻进程：**TWS**（或 Gateway）、`ats serve`（审批回调）、`ats schedule`（定时）。建议用 `tmux`/`launchd`/`systemd` 守护。

---

## 9. 日常运维

- [ ] **每日**：开 TWS（24h 会登出）→ `ats ibkr` 自检
- [ ] **数据库**：`var/ats.sqlite`（历史/绩效）、`var/checkpoints.sqlite`（暂停态），均 gitignore，定期备份
- [ ] **监控**：关注 serve / schedule 日志里的 `WARNING`（数据源降级、IBKR 不可达等）
- [ ] **绩效**：
  ```bash
  PYTHONPATH=src .venv/bin/python -c "from ats.memory import get_store; [print(p.cycle_id, p.net_liquidation, p.daily_pnl) for p in get_store().performance_history()]"
  ```

---

## 10. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `IBKR unavailable ... 7497` | TWS 没开 / 没启用 API / 端口错 / 当天登出了 → 重开并 `ats ibkr` |
| LLM `400 credit balance too low` | OpenRouter 余额不足 → 充值 |
| `Using SOCKS proxy` 报错 | `uv pip install socksio` |
| 飞书收不到卡片 | App 权限未发布 / `FEISHU_CHAT_ID` 错 / 机器人没在群里 |
| 点了按钮没反应 | 隧道断了 / 回调网址错 / serve 没起 / `FEISHU_VERIFICATION_TOKEN` 不匹配 |
| 宏观全是 n/a | `FRED_API_KEY` 没填（其它字段不受影响） |
| 审批卡很久才出 | 单周期 10 次 Opus 调用，1–3 分钟正常 |

---

## 11. 安全红线

- [ ] `.env` 永不提交（已 gitignore，提交前 `git status` 确认）
- [ ] 对话/截图里暴露过的 key 一律轮换（Anthropic / OpenRouter / 飞书 Secret）
- [ ] 长期只用 **paper**；切实盘前务必：独立 live profile、收紧 `config/settings.yaml` 的风控阈值、人工复核每一笔
- [ ] `serve` 暴露公网时保留 Verification Token 校验，最好加 IP 白名单 / 反向代理鉴权
