# Proposal

## Why

TWS 每天由自身的自动重启机制在台北 07:30 重启。2026-11-02 23:45 那次重启卡在启动器：更新检查失败，停在登录界面，直到 11-03 08:34 人工登录才恢复。这段时间 clerk-daily 失败，盘中行情与执行报价全部不可用。现在没有任何机制能发现“TWS 停在登录界面”并自动恢复。实盘审批链依赖 TWS 可连，这类无人值守期间的中断会直接让当天的决策或执行落空。（来源：switch-trade-path-and-retire-r5 tasks 11.2）

## What Changes

- 用 IBC 托管 TWS 的启动与登录。实盘账号走 7496 端口，2FA 仍由手机确认。
- 新增守护检查，定期探测 7496 端口和 API 握手：
  - 端口不可达或握手失败时，按保守策略恢复：先等待 API 初始化；仍不可用再经 IBC 重启；重启有次数上限。
  - 每次恢复动作与结果都写日志，并经现有告警通道发出。
- 恢复期间依赖 TWS 的工作流（clerk-daily、chief-daily、ibkr_news 等）保持现有的 fail-closed 行为，本 change 不改变它们的语义。
- IBC 的下载安装和实盘账号凭据由用户自己完成，凭据不进入仓库、日志或告警内容。

## Capabilities

### New Capabilities

- `execution/broker-session-recovery`：券商会话（TWS）可用性探测、无人值守恢复与告警。

### Modified Capabilities

（无）

## Impact

- **新增脚本：** 参照 `option_quant_trade_system/scripts/start_tws.sh`、`ensure_tws.sh` 编写本仓自己的版本。
- **新增 launchd 任务：** `com.ats.tws-guard`。
- **配置与文档：** `docs/OPERATIONS.md` 补“TWS 托管与恢复”一节。
- **外部依赖：** IBC，由用户安装并填写 `~/IBC/config-live.ini`。
- **不在范围：** paper 账户切换；TWS 版本升级策略。
