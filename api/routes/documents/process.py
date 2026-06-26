# api/routes/documents/process.py
"""文档路由 - 处理相关端点"""

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
from datetime import datetime
import asyncio
from pydantic import BaseModel
from loguru import logger
import uuid
import os

from config.settings import settings
from services.supabase_service import supabase_service
from services.template_service import template_service
from agents.workflow import ocr_workflow
from api.exceptions import DocumentNotFoundError, FileNotFoundError, ProcessingError, AppException
from api.dependencies.auth import get_current_user, CurrentUser
from api.jobs import create_job, update_job, get_job
from .helpers import (
    push_to_feishu,
    handle_processing_success,
    handle_processing_failure,
    handle_processing_exception,
)

# 向后兼容旧测试/旧调用入口
_handle_processing_success = handle_processing_success
_handle_processing_failure = handle_processing_failure
_handle_processing_exception = handle_processing_exception

# 重任务并发限制：避免多用户同时触发时拖垮 API 进程
_DOC_PROCESS_SEMAPHORE = asyncio.Semaphore(max(1, settings.DOC_PROCESS_MAX_CONCURRENCY))


# ============ 请求模型 ============

class ProcessWithTemplateRequest(BaseModel):
    """模板化处理请求"""
    template_id: str
    sync: bool = False


router = APIRouter()



@router.post("/{document_id}/process")
async def process_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    user: CurrentUser = Depends(get_current_user)
):
    """
    处理文档（需要登录）
    
    - **document_id**: 文档ID
    - **sync**: 是否同步处理（默认异步后台处理）
    
    如果文档关联了 template_id，将使用模板化处理流程；
    否则使用原有的自动分类处理流程（质量管理中心）。
    """
    try:
        # 检查用户是否已关联租户
        if not user.tenant_id:
            raise ProcessingError("请先在个人设置中选择所属部门后再处理文档")
        
        # 获取文档信息
        document = await supabase_service.get_document(document_id)
        
        if not document:
            # 尝试从本地查找
            for file in os.listdir(settings.UPLOAD_FOLDER):
                if file.startswith(document_id):
                    file_path = os.path.join(settings.UPLOAD_FOLDER, file)
                    break
            else:
                raise DocumentNotFoundError(document_id)
        else:
            file_path = document.get("file_path")
            if not os.path.exists(file_path):
                raise FileNotFoundError(file_path)
        
        # 检查文档是否关联了模板
        template_id = document.get("template_id") if document else None
        # 优先使用当前用户的 tenant_id（支持重新处理时使用更新后的租户）
        tenant_id = user.tenant_id or document.get("tenant_id")

        current_status = document.get("status") if document else None
        if not sync and current_status == "queued":
            return {
                "document_id": document_id,
                "status": "queued",
                "message": "文档已在队列中，请勿重复提交",
                "estimated_time": "2-5分钟",
                "use_template": template_id is not None,
            }
        if not sync and current_status == "processing":
            return {
                "document_id": document_id,
                "status": "processing",
                "message": "文档已在处理中，请勿重复提交",
                "estimated_time": "2-5分钟",
                "use_template": template_id is not None,
            }
        
        # 如果文档的 tenant_id 为空，更新为当前用户的 tenant_id
        if document and not document.get("tenant_id") and user.tenant_id:
            await supabase_service.update_document(document_id, {"tenant_id": user.tenant_id})
        
        if sync:
            # 同步处理
            if template_id:
                # 使用模板化处理
                result = await ocr_workflow.process_with_template(
                    document_id=document_id,
                    file_path=file_path,
                    template_id=template_id,
                    tenant_id=tenant_id
                )
            else:
                # 原有流程（质量管理中心分类）
                result = await ocr_workflow.process(document_id, file_path, tenant_id=tenant_id)
            
            # 保存结果到数据库
            if result["success"] and result.get("extraction_data"):
                try:
                    auto_approve = False
                    template = None
                    if template_id:
                        template = await template_service.get_template(template_id)
                    elif tenant_id and result.get("document_type"):
                        template = await template_service.get_template_by_code(tenant_id, result.get("document_type"))
                    if template:
                        auto_approve = bool(template.get("auto_approve", False))

                    await _handle_processing_success(
                        document_id=document_id,
                        result=result,
                        template_id=template_id or (template.get("id") if template else None),
                        tenant_id=tenant_id,
                        generate_display_name=True,
                        auto_approve=auto_approve,
                        source_file_path=file_path,
                        custom_push_name=document.get("custom_push_name") if document else None,
                    )
                except Exception as e:
                    logger.warning(f"保存结果到数据库失败: {e}")
            
            return result
        else:
            # 异步处理
            job_id = await create_job(
                job_type="template" if template_id else "single",
                created_by=user.user_id,
                related_document_ids=[document_id],
            )
            try:
                await supabase_service.update_document_status(document_id, "queued")
            except Exception as status_err:
                logger.warning(f"更新排队状态失败: {status_err}")

            return {
                "document_id": document_id,
                "job_id": job_id,
                "status": "queued",
                "message": "文档已加入处理队列",
                "estimated_time": "2-5分钟",
                "use_template": template_id is not None
            }
        
    except (DocumentNotFoundError, FileNotFoundError):
        raise
    except Exception as e:
        logger.error(f"处理失败: {e}")
        raise ProcessingError(f"处理失败: {str(e)}")


