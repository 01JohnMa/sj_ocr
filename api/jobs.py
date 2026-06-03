# api/jobs.py
"""持久化 Job 管理器 - 用于跟踪异步处理任务状态与防重记录"""

import hashlib
import inspect
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from services.supabase_service import supabase_service


# 阶段 → 进度百分比映射
STAGE_PROGRESS: Dict[str, int] = {
    "queued": 0,
    "pending": 5,
    "ocr": 30,
    "llm": 70,
    "saving": 90,
    "completed": 100,
    "failed": -1,
}

_ACTIVE_JOB_STATUSES = {"queued", "pending", "processing"}


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _job_table():
    return supabase_service.client.table("processing_jobs")


def _push_record_table():
    return supabase_service.client.table("feishu_push_records")


async def _run_db(fn):
    runner = getattr(supabase_service, "_run_sync", None)
    if runner is not None and inspect.iscoroutinefunction(runner):
        return await runner(fn)
    return fn()


def _normalize_job_record(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    normalized = dict(row)
    normalized.setdefault("job_id", normalized.get("id"))
    normalized.setdefault("status", "queued")
    normalized.setdefault("stage", "queued")
    normalized.setdefault("progress", STAGE_PROGRESS.get(normalized["stage"], 0))
    normalized.setdefault("document_ids", [])
    normalized.setdefault("items", [])
    normalized.setdefault("total", len(normalized.get("items") or []))
    normalized.setdefault("completed_count", 0)
    normalized.setdefault("error", None)
    return normalized


def build_batch_dedupe_key(items: list[dict]) -> str:
    """为批量处理构建稳定的去重键，保留任务顺序。"""
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        normalized_items.append({
            "document_id": item.get("document_id"),
            "template_id": item.get("template_id"),
            "paired_document_id": item.get("paired_document_id"),
            "paired_template_id": item.get("paired_template_id"),
            "custom_push_name": item.get("custom_push_name") or None,
        })
    payload = json.dumps(normalized_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"batch:{digest}"


async def create_job(
    batch_items: list | None = None,
    *,
    job_type: str = "batch",
    created_by: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    related_document_ids: Optional[list[str]] = None,
) -> str:
    """创建持久化 Job，返回 job_id。"""
    job_id = str(uuid.uuid4())
    items = batch_items or []
    payload: Dict[str, Any] = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "stage": "queued",
        "progress": STAGE_PROGRESS["queued"],
        "document_ids": related_document_ids or [],
        "items": items,
        "total": len(items),
        "completed_count": 0,
        "error": None,
        "created_by": created_by,
        "dedupe_key": dedupe_key,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }
    result = await _run_db(
        lambda: _job_table().insert(payload).execute()
    )
    created = (result.data or [{}])[0]
    return created.get("job_id") or job_id


async def update_job(job_id: str, stage: str, **extra: Any) -> None:
    """更新 Job 阶段和附加字段。"""
    if stage == "failed":
        status = "failed"
    elif stage == "completed":
        status = "completed"
    elif stage in ("queued", "pending"):
        status = "queued"
    else:
        status = "processing"

    payload = {
        "status": status,
        "stage": stage,
        "progress": STAGE_PROGRESS.get(stage, 0),
        "updated_at": _utc_now_iso(),
        **extra,
    }
    if status in ("completed", "failed"):
        payload.setdefault("finished_at", _utc_now_iso())
        payload.setdefault("locked_by", None)
        payload.setdefault("locked_at", None)
    await _run_db(
        lambda: _job_table().update(payload).eq("job_id", job_id).execute()
    )


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 获取 Job，不存在返回 None。"""
    result = await _run_db(
        lambda: _job_table().select("*").eq("job_id", job_id).limit(1).execute()
    )
    row = result.data[0] if result.data else None
    return _normalize_job_record(row)


async def claim_next_job(worker_id: str, stale_after_seconds: int = 1800) -> Optional[Dict[str, Any]]:
    """原子认领下一个 queued job。没有可执行任务时返回 None。"""
    result = await _run_db(
        lambda: supabase_service.client.rpc(
            "claim_next_processing_job",
            {
                "p_worker_id": worker_id,
                "p_stale_after_seconds": stale_after_seconds,
            },
        ).execute()
    )
    data = result.data or []
    if isinstance(data, dict):
        row = data
    else:
        row = data[0] if data else None
    return _normalize_job_record(row)


async def find_job_by_dedupe_key(dedupe_key: str) -> Optional[Dict[str, Any]]:
    """按去重键查找仍处于活动中的任务。"""
    result = await _run_db(
        lambda: _job_table()
        .select("*")
        .eq("dedupe_key", dedupe_key)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    row = result.data[0] if result.data else None
    job = _normalize_job_record(row)
    if job and job.get("status") in _ACTIVE_JOB_STATUSES:
        return job
    return None


async def update_batch_item(job_id: str, index: int, status: str, error: str = None, document_ids: list = None) -> None:
    """更新批量 Job 中某一项的状态，并自动计算进度。"""
    job = await get_job(job_id)
    if not job or "items" not in job:
        return

    items = list(job.get("items") or [])
    if index < 0 or index >= len(items):
        return

    item = dict(items[index])
    item["status"] = status
    if error is not None:
        item["error"] = error
    if document_ids:
        item["document_ids"] = document_ids
    items[index] = item

    accumulated_document_ids = list(job.get("document_ids") or [])
    if document_ids:
        accumulated_document_ids.extend(document_ids)
        accumulated_document_ids = list(dict.fromkeys(accumulated_document_ids))

    completed = sum(1 for it in items if it.get("status") in ("completed", "failed"))
    total = job.get("total", len(items)) or len(items)
    progress = int(completed / total * 100) if total > 0 else 0

    if completed == total and total > 0:
        overall_status = "failed" if all(it.get("status") == "failed" for it in items) else "completed"
        overall_stage = "failed" if overall_status == "failed" else "completed"
        progress = STAGE_PROGRESS[overall_stage] if overall_status == "completed" else progress
    else:
        overall_status = "processing"
        overall_stage = "ocr"

    payload = {
        "items": items,
        "document_ids": accumulated_document_ids,
        "completed_count": completed,
        "total": total,
        "progress": progress,
        "status": overall_status,
        "stage": overall_stage,
        "updated_at": _utc_now_iso(),
    }
    if status == "failed" and error:
        payload["error"] = error

    await _run_db(
        lambda: _job_table().update(payload).eq("job_id", job_id).execute()
    )


async def has_feishu_push_record(dedupe_key: str) -> bool:
    """检查飞书推送是否已记录。"""
    result = await _run_db(
        lambda: _push_record_table().select("dedupe_key").eq("dedupe_key", dedupe_key).limit(1).execute()
    )
    return bool(result.data)


async def record_feishu_push(dedupe_key: str, document_id: str, template_id: Optional[str] = None) -> None:
    """记录飞书推送成功，供幂等防重使用。"""
    payload = {
        "dedupe_key": dedupe_key,
        "document_id": document_id,
        "template_id": template_id,
        "created_at": _utc_now_iso(),
    }
    await _run_db(
        lambda: _push_record_table().insert(payload).execute()
    )


def build_feishu_push_dedupe_key(document_id: str, template_id: Optional[str], extraction_data: dict) -> str:
    """构建飞书推送去重键。"""
    serialized = json.dumps(extraction_data or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return f"feishu:{document_id}:{template_id or 'none'}:{digest}"
