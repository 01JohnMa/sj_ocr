# NeoFlow — OpenAI Agents SDK 集成计划 v2

## Context

在现有 OCR-LLM 文档处理平台基础上，集成 OpenAI Agents SDK。管理员上传一份实际文档（PDF/图片），系统 OCR 后分析文档内容，AI 推荐模板类型、所属部门、识别字段及默认配置。管理员预览确认后一键写入 Supabase。字段配置由模型推荐默认值，管理员后续可在「识别字段管理」中自行修改。

## 核心流程

```
管理员上传一份实际文档（PDF/图片）
        │
        ▼
┌─ Step 1: 文档 OCR ──────────────────────────────────┐
│  系统对文档执行 OCR（复用 ocr_service）                 │
│  → 得到 OCR 文本 + 页面数 + 置信度                     │
│  → 存储文件到 uploads/，session 记录 file_path         │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 2: AI 分析文档 ───────────────────────────────┐
│  Agent: doc_analyzer_agent（OpenAI SDK）              │
│  输入: OCR 文本 + 文件名                              │
│  分析结果:                                            │
│     - 文档类型推荐（检测报告/快递单/抽样单/...）        │
│     - 所属部门推荐（根据文档中的机构/公司名推断）        │
│     - 检测到的所有字段及其推荐默认值:                   │
│         * field_key（蛇形英文）                       │
│         * field_label（中文含义）                     │
│         * field_type（text/date/number）             │
│         * extraction_hint（从文档哪里提取）           │
│         * review_enforced（是否需要强制审核）          │
│         * review_allowed_values（如有固定选项）        │
│     - 建议的 few-shot 示例（从文档原文摘录）            │
│  管理员可修改所有推荐内容                              │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 3: 确认模板信息 ──────────────────────────────┐
│  模板名称、code、描述                                 │
│  确认部门（已有部门选择 / 新建部门）                    │
│  确认 extraction_mode（ocr_llm / vlm）               │
│  确认 per_page_extraction（是否逐页提取）              │
│  管理员确认                                           │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 4: Prompt 预览 ───────────────────────────────┐
│  Agent: prompt_agent                                │
│  基于确认的字段+示例，生成完整 extraction prompt        │
│  格式与现有 build_extraction_prompt 一致               │
│  管理员预览/修改                                      │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 5: 清洗代码预览（可选） ───────────────────────┐
│  Agent: code_agent                                  │
│  根据字段类型(hint)生成字段级清洗函数                   │
│  例: clean_sampling_date / clean_phone_number       │
└─────────────────────────────────────────────────────┘
        │
        ▼
┌─ Step 6: 一键写入 Supabase ─────────────────────────┐
│  1. 如有新部门 → tenant_service.create_tenant()       │
│  2. 写入 document_templates                          │
│  3. 逐字段 create_field → 自动触发 ADD COLUMN         │
│  4. 逐示例 create_example                            │
│  5. 代码写入 sdk/generated/{code}_cleaners.py        │
└─────────────────────────────────────────────────────┘
```

文件路径: `api/routes/sdk.py`

---

## 新增文件结构

```
sdk/
├── __init__.py
├── config.py                  # SDK 模型配置（DeepSeek）
├── session.py                 # 内存 Session（带 TTL）
├── models.py                  # Pydantic 模型
├── agents/
│   ├── __init__.py
│   ├── doc_analyzer_agent.py  # 文档分析 Agent（核心）
│   ├── prompt_agent.py        # Prompt 生成 Agent
│   ├── code_agent.py          # 清洗代码 Agent
│   └── orchestrator.py        # 主控编排器
├── generated/
│   └── __init__.py
api/routes/
└── sdk.py                     # 新增 API 路由
tests/
└── test_sdk/                  # 新增测试
supabase/migrations/
└── 015_add_prompt_and_cleaner.sql  # 新增迁移
```

## API 设计

```
POST   /api/sdk/sessions                       创建会话 + 上传文档 + 执行 OCR
GET    /api/sdk/sessions/{id}                  获取会话状态（含 OCR 文本 + 分析结果）
POST   /api/sdk/sessions/{id}/analyze          文档分析 → 推荐类型/部门/字段/示例
POST   /api/sdk/sessions/{id}/confirm-template 确认模板信息（含部门选择）
POST   /api/sdk/sessions/{id}/prompt           生成 Prompt 预览
POST   /api/sdk/sessions/{id}/code             生成清洗代码预览
POST   /api/sdk/sessions/{id}/commit           一键写入 Supabase
DELETE /api/sdk/sessions/{id}                  取消会话，清理文件
```