async def process_document_task(
    document_id: str,
    file_path: str,
    template_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    custom_push_name: Optional[str] = None,
    job_id: Optional[str] = None,
    force_auto_approve: bool = False,
):
    """后台处理任务"""
    try:
        async with _DOC_PROCESS_SEMAPHORE:
            if job_id:
                await update_job(job_id, "ocr")
            await supabase_service.update_document_status(document_id, "processing")
            logger.info(f"开始后台处理: {document_id}, 模板: {template_id or '无(自动分类)'}")

            # 根据是否有模板选择处理方式
            if template_id:
                result = await ocr_workflow.process_with_template(
                    document_id=document_id,
                    file_path=file_path,
                    template_id=template_id,
                    tenant_id=tenant_id
                )
            else:
                result = await ocr_workflow.process(document_id, file_path, tenant_id=tenant_id)

            if result["success"] and result.get("extraction_data"):
                auto_approve = False
                template = None
                if template_id:
                    template = await template_service.get_template(template_id)
                elif tenant_id and result.get("document_type"):
                    template = await template_service.get_template_by_code(tenant_id, result.get("document_type"))
                if template:
                    auto_approve = bool(template.get("auto_approve", False))
                if force_auto_approve:
                    auto_approve = True

                await handle_processing_success(
                    document_id=document_id,
                    result=result,
                    template_id=template_id or (template.get("id") if template else None),
                    tenant_id=tenant_id,
                    generate_display_name=True,
                    auto_approve=auto_approve,
                    source_file_path=file_path,
                    custom_push_name=custom_push_name,
                )
                if job_id:
                    await update_job(job_id, "completed", document_ids=[document_id], error=None)
            else:
                error_message = result.get("error") or "未提取到有效字段"
                await _handle_processing_failure(
                    document_id,
                    error_message
                )
                if job_id:
                    await update_job(job_id, "failed", error=error_message)

    except Exception as e:
        if job_id:
            await update_job(job_id, "failed", error=str(e))
        await _handle_processing_exception(document_id, e)


@router.post("/process-text")
async def process_text_directly(
    text: str = Form(...),
    document_id: Optional[str] = Form(None)
):
    """
    直接处理OCR文本（跳过OCR步骤）
    
    用于已经有OCR结果的场景
    """
    try:
        if not text.strip():
            raise HTTPException(status_code=400, detail="文本不能为空")
        
        doc_id = document_id or str(uuid.uuid4())
        
        result = await ocr_workflow.process_with_text(doc_id, text)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise ProcessingError(f"处理失败: {str(e)}")


# ============ 模板化处理端点 ============

