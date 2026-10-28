# Anthropic Economic Index Platform 发布记录

发布日期：2026-09-06

## 发布对象

- Source：`anthropic_economic_index`
- Dataset：`ai_work_adoption`
- Release：`release_2026_06_26`
- Upstream commit：`2ea58ff75e4247d26810c37f10c179edc2466cac`
- 月度期间：`2026-04`、`2026-05`
- 开放范围：受治理的研究查询与 L1 Evidence Observer
- 明确不开放：PEAD、Chain、Macro、Sector、Chief 和交易决策 workflow 的默认依赖或证据权重

## 平台入库

正式平台库的 ingestion run 为 `63d02d4f0d76595e0f4b27dc`：

| 项目 | 结果 |
|---|---:|
| Accepted observations | 153,218 |
| Quarantined observations | 0 |
| Created relations | 22,077 |
| Quarantined relations | 0 |
| Unchanged reference inputs | 6 |

月度大文件使用已校验的本地传输副本，metadata、taxonomy、source version 和最终 lineage 仍固定到官方 Hugging Face commit。首次受限环境执行产生一次 `parse_failed: Operation not permitted` 运行记录；允许公开 metadata 网络访问后重跑成功。失败运行没有发布 observation。

## Release preflight

正式平台 repository 上的 source preflight 返回 `ready=true`：

- source registration：通过
- repository registration：通过
- latest ingestion：`succeeded`
- `ai_work_adoption` quality：`passed`

随后执行 source publish，写入 `var/structured_data/releases.yaml`：

```text
source = anthropic_economic_index
previous_mode = ""
mode = platform
action = publish
actor = cli
at = 2026-09-06T07:14:04.212035+00:00
```

发布后 `source_mode("anthropic_economic_index")` 返回 `platform`。

## L1 Consumer 验收

通过正式平台 DataProducts 调用：

```text
observe_work_adoption(
    source_product="1p_api",
    period="2026-05",
    occupation="15-2031.00",
    top_n=3,
)
```

结果：

- status：`ok`
- consumer：`evidence_observer`
- source access：`data_products_only`
- job profile：`ok`
- snapshot manifest：`f0a877c10a72b846ace0ddb0`
- 语义 guardrails 包含 Usage Share、SOC major group、Observed Exposure、允许陈述和禁止陈述边界

现有静态 consumer-boundary 测试继续约束其他 workflow 不直接导入或读取该数据产品。

## 后续更新验收

Anthropic 下一次公开 release 出现后，再执行真实 release discovery 的首次更新验收，重点核对新 commit、period、schema drift、taxonomy coverage 和网络异常状态。该事项不是本次首版发布的完成条件。

关闭 source 的 platform exposure 只改变运行路由，不会删除既有 artifacts、observations、relations、run history 或 snapshot manifests；本次不执行额外 rollback drill。
