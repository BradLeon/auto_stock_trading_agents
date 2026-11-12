## REMOVED Requirements

### Requirement: Phase F 前置接收必须以版本化矩阵进行

**Reason**: 前置接收矩阵服务于逐项、逐批接收 A–E 专项后再按批次切流的流程，该流程已被 2026-10-15 裁决（研究读取按消费者整体切换，前置为消费者层面的数据产品健康检查）取代；研究侧切换已完成，对应的接入核验与接收工具随本 change 退役。历史接收记录作为审计材料保留。

**Migration**: 新路径是否达标由退役前置门（`workflow/legacy-retirement` 的「退役执行必须以新路径人工核验通过为前置」）逐项核验；日常运行由数据产品在发布时与读取前的检查保证（`workflow/cutover-control`）。

### Requirement: 前置门禁必须交付逐批 eligible 与 ineligible 报告

**Reason**: 逐批 eligible / ineligible 报告是批次切流的放行依据，批次切流已被取代、研究侧切换已完成，批次清单与报告工具随本 change 退役。交易路径的部署与实盘授权要求不依赖本条，由 `execution/live-route-switch` 与 `execution/authorization-gate` 约束。

**Migration**: 研究侧不再需要放行报告；交易路径切换时，按 `execution/live-route-switch`（「真实实盘切换必须由外部明确授权解锁」等）与 `workflow/cutover-control`（「联合边界切换必须显式且不可暴露半迁移状态」「切流门禁不变量必须由机器校验」）执行。
