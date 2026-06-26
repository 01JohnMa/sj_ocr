"""CRM 集成入口。"""

import os
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger

from api.dependencies.auth import CurrentUser, get_current_user
from api.exceptions import AuthorizationError, FileSizeError, FileTypeError, ProcessingError
from api.jobs import create_job
from api.routes.documents.helpers import save_upload_file, validate_file_extension
from config.settings import settings
from services.supabase_service import supabase_service
from services.template_service import template_service


router = APIRouter(prefix="/crm", tags=["CRM集成"])


@router.post("/documents/submit", status_code=202)
async def submit_crm_document(
    file: UploadFile = File(...),
    template_id: str = Form(...),
    custom_push_name: Optional[str] = Form(None),
    user: CurrentUser = Depends(get_current_user),
):
    """CRM 文档提交入口：入队后由 worker 强制自动通过。"""
    try:
        if not user.is_tenant_admin():
            raise AuthorizationError("仅管理员或 CRM 服务账号可访问此接口")

        if not user.tenant_id:
            raise ProcessingError("请先为 CRM 调用用户配置所属部门")

        template = await template_service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        if not user.can_access_tenant(template.get("tenant_id")):
            raise AuthorizationError("无权使用此模板")

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
            "auto_approve_forced": True,
        }

    except (AuthorizationError, HTTPException, FileTypeError, FileSizeError, ProcessingError):
        raise
    except Exception as exc:
        logger.opt(exception=exc).error("CRM文档提交失败")
        raise ProcessingError(f"CRM文档提交失败: {str(exc)}")
