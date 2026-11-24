# Proposal

## Why

财报以外的重大事件，现有两条判断主线都接不住：基本面只围绕财报和预期，层次分析只验证预先定义的经营命题。

2026-11-09 至 11-12 的黄仁勋事件就是这样：
- NVDA 的基本面只记了几条"新增叙事"，没有回答它对论点和预期意味着什么；
- L5 层分析和板块配置一个字都没提，L5 仍是"超配"；
- 出口管制可能收紧、同行连带下跌这类**整层**冲击，没有任何一个分析岗负责；
- 层次分析每周六才跑，时效也跟不上。

现在要补，是因为同类事件（关键人、监管、出口管制）在 AI 硬件链上反复出现，而 Chief 每天的决策只能读到零散的叙事条目。

## What Changes

- **信息简报拆事件成分并评级**：每件事拆成若干成分，每个成分标注以下几项。评级由信息分析师的模型给出，代码校验范围与引用：
  - 影响面：本公司 / 本层 / 跨层，及涉及的层和成员；
  - 重要性：轻微 / 中等 / 重大 / 极重大；
  - 直接还是连带、持续还是了结；
  - 新颖度：首发由代码判定，升级由模型判定。
  - 评级在简报的事实汇总好之后单独做一次：每个简报 1 次 flash 请求，不改动逐篇抽取（不让抽取缓存失效）；由默认关闭的开关控制。
  - observe 名单中属于产业链层的 9 家成员出轻量简报，只出事件成分。
- **基本面新增重大事件评审**：
  - 新角色 `fundamental_material_review`，与财报口径的基本面**叠加**，不参与"例行 / 财报事件"二选一；
  - 新增按标的维护的**公司风险项**；
  - 只评估公司特有的部分。
- **层次分析新增三节**：
  - 风险命题 `kind: risk`：只用于整层或跨层风险，证据按独立来源计数，≥2 个独立来源且至少 1 个一手或权威；
  - 论点外冲击；
  - 补认领：安全网，本层成员的"重大"级成分都可见。
  - 另外给出下调建议；冲击与评审的有效期为 14 天，有新进展时续期。
- **板块配置只能下调**：
  - 风险命题承压或兑现、或出现"重大、负面且持续"的冲击时，可以下调层的使用率；
  - 临时复盘单次最多下调一档，周六例行复盘不限；
  - 天花板与风控静态上限不变。
- **事件触发**：
  - 新增事件类型 `material_development`，只有"重大"及以上且首发或升级的成分才触发：公司成分触发重大事件评审，层面成分触发相关层的 `layer-review` 和 `sector-review`；
  - 同一事件簇、同一下游每天最多触发一次，触发记入台账；
  - "中等"成分进入例行运行。
- **Chief**：
  - 不临时启动，每天例行；
  - 决策档案把重大事件评审登记为可选类别；
  - 渲染时按事件簇对齐各方评估，提示词写明这是一件事的几个部分，不要重复计算。
- **评级验证**：
  - 上线门槛：40 个成分的标注基准集，由用户标注，相邻档以内一致 ≥ 85%，"重大"及以上零漏判；
  - 上线后：发布契约校验、下游复核分歧、每周按重要性对照事后超额收益（纯代码）、每周抽检。
- **文档同步**：README、ARCHITECTURE、OPERATIONS、`docs/agents/` 四份角色文档与 Chief skill 说明、`docs/data/`、`ai_hardware.yaml` 中 L5 出口管制段的注释。
- 所有投影的新字段在 v1 中按可省略添加，旧投影仍能通过校验，存量不重跑。

## Capabilities

### New Capabilities

- `scheduling/material-event-routing`：由简报事件成分驱动的触发、路由、去重与触发台账。

### Modified Capabilities

- `agent/information-analyst`：简报必须包含事件成分及评级，评级受发布契约约束，并有基准集与事后校验；observe 产业链成员出轻量简报。
- `agent/fundamental-pead`：新增公司风险项与重大事件评审模式（叠加角色），只评估公司特有部分。
- `agent/layer-analyst`：新增风险命题、论点外冲击、补认领与下调建议，冲击 14 天过期。
- `agent/sector-allocation`：风险与冲击只能下调层使用率，临时复盘单次最多一档，下调须引用原因。
- `agent/task-projection`：新增 `FundamentalMaterialReview` 投影，简报、层分析、板块配置新增字段，均为 v1 可省略。
- `decision/research-snapshot`：重大事件评审为可选类别，冻结与校验规则同其他类别；按事件簇对齐渲染。
- `sector/chain-layers`：新增 `kind: risk` 命题及其按独立来源计数的证据规则，经营命题门槛不变。

## Impact

- **依赖：** `article-witness-observations` 必须先完成，整层风险命题依赖其恢复的第三方文章证人。
- **代码：**
  - `src/ats/agents/information/`（抽取、发布契约）
  - `src/ats/agents/fundamental/`（风险项、重大事件评审）
  - `src/ats/agents/layer/layer_review.py`
  - `src/ats/agents/sector/`
  - `src/ats/agent/task_projection.py`
  - `src/ats/workflow/`（`planning.py` 新任务与角色、`decision_requirements.py`、事件路由与台账）
  - `src/ats/agents/chief/assemble.py`
  - `src/ats/runtime/dispatcher_daemon.py`（事件消费）
- **配置：**
  - `config/workflow/event_routes.yaml`、`decision_profiles.yaml`
  - `config/sectors/ai_hardware.yaml`：L5 新增出口准入风险命题，其他层按需；注释更新
  - `config/workflow/event_impact.yaml`：事件成分与路由开关（轻量简报名单由 `pead.yaml` 的 observe 与产业链配置推导，不另设名单）
- **技能 / 提示词：** 信息、基本面、层次、板块、Chief 的 skill。
- **成本：**
  - 简报成分：开关打开后每个简报 +1 次 flash 请求（约 27 次/天，含轻量简报）；
  - 轻量简报（9 家）每天约 +32 次逐篇抽取与 +9 次评级，首次运行补抽 7 天窗口约 226 次（2026-11-13 实测更正，用户确认）；
  - 公司风险项初始化一次性约 18 次；
  - 每次触发：重大事件评审 1–2 次 + 每个受影响层 1 次 + 板块复盘 1 次；
  - 回放验收与基准集评测一次性，执行前报数。
- **不变：** Chief 不临时启动；交易门槛、风控限额、`weight_cap` 不变；模型不改配置文件；宏观不进层次分析。
