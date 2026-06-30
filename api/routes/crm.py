"""CRM 集成入口。"""

from datetime import datetime
import inspect
import os
from typing import Any, Dict, Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from api.dependencies.auth import CurrentUser, get_crm_current_user
from api.exceptions import (
    AuthorizationError,
    DocumentNotFoundError,
    ExternalServiceError,
    FileSizeError,
    FileTypeError,
    ProcessingError,
    ValidationError,
)
from api.jobs import build_feishu_push_dedupe_key, create_job, has_feishu_push_record
from api.routes.documents.helpers import push_to_feishu, save_upload_file, validate_file_extension
from api.routes.documents.query import _check_document_access
from config.settings import settings
from services.supabase_service import supabase_service
from services.template_service import template_service


router = APIRouter(prefix="/crm", tags=["CRM集成"])

QUALITY_CRM_TEMPLATE_IDS = {
    "b0000000-0000-0000-0000-000000000001",  # 检测报告
    "b0000000-0000-0000-0000-000000000003",  # 抽样单
}
CRM_EXTRA_FIELD_MAPPING = {
    "alipay_account": "支付宝账号",
    "alipay_name": "支付宝姓名",
}


class CrmFeishuPushRequest(BaseModel):
    """CRM 审核完成后的飞书推送请求。"""

    alipay_account: str
    alipay_name: str
    reviewed_data: Optional[Dict[str, Any]] = None
    custom_push_name: Optional[str] = None


async def _run_supabase(fn):
    runner = getattr(supabase_service, "_run_sync", None)
    if runner is not None and inspect.iscoroutinefunction(runner):
        return await runner(fn)
    return fn()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _ensure_crm_admin(user: CurrentUser) -> None:
    if not user.is_tenant_admin():
        raise AuthorizationError("仅管理员或 CRM 服务账号可访问此接口")
    if not user.tenant_id:
        raise ProcessingError("请先为 CRM 调用用户配置所属部门")


def _ensure_supported_crm_template(template: dict) -> None:
    if template.get("id") not in QUALITY_CRM_TEMPLATE_IDS:
        raise ValidationError("CRM接口仅支持质量管理中心的检测报告和抽样单模板")


def _template_field_keys(template: dict) -> set[str]:
    return {
        field.get("field_key")
        for field in template.get("template_fields") or []
        if field.get("field_key")
    }


