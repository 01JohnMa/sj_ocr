# NeoFlow - 智能 OCR-LLM 文档平台

基于 PaddleOCR + LangGraph + LLM 的智能文档识别系统，支持检测报告、快递单、抽样单等多类型文档的 OCR 识别与结构化提取。

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| **后端** | FastAPI + LangGraph + PaddleOCR + LLM (DeepSeek/GPT-4o/Claude) |
| **前端** | React + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| **数据库** | Supabase (PostgreSQL + Auth + Storage) |
| **OCR模型** | PP-OCRv5 (PaddlePaddle) |
| **AI Agent** | LangGraph 工作流编排 |

## 核心功能

- **文档上传** - 支持 PDF、图片批量上传
- **OCR 识别** - PP-OCRv5 高精度文字识别
- **智能提取** - LLM 结构化字段提取（检测报告、快递单、抽样单等）
- **模板化管理** - 按模板定义字段、自动同步数据库列、飞书多维表格映射
- **合并模式** - 多文件合并处理（如积分球+光分布 → 照明综合报告）
- **人工审核** - 识别结果校验与修正
- **数据存储** - Supabase 持久化存储
- **飞书同步** - 审核后自动推送到飞书多维表格（按模板配置）
- **多租户** - 基于 Supabase RLS 的部门/租户权限隔离

## 快速启动

### 1. 启动数据库 (Supabase)

```bash
cd supabase
docker-compose up -d

# 首次部署初始化 storage
docker cp storage_base_tables.sql supabase-db:/tmp/
docker exec supabase-db psql -U postgres -d postgres -f /tmp/storage_base_tables.sql
docker restart supabase-storage
```

### 生产部署（单机一体化）

> 说明：以下方式把 web + 后端 + 统一入口（Nginx）与 Supabase 组合部署，外部只暴露 80 端口。

**环境变量**：在仓库根目录放 `.env`，变量名与 `supabase/.env` 一致（如 `ANON_KEY`、`SERVICE_ROLE_KEY`、`POSTGRES_PASSWORD`、`JWT_SECRET`）。可复制 `env.example.txt` 为 `.env` 后按需修改。

```bash
# 在仓库根目录执行
# Windows: copy env.example.txt .env
# Linux/Mac: cp env.example.txt .env
# 编辑 .env 后启动
docker compose -f supabase/docker-compose.yml -f docker-compose.prod.yml up -d
```

默认路由：
- `/` → 前端页面
- `/api` → 后端 API
- `/supabase` → Supabase Kong 网关

### 2. 启动后端

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
# Windows: copy env.example.txt .env
# Linux/Mac: cp env.example.txt .env
# 编辑 .env 填入 LLM_API_KEY、SUPABASE_*

