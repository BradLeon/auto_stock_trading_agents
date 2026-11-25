# Spec Delta

## ADDED Requirements

### Requirement: 持仓论点监测是可选类别
决策档案 SHALL 把 `thesis_monitor` 登记为可选类别：没有有效投影时 SHALL NOT 视为缺口；有有效投影时 SHALL 冻结进研究快照，并与其他类别一样做血缘与新鲜度校验。投影的有效期 SHALL 为发布后 8 天，覆盖两次周六运行之间的间隔。监测投影 SHALL NOT 改变六类必需分析的齐备判定。

#### Scenario: 监测投影过期
- **WHEN** ANT 最近一份监测投影发布已超过 8 天
- **THEN** 该投影 SHALL 不进入快照，并在快照明细中注明"过期"
- **AND** 周期 SHALL NOT 因此被阻断

#### Scenario: 有效监测投影
- **WHEN** ANT 有一份 3 天前发布的监测投影
- **THEN** 它 SHALL 冻结进快照，主理人上下文中 SHALL 可见其规则结果与复核点标记