def _sanitize_reviewed_data(template: dict, reviewed_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = dict(reviewed_data or {})
    for crm_only_key in CRM_EXTRA_FIELD_MAPPING:
        cleaned.pop(crm_only_key, None)
    if not cleaned:
        return {}

    allowed_keys = _template_field_keys(template)
    unknown_keys = sorted(set(cleaned) - allowed_keys)
    if unknown_keys:
        raise ValidationError(f"reviewed_data包含不支持的字段: {', '.join(unknown_keys)}")
    return cleaned


def _extract_template_data(template: dict, result_row: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = _template_field_keys(template)
    return {key: result_row.get(key) for key in allowed_keys if key in result_row}


def _required_str(value: str, label: str) -> str:
    cleaned = value.strip() if value else ""
    if not cleaned:
        raise ValidationError(f"{label}不能为空")
    return cleaned


async def _fetch_extraction_result(table_name: str, document_id: str) -> Optional[Dict[str, Any]]:
    result = await _run_supabase(
        lambda: supabase_service.client.table(table_name).select("*").eq("document_id", document_id).execute()
    )
    return result.data[0] if result.data else None


async def _mark_crm_push_completed(
    table_name: str,
    document_id: str,
    reviewed_data: Dict[str, Any],
    user_id: str,
) -> None:
    update_data = {**reviewed_data}
    update_data["is_validated"] = True
    update_data["validated_at"] = datetime.now().isoformat()
    update_data["validated_by"] = user_id

    await _run_supabase(
        lambda: (
            supabase_service.client.table(table_name)
            .update(update_data)
            .eq("document_id", document_id)
            .execute()
        )
    )

    result_row = await _fetch_extraction_result(table_name, document_id)
    if not result_row or result_row.get("is_validated") is not True:
        raise ProcessingError("CRM推送成功后更新审核结果失败")

    await supabase_service.update_document(document_id, {"status": "completed"})


@router.post("/documents/submit", status_code=202)
async def submit_crm_document(
    file: UploadFile = File(...),
    template_id: str = Form(...),
    custom_push_name: Optional[str] = Form(None),
    user: CurrentUser = Depends(get_crm_current_user),
):
    """CRM 文档提交入口：入队处理后等待 CRM 审核推送。"""
    try:
        _ensure_crm_admin(user)

        template = await template_service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        if not user.can_access_tenant(template.get("tenant_id")):
            raise AuthorizationError("无权使用此模板")
        _ensure_supported_crm_template(template)

        if not validate_file_extension(file.filename):
            raise FileTypeError(settings.ALLOWED_EXTENSIONS)

        document_id = str(uuid.uuid4())
        file_ext = os.path.splitext(file.filename)[1]
        stored_filename = f"{document_id}{file_ext}"
        os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
        file_path = os.path.join(settings.UPLOAD_FOLDER, stored_filename)

        file_size = await save_upload_file(file, file_path)
        if file_size > settings.MAX_FILE_SIZE:
            os.remove(file_path)
            raise FileSizeError(settings.MAX_FILE_SIZE / 1024 / 1024)

        cleaned_push_name = custom_push_name.strip() if custom_push_name else None
        if cleaned_push_name and len(cleaned_push_name) > 100:
            cleaned_push_name = cleaned_push_name[:100]

        document_data = {
            "id": document_id,
            "user_id": user.user_id,
            "file_name": stored_filename,
            "original_file_name": file.filename,
            "file_path": file_path,
            "file_size": file_size,
            "file_type": file.content_type,
            "file_extension": file_ext,
            "mime_type": file.content_type,
            "status": "uploaded",
            "template_id": template_id,
            "tenant_id": user.tenant_id,
            "custom_push_name": cleaned_push_name or None,
        }

        try:
            await supabase_service.create_document(document_data)
        except Exception as exc:
            logger.bind(document_id=document_id, user_id=user.user_id).opt(exception=exc).error("CRM文档保存失败")
            if os.path.exists(file_path):
                os.remove(file_path)
            raise ProcessingError(f"CRM文档保存失败，请重试: {str(exc)}")

        job_id = await create_job(
            job_type="crm",
            created_by=user.user_id,
            related_document_ids=[document_id],
        )
        await supabase_service.update_document_status(document_id, "queued")

        logger.info(f"CRM文档已入队: {document_id}, job_id={job_id}, 用户: {user.user_id}")

        return {
            "document_id": document_id,
            "job_id": job_id,
            "status": "queued",
            "message": "CRM文档已加入处理队列",
            "crm_review_required": True,
        }

    except (AuthorizationError, HTTPException, FileTypeError, FileSizeError, ProcessingError, ValidationError):
        raise
    except Exception as exc:
        logger.opt(exception=exc).error("CRM文档提交失败")
        raise ProcessingError(f"CRM文档提交失败: {str(exc)}")


@router.post("/documents/{document_id}/feishu/push")
async def push_crm_document_to_feishu(
    document_id: str,
    request: CrmFeishuPushRequest,
    user: CurrentUser = Depends(get_crm_current_user),
):
    """CRM 审核完成后，将结果和手填支付宝字段原子推送到飞书。"""
    try:
        _ensure_crm_admin(user)

        alipay_account = _required_str(request.alipay_account, "支付宝账号")
        alipay_name = _required_str(request.alipay_name, "支付宝姓名")

        document = await supabase_service.get_document(document_id)
        if not document:
            raise DocumentNotFoundError(document_id)
        _check_document_access(document, user, document_id)

        template_id = document.get("template_id")
        template = await template_service.get_template_with_details(template_id) if template_id else None
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        if not user.can_access_tenant(template.get("tenant_id")):
            raise AuthorizationError("无权使用此模板")
        _ensure_supported_crm_template(template)
        reviewed_data = _sanitize_reviewed_data(template, request.reviewed_data)

        table_name = await _maybe_await(supabase_service.resolve_table_name(
            template_id=template_id,
            document_type=document.get("document_type"),
        ))
        if not table_name:
            raise ProcessingError("无法确定文档结果表")

        result_row = await _fetch_extraction_result(table_name, document_id)
        if not result_row:
            raise ProcessingError("文档提取结果不存在，无法推送飞书")

        push_data = {**_extract_template_data(template, result_row), **reviewed_data}
        extra_data = {
            "alipay_account": alipay_account,
            "alipay_name": alipay_name,
        }
        dedupe_key = build_feishu_push_dedupe_key(
            document_id,
            template.get("id"),
            {**push_data, **extra_data},
        )
        if await has_feishu_push_record(dedupe_key):
            if document.get("status") == "pending_review":
                await _mark_crm_push_completed(table_name, document_id, reviewed_data, user.user_id)
            return {
                "success": True,
                "status": "skipped",
                "document_id": document_id,
                "message": "相同内容已推送，已跳过",
            }

        if document.get("status") != "pending_review":
            raise ValidationError("文档不是待CRM审核状态，不能推送飞书")

        custom_push_name = (
            request.custom_push_name.strip()
            if request.custom_push_name and request.custom_push_name.strip()
            else document.get("custom_push_name")
        )

        pushed = await push_to_feishu(
            template=template,
            extraction_data=push_data,
            display_name=document.get("display_name"),
            document_id=document_id,
            source_file_path=document.get("file_path", ""),
            custom_push_name=custom_push_name,
            dedupe_key=dedupe_key,
            extra_data=extra_data,
            extra_field_mapping=CRM_EXTRA_FIELD_MAPPING,
        )
        if not pushed:
            raise ExternalServiceError("飞书", "CRM审核后推送失败")

        await _mark_crm_push_completed(table_name, document_id, reviewed_data, user.user_id)
        logger.info(f"CRM审核后飞书推送完成: {document_id}, 用户: {user.user_id}")

        return {
            "success": True,
            "status": "pushed",
            "document_id": document_id,
            "message": "CRM审核结果已推送飞书",
        }

    except (
        AuthorizationError,
        DocumentNotFoundError,
        ExternalServiceError,
        HTTPException,
        ProcessingError,
        ValidationError,
    ):
        raise
    except Exception as exc:
        logger.bind(document_id=document_id).opt(exception=exc).error("CRM飞书推送失败")
        raise ProcessingError(f"CRM飞书推送失败: {str(exc)}")
