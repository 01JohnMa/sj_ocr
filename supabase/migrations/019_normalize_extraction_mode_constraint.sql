-- ============================================================
-- Normalize document_templates.extraction_mode enum values
-- ============================================================
-- 001_multi_tenant.sql originally created extraction_mode as llm/vlm.
-- Later application code and UI use ocr_llm/vlm. Existing databases need
-- their old constraint, default, and rows normalized so admin config saves.
-- ============================================================

ALTER TABLE public.document_templates
    DROP CONSTRAINT IF EXISTS document_templates_extraction_mode_check;

UPDATE public.document_templates
SET extraction_mode = 'ocr_llm'
WHERE extraction_mode IS NULL
   OR extraction_mode = 'llm';

ALTER TABLE public.document_templates
    ALTER COLUMN extraction_mode SET DEFAULT 'ocr_llm';

ALTER TABLE public.document_templates
    ADD CONSTRAINT document_templates_extraction_mode_check
    CHECK (extraction_mode IN ('ocr_llm', 'vlm'));

COMMENT ON COLUMN public.document_templates.extraction_mode IS
    '提取引擎：ocr_llm=PaddleOCR+LLM（印刷体），vlm=多模态VLM（手写/复杂版式）';
