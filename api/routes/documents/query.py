# api/routes/documents/query.py
"""文档路由 - 查询相关端点"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional
from loguru import logger
import inspect
import os

from services.supabase_service import supabase_service
from services.template_service import template_service
from api.dependencies.auth import get_current_user, get_crm_current_user, CurrentUser
from api.exceptions import DocumentNotFoundError, FileNotFoundError, ProcessingError
from .helpers import parse_allowed_values, raise_auth_or_processing_error

router = APIRouter()


async def _run_supabase(fn):
    runner = getattr(supabase_service, "_run_sync", None)
    if runner is not None and inspect.iscoroutinefunction(runner):
        return await runner(fn)
    return fn()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: str,
    user: CurrentUser = Depends(get_crm_current_user)
):
    """
    获取文档处理状态（需要登录）
    
    使用 service_role 查询，手动验证用户权限：
    - 普通用户只能访问自己的文档
    - 租户管理员可以访问本租户所有文档
    - 超级管理员可以访问所有文档
    """
    try:
        # 使用 service_role 查询（绕过 RLS）
        result = await _run_supabase(
            lambda: supabase_service.client.table("documents").select("*").eq("id", document_id).execute()
        )
        document = result.data[0] if result.data else None
        
        if not document:
            raise DocumentNotFoundError(document_id)
        
        # 手动验证权限
        doc_user_id = document.get("user_id")
        doc_tenant_id = document.get("tenant_id")
        
        # 超级管理员可以访问所有文档
        if user.is_super_admin():
            pass  # 允许访问
        # 租户管理员可以访问本租户的文档
        elif user.is_tenant_admin() and doc_tenant_id == user.tenant_id:
            pass  # 允许访问
        # 普通用户只能访问自己的文档
        elif doc_user_id != user.user_id:
            raise DocumentNotFoundError(document_id)
        
        return {
            "document_id": document_id,
            "status": document.get("status", "unknown"),
            "document_type": document.get("document_type"),
            "display_name": document.get("display_name"),
            "original_file_name": document.get("original_file_name"),
            "error_message": document.get("error_message"),
            "created_at": document.get("created_at"),
            "updated_at": document.get("updated_at"),
            "processed_at": document.get("processed_at")
        }
        
    except DocumentNotFoundError:
        raise
    except Exception as e:
        raise_auth_or_processing_error(e, "获取状态失败")


def _check_document_access(document: dict, user: CurrentUser, document_id: str):
    """
    验证用户是否有权访问文档
    
    权限规则：
    - 超级管理员可以访问所有文档
    - 租户管理员可以访问本租户的文档
    - 普通用户只能访问自己的文档
    """
    doc_user_id = document.get("user_id")
    doc_tenant_id = document.get("tenant_id")
    
    if user.is_super_admin():
        return  # 超级管理员可以访问所有文档
    if user.is_tenant_admin() and doc_tenant_id == user.tenant_id:
        return  # 租户管理员可以访问本租户的文档
    if doc_user_id == user.user_id:
        return  # 普通用户可以访问自己的文档
    
    raise DocumentNotFoundError(document_id)


@router.get("/{document_id}/result")
async def get_extraction_result(
    document_id: str,
    user: CurrentUser = Depends(get_crm_current_user)
):
    """
    获取提取结果（需要登录）
    
    返回状态码：
    - 200: 成功返回结果
    - 202: 文档正在处理中（前端应继续轮询）
    - 404: 文档不存在或无权访问
    """
    try:
        # 使用 service_role 查询（绕过 RLS），手动验证权限
        doc_result = await _run_supabase(
            lambda: supabase_service.client.table("documents").select("*").eq("id", document_id).execute()
        )
        document = doc_result.data[0] if doc_result.data else None
        
        if not document:
            raise DocumentNotFoundError(document_id)
        
        # 验证用户权限
        _check_document_access(document, user, document_id)
        
        document_type = document.get("document_type")
        doc_status = document.get("status")

        # 优先用 template_id 查 target_table，fallback 到 TABLE_MAP
        table_name = await _maybe_await(supabase_service.resolve_table_name(
            template_id=document.get("template_id"),
            document_type=document_type,
        ))
        
        # 如果文档正在处理中或尚未有类型
        if not document_type:
            if doc_status in ("uploaded", "queued", "processing"):
                message = "文档正在排队中，请稍后重试" if doc_status == "queued" else "文档正在处理中，请稍后重试"
                return JSONResponse(
                    status_code=202,
                    content={
                        "document_id": document_id,
                        "status": doc_status,
                        "message": message
                    }
                )
            elif doc_status in ("completed", "pending_review"):
                # 兜底逻辑：尝试从所有结果表查询
                logger.warning(f"文档 {document_id} 状态为 {doc_status} 但 document_type 为空")
                
                result = None
                inferred_type = None
                for type_name, tbl_name in [("检测报告", "inspection_reports"), ("快递单", "expresses"), ("抽样单", "sampling_forms")]:
                    try:
                        query_result = await _run_supabase(
                            lambda tbl_name=tbl_name: supabase_service.client.table(tbl_name).select("*").eq("document_id", document_id).execute()
                        )
                        if query_result.data:
                            result = query_result.data[0]
                            inferred_type = type_name
                            logger.info(f"从 {tbl_name} 表找到结果，推断文档类型为: {type_name}")
                            break
                    except Exception as e:
                        logger.debug(f"查询 {tbl_name} 失败: {e}")
                        continue
                
                if result and inferred_type:
                    try:
                        await _run_supabase(
                            lambda: supabase_service.client.table("documents").update({"document_type": inferred_type}).eq("id", document_id).execute()
                        )
                        logger.info(f"已自动修复文档 {document_id} 的 document_type")
                    except Exception as e:
                        logger.warning(f"自动修复 document_type 失败: {e}")
                    
                    document_type = inferred_type
                else:
                    return JSONResponse(
                        status_code=202,
                        content={
                            "document_id": document_id,
                            "status": doc_status,
                            "message": "提取结果正在同步中，请稍后重试"
                        }
                    )
            elif doc_status == "failed":
                raise HTTPException(status_code=422, detail=document.get("error_message") or "文档处理失败")
            else:
                return JSONResponse(
                    status_code=202,
                    content={
                        "document_id": document_id,
                        "status": doc_status or "unknown",
                        "message": "文档尚未完成处理，请稍后重试"
                    }
                )
        else:
            result = None
        
        # 按 document_type 查询（复用上面已解析的 table_name）
        if result is None:
            if table_name:
                result_query = await _run_supabase(
                    lambda: supabase_service.client.table(table_name).select("*").eq("document_id", document_id).execute()
                )
                result = result_query.data[0] if result_query.data else None
        
        if not result:
            if doc_status in ("completed", "pending_review"):
                logger.warning(f"文档 {document_id} 状态为 {doc_status} 但提取结果尚未查询到")
                return JSONResponse(
                    status_code=202,
                    content={
                        "document_id": document_id,
                        "status": doc_status,
                        "message": "提取结果正在同步中，请稍后重试"
                    }
                )
            elif doc_status == "failed":
                raise HTTPException(status_code=422, detail=document.get("error_message") or "文档处理失败")
            else:
                message = "文档正在排队中，请稍后重试" if doc_status == "queued" else "文档尚未完成处理，请稍后重试"
                return JSONResponse(
                    status_code=202,
                    content={
                        "document_id": document_id,
                        "status": doc_status or "unknown",
                        "message": message
                    }
                )
        
        ocr_text = document.get("ocr_text") or ""

        # 从模板字段中收集：
        #   1. review_hint_fields — 有 review_allowed_values 的字段，用于前端保存前提示
        #   2. template_fields    — 完整白名单字段列表，供前端详情页纯模板驱动渲染
        review_hint_fields = []
        template_fields = []
        template_id = document.get("template_id")
        tenant_id = document.get("tenant_id")
        try:
            fields_list: list = []
            if template_id:
                # 直接查询字段表，避免 get_template_with_details 中复杂的 merge_rules 关联查询
                fields_list = await template_service.get_template_fields(template_id)
            elif tenant_id and document_type:
                template = await template_service.get_template_by_code(tenant_id, document_type)
                if template:
                    fields_list = template.get("template_fields") or []
            for field in fields_list:
                allowed = parse_allowed_values(field.get("review_allowed_values"))
                if allowed:
                    review_hint_fields.append({
                        "field_key": field.get("field_key"),
                        "field_label": field.get("field_label") or field.get("field_key"),
                        "allowed_values": allowed,
                    })
            # 构造前端渲染所需白名单字段列表（按 sort_order 升序）
            template_fields = [
                {
                    "field_key": f.get("field_key"),
                    "field_label": f.get("field_label") or f.get("field_key"),
                    "field_type": f.get("field_type", "text"),
                    "is_required": bool(f.get("is_required", False)),
                    "sort_order": f.get("sort_order", 0),
                    "review_enforced": bool(f.get("review_enforced", False)),
                    "review_allowed_values": parse_allowed_values(f.get("review_allowed_values")),
                }
                for f in sorted(fields_list, key=lambda x: x.get("sort_order", 0))
            ]
        except Exception as hint_err:
            logger.warning(f"获取模板字段失败（不影响主流程）: {hint_err}")

        return {
            "document_id": document_id,
            "document_type": document_type,
            "extraction_data": result,
            "ocr_text": ocr_text[:1000] if ocr_text else "",
            "ocr_confidence": document.get("ocr_confidence"),
            "created_at": result.get("created_at"),
            "is_validated": result.get("is_validated", False),
            "review_hint_fields": review_hint_fields,
            "template_fields": template_fields,
        }
        
    except (DocumentNotFoundError, HTTPException):
        raise
    except Exception as e:
        raise_auth_or_processing_error(e, "获取结果失败")


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user)
):
    """
    下载原始文档（需要登录）
    """
    try:
        # 使用 service_role 查询，手动验证权限
        result = await _run_supabase(
            lambda: supabase_service.client.table("documents").select("*").eq("id", document_id).execute()
        )
        document = result.data[0] if result.data else None
        
        if not document:
            raise DocumentNotFoundError(document_id)
        
        # 验证用户权限
        _check_document_access(document, user, document_id)
        
        file_path = document.get("file_path")
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        
        return FileResponse(
            path=file_path,
            filename=document.get("original_file_name", document.get("file_name")),
            media_type=document.get("mime_type", "application/octet-stream")
        )
        
    except (DocumentNotFoundError, FileNotFoundError):
        raise
    except Exception as e:
        raise_auth_or_processing_error(e, "下载失败")


@router.get("/")
async def list_documents(
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user)
):
    """
    列出文档（需要登录）
    
    权限过滤：
    - 超级管理员可以看到所有文档
    - 租户管理员可以看到本租户的文档
    - 普通用户只能看到自己的文档
    """
    try:
        # 使用 service_role 查询，手动添加权限过滤
        query = supabase_service.client.table("documents").select("*")
        
        # 根据用户角色添加过滤条件
        if user.is_super_admin():
            pass  # 超级管理员可以看到所有文档
        elif user.is_tenant_admin() and user.tenant_id:
            query = query.eq("tenant_id", user.tenant_id)
        else:
            query = query.eq("user_id", user.user_id)
        
        if status:
            query = query.eq("status", status)
        if document_type:
            query = query.eq("document_type", document_type)
        
        offset = (page - 1) * limit
        result = await _run_supabase(
            lambda: query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        )
        
        # 统计总数（使用相同的权限过滤）
        count_query = supabase_service.client.table("documents").select("id", count="exact")
        
        # 根据用户角色添加过滤条件（与上面保持一致）
        if user.is_super_admin():
            pass
        elif user.is_tenant_admin() and user.tenant_id:
            count_query = count_query.eq("tenant_id", user.tenant_id)
        else:
            count_query = count_query.eq("user_id", user.user_id)
        
        if status:
            count_query = count_query.eq("status", status)
        if document_type:
            count_query = count_query.eq("document_type", document_type)
        count_result = await _run_supabase(count_query.execute)
        
        total = count_result.count or 0
        
        return {
            "items": result.data or [],
            "total": total,
            "page": page,
            "limit": limit,
            "has_more": (page * limit) < total
        }
        
    except Exception as e:
        logger.error(f"查询文档失败: {e}")
        raise_auth_or_processing_error(e, "查询失败")


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user)
):
    """
    删除文档（需要登录）
    """
    try:
        # 使用 service_role 查询，手动验证权限
        result = await _run_supabase(
            lambda: supabase_service.client.table("documents").select("*").eq("id", document_id).execute()
        )
        document = result.data[0] if result.data else None
        
        if not document:
            raise DocumentNotFoundError(document_id)
        
        # 验证用户权限（只有文档所有者或管理员可以删除）
        _check_document_access(document, user, document_id)
        
        # 删除文件
        file_path = document.get("file_path")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        
        # 删除数据库记录
        await _run_supabase(
            lambda: supabase_service.client.table("documents").delete().eq("id", document_id).execute()
        )
        
        return {
            "document_id": document_id,
            "message": "文档删除成功"
        }
        
    except DocumentNotFoundError:
        raise
    except Exception as e:
        raise_auth_or_processing_error(e, "删除失败")
