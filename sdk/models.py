"""Pydantic models for the AI template generation flow."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SDKSessionState(str, Enum):
    OCR_COMPLETED = "ocr_completed"
    ANALYZED = "analyzed"
    TEMPLATE_CONFIRMED = "template_confirmed"
    PROMPT_GENERATED = "prompt_generated"
    CODE_GENERATED = "code_generated"
    COMMITTED = "committed"


class RecommendedTenant(BaseModel):
    suggest_name: str
    suggest_code: str
    reason: str = ""
    match_existing_tenant_id: Optional[str] = None


class DetectedField(BaseModel):
    field_key: str
    field_label: str
    field_type: str = "text"
    extraction_hint: str = ""
    review_enforced: bool = False
    review_allowed_values: Optional[List[str]] = None
    sample_value: Optional[str] = None


class SuggestedExample(BaseModel):
    example_input: str
    example_output: Dict[str, Any]


class ExcelTemplatePlaceholder(BaseModel):
    sheet_name: str
    coordinate: str
    field_key: str
    raw_value: str


class DocumentAnalysis(BaseModel):
    recommended_doc_type: str
    recommended_doc_code: str
    confidence: float = Field(ge=0, le=1)
    recommended_tenant: RecommendedTenant
    detected_fields: List[DetectedField]
    suggested_examples: List[SuggestedExample] = []


class ConfirmTemplateRequest(BaseModel):
    template_name: str
    template_code: str
    description: Optional[str] = None
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    tenant_code: Optional[str] = None
    extraction_mode: str = "ocr_llm"
    per_page_extraction: bool = False
    fields: List[DetectedField]
    examples: List[SuggestedExample] = []


class CommitSessionRequest(BaseModel):
    prompt: Optional[str] = None
    cleaner_code: Optional[str] = None


class CommitResult(BaseModel):
    tenant_id: str
    template_id: str
    field_ids: List[str] = []
    example_ids: List[str] = []
    cleaner_module: Optional[str] = None


class SDKSession(BaseModel):
    id: str
    file_name: str
    file_path: str
    excel_template_file_name: Optional[str] = None
    excel_template_path: Optional[str] = None
    excel_placeholders: List[ExcelTemplatePlaceholder] = Field(default_factory=list)
    ocr_text: str
    ocr_confidence: float
    page_count: int
    user_id: str
    state: SDKSessionState
    created_at: float
    updated_at: float
    analysis: Optional[DocumentAnalysis] = None
    confirmed_template: Optional[ConfirmTemplateRequest] = None
    prompt: Optional[str] = None
    cleaner_code: Optional[str] = None
    commit_result: Optional[CommitResult] = None


class SDKSessionResponse(BaseModel):
    id: str
    file_name: str
    state: SDKSessionState
    excel_template_file_name: Optional[str] = None
    excel_placeholders: List[ExcelTemplatePlaceholder] = Field(default_factory=list)
    ocr_text: str
    ocr_confidence: float
    page_count: int
    analysis: Optional[DocumentAnalysis] = None
    confirmed_template: Optional[ConfirmTemplateRequest] = None
    prompt: Optional[str] = None
    cleaner_code: Optional[str] = None
    commit_result: Optional[CommitResult] = None
