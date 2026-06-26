## Why

NeoFlow 原有模板配置更偏向“字段已经确定后再维护配置”，复杂稠密版式文档和固定 Excel 填报场景需要管理员反复人工定位字段、维护映射，成本高且容易出错。

当前需求是把现有 OpenAI Agents SDK 模板生成能力升级为可审查的“文档应用构建器”：先解析待识别图片/文档生成字段草案，再让管理员结构化确认字段；如果存在空 Excel 输出模板，则直接使用 `{{field_key}}` 占位符作为填充槽位，避免维护单独的单元格映射。

## What Changes

- 新增管理员侧 AI 模板构建入口，支持上传待识别图片/文档并生成字段草案。
- 支持可选上传空 Excel 输出模板，扫描 `{{field_key}}` 占位符，并把占位符补入字段草案。
- 管理员在字段草案页修改字段名称、`field_key`、类型、抽取要求、复核规则和 few-shot 示例后，再提交生成模板配置。
- 模板发布时保存 prompt、字段配置、示例、Excel 模板引用和占位符列表。
- 文档处理完成后，若模板配置固定 Excel 输出，系统用抽取结果填充空 Excel 模板并作为飞书附件推送。
- 保留既有多维表格推送路径；固定 Excel 输出是新增输出能力，不替代原有字段入库和审核流程。

## Capabilities

### New Capabilities

- `ai-template-builder`: 管理员通过 AI 辅助流程从样本文档生成、审查并发布文档抽取模板。
- `fixed-excel-template-output`: 系统扫描空 Excel 模板中的 `{{field_key}}` 占位符，并在文档处理结果输出时填充生成固定版式 Excel 文件。

### Modified Capabilities

- None.

## Impact

- Backend: 新增 `/api/sdk/*` AI 模板构建会话接口；模板服务需要创建模板基本信息并保存 prompt、cleaner 和 Excel 元数据。
- Backend: 文档飞书推送链路需要在推送附件前生成固定 Excel 输出文件。
- Frontend: 管理后台新增 AI 生成模板 tab，支持待识别文件和可选空 Excel 模板上传、字段草案编辑、prompt/code 预览和发布。
- Database: `document_templates` 增加输出模式和 Excel 模板元数据字段。
- Dependencies: 新增 OpenAI Agents SDK 运行依赖和 `openpyxl`，用于 Agent 编排和 Excel workbook 保样式读写。
