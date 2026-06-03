"""文档处理独立 worker。

API 进程只负责创建 processing_jobs；本模块负责认领并执行重 OCR/VLM/LLM 任务。
"""

import asyncio
import os
import socket
from typing import Any, Dict, List, Optional

from loguru import logger

from api.dependencies.auth import CurrentUser
from api.jobs import claim_next_job, update_job
from api.routes.documents.batch import BatchItem, _run_batch_job
from api.routes.documents.process import process_document_task
from config.settings import settings
from services.ocr_service import ocr_service
from services.supabase_service import supabase_service


def build_worker_id() -> str:
    """构建稳定可读的 worker id。"""
    if settings.DOC_WORKER_ID:
        return settings.DOC_WORKER_ID
    return f"{socket.gethostname()}:{os.getpid()}"


async def _get_first_document(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    document_ids = job.get("document_ids") or []
    if document_ids:
        return await supabase_service.get_document(document_ids[0])

    items = job.get("items") or []
    if items and items[0].get("document_id"):
        return await supabase_service.get_document(items[0]["document_id"])

    return None


async def _build_worker_user(job: Dict[str, Any]) -> CurrentUser:
    first_doc = await _get_first_document(job)
    return CurrentUser(
        user_id=str(job.get("created_by") or (first_doc or {}).get("user_id") or ""),
        token="worker",
        tenant_id=(first_doc or {}).get("tenant_id"),
        role="user",
    )


def _build_batch_items(raw_items: List[Dict[str, Any]]) -> List[BatchItem]:
    return [
        BatchItem(
            document_id=item["document_id"],
            template_id=item["template_id"],
            paired_document_id=item.get("paired_document_id"),
            paired_template_id=item.get("paired_template_id"),
            custom_push_name=item.get("custom_push_name"),
        )
        for item in raw_items
    ]


async def execute_job(job: Dict[str, Any]) -> None:
    """执行一个已认领 job。"""
    job_id = str(job.get("job_id"))
    job_type = job.get("job_type")

    if job_type == "batch":
        items = _build_batch_items(job.get("items") or [])
        if not items:
            await update_job(job_id, "failed", error="批量任务缺少可执行 items")
            return
        user = await _build_worker_user(job)
        if not user.tenant_id:
            await update_job(job_id, "failed", error="批量任务缺少 tenant_id")
            return
        await _run_batch_job(job_id, items, user)
        return

    document_ids = job.get("document_ids") or []
    if not document_ids:
        await update_job(job_id, "failed", error="任务缺少 document_ids")
        return

    document_id = document_ids[0]
    document = await supabase_service.get_document(document_id)
    if not document:
        await update_job(job_id, "failed", error=f"文档不存在: {document_id}")
        return

    await process_document_task(
        document_id=document_id,
        file_path=document.get("file_path", ""),
        template_id=document.get("template_id"),
        tenant_id=document.get("tenant_id"),
        custom_push_name=document.get("custom_push_name"),
        job_id=job_id,
    )


async def poll_once(worker_id: str) -> bool:
    """认领并执行一个 job。返回本轮是否执行了任务。"""
    job = await claim_next_job(worker_id, settings.DOC_WORKER_STALE_LOCK_SECONDS)
    if not job:
        return False

    logger.info(f"[worker={worker_id}] 认领任务: {job.get('job_id')} type={job.get('job_type')}")
    try:
        await execute_job(job)
    except Exception as exc:
        logger.opt(exception=exc).error(f"[worker={worker_id}] 任务执行异常: {job.get('job_id')}")
        await update_job(str(job.get("job_id")), "failed", error=str(exc))
    return True


async def run_forever(worker_id: Optional[str] = None) -> None:
    """持续轮询并执行任务。"""
    effective_worker_id = worker_id or build_worker_id()
    logger.info(f"文档 worker 启动: {effective_worker_id}")

    try:
        await supabase_service.initialize()
    except Exception as exc:
        logger.opt(exception=exc).warning("Supabase 初始化失败，worker 将在任务执行时继续尝试")

    if settings.OCR_ENABLED:
        try:
            await ocr_service.initialize()
        except Exception as exc:
            logger.opt(exception=exc).warning("OCR 初始化失败，worker 将在任务执行时继续尝试")

    while True:
        did_work = await poll_once(effective_worker_id)
        if not did_work:
            await asyncio.sleep(settings.DOC_WORKER_POLL_INTERVAL_SECONDS)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
