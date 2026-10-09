# Phase F Trader 方案 A 与隔离下单验收修订

日期：2026-10-08。结论：**7.14、7.15、7.12、7.16 完成；52/116 完成，64 项待办**。按 7.14 → 7.15 → 7.12 → 7.16 闭合前置。使用 uv 项目环境，测试行情、人工审批通道与券商传输均为本地模拟；未连接真实 TWS、提交 IBKR Paper/live 订单、登记生产资格或修改生产路由。

## 已实现的目标

Trader/Risk 同步升级为 `target-dataflow-v2`，Trader 的产品为批准授权及受限 MARKET_DATA。
清单同步 allowed_consumers、两条 runtime 边、用途/USD 价格政策及资格证明类型；契约守卫
拒绝旧版本、缺行情证据和错用途/币种。新资格必须有 runtime_query、timestamp、
failure_semantics、no_persistence，只有批准授权或内部状态证明仍不合格。旧版本证据保留，
不能自动迁移为当前资格。其他角色和 SEC optional 政策保持原范围。

Risk/Trader 经 consumer_api 调用同一 execution_prices 服务。查询绑定 cycle、订单标的、
当次业务 scope、用途与币种，执行读取须逐字段匹配完整已批准修订。生产 exact-scope 门禁
继续执行；未开放的生产 legacy 范围不能借隔离候选或 native API 获得 target 数据。
Trader 不因此获得任意标的、研究历史或期权查询权限，Risk 不再反向调用 Trader 私有取价。

价格证据区分 source_as_of 与 queried_at。日间使用带服务端时点的 IBKR BidAsk tick，
仅接受 30 秒内实时、常规时段、价差不超过中点 1% 的正有限 USD 报价；券商确认 minSize、
sizeIncrement、minTick，未知精度拒绝。隔夜可用上一完整交易日的原始 Close，明确标注
historical/session_date；XNYS 日历处理假期和提前收盘，历史 Close 不作为执行实时价。
实际只读适配器使用模拟 IB 会话验证服务端时间，真实数据订阅能力由 7.13 后续核验。

实际 Chief risk_gate 在 Risk 前完成金额换股数、限价、订单类型和精度规范化，冻结完整
revision/hash 后再审查、审批；Risk 经自己的读取门禁消费同一捕获报价。买入以最坏批准
价格向下取股数，实际金额可以低于预算，不能为凑满预算超限。缺价、零股、错币种或无组合
快照明确进入拒绝/人工复核，不伪造正式审查的行情时点。审批卡保存报价引用、来源时点与原因。
同标的多笔订单按各自报价引用和完整订单绑定，不使用 symbol 缓存替代真实数量。

执行前及逐笔交接前重新校验当前报价、批准偏离上限、限价和资金上限，分别保存审批/执行
报价审计。不静默修改 quantity/limit/type/TIF/direction/hash。失效或超约束返回新 Risk/Boss；
常规时段到来或报价过期时，重新规范化只发生在新审查之前，新修订不能复用旧 Boss 批准。
未知/已提交回执不重复提交；后来一笔交接失效不会抹除前一笔接收，部分执行进入人工对账。

## 用户裁决落地与后续验收

完整路径重定向、券商进程禁写和实际 SQLite 隔离均成立时，允许显式选择无网络 FakeBroker。
实际 Trader 在完整风险审查、人工批准、账户/grant/代次/冻结及幂等门禁后产生非空接收回执；
成交只能由该接收产生，支持部分/迟到模拟回报。退出模拟上下文时撤销测试 grant，保存的
FakeBroker 实例不能在上下文外复用。真实 IBKR 传输仍受禁写保护，生产 C3 保持停用。

已同步子 change proposal/design/spec/tasks、父 change 与架构/API 文档。
7.5 后续必须从实际完整研究/Chief/Risk/审批/Trader/Clerk 入口运行，禁止预写 submitted/filled
冒充链路。11.2 必须覆盖真实 broker 误选及退出隔离复用。IBKR Paper 写入需另行明确授权。

本轮 Chief 测试使用 `decide=False` 种子指令和模拟组合，不代表六分析角色/实际研究快照已齐备；
也不代表完整 Clerk 协议、恢复、绩效重建或十角色动态接入已验收。

## 验证证据

- 业务核心回归 **163 passed**：报价、规范化、审查/审批、授权、Trader、隔夜及风险，见
  [final-business-v2.junit.xml](phase_f_trader_a_20261008/final-business-v2.junit.xml)。
- 控制面回归 **520 passed**：资格/指纹、实际接线、路由/禁写、隔离、调度、Clerk 等，见
  [control-regression.junit.xml](phase_f_trader_a_20261008/control-regression.junit.xml)。
- 审批与服务入口回归 **30 passed**：E2E 审批、修改拒绝、server/Feishu，见
  [entry-regression.junit.xml](phase_f_trader_a_20261008/entry-regression.junit.xml)。
- 最后补充隔夜到常规时段更新、交接失效和 grant 退出检查，价格场景及审批链重查分别见
  **61 passed / 56 passed**：
  [final-price.junit.xml](phase_f_trader_a_20261008/final-price.junit.xml) 和
  [final-approval.junit.xml](phase_f_trader_a_20261008/final-approval.junit.xml)。这些与上述回归有重叠，不累加为独立场景。
- 文档记录守卫、两 change strict、依赖无环/前置闭合、源码摘要、生产只读 inventory 对照见
  [verification.json](phase_f_trader_a_20261008/verification.json)。新增模块/价格 harness 的 ruff 检查通过。

中间失败 JUnit 全部保留在同目录：发现并修复测试环境变量泄漏、缺 import、旧裸授权/预制回执、
按参考价凑满金额的旧期待，以及导入时固定组合时点导致扩大回归时过期。以上不是全部归为
“既有失败”。本轮已更新原三条 C3 Chief 测试为明确隔离模拟；原事实重处理失败未处理，
此前全量和有限 HEAD 模块对照保持原时点，本轮没有宣称全套测试全绿。

## 指纹影响、缺失与推进顺序

本轮 manifest、consumer_api 与契约守卫漂移影响**全部十角色**。新增价格/审计/模拟代码、
相关业务入口与 uv.lock 已纳入 Risk/Trader 指纹闭包，受保护面现为 **24 个路径**；before/after 摘要见 verification.json。
旧材料、验证记录、资格和接线包保留；局部合成场景不是可登记的最终生产资格。

**后续注意/待修复项**：先补 5.7 安全回退、7.1 完整业务隔离入口、7.11 剩余真实消费边界，
及 7.2 → 7.4 的六分析/所选 Event 或 Routine 完整性门禁，再执行 7.5 的非空订单→部分/迟到
成交→实际 Clerk 恢复/绩效链；随后 7.17 核对十角色影响与最小补验。6.1 冻结后由 6.4
追加新指纹证据，短 TTL 在切前刷新，可复用原始材料而不全量重采。真实 TWS 只读验证 7.13
与完整隔离授权出口验收 11.2 仍待完成。

**为何现在不修**：完整链路有独立尚未闭合的前置，不能用本轮种子订单/模拟组合把 7.5、7.17、
11.2 或生产资格提前勾选。用户允许的是隔离 FakeBroker 测试，未授权真实券商写入或生产切流。
