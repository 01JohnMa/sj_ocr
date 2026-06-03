# tests/routes/test_process.py
"""文档处理路由测试 — POST /api/documents/{id}/process、/process-with-template"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import DOCUMENT_ID, TENANT_ID, USER_ID, MOCK_DOCUMENT, TEMPLATE_ID


def _patch_supabase():
    return patch("api.routes.documents.process.supabase_service")


def _patch_supabase_in_helpers():
    """Patch supabase where helpers imports it (used by handler tests)."""
    return patch("api.routes.documents.helpers.supabase_service")


def _patch_workflow():
    return patch("api.routes.documents.process.ocr_workflow")


# ============ process ============

class TestProcessDocument:
    """POST /api/documents/{document_id}/process"""

    def test_process_async_accepted(self, client):
        """异步处理返回 200，并进入排队状态"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf", "template_id": TEMPLATE_ID}
        with _patch_supabase() as mock_svc, \
             _patch_workflow(), \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process.create_job", new_callable=AsyncMock, return_value="job-001"), \
             patch("api.routes.documents.process.process_document_task", new_callable=AsyncMock) as mock_task:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == DOCUMENT_ID
        assert data["job_id"] == "job-001"
        assert data["status"] == "queued"
        mock_task.assert_not_called()

    def test_process_document_not_found(self, client):
        """文档不存在返回 404"""
        with _patch_supabase() as mock_svc, \
             patch("os.listdir", return_value=[]):
            mock_svc.get_document = AsyncMock(return_value=None)
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 404

    def test_process_file_not_found(self, client):
        """文件不存在返回 404"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/missing.pdf"}
        with _patch_supabase() as mock_svc, \
             patch("os.path.exists", return_value=False):
            mock_svc.get_document = AsyncMock(return_value=doc)
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 404

    def test_process_unauthenticated(self, unauth_client):
        """未认证返回 401"""
        resp = unauth_client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 401

    def test_process_sync_success(self, client):
        """同步处理返回 200 和结果"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf", "template_id": TEMPLATE_ID}
        mock_result = {
            "success": True,
            "document_type": "inspection_report",
            "extraction_data": {"sample_name": "LED灯"},
            "template_name": "检测报告",
        }
        with _patch_supabase() as mock_svc, \
             _patch_workflow() as mock_wf, \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process._handle_processing_success", new_callable=AsyncMock), \
             patch("api.routes.documents.process.template_service") as mock_ts:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            mock_wf.process_with_template = AsyncMock(return_value=mock_result)
            mock_ts.get_template = AsyncMock(return_value={"id": TEMPLATE_ID, "auto_approve": False})
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process?sync=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_process_async_is_idempotent_when_already_processing(self, client):
        """文档已在处理中时，不应重复提交后台任务"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf", "template_id": TEMPLATE_ID, "status": "processing"}
        with _patch_supabase() as mock_svc, \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process.process_document_task", new_callable=AsyncMock) as mock_task:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_id"] == DOCUMENT_ID
        assert data["status"] == "processing"
        assert "已在处理中" in data["message"]
        mock_svc.update_document_status.assert_not_called()
        mock_task.assert_not_awaited()

    def test_process_async_returns_queued_when_document_first_submitted(self, client):
        """首次异步提交应返回 queued，等待后台槽位执行"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf", "template_id": TEMPLATE_ID, "status": "uploaded"}
        with _patch_supabase() as mock_svc, \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process.create_job", new_callable=AsyncMock, return_value="job-002"), \
             patch("api.routes.documents.process.process_document_task", new_callable=AsyncMock) as mock_task:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "job-002"
        assert data["status"] == "queued"
        assert "已加入处理队列" in data["message"]
        mock_svc.update_document_status.assert_called_once_with(DOCUMENT_ID, "queued")
        mock_task.assert_not_called()

    def test_process_async_is_idempotent_when_document_already_queued(self, client):
        """文档已在排队时，不应重复提交后台任务"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf", "template_id": TEMPLATE_ID, "status": "queued"}
        with _patch_supabase() as mock_svc, \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process.process_document_task", new_callable=AsyncMock) as mock_task:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            resp = client.post(f"/api/documents/{DOCUMENT_ID}/process")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "已在队列中" in data["message"]
        mock_svc.update_document_status.assert_not_called()
        mock_task.assert_not_awaited()


# ============ process-with-template ============

class TestProcessWithTemplate:
    """POST /api/documents/{document_id}/process-with-template"""

    def test_process_with_template_accepted(self, client):
        """模板化异步处理返回 202"""
        doc = {**MOCK_DOCUMENT, "file_path": "/tmp/test.pdf"}
        with _patch_supabase() as mock_svc, \
             _patch_workflow(), \
             patch("os.path.exists", return_value=True), \
             patch("api.routes.documents.process.create_job", new_callable=AsyncMock, return_value="job-003"), \
             patch("api.routes.documents.process.process_document_with_template_task", new_callable=AsyncMock) as mock_task, \
             patch("api.routes.documents.process.template_service") as mock_ts:
            mock_svc.get_document = AsyncMock(return_value=doc)
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            mock_ts.get_template = AsyncMock(return_value={
                "id": TEMPLATE_ID, "name": "检测报告", "tenant_id": TENANT_ID,
                "auto_approve": False,
            })
            resp = client.post(
                f"/api/documents/{DOCUMENT_ID}/process-with-template",
                json={"template_id": TEMPLATE_ID},
            )
        assert resp.status_code == 202
        mock_task.assert_not_called()

    def test_process_with_template_not_found(self, client):
        """文档不存在返回 404"""
        with _patch_supabase() as mock_svc, \
             patch("os.listdir", return_value=[]):
            mock_svc.get_document = AsyncMock(return_value=None)
            resp = client.post(
                f"/api/documents/{DOCUMENT_ID}/process-with-template",
                json={"template_id": TEMPLATE_ID},
            )
        assert resp.status_code == 404

    def test_process_with_template_unauthenticated(self, unauth_client):
        """未认证返回 401"""
        resp = unauth_client.post(
            f"/api/documents/{DOCUMENT_ID}/process-with-template",
            json={"template_id": TEMPLATE_ID},
        )
        assert resp.status_code == 401


# ============ _handle_processing_success / failure ============

class TestProcessingHandlers:
    """后台任务辅助函数测试"""

    @pytest.mark.asyncio
    async def test_handle_success_saves_result(self):
        """成功处理保存提取结果"""
        from api.routes.documents.process import _handle_processing_success
        result = {
            "document_type": "inspection_report",
            "extraction_data": {"sample_name": "LED灯"},
        }
        with _patch_supabase_in_helpers() as mock_svc, \
             patch("api.routes.documents.helpers.template_service") as mock_ts, \
             patch("api.routes.documents.helpers.push_to_feishu", new_callable=AsyncMock):
            mock_svc.save_extraction_result = AsyncMock()
            mock_svc.generate_display_name.return_value = "报告_LED灯"
            mock_svc.update_document_status = AsyncMock()
            mock_svc.update_document = AsyncMock()
            mock_ts.get_template = AsyncMock(return_value=None)
            mock_ts.get_template_with_details = AsyncMock(return_value=None)
            mock_ts.get_template_by_code = AsyncMock(return_value=None)
            await _handle_processing_success(
                document_id="doc-001",
                result=result,
                template_id=None,
                tenant_id=None,
                generate_display_name=True,
                auto_approve=False,
                source_file_path=None,
            )
        mock_svc.save_extraction_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_updates_status(self):
        """失败处理更新文档状态为 failed"""
        from api.routes.documents.process import _handle_processing_failure
        with _patch_supabase_in_helpers() as mock_svc:
            mock_svc.update_document_status = AsyncMock()
            await _handle_processing_failure("doc-001", "OCR失败")
        mock_svc.update_document_status.assert_called_once_with(
            "doc-001", "failed", error_message="OCR失败"
        )

    @pytest.mark.asyncio
    async def test_handle_exception_updates_status(self):
        """异常处理更新文档状态为 failed"""
        from api.routes.documents.process import _handle_processing_exception
        with _patch_supabase_in_helpers() as mock_svc:
            mock_svc.update_document_status = AsyncMock()
            await _handle_processing_exception("doc-001", RuntimeError("意外错误"))
        mock_svc.update_document_status.assert_called_once_with(
            "doc-001", "failed", "意外错误"
        )

    @pytest.mark.asyncio
    async def test_handle_exception_swallows_db_error(self):
        """异常处理中数据库更新失败不会再抛异常"""
        from api.routes.documents.process import _handle_processing_exception
        with _patch_supabase_in_helpers() as mock_svc:
            mock_svc.update_document_status = AsyncMock(side_effect=Exception("DB挂了"))
            # 不应抛异常
            await _handle_processing_exception("doc-001", RuntimeError("原始错误"))


class TestStuckDocumentRecovery:
    """超时恢复任务集成测试"""

    @pytest.mark.asyncio
    async def test_recovery_task_resets_stuck_documents(self):
        """定时任务调用 reset_stuck_processing_documents 并记录结果"""
        from api.main import _run_stuck_document_recovery

        with patch("api.main.supabase_service") as mock_svc:
            mock_svc.reset_stuck_processing_documents = AsyncMock(return_value=3)
            await _run_stuck_document_recovery()

        mock_svc.reset_stuck_processing_documents.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recovery_task_handles_db_error_gracefully(self):
        """定时任务异常时不应崩溃"""
        from api.main import _run_stuck_document_recovery

        with patch("api.main.supabase_service") as mock_svc, \
             patch("api.main.logger") as mock_logger:
            mock_svc.reset_stuck_processing_documents = AsyncMock(
                side_effect=Exception("DB 不可用")
            )
            # 不应抛异常
            await _run_stuck_document_recovery()

        # 必须有 warning/error 日志
        assert mock_logger.error.called or mock_logger.warning.called
