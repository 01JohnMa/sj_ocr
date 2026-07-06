"""CRM 集成入口。"""

import asyncio
from datetime import datetime
import ipaddress
import inspect
import mimetypes
import os
from pathlib import Path
import shutil
import socket
import tempfile
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urljoin, urlparse
import uuid

import aiofiles
import httpx
from fastapi import APIRouter, Depends, HTTPException
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
from api.routes.documents.helpers import push_to_feishu
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
CRM_MAX_JSON_FILES = 20
CRM_MAX_DOWNLOAD_REDIRECTS = 3
CRM_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
CRM_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp"}
CRM_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
}


class CrmSubmitFileItem(BaseModel):
    """CRM JSON 提交的文件 URL。"""

    url: str
    type: Optional[str] = None


class CrmSubmitRequest(BaseModel):
    """CRM 文档提交请求。"""

    template_id: str
    custom_push_name: Optional[str] = None
    file: Optional[List[CrmSubmitFileItem]] = None
    files: Optional[List[CrmSubmitFileItem]] = None


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


def _crm_submit_files(request: CrmSubmitRequest) -> List[CrmSubmitFileItem]:
    if request.file is not None and request.files is not None:
        raise ValidationError("file和files不能同时传")

    files = request.file if request.file is not None else request.files
    if not files:
        raise ValidationError("file不能为空")
    if len(files) > CRM_MAX_JSON_FILES:
        raise ValidationError(f"同一份文档最多支持{CRM_MAX_JSON_FILES}个文件URL")
    return files


def _host_addresses(host: str) -> List[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProcessingError(f"解析CRM文件域名失败: {host}") from exc

    addresses = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0]).split("%", 1)[0]
        try:
            addresses.append(ipaddress.ip_address(address))
        except ValueError:
            continue

    if not addresses:
        raise ProcessingError(f"解析CRM文件域名失败: {host}")
    return addresses


def _ensure_crm_download_host_is_public(host: str) -> None:
    normalized_host = (host or "").strip().strip("[]").lower()
    if normalized_host in {"localhost", "localhost.localdomain"}:
        raise ValidationError("文件URL不允许指向内网或本机地址")

    addresses = _host_addresses(normalized_host)
    if any(not address.is_global for address in addresses):
        raise ValidationError("文件URL不允许指向内网或本机地址")


def _validate_crm_file_url(raw_url: str, expected_host: Optional[str] = None) -> str:
    url = raw_url.strip() if raw_url else ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValidationError("文件URL必须是http或https地址")
    if parsed.username or parsed.password:
        raise ValidationError("文件URL不能包含用户名或密码")
    try:
        parsed.port
    except ValueError as exc:
        raise ValidationError("文件URL端口非法") from exc

    if expected_host and parsed.hostname.lower() != expected_host.lower():
        raise ValidationError("文件URL重定向不允许跨域")

    _ensure_crm_download_host_is_public(parsed.hostname)
    return url


def _extension_from_url_or_content_type(url: str, content_type: str) -> str:
    ext = os.path.splitext(unquote(urlparse(url).path))[1].lower()
    if ext in settings.allowed_extensions_list:
        return ext

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    ext = CRM_CONTENT_TYPE_EXTENSIONS.get(normalized_content_type)
    if ext in settings.allowed_extensions_list:
        return ext

    guessed_ext = mimetypes.guess_extension(normalized_content_type) if normalized_content_type else None
    if guessed_ext == ".jpe":
        guessed_ext = ".jpg"
    if guessed_ext and guessed_ext.lower() in settings.allowed_extensions_list:
        return guessed_ext.lower()

    raise FileTypeError(settings.ALLOWED_EXTENSIONS)


async def _download_crm_file_url(item: CrmSubmitFileItem, destination_dir: Path, index: int) -> Dict[str, Any]:
    url = _validate_crm_file_url(item.url)
    expected_host = urlparse(url).hostname
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        current_url = url
        for _redirect_count in range(CRM_MAX_DOWNLOAD_REDIRECTS + 1):
            try:
                current_url = _validate_crm_file_url(current_url, expected_host=expected_host)
                async with client.stream("GET", current_url) as response:
                    if response.status_code in CRM_REDIRECT_STATUS_CODES:
                        location = response.headers.get("location")
                        if not location:
                            response.raise_for_status()
                        current_url = urljoin(str(response.url), location)
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    file_ext = _extension_from_url_or_content_type(str(response.url), content_type)
                    destination = destination_dir / f"page_{index}{file_ext}"
                    total_size = 0
                    async with aiofiles.open(destination, "wb") as out_file:
                        async for chunk in response.aiter_bytes():
                            total_size += len(chunk)
                            if total_size > settings.MAX_FILE_SIZE:
                                raise FileSizeError(settings.MAX_FILE_SIZE / 1024 / 1024)
                            await out_file.write(chunk)
                    break
            except (FileSizeError, FileTypeError, ValidationError):
                raise
            except httpx.HTTPError as exc:
                raise ProcessingError(f"下载CRM文件失败: {current_url}") from exc
        else:
            raise ValidationError("文件URL重定向次数过多")

    return {
        "path": str(destination),
        "extension": file_ext,
        "content_type": (content_type or mimetypes.types_map.get(file_ext) or "application/octet-stream").split(";", 1)[0],
        "url": url,
    }