# 启动服务
uvicorn api.main:app --reload --port 8080
```

### 3. 启动前端

```bash
cd web
npm install
npm run dev
```

## 服务地址

| 服务 | 地址 | 说明 |
|------|------|------|
| 统一入口 | http://localhost | Nginx 入口 |
| API | http://localhost/api | FastAPI 接口 |
| API 文档 | http://localhost/docs | Swagger UI |
| Supabase | http://localhost/supabase | Kong 网关 |
| Studio | http://localhost:3001 | 数据库管理 |

## 项目结构

```
neoflow/
├── api/                        # FastAPI 应用层
│   ├── main.py                # 应用入口
│   ├── jobs.py                # 合并任务状态管理
│   ├── dependencies/          # 依赖注入（auth 等）
│   └── routes/
│       ├── documents/         # 文档路由（子模块）
│       │   ├── upload.py      # 上传
│       │   ├── process.py     # 处理（含 process-with-template、process-merge）
│       │   ├── query.py       # 查询
│       │   ├── review.py     # 审核
│       │   ├── helpers.py    # 辅助函数（飞书推送等）
│       │   └── schemas.py    # 请求/响应模型
│       ├── admin.py           # 管理员配置（模板、部门、示例）
│       ├── tenants.py         # 租户/部门管理
│       └── health.py          # 健康检查
│
├── web/                        # React 前端 (Vite)
│   ├── src/
│   │   ├── components/        # 组件（ui/、layout/）
│   │   ├── pages/             # 页面
│   │   │   ├── Login.tsx      # 登录
│   │   │   ├── Dashboard.tsx  # 仪表盘
│   │   │   ├── Upload.tsx     # 上传（单文件/合并/相机）
│   │   │   ├── Documents.tsx  # 文档列表
│   │   │   ├── DocumentDetail.tsx # 文档详情/审核
│   │   │   ├── AdminConfig.tsx    # 管理配置入口
│   │   │   ├── AdminFeishuTab.tsx # 飞书配置
│   │   │   ├── AdminFieldsTab.tsx # 模板字段
│   │   │   └── AdminExamplesTab.tsx # 示例管理
│   │   ├── hooks/             # 自定义 Hooks
│   │   ├── services/          # API 服务
│   │   ├── store/             # 状态管理
│   │   └── lib/               # 工具库
│   └── vite.config.ts
│
├── agents/                     # LangGraph 智能体
│   ├── workflow.py            # OCR 处理工作流
│   ├── json_cleaner.py        # JSON 清洗
│   └── result_builder.py      # 结果构建
│
├── config/                     # 配置
│   ├── settings.py            # 应用配置
│   └── prompts.py             # LLM Prompt 配置
│
├── services/                   # 业务服务
│   ├── base.py                # 基类（SupabaseClientMixin、build_field_table）
│   ├── ocr_service.py         # OCR 服务
│   ├── supabase_service.py    # 数据库服务
│   ├── template_service.py    # 模板服务
│   ├── tenant_service.py       # 租户服务
│   ├── feishu_service.py       # 飞书推送服务
│   ├── schema_sync_service.py # 模板字段与数据库列同步
│   └── vlm_service.py         # 多模态 VLM 服务（可选）
│
├── constants/                  # 常量
│   └── document_types.py      # 文档类型定义
│
├── model/                      # PaddleOCR 模型（需自行下载）
│   ├── PP-OCRv5_server_det_infer/
│   ├── PP-OCRv5_server_rec_infer/
│   ├── PP-LCNet_x1_0_textline_ori_infer/
│   └── PP-LCNet_x1_0_doc_ori_infer/
│
├── supabase/                   # Supabase 本地部署
│   ├── docker-compose.yml     # Docker 编排
│   ├── migrations/            # 数据库迁移
│   └── .env                   # 环境变量
│
├── tests/                      # 测试
│   ├── conftest.py            # pytest 配置
│   ├── routes/                # 路由测试
│   ├── services/              # 服务测试
│   └── agents/                # 智能体测试
│
├── uploads/                    # 上传文件目录
├── logs/                       # 日志目录
├── requirements.txt            # Python 依赖
├── env.example.txt             # 环境变量示例
└── docker-compose.prod.yml     # 生产部署编排
```

## API 接口

### 文档管理

```bash
# 上传文档
POST /api/documents/upload

# 处理文档（自动分类或按文档关联模板）
POST /api/documents/{id}/process?sync=false

# 按指定模板处理
POST /api/documents/{id}/process-with-template
Body: { "template_id": "xxx", "sync": false }

# 合并模式（多文件 → 单模板多样品）
POST /api/documents/process-merge
Body: { "template_id": "xxx", "files": [{ "file_path": "...", "doc_type": "积分球" }, ...] }

# 查询合并任务状态
GET /api/documents/jobs/{job_id}

# 获取结果
GET /api/documents/{id}/result

# 获取文档列表
GET /api/documents

# 审核通过
PUT /api/documents/{id}/validate

# 打回重做
PUT /api/documents/{id}/reject

# CRM 提交文档（强制自动通过，审核由 CRM 侧负责）
POST /api/crm/documents/submit
Form: file, template_id, custom_push_name?
# 详细文档: docs/crm-api.md

