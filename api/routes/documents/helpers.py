# api/routes/documents/helpers.py
"""文档路由 - 辅助函数"""

import os
import json
import aiofiles
import re
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime
from uuid import uuid4
from fastapi import UploadFile
from loguru import logger

from config.settings import settings
from services.supabase_service import supabase_service
from services.template_service import template_service
from sdk.excel_template import fill_excel_template
from api.jobs import build_feishu_push_dedupe_key, has_feishu_push_record, record_feishu_push


async def save_upload_file(file: UploadFile, destination: str) -> int:
    """
    保存上传的文件
    
    Args:
        file: 上传的文件对象
        destination: 目标路径
        
    Returns:
        文件大小（字节）
    """
    async with aiofiles.open(destination, 'wb') as out_file:
        content = await file.read()
        await out_file.write(content)
        return len(content)


def normalize_review_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def parse_allowed_values(raw_values: object) -> list:
    if raw_values is None:
        return []
    if isinstance(raw_values, list):
        return [str(item) for item in raw_values if item is not None]
    if isinstance(raw_values, str):
        text = raw_values.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item is not None]
        except json.JSONDecodeError:
            pass
        return [text]
    return [str(raw_values)]


def validate_file_extension(filename: str) -> bool:
    """
    验证文件扩展名
    
    Args:
        filename: 文件名
        
    Returns:
        是否为允许的扩展名
    """
    ext = os.path.splitext(filename)[1].lower()
    return ext in settings.allowed_extensions_list


def is_auth_error(error: Exception) -> bool:
    """检测是否为认证/授权相关错误（token 过期等）"""
    error_str = str(error).lower()
    auth_keywords = ['jwt', 'token', '401', '502', 'expired', 'invalid', 'unauthorized']
    return any(keyword in error_str for keyword in auth_keywords)


def raise_auth_or_processing_error(error: Exception, message: str) -> None:
    """
    统一 auth error 处理：若为认证错误则抛 AuthenticationError，否则抛 ProcessingError。
    用于替代各端点 except 块中重复的 _is_auth_error 检查。
    """
    from api.exceptions import AuthenticationError, ProcessingError
    if is_auth_error(error):
        logger.warning(f"Token 认证失败，需要重新登录: {error}")
        raise AuthenticationError("登录已过期，请重新登录")
    raise ProcessingError(f"{message}: {str(error)}")


def _safe_excel_output_name(name: str) -> str:
    stem = Path(name or "excel_output").stem
    stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._-")
    return f"{(stem or 'excel_output')[:80]}.xlsx"


def _build_excel_output_path(document_id: str, file_name_for_push: str) -> str:
    output_dir = Path(settings.UPLOAD_FOLDER) / "excel_outputs" / f"{document_id}_{uuid4().hex}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / _safe_excel_output_name(file_name_for_push))


def _generate_excel_output_attachment(
    template: dict,
    extraction_data: dict,
    document_id: str,
    file_name_for_push: str,
) -> Optional[str]:
    output_mode = template.get("output_mode") or "bitable"
    if output_mode not in {"excel_template", "both"}:
        return None

    excel_template_path = template.get("excel_template_path")
    if not excel_template_path:
        return None
    if not os.path.exists(excel_template_path):
        logger.warning(f"固定 Excel 模板文件不存在，跳过生成: {excel_template_path}")
        return None

    output_path = _build_excel_output_path(document_id, file_name_for_push)
    try:
        result = fill_excel_template(excel_template_path, output_path, extraction_data)
        if result.missing_fields:
            logger.warning(
                "固定 Excel 模板存在未填充字段: document_id={}, fields={}",
                document_id,
                result.missing_fields,
            )
        return output_path
    except Exception as exc:
        logger.warning(f"固定 Excel 模板生成失败，跳过附件生成: {exc}")
        output_parent = Path(output_path).parent
        if output_parent.exists():
            shutil.rmtree(output_parent, ignore_errors=True)
        return None


