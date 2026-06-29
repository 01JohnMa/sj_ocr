"""CRM 文档入口测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies.auth import CurrentUser, get_current_user
from tests.conftest import DOCUMENT_ID, TEMPLATE_ID
from tests.conftest import TENANT_ID, USER_ID

QUALITY_TENANT_ID = "a0000000-0000-0000-0000-000000000001"
INSPECTION_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000001"
SAMPLING_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000003"
EXPRESS_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000002"


async def _mock_current_user():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
        role="tenant_admin",
    )


async def _mock_quality_admin():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=QUALITY_TENANT_ID,
        role="tenant_admin",
    )


async def _mock_regular_user():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
        role="user",
    )


def _build_test_app(crm_route, current_user):
    app = FastAPI()
    app.include_router(crm_route.router, prefix="/api")
    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app)


def test_crm_submit_uploads_document_and_queues_review_required_job(tmp_path, monkeypatch):
    """CRM/管理员提交入口应入队 crm job，后续等待 CRM 审核推送。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(crm_route.uuid, "uuid4", lambda: DOCUMENT_ID)

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm.create_job", new_callable=AsyncMock, return_value="job-crm"):
        mock_template_service.get_template = AsyncMock(return_value={
            "id": INSPECTION_TEMPLATE_ID,
            "tenant_id": QUALITY_TENANT_ID,
        })
        mock_svc.create_document = AsyncMock()
        mock_svc.update_document_status = AsyncMock()

        response = client.post(
            "/api/crm/documents/submit",
            data={
                "template_id": INSPECTION_TEMPLATE_ID,
                "custom_push_name": "CRM单据",
            },
            files={
                "file": ("report.pdf", b"%PDF-1.4 test", "application/pdf"),
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "document_id": DOCUMENT_ID,
        "job_id": "job-crm",
        "status": "queued",
        "message": "CRM文档已加入处理队列",
        "crm_review_required": True,
    }
    created_document = mock_svc.create_document.await_args.args[0]
    assert created_document["id"] == DOCUMENT_ID
    assert created_document["template_id"] == INSPECTION_TEMPLATE_ID
    assert created_document["status"] == "uploaded"
    assert created_document["custom_push_name"] == "CRM单据"
    mock_svc.update_document_status.assert_awaited_once_with(DOCUMENT_ID, "queued")


def test_crm_submit_rejects_regular_user():
    """普通登录用户不能调用 CRM 集成入口。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_regular_user)

    response = client.post(
        "/api/crm/documents/submit",
        data={"template_id": TEMPLATE_ID},
        files={"file": ("report.pdf", b"%PDF-1.4 test", "application/pdf")},
    )

    assert response.status_code == 403


def test_crm_submit_rejects_template_from_other_tenant():
    """CRM 入口不能使用当前调用方无权访问的模板。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_current_user)

    with patch("api.routes.crm.template_service") as mock_template_service:
        mock_template_service.get_template = AsyncMock(return_value={
            "id": TEMPLATE_ID,
            "tenant_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        })

        response = client.post(
            "/api/crm/documents/submit",
            data={"template_id": TEMPLATE_ID},
            files={"file": ("report.pdf", b"%PDF-1.4 test", "application/pdf")},
        )

    assert response.status_code == 403


def test_crm_submit_rejects_express_template():
    """CRM 入口不支持快递单模板。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)

    with patch("api.routes.crm.template_service") as mock_template_service:
        mock_template_service.get_template = AsyncMock(return_value={
            "id": EXPRESS_TEMPLATE_ID,
            "tenant_id": QUALITY_TENANT_ID,
        })

        response = client.post(
            "/api/crm/documents/submit",
            data={"template_id": EXPRESS_TEMPLATE_ID},
            files={"file": ("express.pdf", b"%PDF-1.4 test", "application/pdf")},
        )

    assert response.status_code == 400


def test_crm_feishu_push_merges_reviewed_data_and_alipay_then_completes():
    """CRM 审核后推送飞书，成功后才标记完成。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)
    document = {
        "id": DOCUMENT_ID,
        "user_id": USER_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "status": "pending_review",
        "template_id": INSPECTION_TEMPLATE_ID,
        "document_type": "inspection_report",
        "file_path": "/tmp/report.pdf",
        "custom_push_name": "原始推送名",
    }
    template = {
        "id": INSPECTION_TEMPLATE_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "code": "inspection_report",
        "name": "检测报告",
        "feishu_bitable_token": "bitable-token",
        "feishu_table_id": "table-id",
        "template_fields": [{"field_key": "sample_name", "feishu_column": "样品名称"}],
    }
    result_row = {
        "document_id": DOCUMENT_ID,
        "sample_name": "原识别值",
        "is_validated": False,
    }

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm._fetch_extraction_result", new_callable=AsyncMock, return_value=result_row), \
         patch("api.routes.crm._mark_crm_push_completed", new_callable=AsyncMock) as mock_mark_completed, \
         patch("api.routes.crm.has_feishu_push_record", new_callable=AsyncMock, return_value=False), \
         patch("api.routes.crm.build_feishu_push_dedupe_key", return_value="crm-dedupe"), \
         patch("api.routes.crm.push_to_feishu", new_callable=AsyncMock, return_value=True) as mock_push:
        mock_svc.get_document = AsyncMock(return_value=document)
        mock_svc.resolve_table_name = AsyncMock(return_value="inspection_reports")
        mock_template_service.get_template_with_details = AsyncMock(return_value=template)

        response = client.post(
            f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
            json={
                "alipay_account": "  pay@example.com  ",
                "alipay_name": " 张三 ",
                "reviewed_data": {
                    "sample_name": "CRM修正值",
                    "alipay_account": "不应作为识别字段保存",
                },
                "custom_push_name": "CRM审核单",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pushed"
    mock_push.assert_awaited_once()
    push_kwargs = mock_push.await_args.kwargs
    assert push_kwargs["extraction_data"]["sample_name"] == "CRM修正值"
    assert "alipay_account" not in push_kwargs["extraction_data"]
    assert "is_validated" not in push_kwargs["extraction_data"]
    assert push_kwargs["extra_data"] == {
        "alipay_account": "pay@example.com",
        "alipay_name": "张三",
    }
    assert push_kwargs["extra_field_mapping"] == {
        "alipay_account": "支付宝账号",
        "alipay_name": "支付宝姓名",
    }
    assert push_kwargs["custom_push_name"] == "CRM审核单"
    assert push_kwargs["dedupe_key"] == "crm-dedupe"
    mock_mark_completed.assert_awaited_once_with(
        "inspection_reports",
        DOCUMENT_ID,
        {"sample_name": "CRM修正值"},
        USER_ID,
    )


def test_crm_feishu_push_requires_alipay_fields():
    """支付宝账号和姓名为 CRM 手填必填字段。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)

    response = client.post(
        f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
        json={"alipay_account": "pay@example.com", "alipay_name": " "},
    )

    assert response.status_code == 400


def test_crm_feishu_push_rejects_express_template():
    """CRM 飞书推送仅支持检测报告和抽样单。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)
    document = {
        "id": DOCUMENT_ID,
        "user_id": USER_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "status": "pending_review",
        "template_id": EXPRESS_TEMPLATE_ID,
        "document_type": "express",
    }

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service:
        mock_svc.get_document = AsyncMock(return_value=document)
        mock_template_service.get_template_with_details = AsyncMock(return_value={
            "id": EXPRESS_TEMPLATE_ID,
            "tenant_id": QUALITY_TENANT_ID,
            "code": "express",
            "name": "快递单",
        })

        response = client.post(
            f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
            json={"alipay_account": "pay@example.com", "alipay_name": "张三"},
        )

    assert response.status_code == 400


def test_crm_feishu_push_does_not_complete_when_push_fails():
    """推送失败时保持 pending_review，不标记完成。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)
    document = {
        "id": DOCUMENT_ID,
        "user_id": USER_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "status": "pending_review",
        "template_id": SAMPLING_TEMPLATE_ID,
        "document_type": "sampling",
        "file_path": "/tmp/sampling.pdf",
    }
    template = {
        "id": SAMPLING_TEMPLATE_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "code": "sampling",
        "name": "抽样单",
        "feishu_bitable_token": "bitable-token",
        "feishu_table_id": "table-id",
        "template_fields": [{"field_key": "sample_name", "feishu_column": "样品名称"}],
    }

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm._fetch_extraction_result", new_callable=AsyncMock, return_value={"sample_name": "样品"}), \
         patch("api.routes.crm._mark_crm_push_completed", new_callable=AsyncMock) as mock_mark_completed, \
         patch("api.routes.crm.has_feishu_push_record", new_callable=AsyncMock, return_value=False), \
         patch("api.routes.crm.build_feishu_push_dedupe_key", return_value="crm-dedupe"), \
         patch("api.routes.crm.push_to_feishu", new_callable=AsyncMock, return_value=False):
        mock_svc.get_document = AsyncMock(return_value=document)
        mock_svc.resolve_table_name = AsyncMock(return_value="sampling_forms")
        mock_template_service.get_template_with_details = AsyncMock(return_value=template)

        response = client.post(
            f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
            json={"alipay_account": "pay@example.com", "alipay_name": "张三"},
        )

    assert response.status_code == 502
    mock_mark_completed.assert_not_awaited()


def test_crm_feishu_push_rejects_unknown_reviewed_data_fields_before_push():
    """reviewed_data 只能覆盖模板字段，避免飞书成功后结果表更新失败。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)
    document = {
        "id": DOCUMENT_ID,
        "user_id": USER_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "status": "pending_review",
        "template_id": INSPECTION_TEMPLATE_ID,
        "document_type": "inspection_report",
        "file_path": "/tmp/report.pdf",
    }
    template = {
        "id": INSPECTION_TEMPLATE_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "code": "inspection_report",
        "name": "检测报告",
        "feishu_bitable_token": "bitable-token",
        "feishu_table_id": "table-id",
        "template_fields": [{"field_key": "sample_name", "feishu_column": "样品名称"}],
    }

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm._fetch_extraction_result", new_callable=AsyncMock, return_value={"sample_name": "样品"}), \
         patch("api.routes.crm.push_to_feishu", new_callable=AsyncMock) as mock_push:
        mock_svc.get_document = AsyncMock(return_value=document)
        mock_svc.resolve_table_name = AsyncMock(return_value="inspection_reports")
        mock_template_service.get_template_with_details = AsyncMock(return_value=template)

        response = client.post(
            f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
            json={
                "alipay_account": "pay@example.com",
                "alipay_name": "张三",
                "reviewed_data": {"unknown_field": "不应允许"},
            },
        )

    assert response.status_code == 400
    mock_push.assert_not_awaited()


def test_crm_feishu_push_existing_record_marks_completed_without_second_push():
    """已有相同飞书推送记录时不重复推送，并补齐本地完成状态。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)
    document = {
        "id": DOCUMENT_ID,
        "user_id": USER_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "status": "pending_review",
        "template_id": INSPECTION_TEMPLATE_ID,
        "document_type": "inspection_report",
        "file_path": "/tmp/report.pdf",
    }
    template = {
        "id": INSPECTION_TEMPLATE_ID,
        "tenant_id": QUALITY_TENANT_ID,
        "code": "inspection_report",
        "name": "检测报告",
        "feishu_bitable_token": "bitable-token",
        "feishu_table_id": "table-id",
        "template_fields": [{"field_key": "sample_name", "feishu_column": "样品名称"}],
    }

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm._fetch_extraction_result", new_callable=AsyncMock, return_value={"sample_name": "样品"}), \
         patch("api.routes.crm._mark_crm_push_completed", new_callable=AsyncMock) as mock_mark_completed, \
         patch("api.routes.crm.has_feishu_push_record", new_callable=AsyncMock, return_value=True), \
         patch("api.routes.crm.build_feishu_push_dedupe_key", return_value="crm-dedupe"), \
         patch("api.routes.crm.push_to_feishu", new_callable=AsyncMock) as mock_push:
        mock_svc.get_document = AsyncMock(return_value=document)
        mock_svc.resolve_table_name = AsyncMock(return_value="inspection_reports")
        mock_template_service.get_template_with_details = AsyncMock(return_value=template)

        response = client.post(
            f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
            json={"alipay_account": "pay@example.com", "alipay_name": "张三"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "skipped"
    mock_push.assert_not_awaited()
    mock_mark_completed.assert_awaited_once_with(
        "inspection_reports",
        DOCUMENT_ID,
        {},
        USER_ID,
    )


@pytest.mark.asyncio
async def test_mark_crm_push_completed_selects_updated_result(monkeypatch):
    """完成标记更新应显式返回更新行，避免 Supabase 空 data 误判失败。"""
    import api.routes.crm as crm_route

    class FakeUpdateQuery:
        def __init__(self):
            self.update_data = None
            self.eq_args = None
            self.select_args = None

        def update(self, data):
            self.update_data = data
            return self

        def eq(self, *args):
            self.eq_args = args
            return self

        def select(self, *args):
            self.select_args = args
            return self

        def execute(self):
            return SimpleNamespace(data=[{"document_id": DOCUMENT_ID}])

    query = FakeUpdateQuery()

    class FakeClient:
        def table(self, table_name):
            assert table_name == "inspection_reports"
            return query

    class FakeSupabaseService:
        client = FakeClient()
        update_document = AsyncMock()

    fake_service = FakeSupabaseService()
    monkeypatch.setattr(crm_route, "supabase_service", fake_service)

    await crm_route._mark_crm_push_completed(
        "inspection_reports",
        DOCUMENT_ID,
        {"sample_name": "CRM修正值"},
        USER_ID,
    )

    assert query.update_data["sample_name"] == "CRM修正值"
    assert query.update_data["is_validated"] is True
    assert query.eq_args == ("document_id", DOCUMENT_ID)
    assert query.select_args == ("*",)
    fake_service.update_document.assert_awaited_once_with(DOCUMENT_ID, {"status": "completed"})
