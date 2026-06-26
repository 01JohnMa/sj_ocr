"""CRM 文档入口测试。"""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies.auth import CurrentUser, get_current_user
from tests.conftest import DOCUMENT_ID, TEMPLATE_ID
from tests.conftest import TENANT_ID, USER_ID


async def _mock_current_user():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
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


def test_crm_submit_uploads_document_and_queues_forced_auto_approve_job(tmp_path, monkeypatch):
    """CRM/管理员提交入口应入队 crm job，由 worker 覆盖模板审核配置。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_current_user)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(crm_route.uuid, "uuid4", lambda: DOCUMENT_ID)

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm.create_job", new_callable=AsyncMock, return_value="job-crm"):
        mock_template_service.get_template = AsyncMock(return_value={
            "id": TEMPLATE_ID,
            "tenant_id": TENANT_ID,
        })
        mock_svc.create_document = AsyncMock()
        mock_svc.update_document_status = AsyncMock()

        response = client.post(
            "/api/crm/documents/submit",
            data={
                "template_id": TEMPLATE_ID,
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
        "auto_approve_forced": True,
    }
    created_document = mock_svc.create_document.await_args.args[0]
    assert created_document["id"] == DOCUMENT_ID
    assert created_document["template_id"] == TEMPLATE_ID
    assert created_document["status"] == "uploaded"
    assert created_document["custom_push_name"] == "CRM单据"
    mock_svc.update_document_status.assert_awaited_once_with(DOCUMENT_ID, "queued")


def test_crm_submit_rejects_regular_user():
    """普通登录用户不能调用 CRM 强制自动通过入口。"""
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
