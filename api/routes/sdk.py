"""AI template generation SDK routes."""

import os
import re
from pathlib import Path
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from api.dependencies.auth import CurrentUser, get_current_user
from api.exceptions import AuthorizationError
from config.settings import settings
from services.ocr_service import ocr_service
from sdk.agents.orchestrator import orchestrator
from sdk.excel_template import scan_excel_placeholders
from sdk.models import (
    CommitSessionRequest,
    ConfirmTemplateRequest,
    DetectedField,
    DocumentAnalysis,
    ExcelTemplatePlaceholder,
    SDKSessionResponse,
    SDKSessionState,
)
from sdk.session import session_store

router = APIRouter(prefix="/sdk", tags=["AI模板生成"])


def _require_admin(user: CurrentUser) -> None:
    if not user.is_tenant_admin():
        raise AuthorizationError("仅管理员可访问此接口")


def _get_session_or_404(session_id: str, user: CurrentUser):
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    if session.user_id != user.user_id and not user.is_super_admin():
        raise AuthorizationError("无权访问该 AI 模板会话")
    return session


def _response(session) -> SDKSessionResponse:
    return SDKSessionResponse(
        id=session.id,
        file_name=session.file_name,
        state=session.state,
        excel_template_file_name=session.excel_template_file_name,
        excel_placeholders=session.excel_placeholders,
        ocr_text=session.ocr_text,
        ocr_confidence=session.ocr_confidence,
        page_count=session.page_count,
        analysis=session.analysis,
        confirmed_template=session.confirmed_template,
        prompt=session.prompt,
        cleaner_code=session.cleaner_code,
        commit_result=session.commit_result,
    )


def _safe_file_name(file_name: str) -> str:
    base_name = Path(file_name).name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base_name) or "upload.bin"


async def _save_upload(file: UploadFile) -> str:
    upload_dir = Path(settings.UPLOAD_FOLDER) / "sdk_sessions"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{uuid4()}_{_safe_file_name(file.filename or 'upload.bin')}"
    async with aiofiles.open(file_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            await out.write(chunk)
    return str(file_path)


@router.post("/sessions", response_model=SDKSessionResponse, status_code=201)
async def create_session(
    file: UploadFile = File(...),
    excel_template: UploadFile | None = File(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)

    file_path = await _save_upload(file)
    excel_template_path = None
    excel_placeholders: list[ExcelTemplatePlaceholder] = []
    try:
        if excel_template and excel_template.filename:
            excel_template_path = await _save_upload(excel_template)
            excel_placeholders = [
                ExcelTemplatePlaceholder(**placeholder.__dict__)
                for placeholder in scan_excel_placeholders(excel_template_path)
            ]
        ocr_result = await ocr_service.process_document(file_path)
    except Exception:
        if os.path.exists(file_path):
            os.remove(file_path)
        if excel_template_path and os.path.exists(excel_template_path):
            os.remove(excel_template_path)
        raise

    session = session_store.create(
        file_name=file.filename or Path(file_path).name,
        file_path=file_path,
        excel_template_file_name=excel_template.filename if excel_template else None,
        excel_template_path=excel_template_path,
        excel_placeholders=excel_placeholders,
        ocr_text=ocr_result.get("text", ""),
        ocr_confidence=ocr_result.get("confidence", 0),
        page_count=max(1, len(ocr_result.get("text", "").split("\n\n"))),
        user_id=user.user_id,
    )
    return _response(session)


@router.get("/sessions/{session_id}", response_model=SDKSessionResponse)
async def get_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    return _response(_get_session_or_404(session_id, user))


@router.post("/sessions/{session_id}/analyze")
async def analyze_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    analysis = await orchestrator.analyze_document(session)
    if not isinstance(analysis, DocumentAnalysis):
        analysis = DocumentAnalysis.model_validate(analysis)
    _merge_excel_placeholder_fields(analysis, session.excel_placeholders)
    session.analysis = analysis
    session.state = SDKSessionState.ANALYZED
    session_store.save(session)
    return {"success": True, "analysis": analysis}


@router.post("/sessions/{session_id}/confirm-template", response_model=SDKSessionResponse)
async def confirm_template(
    session_id: str,
    request: ConfirmTemplateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    session.confirmed_template = request
    session.state = SDKSessionState.TEMPLATE_CONFIRMED
    session_store.save(session)
    return _response(session)


@router.post("/sessions/{session_id}/prompt")
async def generate_prompt(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    prompt = await orchestrator.generate_prompt(session)
    session.prompt = prompt
    session.state = SDKSessionState.PROMPT_GENERATED
    session_store.save(session)
    return {"success": True, "prompt": prompt}


@router.post("/sessions/{session_id}/code")
async def generate_code(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    cleaner_code = await orchestrator.generate_code(session)
    session.cleaner_code = cleaner_code
    session.state = SDKSessionState.CODE_GENERATED
    session_store.save(session)
    return {"success": True, "code": cleaner_code}


@router.post("/sessions/{session_id}/commit")
async def commit_session(
    session_id: str,
    request: CommitSessionRequest | None = Body(default=None),
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    if request:
        if request.prompt is not None:
            session.prompt = request.prompt
        if request.cleaner_code is not None:
            session.cleaner_code = request.cleaner_code
    result = await orchestrator.commit(session)
    session.commit_result = result
    session.state = SDKSessionState.COMMITTED
    session_store.save(session)
    return {"success": True, "commit_result": result}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    session = _get_session_or_404(session_id, user)
    if os.path.exists(session.file_path):
        os.remove(session.file_path)
    if session.excel_template_path and os.path.exists(session.excel_template_path):
        os.remove(session.excel_template_path)
    deleted = session_store.delete(session_id)
    return {"success": deleted}


def _merge_excel_placeholder_fields(
    analysis: DocumentAnalysis,
    placeholders: list[ExcelTemplatePlaceholder],
) -> None:
    existing_keys = {field.field_key for field in analysis.detected_fields}
    for placeholder in placeholders:
        if placeholder.field_key in existing_keys:
            continue
        analysis.detected_fields.append(
            DetectedField(
                field_key=placeholder.field_key,
                field_label=placeholder.field_key,
                field_type="text",
                extraction_hint=(
                    f"从待识别图片/文档中提取 {placeholder.field_key}，"
                    f"并填入 Excel 模板槽位 {{{{{placeholder.field_key}}}}}。"
                ),
                review_enforced=True,
            )
        )
        existing_keys.add(placeholder.field_key)
