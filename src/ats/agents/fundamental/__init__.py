"""基本面分析师（Phase D）——例行与事件双模式。

包结构与职责（agent/fundamental-pead 能力）：
- `entry`   双模式触发契约与显式入口（例行/事件互不混淆）；
- `routine` 例行模式：消费 InformationBrief，把新信息分为确认/否定/新增/待验证，
  产出 `FundamentalExpectationUpdate` 投影；
- `event`   事件模式：cutoff 冻结基线、三类差异分列、产出 `FundamentalEventReview`
  投影并保留版本。

两种模式都只产出非可执行判断：方向、幅度、信心、理由与可证伪条件。
数量、动作与风控结论属于风控主管与主理人（审批链），不在此包内产生。
"""
