-- MIGRATION 016: fixed Excel template output metadata

ALTER TABLE document_templates
    ADD COLUMN IF NOT EXISTS output_mode VARCHAR(32) NOT NULL DEFAULT 'bitable',
    ADD COLUMN IF NOT EXISTS excel_template_file_name TEXT,
    ADD COLUMN IF NOT EXISTS excel_template_path TEXT,
    ADD COLUMN IF NOT EXISTS excel_template_placeholders JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN document_templates.output_mode IS
    '模板输出模式：bitable=仅多维表格，excel_template=仅固定 Excel，both=多维表格和固定 Excel';

COMMENT ON COLUMN document_templates.excel_template_file_name IS
    '固定 Excel 空模板原始文件名，用于展示和审计';

COMMENT ON COLUMN document_templates.excel_template_path IS
    '固定 Excel 空模板存储路径；模板内使用 {{field_key}} 作为填充槽位';

COMMENT ON COLUMN document_templates.excel_template_placeholders IS
    '固定 Excel 空模板扫描出的填充槽位列表，包含 sheet、coordinate、field_key 和 raw_value';

SELECT '016: document_templates fixed Excel template output metadata added' AS message;
