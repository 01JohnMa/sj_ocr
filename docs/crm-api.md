# CRM 文档提交接口

本文档给 CRM 调用方使用，说明如何把已审核的文档提交到 NeoFlow 处理。

## 核心规则

- CRM 侧负责审核，NeoFlow 不再二次人工审核。
- 只要请求来自 CRM 专用入口，即使模板配置为需要审核，也会强制自动通过。
- 提交接口只返回排队结果，不同步返回最终识别结果。
- 最终识别结果用 `document_id` 查询。

## 调用前准备

CRM 调用账号需要满足：

- 请求头带 `Authorization: Bearer <token>`。
- 账号角色为 `tenant_admin` 或 `super_admin`。
- 账号已配置所属部门 `tenant_id`。
- `template_id` 必须是当前账号有权限访问的模板。

CRM 当前只使用质量管理中心下列模板：

| 文档类型 | template_id | 说明 |
| --- | --- | --- |
| 检测报告 | `b0000000-0000-0000-0000-000000000001` | 产品质量检测报告 |
| 抽样单 | `b0000000-0000-0000-0000-000000000003` | 市场监督抽样单 |

支持上传文件：

- 文件类型：`.pdf`, `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp`
- 文件大小：最大 `20MB`

## 1. 提交文档

```http
POST /api/crm/documents/submit
Content-Type: multipart/form-data
Authorization: Bearer <token>
```

### 表单参数

| 参数 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `file` | 是 | file | 要处理的 PDF 或图片文件 |
| `template_id` | 是 | string | 模板 ID，只传上表中的检测报告或抽样单 ID |
| `custom_push_name` | 否 | string | 自定义推送/展示名称，最长 100 字符 |

### curl 示例

```bash
curl -X POST "https://<neoflow-host>/api/crm/documents/submit" \
  -H "Authorization: Bearer <token>" \
  -F "template_id=<template_id>" \
  -F "custom_push_name=CRM单据号-20260626" \
  -F "file=@/path/to/report.pdf"
```

### 成功返回

HTTP 状态码：`202 Accepted`

```json
{
  "document_id": "11111111-1111-4111-8111-111111111111",
  "job_id": "22222222-2222-4222-8222-222222222222",
  "status": "queued",
  "message": "CRM文档已加入处理队列",
  "auto_approve_forced": true
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `document_id` | 文档 ID，后续查询结果使用 |
| `job_id` | 处理任务 ID，后续查询任务进度使用 |
| `status` | 当前为 `queued`，表示已入队 |
| `auto_approve_forced` | 固定为 `true`，表示本次走 CRM 强制自动通过逻辑 |

## 2. 查询任务进度

```http
GET /api/documents/jobs/{job_id}
Authorization: Bearer <token>
```

### 返回示例

```json
{
  "job_id": "22222222-2222-4222-8222-222222222222",
  "status": "processing",
  "stage": "ocr",
  "progress": 30,
  "document_ids": [
    "11111111-1111-4111-8111-111111111111"
  ],
  "error": null
}
```

常见状态：

| status | 说明 |
| --- | --- |
| `queued` | 已排队，等待 worker 处理 |
| `processing` | 正在 OCR、模型提取或保存结果 |
| `completed` | 处理完成，可以查询结果 |
| `failed` | 处理失败，查看 `error` |

## 3. 查询识别结果

```http
GET /api/documents/{document_id}/result
Authorization: Bearer <token>
```

### 结果未完成

HTTP 状态码：`202 Accepted`

```json
{
  "document_id": "11111111-1111-4111-8111-111111111111",
  "status": "processing",
  "message": "文档正在处理中，请稍后重试"
}
```

### 结果完成

HTTP 状态码：`200 OK`

```json
{
  "document_id": "11111111-1111-4111-8111-111111111111",
  "document_type": "检测报告",
  "extraction_data": {
    "sample_name": "样品名称",
    "inspection_date": "2026-06-26"
  },
  "ocr_text": "OCR文本前1000字符",
  "ocr_confidence": 0.98,
  "created_at": "2026-06-26T10:00:00",
  "is_validated": true,
  "review_hint_fields": [],
  "template_fields": []
}
```

CRM 通常只需要读取：

| 字段 | 说明 |
| --- | --- |
| `document_id` | 文档 ID |
| `document_type` | 识别出的文档类型 |
| `extraction_data` | 最终结构化结果 |
| `is_validated` | CRM 入口会自动通过，完成后通常为 `true` |

## 错误返回

统一错误格式：

```json
{
  "error": "错误说明",
  "code": "ERROR_CODE",
  "status_code": 400
}
```

常见错误：

| HTTP 状态码 | code | 场景 |
| --- | --- | --- |
| `401` | `AUTH_REQUIRED` | 未带 token 或 token 无法识别用户 |
| `403` | `FORBIDDEN` | 账号不是管理员，或无权使用该模板 |
| `404` | `HTTP_ERROR` | 模板不存在，或任务不存在 |
| `400` | `INVALID_FILE_TYPE` | 文件类型不支持 |
| `400` | `FILE_TOO_LARGE` | 文件超过 20MB |
| `422` | `HTTP_ERROR` | 文档处理失败，查询结果时返回失败原因 |
| `500` | `PROCESSING_FAILED` | 保存或提交处理失败 |

## 推荐调用流程

1. CRM 完成本侧审核。
2. 调用 `POST /api/crm/documents/submit` 上传文件和模板 ID。
3. 保存返回的 `document_id` 和 `job_id`。
4. 轮询 `GET /api/documents/jobs/{job_id}`，直到 `completed` 或 `failed`。
5. 如果任务完成，调用 `GET /api/documents/{document_id}/result` 获取 `extraction_data`。
