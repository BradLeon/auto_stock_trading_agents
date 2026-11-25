# Tasks

> 前置：第 1、2 组需要 `ant-thesis-monitor` 的投影；第 4、5 组只依赖 `ant-data-onboarding` 5.2 核对过的解禁日期，可以先做，并且**必须在 2026-12-16 前上线**。验收结果写入本 change 的 `verification.md`。

## 1. 快照与主理人上下文

- [ ] 1.1 在 `decision_profiles.yaml` 的 `optional_categories` 加 `thesis_monitor`，有效期 8 天；快照组装时冻结该类别（规格 `decision/research-snapshot`）。验证：测试覆盖"过期不进快照且不阻断""有效时可见"；已冻结周期的计划哈希不变。
- [ ] 1.2 调整 `skills/chief/SKILL.md`（D7）。验证：用合成快照让主理人跑一次（先报告请求数），持仓复核引用监测，ANT 交易的 `invalidation` 引用 E 编号。

## 2. 退出信号表态关口

- [ ] 2.1 决策 schema 新增可选字段 `signal_responses`（D2）。验证：schema 测试；旧决策无需改动即通过校验。
- [ ] 2.2 在主理人图中 `risk_gate` 之前加入表态关口节点（D1，规格 `decision/thesis-signal-response`）。验证：测试覆盖"缺表态被退回且不进风控""有理由通过""非复核点不要求表态""退回计入修订上限"。
- [ ] 2.3 用合成的 E1 信号回放一次主理人完整循环（不下单）。验证：主理人提出提案或写明理由；人为删掉表态的修订被退回；全程没有未经批准的订单。记录写入 `verification.md`。

## 3. 仓位档位

- [ ] 3.1 `config/thesis/ANT.yaml` 增加 `tiers`，加载时校验不超过层静态上限。验证：测试"超限配置加载失败"。
- [ ] 3.2 与用户确认三档权重后写入。验证：权重写入 `verification.md`。
- [ ] 3.3 skill 中规定因监测信号的增减按档位一次只跨一档。验证：合成升档信号回放，主理人提出的目标权重等于下一档。

## 4. 解禁窗口规则

- [ ] 4.1 `risk.yaml` 新增 `event_windows`，`review_revision` 加入 `lockup_window` 规则（D4，规格 `risk/deterministic-review`）。验证：测试覆盖"窗口内买单被驳回""窗口内卖单不受影响""通过期权增加敞口也被驳回"。
- [ ] 4.2 写入用 424B4 核对过的 ANT 解禁日期与窗口。验证：规则集版本变化；风险日报显示下一个窗口。

## 5. 代理 beta 与代理簇

- [ ] 5.1 `risk/inputs.beta` 与 `risk/correlation` 支持 `instruments.<SYM>.proxy`，并在历史足够后自动切换（D5，规格 `agent/risk-officer-inputs`）。验证：测试覆盖"历史不足用代理并标注""达到长度后切换""未声明代理保持现有处理"。
- [ ] 5.2 给出代理值的建议与依据，由用户确认后写入。验证：风险日报中 ANT 归入 AI 簇并标注代理值；结果写入 `verification.md`。

## 6. 交易日志

- [ ] 6.1 交易日志记录信号依据（规则编号、监测投影标识、关键读数）。验证：测试"降档交易落库后包含规则编号与读数"。
- [ ] 6.2 更新 `docs/ARCHITECTURE.md` 第 10 节（Chief—Risk—Boss—Trader 审批链，补充表态关口）与 `risk.yaml` 注释。验证：文档与实现一致，由用户确认。

## 7. 集成核对

- [ ] 7.1 阶段关口：在后台跑决策链与风控相关的回归测试。验证：与 `docs/TEST_BASELINE.md` 对比，新增失败为 0。
- [ ] 7.2 上线后观察 12/16 解禁窗口：窗口内没有 ANT 加仓提案通过风控。验证：当期的审查记录写入 `verification.md`。