# 删除文档
DELETE /api/documents/{id}
```

### 租户与认证

认证由 Supabase Auth 提供（JWT）。用户需在个人设置中选择所属部门（tenant_id）后才能处理文档。

```bash
# 获取租户/部门列表
GET /api/tenants
```

### 管理员配置

```bash
# 模板、部门、示例等配置接口（需管理员权限）
# 详见 /docs
```

## 环境变量

```env
# .env 文件（可复制 env.example.txt）

# Supabase（与 supabase/.env 一致）
SUPABASE_URL=http://localhost:8000
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-key

# LLM 配置（默认 DeepSeek）
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_ID=deepseek-v4-flash

# 飞书配置（可选，推送目标按模板在 Supabase 中配置）
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
FEISHU_PUSH_ENABLED=false

# 文档处理模式：ocr_llm（默认）| vlm
DOC_PROCESS_MODE=ocr_llm

# VLM 配置（DOC_PROCESS_MODE=vlm 时生效）
VLM_API_KEY=
VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VLM_MODEL_ID=qwen3.7-plus
```

> 各模板的飞书目标表格（feishu_bitable_token、feishu_table_id）在
> `supabase/migrations/002_init_data.sql` 及管理后台中配置，无需环境变量。

## UI/UX Pro Max

本项目使用 UI/UX Pro Max 设计系统进行前端开发，确保专业级的 UI/UX 质量。

### 使用方法

在 Cursor 中使用 `/ui-ux-pro-max` 命令：

```bash
# 生成设计系统
python3 .shared/ui-ux-pro-max/scripts/search.py "ocr llm document platform" --design-system -p "NeoFlow"

# 搜索配色方案 (磨砂玻璃风格)
python3 .shared/ui-ux-pro-max/scripts/search.py "glassmorphism" --domain style

# 搜索组件布局
python3 .shared/ui-ux-pro-max/scripts/search.py "dashboard layout" --stack html-tailwind
```

### 设计规范

- **配色**: 翡翠绿 (#018C39) + 紫色 (#8B5CF6) + 磨砂玻璃质感
- **风格**: 科技简约、暗色主题、毛玻璃卡片
- **图标**: Heroicons SVG 图标
- **字体**: Inter / 中文思源黑体

## 文档类型

| 类型 | display_name 规则 | 说明 |
|------|-------------------|------|
| 测试单 | `报告_{样品名称}_{规格型号}_{抽样日期}` | 检测报告 |
| 快递单 | 自动识别 | 物流单据 |
| 抽样单 | 自动识别 | 抽样记录 |

## 开发日志

| 日期 | 类型 | 内容 |
|------|------|------|
| 2026-01-04 | feat | 添加 React 前端业务页面 |
| 2026-01-06 | feat | 用户权限隔离 (RLS) |
| 2026-01-08 | feat | 飞书多维表格推送服务 |
| 2026-01-13 | refactor | 路由拆分、异常体系重构 |
| 2026-01-13 | feat | pending_review 强制审核流程 |
| 2026-03 | feat | 模板化管理、合并模式、多部门配置 |
| 2026-03 | feat | VLM 多模态提取模式、process-with-template 返回 202 |
| 2026-03 | refactor | 代码质量优化：飞书推送统一、权限检查复用、auth 中间件、安全加固、死代码清理、过长函数拆分、服务层装饰器、共享基类 |

## 代码规范

项目遵循 [.cursor/rules/code-simplifier.mdc](.cursor/rules/code-simplifier.mdc) 原则：保持功能不变的前提下提升可读性与可维护性，避免嵌套三元、冗余抽象，优先显式逻辑。

## 测试

```bash
# 运行全部测试
pytest -v

# 运行指定模块
pytest tests/routes/ -v
pytest tests/services/ -v
```

## 相关文档

- [Supabase 部署指南](./supabase/README.md)
- [生产部署说明](./deploy/REDEPLOY_WITH_NEW_CODE.md)
- [代码质量优化计划](./.cursor/plans/代码质量优化计划_f9149055.plan.md)

## License

MIT