def _json_original_file_name(request: CrmSubmitRequest, file_ext: str) -> str:
    raw_name = (request.custom_push_name or "crm_url_document").strip() or "crm_url_document"
    return f"{Path(raw_name).stem[:100]}{file_ext}"


def _images_to_pdf_sync(image_paths: List[str], destination: str) -> None:
    from PIL import Image

    images = []
    try:
        for image_path in image_paths:
            with Image.open(image_path) as image:
                images.append(image.convert("RGB").copy())

        if not images:
            raise ValidationError("file不能为空")

        first_image, rest_images = images[0], images[1:]
        first_image.save(destination, "PDF", save_all=True, append_images=rest_images)
    finally:
        for image in images:
            image.close()


async def _prepare_crm_json_upload(document_id: str, request: CrmSubmitRequest) -> Dict[str, Any]:
    files = _crm_submit_files(request)
    os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
    file_path: Optional[str] = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"crm_{document_id}_") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            downloaded = []
            total_download_size = 0
            for index, item in enumerate(files, 1):
                downloaded_file = await _download_crm_file_url(item, temp_dir, index)
                total_download_size += os.path.getsize(downloaded_file["path"])
                if total_download_size > settings.MAX_FILE_SIZE:
                    raise FileSizeError(settings.MAX_FILE_SIZE / 1024 / 1024)
                downloaded.append(downloaded_file)

            if len(downloaded) == 1 and downloaded[0]["extension"] == ".pdf":
                file_ext = ".pdf"
                stored_filename = f"{document_id}{file_ext}"
                file_path = os.path.join(settings.UPLOAD_FOLDER, stored_filename)
                shutil.copyfile(downloaded[0]["path"], file_path)
            else:
                non_image = [item["url"] for item in downloaded if item["extension"] not in CRM_IMAGE_EXTENSIONS]
                if non_image:
                    raise ValidationError("多文件JSON提交只支持图片URL；单个PDF请只传一个URL")
                file_ext = ".pdf"
                stored_filename = f"{document_id}{file_ext}"
                file_path = os.path.join(settings.UPLOAD_FOLDER, stored_filename)
                await asyncio.to_thread(
                    _images_to_pdf_sync,
                    [item["path"] for item in downloaded],
                    file_path,
                )

        file_size = os.path.getsize(file_path)
        if file_size > settings.MAX_FILE_SIZE:
            raise FileSizeError(settings.MAX_FILE_SIZE / 1024 / 1024)

        return {
            "file_name": stored_filename,
            "original_file_name": _json_original_file_name(request, file_ext),
            "file_path": file_path,
            "file_size": file_size,
            "file_extension": file_ext,
            "file_type": "application/pdf",
            "mime_type": "application/pdf",
        }
    except Exception:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise


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
    request: CrmSubmitRequest,
    user: CurrentUser = Depends(get_crm_current_user),
):
    """CRM 文档提交入口：入队处理后等待 CRM 审核推送。"""
    try:
        _ensure_crm_admin(user)

        template = await template_service.get_template(request.template_id)
        if not template:
            raise HTTPException(status_code=404, detail="模板不存在")
        if not user.can_access_tenant(template.get("tenant_id")):
            raise AuthorizationError("无权使用此模板")
        _ensure_supported_crm_template(template)

        document_id = str(uuid.uuid4())
        upload_info = await _prepare_crm_json_upload(document_id, request)
        file_path = upload_info["file_path"]

        cleaned_push_name = request.custom_push_name.strip() if request.custom_push_name else None
        if cleaned_push_name and len(cleaned_push_name) > 100:
            cleaned_push_name = cleaned_push_name[:100]

        document_data = {
            "id": document_id,
            "user_id": user.user_id,
            "file_name": upload_info["file_name"],
            "original_file_name": upload_info["original_file_name"],
            "file_path": upload_info["file_path"],
            "file_size": upload_info["file_size"],
            "file_type": upload_info["file_type"],
            "file_extension": upload_info["file_extension"],
            "mime_type": upload_info["mime_type"],
            "status": "uploaded",
            "template_id": request.template_id,
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