@router.post("/{document_id}/process-with-template")
async def process_document_with_template(
    document_id: str,
    request: ProcessWithTemplateRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user)
):
    """
    使用指定模板处理文档
    
    - **document_id**: 文档ID
    - **template_id**: 模板ID
    - **sync**: 是否同步处理（默认异步后台处理）
    """
    try:
        # 检查用户租户
        if not user.tenant_id:
            raise HTTPException(status_code=400, detail="请先选择所属部门")
        
        # 获取文档信息
        document = await supabase_service.get_document(document_id)
        if not document:
            raise DocumentNotFoundError(document_id)
        
        file_path = document.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(file_path or "未知路径")
        
        # 获取模板信息
        template = await template_service.get_template(request.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        
        # 检查模板是否属于用户的租户
        if template.get("tenant_id") != user.tenant_id and not user.is_super_admin():
            raise HTTPException(status_code=403, detail="无权使用此模板")
        
        auto_approve = bool(template.get("auto_approve", False))
        custom_push_name = document.get("custom_push_name")

        current_status = document.get("status")
        if not request.sync and current_status == "queued":
            return JSONResponse(status_code=200, content={
                "document_id": document_id,
                "template_id": request.template_id,
                "status": "queued",
                "message": "文档已在队列中，请勿重复提交"
            })
        if not request.sync and current_status == "processing":
            return JSONResponse(status_code=200, content={
                "document_id": document_id,
                "template_id": request.template_id,
                "status": "processing",
                "message": "文档已在处理中，请勿重复提交"
            })

        if request.sync:
            # 同步处理
            result = await ocr_workflow.process_with_template(
                document_id=document_id,
                file_path=file_path,
                template_id=request.template_id,
                tenant_id=user.tenant_id
            )

            # 保存结果
            if result["success"] and result.get("extraction_data"):
                await _save_template_extraction_result(
                    document_id=document_id,
                    template=template,
                    result=result,
                    user=user,
                    file_path=file_path,
                    auto_approve=auto_approve,
                    custom_push_name=custom_push_name,
                )

            return result
        else:
            # 异步处理
            job_id = await create_job(
                job_type="template",
                created_by=user.user_id,
                related_document_ids=[document_id],
            )
            await supabase_service.update_document_status(document_id, "queued")

            return JSONResponse(status_code=202, content={
                "document_id": document_id,
                "template_id": request.template_id,
                "job_id": job_id,
                "status": "queued",
                "message": "文档已加入处理队列"
            })
        
    except (DocumentNotFoundError, FileNotFoundError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"模板化处理失败: {e}")
        raise ProcessingError(f"处理失败: {str(e)}")


async def process_document_with_template_task(
    document_id: str,
    file_path: str,
    template_id: str,
    tenant_id: str,
    auto_approve: bool = False,
    custom_push_name: Optional[str] = None,
    job_id: Optional[str] = None,
):
    """模板化处理后台任务"""
    try:
        async with _DOC_PROCESS_SEMAPHORE:
            if job_id:
                await update_job(job_id, "ocr")
            await supabase_service.update_document_status(document_id, "processing")
            logger.info(f"开始模板化后台处理: {document_id}, 模板: {template_id}")

            result = await ocr_workflow.process_with_template(
                document_id=document_id,
                file_path=file_path,
                template_id=template_id,
                tenant_id=tenant_id
            )

            if result["success"] and result.get("extraction_data"):
                await handle_processing_success(
                    document_id=document_id,
                    result=result,
                    template_id=template_id,
                    tenant_id=tenant_id,
                    generate_display_name=False,
                    auto_approve=auto_approve,
                    source_file_path=file_path,
                    custom_push_name=custom_push_name,
                )
                if job_id:
                    await update_job(job_id, "completed", document_ids=[document_id], error=None)
            else:
                error_message = result.get("error") or "未提取到有效字段"
                await _handle_processing_failure(
                    document_id,
                    error_message
                )
                if job_id:
                    await update_job(job_id, "failed", error=error_message)
            
    except Exception as e:
        if job_id:
            await update_job(job_id, "failed", error=str(e))
        await _handle_processing_exception(document_id, e)


@router.get("/jobs/{job_id}")
async def get_merge_job_status(
    job_id: str,
    user: CurrentUser = Depends(get_current_user)
):
    """查询合并任务状态（前端轮询用）

    Returns:
        {
            "job_id": "...",
            "status": "queued|processing|completed|failed",
            "stage": "queued|ocr|llm|saving|completed|failed",
            "progress": 0~100,
            "document_ids": [...],   # completed 时填入
            "error": "..."           # failed 时填入
        }
    """
    job = await get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job


async def _save_template_extraction_result(
    document_id: str,
    template: dict,
    result: dict,
    user: CurrentUser,
    file_path: Optional[str] = None,
    auto_approve: bool = False,
    custom_push_name: Optional[str] = None,
):
    """保存模板化提取结果"""
    try:
        await handle_processing_success(
            document_id=document_id,
            result=result,
            template_id=template.get("id"),
            tenant_id=user.tenant_id,
            generate_display_name=False,
            auto_approve=auto_approve,
            source_file_path=file_path,
            custom_push_name=custom_push_name,
        )
    except Exception as e:
        logger.error(f"保存模板提取结果失败: {e}")