async def push_to_feishu(
    template: dict,
    extraction_data: dict,
    display_name: Optional[str],
    document_id: str,
    source_file_path: Optional[str | list] = None,
    log_prefix: str = "",
    extra_template: Optional[dict] = None,
    custom_push_name: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    extra_data: Optional[dict] = None,
    extra_field_mapping: Optional[dict] = None,
) -> bool:
    """
    统一飞书推送逻辑：构建 field_mapping → 生成文件名 → 上传附件 → push_by_template

    文件名优先级：
    1. custom_push_name（用户上传时指定）
    2. 默认：{模板名}_YYYYMMDD_HHmmss，merge 时为 {模板A名}+{模板B名}_YYYYMMDD_HHmmss

    Args:
        source_file_path: 单个文件路径（str）或多个文件路径列表（list），merge 时传 [fp_a, fp_b]
        custom_push_name: 用户自定义的飞书推送文件名，优先于默认生成规则
        extra_data: 只参与本次推送的额外字段，不写入提取结果
        extra_field_mapping: extra_data 对应的飞书列映射
    """
    from services.feishu_service import feishu_service

    bitable_token = template.get("feishu_bitable_token")
    table_id = template.get("feishu_table_id")
    effective_dedupe_key = dedupe_key or build_feishu_push_dedupe_key(document_id, template.get("id"), extraction_data)

    if await has_feishu_push_record(effective_dedupe_key):
        logger.info(f"{log_prefix}检测到重复飞书推送，跳过: {document_id}")
        return True

    if not (bitable_token and table_id):
        logger.info(f"{log_prefix}模板未配置飞书，跳过推送: {document_id}")
        return False

    field_mapping = template_service.build_field_mapping(template)

    # merge 模式：合并附加模板的字段映射（B 的字段也推到同一行）
    if extra_template:
        extra_mapping = template_service.build_field_mapping(extra_template)
        field_mapping = {**extra_mapping, **field_mapping}  # A 优先（覆盖同名 key）
    if extra_field_mapping:
        field_mapping = {**field_mapping, **extra_field_mapping}

    push_data = {**extraction_data}
    if extra_data:
        push_data.update(extra_data)

    # 文件名优先使用用户自定义，否则按模板名+时间戳规则生成
    if custom_push_name and custom_push_name.strip():
        file_name_for_push = custom_push_name.strip()
    else:
        template_name = str(template.get("name") or "文档")
        if extra_template:
            template_name = f"{template_name}+{extra_template.get('name', '')}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name_for_push = f"{template_name}_{timestamp}"

    if "file_name" not in field_mapping:
        field_mapping["file_name"] = "文件名"
    push_data["file_name"] = file_name_for_push

    generated_excel_path = _generate_excel_output_attachment(
        template=template,
        extraction_data=extraction_data,
        document_id=document_id,
        file_name_for_push=file_name_for_push,
    )

    try:
        # 附件上传：传入的文件全部上传，调用方负责按 push_attachment 过滤。
        # 固定 Excel 是模板输出，不受原始文件 push_attachment 开关影响。
        file_paths = (
            source_file_path.copy() if isinstance(source_file_path, list)
            else ([source_file_path] if source_file_path else [])
        )
        if generated_excel_path:
            file_paths.append(generated_excel_path)

        file_tokens = []
        for fp in file_paths:
            if fp and os.path.exists(fp):
                token = await feishu_service._upload_file_to_feishu(fp, bitable_token)
                if token:
                    file_tokens.append({"file_token": token})
        if file_tokens:
            push_data["attachment"] = file_tokens
            field_mapping["attachment"] = "文件"

        success = await feishu_service.push_by_template(push_data, field_mapping, bitable_token, table_id)
        if success:
            await record_feishu_push(effective_dedupe_key, document_id, template.get("id"))
            logger.info(f"{log_prefix}飞书推送成功: {document_id}")
            return True
        else:
            logger.warning(f"{log_prefix}飞书推送失败: {document_id}")
            return False
    finally:
        if generated_excel_path:
            shutil.rmtree(Path(generated_excel_path).parent, ignore_errors=True)


async def handle_processing_success(
    document_id: str,
    result: dict,
    template_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    generate_display_name: bool = True,
    auto_approve: bool = False,
    source_file_path: Optional[str] = None,
    custom_push_name: Optional[str] = None,
    skip_feishu_push: bool = False,
) -> None:
    """处理成功时的统一逻辑"""
    await supabase_service.save_extraction_result(
        document_id=document_id,
        document_type=result.get("document_type") or result.get("template_name", "未知"),
        extraction_data=result["extraction_data"],
        template_id=template_id,
    )
    logger.info(f"提取结果已保存: {document_id}")

    display_name = None
    if generate_display_name:
        display_name = supabase_service.generate_display_name(
            document_type=result.get("document_type") or result.get("template_name"),
            extraction_data=result["extraction_data"]
        )

    doc_status = "completed" if auto_approve else "pending_review"
    update_data = {
        "status": doc_status,
        "document_type": result.get("document_type") or result.get("template_name"),
        "template_id": template_id,
        "tenant_id": tenant_id,
        "ocr_text": result.get("ocr_text", ""),
        "ocr_confidence": result.get("ocr_confidence"),
        "processed_at": datetime.now().isoformat(),
        "error_message": None
    }
    if display_name:
        update_data["display_name"] = display_name

    await supabase_service.update_document(document_id, update_data)
    logger.info(
        f"后台处理完成: {document_id}, 状态: {doc_status}"
        + (f", 显示名称: {display_name}" if display_name else "")
    )

    if auto_approve and not skip_feishu_push:
        try:
            template = None
            if template_id:
                template = await template_service.get_template_with_details(template_id)
            elif tenant_id and (result.get("document_type") or result.get("template_name")):
                template = await template_service.get_template_by_code(
                    tenant_id,
                    result.get("document_type") or result.get("template_name")
                )

            if template:
                await push_to_feishu(
                    template=template,
                    extraction_data=result.get("extraction_data", {}),
                    display_name=display_name,
                    document_id=document_id,
                    source_file_path=source_file_path,
                    custom_push_name=custom_push_name,
                )
            else:
                logger.info(f"auto_approve 单模板模板未配置飞书，跳过推送: {document_id}")
        except Exception as feishu_error:
            logger.warning(f"auto_approve 单模板飞书推送失败（不影响结果）: {feishu_error}")


async def handle_processing_failure(document_id: str, error_message: str) -> None:
    """处理失败时的统一逻辑"""
    await supabase_service.update_document_status(
        document_id, "failed", error_message=error_message
    )
    logger.error(f"后台处理失败: {document_id} - {error_message}")


async def handle_processing_exception(document_id: str, exception: Exception) -> None:
    """处理异常时的统一逻辑"""
    logger.error(f"后台任务异常: {document_id} — {exception}")
    for attempt in range(2):
        try:
            await supabase_service.update_document_status(document_id, "failed", str(exception))
            return
        except Exception as update_err:
            if attempt == 0:
                logger.error(
                    f"更新失败状态时出错（将重试）: {document_id} — {update_err}"
                )
            else:
                logger.critical(
                    f"更新失败状态重试仍失败，文档可能永久卡死: {document_id} — {update_err}"
                )
