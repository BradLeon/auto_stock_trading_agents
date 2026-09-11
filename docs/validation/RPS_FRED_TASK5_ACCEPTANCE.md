# RPS/FRED 工作用途 GenAI 数据验收（OpenSpec Tasks 5）
验收日期：2026-09-09。使用无需 API key 的 FRED 官方 CSV 与公开 chart metadata API，隔离数据库和 artifacts 位于 `/private/tmp`。

## 准入口径

仅准入以下五条工作序列：工作采用、上周工作使用、每日工作使用、AI 辅助工时占比、自报节省工时占比。每条序列固定并校验 Quarterly、Not Seasonally Adjusted、Percent、官方 notes、来源及美国 18–64 岁 employed adults 分母。个人/教育/全部用途序列不作为回退。

## 官方回填结果

| 项目 | 结果 |
|---|---:|
| 独立 series artifacts | 5 |
| 历史 observations | 38 |
| 最早期间 | 2024-Q3 |
| 最新期间 | 2026-Q2 |
| 最新期五序列对齐 | 是 |
| quarantined | 0 |

最新期原始值：

| 指标 | 2026-Q2 |
|---|---:|
| 工作采用率 | 45.1993% |
| 上周工作使用率 | 39.2337% |
| 每日工作使用率 | 13.6934% |
| AI 辅助工时占比 | 6.2669% |
| 自报节省工时占比 | 2.1673% |

透明派生：

- `weekly_persistence_proxy = 39.2337 / 45.1993 = 0.8680`。
- `daily_persistence_proxy = 13.6934 / 45.1993 = 0.3030`。
- 两者都是总体比例之比，不是 cohort retention，也不能证明企业正式部署。
- 最新季度工作采用率环比 +1.7868 个百分点，同比 +10.1199 个百分点。

重复官方 discovery 返回 `no_change`；无需 `FRED_API_KEY`。测试另验证单 series 失败可隔离、内容相同不重复写入、单 series 修订追加 vintage 且 `as_of` 可重放旧值、逻辑顺序异常阻止 persistence 派生。

专项测试结果：21 passed。
