# Spec Delta

## Purpose

新上市发行人的注册类文件（S-1、S-1/A、424B4）作为准入的官方文档入库，既支持运维者提供的本地文件，也支持从 SEC 按发行人补采，并保留版本之间的取代关系。

## ADDED Requirements

### Requirement: 注册类文件可以作为官方文档入库
系统 SHALL 支持把 S-1、S-1/A 与 424B4 作为发行人的官方注册类文件入库，文档类型标为注册文件，并绑定发行人实体、表单类型、SEC 申报日期与 accession 号（若已知）。入库 SHALL 经过与其他官方文档相同的强制准入：身份、表单类型、正文完整性不合格的文件 SHALL 被隔离。注册类文件 SHALL NOT 被当作财报事件材料，SHALL NOT 触发财报事件路由。

#### Scenario: SEC 补采 424B4
- **WHEN** 运维者对 ANT 发起注册类文件补采，SEC 上存在 ANT 的 424B4
- **THEN** 系统 SHALL 下载该文件正文并以注册文件类型入库，绑定 ANT、表单类型 424B4 与 accession 号
- **AND** 系统 SHALL NOT 为它创建或标记任何财报事件

#### Scenario: 正文属于其他发行人
- **WHEN** 待入库的 S-1 正文封面上的注册人不是 Anthropic, PBC
- **THEN** 该文件 SHALL 被隔离并标记 identity mismatch

### Requirement: 运维者可以把本地文件作为注册类文件入库
系统 SHALL 提供一个运维入口，把本地已下载的注册类文件（HTML 或 SEC 完整提交文本）作为指定发行人的注册文件入库。入口 SHALL 要求指定发行人与表单类型，SHALL 从文件中解析并核对注册人名称与申报日期，SHALL 保存原始字节的哈希与来源标注"本地提供"。同一文件重复入库 SHALL 幂等。

#### Scenario: 用户提供的首版 S-1
- **WHEN** 运维者以发行人 ANT、表单 S-1 提交本地文件 `Anthropic, PBC - S-1 (Q2).html`
- **THEN** 系统 SHALL 解析出注册人 Anthropic, PBC 与申报日期 2026-10-26，并以注册文件入库
- **AND** 文档来源 SHALL 标注为本地提供，并保存原始字节哈希

#### Scenario: 重复提交同一文件
- **WHEN** 同一份文件被再次提交
- **THEN** 系统 SHALL 返回已有文档的标识，不产生新版本

### Requirement: 注册类文件的版本取代关系可查询
同一发行人有多份注册类文件时，系统 SHALL 按申报时间保存版本链，并能回答"截至某日最新的注册文件是哪一份"。后版文件 SHALL NOT 覆盖或删除前版。首版中留空的数值（发行股数、承诺金额等）SHALL 不被视为零或缺失数据，读取方 SHALL 能看到该数值在哪一版首次出现。

#### Scenario: 首版留空、424B4 给出数值
- **WHEN** 首版 S-1 中算力承诺总额为空白，424B4 中给出具体金额
- **THEN** 按"最新注册文件"读取 SHALL 返回 424B4
- **AND** 首版 S-1 SHALL 仍可按文档标识读取，且不被标记为错误
