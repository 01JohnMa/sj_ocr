## Context

NeoFlow 现有文档处理链路包含上传、OCR/VLM 抽取、结果保存、人工审核和飞书多维表格推送。管理员模板配置已支持字段、示例、提取模式、逐页提取和飞书目标表配置，但创建新模板仍需要人工设计字段和 prompt。

这次变化引入 OpenAI Agents SDK 作为管理员构建模板时的辅助能力，不改变普通用户文档处理入口。固定 Excel 模板不是待解析源，而是输出模板；字段值仍来自上传的待识别图片/文档。

## Goals / Non-Goals

**Goals:**

- 管理员能从待识别样本文档启动 AI 模板构建流程。
- AI 分析结果必须先成为可编辑字段草案，管理员确认后才写入模板配置。
- 空 Excel 输出模板通过 `{{field_key}}` 直接声明填充槽位，系统扫描后把缺失字段补进草案。
- 发布后的模板保留既有多维表格输出，同时可生成固定 Excel 文件并作为飞书附件推送。
- Excel 填充应保留 workbook 样式、合并单元格、公式和图片等原模板结构。

**Non-Goals:**

- 不新增公开的模板构建页面；入口仍在管理员后台。
- 不让普通用户直接访问 AI 模板构建流程。
- 不新增固定 Excel 输出的独立下载中心或历史文件管理表。
- 不用 Excel 模板反向解析字段值；Excel 只定义输出槽位。

## Decisions

### Decision 1: 复用管理员后台作为构建入口

管理员已有模板配置权限和租户上下文，AI 模板构建应作为 `/admin` 内的能力增强，而不是新建公开页面。

Alternatives considered:

- 独立页面：导航和权限边界更复杂，不符合当前“管理员配置”语义。
- 普通上传页内引导：会把模板创建能力暴露到普通处理流程，权限风险更高。

### Decision 2: Excel 占位符直接使用 `field_key`

空 Excel 模板中的 `{{field_key}}` 同时作为填充槽位和字段键名，不额外维护单元格映射表。系统扫描 workbook 文本单元格得到 `sheet_name`、`coordinate`、`field_key`、`raw_value`，用于展示和发布时持久化。

Alternatives considered:

- 维护字段到单元格映射表：更灵活，但管理员需要重复配置位置，违背“占位符免映射”的需求。
- 使用具体数字作为占位符：用户可读性差，且无法直接对应字段 schema。

### Decision 3: 字段草案先合并 Excel 槽位，再由管理员确认

AI 先基于待识别图片/文档生成字段建议；如果 Excel 槽位里有 AI 未识别出的 `field_key`，系统补成待确认字段。发布前仍要求管理员检查字段名称、类型、抽取要求和复核规则。

Alternatives considered:

- 扫描 Excel 后直接发布字段：会跳过字段语义、类型和提示词审查。
- 完全依赖对话补字段：对固定模板场景效率低，且容易遗漏占位符。

### Decision 4: 固定 Excel 输出接入现有飞书附件上传

文档处理结果仍按现有业务表保存，审核/自动通过后走既有 `push_to_feishu()`。当模板 `output_mode` 为 `excel_template` 或 `both` 且存在模板文件时，系统临时生成填值后的 xlsx，并把它作为附件上传到飞书记录。

Alternatives considered:

- 新增输出文件表和下载接口：适合后续文件归档，但这次会扩大数据库和前端范围。
- 替代多维表格字段推送：会破坏现有运营数据沉淀。

## Risks / Trade-offs

- Excel 模板文件丢失或路径不可访问 -> 推送流程记录 warning 并继续多维表格推送，避免阻断既有业务。
- 占位符字段没有抽取值 -> 填充为空并记录 warning；管理员可在审核字段时补值或调整模板。
- `openpyxl` 对复杂 Excel 对象的保真度受库能力限制 -> 仅通过 load/save 保留 workbook 结构，不在本阶段做高级渲染或 PDF 化。
- 临时生成文件不做长期留存 -> 当前满足飞书附件输出；后续如需下载/审计，应新增输出文件持久化能力。

## Migration Plan

1. 执行数据库迁移，为 `document_templates` 添加 `output_mode`、`excel_template_file_name`、`excel_template_path`、`excel_template_placeholders`。
2. 部署后端依赖和 `/api/sdk/*` 路由。
3. 部署前端管理员后台 AI 生成模板入口。
4. 对既有模板保持默认 `output_mode='bitable'`，不改变原有推送行为。
5. 回滚时可先隐藏前端入口并停用 `/api/sdk/*` 路由；已有模板的 Excel 元数据字段不影响旧流程。

## Open Questions

- 是否需要在下一阶段新增固定 Excel 输出文件的下载历史和保留周期配置？
- 是否需要支持一个模板关联多个 Excel 输出模板？
- 是否需要对 `field_key` 命名规则做更严格的前端校验和自动修复建议？
