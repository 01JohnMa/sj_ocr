-- MIGRATION 015: AI template generation prompt and cleaner metadata

ALTER TABLE document_templates
    ADD COLUMN IF NOT EXISTS extraction_prompt_template TEXT,
    ADD COLUMN IF NOT EXISTS cleaner_module VARCHAR(255);

COMMENT ON COLUMN document_templates.extraction_prompt_template IS
    'AI 模板向导生成或管理员确认的提取 Prompt 模板，使用 {ocr_text} 作为 OCR 文本占位符';

COMMENT ON COLUMN document_templates.cleaner_module IS
    '预留字段级清洗模块路径；当前版本仅保存元数据，不自动执行模型生成代码';

SELECT '015: document_templates prompt/cleaner 字段补充完成' AS message;