全部需要 `_require_admin(user)` 权限。

## Agent 设计

### doc_analyzer_agent（核心）

**输入**: OCR 文本 + 文件名 + 已有部门列表
**输出**:

```json
{
  "recommended_doc_type": "检测报告",
  "recommended_doc_code": "inspection_report",
  "confidence": 0.95,
  "recommended_tenant": {
    "suggest_name": "品质部",
    "suggest_code": "quality",
    "reason": "文档中出现'品质管理部'字样",
    "match_existing_tenant_id": null
  },
  "detected_fields": [
    {
      "field_key": "sample_name",
      "field_label": "样品名称",
      "field_type": "text",
      "extraction_hint": "通常位于报告顶部'样品名称'标签后",
      "review_enforced": false,
      "review_allowed_values": null,
      "sample_value": "小型断路器"  // 从文档中实际提取的值
    }
  ],
  "suggested_examples": [
    {
      "example_input": "样品名称：小型断路器\n规格型号：LB12-63a...",
      "example_output": {"sample_name": "小型断路器", ...}
    }
  ]
}
```

**指令核心**:
- 你是文档分析专家，分析 OCR 文本推断文档类型、归属部门和所有可提取字段
- 每个字段给出完整默认配置，特别是 `extraction_hint`（从文档哪里找）
- `review_enforced` 默认 false，只有关键字段（如检验结论）才设为 true
- `review_allowed_values` 仅当字段有明确有限选项时给出（如"合格/不合格"）
- 从原文摘录片段作为 few-shot 示例的 input

### prompt_agent

基于确认字段+示例，生成格式与现有 `template_service.build_extraction_prompt()` 一致的完整 prompt。用 `{ocr_text}` 占位符。

### code_agent

根据 `field_type` 和 `extraction_hint` 判断哪些字段需要清洗函数，生成 `clean_{field_key}(value: str) -> str` 函数。

## 与现有代码的集成

### 复用现有服务
- `ocr_service.process_document()` — Step 1 OCR
- `tenant_service.get_all_tenants()` — 获取已有部门列表供匹配
- `tenant_service.create_tenant()` — 新建部门
- `template_service.create_field()` — 写入字段（自动触发 schema_sync）
- `template_service.create_example()` — 写入示例
- `template_service.build_extraction_prompt()` — Prompt 格式参考

### 数据库迁移（015_...sql）
```sql
ALTER TABLE document_templates ADD COLUMN IF NOT EXISTS extraction_prompt_template TEXT;
ALTER TABLE document_templates ADD COLUMN IF NOT EXISTS cleaner_module VARCHAR(255);
```

### 修改 `build_extraction_prompt()`
优先使用模板的 `extraction_prompt_template`，fallback 现有硬编码模板。

## 前端集成

| 文件 | 改动 |
|---|---|
| `web/src/types/index.ts` | 新增 session、分析结果等 TS 类型 |
| `web/src/services/sdk.ts` | 新增 SDK API 调用函数 |
| `web/src/components/admin/AiTemplateWizard.tsx` | **新建**：多步向导 |
| `web/src/pages/AdminConfig.tsx` | 新增 "AI 生成模板" Tab |

## 实施顺序

| Phase | 内容 |
|---|---|
| 1 | 安装 `openai-agents`，建 `sdk/` 骨架（config/session/models） |
| 2 | 实现 doc_analyzer_agent + prompt_agent + code_agent + orchestrator |
| 3 | API 路由 + 注册到 main.py |
| 4 | commit 逻辑 + 数据库迁移 |
| 5 | 前端：类型、API service、向导组件 |
| 6 | 流水线集成：prompt 覆盖 + 清洗函数加载 |
| 7 | 测试 |

## 验证

1. `pytest tests/test_sdk/ -v`
2. 手动：admin 上传一份实际检测报告 PDF → 进入向导 → 确认 AI 推荐的类型/部门/字段默认值 → 修改/确认 → commit → 在字段管理中看到写入的配置 → 用工作流测试该模板能否正常提取
