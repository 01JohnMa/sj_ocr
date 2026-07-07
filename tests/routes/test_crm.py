"""CRM 文档入口测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies.auth import CurrentUser
from tests.conftest import DOCUMENT_ID, TEMPLATE_ID
from tests.conftest import TENANT_ID, USER_ID

QUALITY_TENANT_ID = "a0000000-0000-0000-0000-000000000001"
CRM_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
INSPECTION_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000001"
SAMPLING_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000003"
EXPRESS_TEMPLATE_ID = "b0000000-0000-0000-0000-000000000002"


def _crm_feishu_push_payload(**overrides):
    payload = {
        "alipay_account": "pay@example.com",
        "alipay_name": "张三",
        "dealer": "宁波经销商",
        "dealer_name": "李四",
        "contact_phone": "13800000000",
    }
    payload.update(overrides)
    return payload


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
    if current_user:
        app.dependency_overrides[crm_route.get_crm_current_user] = current_user
    return TestClient(app)


def test_crm_submit_uploads_document_and_queues_review_required_job(tmp_path, monkeypatch):
    """CRM/管理员用 JSON URL 数组提交同一份多页文档后应入队 crm job。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(crm_route.uuid, "uuid4", lambda: DOCUMENT_ID)

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm.create_job", new_callable=AsyncMock, return_value="job-crm"), \
         patch("api.routes.crm._prepare_crm_json_upload", new_callable=AsyncMock) as mock_prepare:
        mock_template_service.get_template = AsyncMock(return_value={
            "id": INSPECTION_TEMPLATE_ID,
            "tenant_id": QUALITY_TENANT_ID,
        })
        mock_prepare.return_value = {
            "file_name": f"{DOCUMENT_ID}.pdf",
            "original_file_name": "CRM单据.pdf",
            "file_path": str(upload_dir / f"{DOCUMENT_ID}.pdf"),
            "file_size": 2048,
            "file_extension": ".pdf",
            "file_type": "application/pdf",
            "mime_type": "application/pdf",
        }
        mock_svc.create_document = AsyncMock()
        mock_svc.update_document_status = AsyncMock()

        response = client.post(
            "/api/crm/documents/submit",
            json={
                "template_id": INSPECTION_TEMPLATE_ID,
                "custom_push_name": "CRM单据",
                "file": [
                    {"type": "images", "url": "http://crm.example.com/page1.jpg"},
                    {"type": "images", "url": "http://crm.example.com/page2.jpg"},
                ],
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
    assert created_document["file_extension"] == ".pdf"
    mock_prepare.assert_awaited_once()
    mock_svc.update_document_status.assert_awaited_once_with(DOCUMENT_ID, "queued")


def test_crm_submit_accepts_configured_fixed_token(tmp_path, monkeypatch):
    """配置 CRM_API_TOKEN 后，CRM 可用固定 Bearer token 调用提交入口。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, None)

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(crm_route.settings, "CRM_API_TOKEN", "crm-fixed-token")
    monkeypatch.setattr(crm_route.uuid, "uuid4", lambda: DOCUMENT_ID)

    with patch("api.routes.crm.supabase_service") as mock_svc, \
         patch("api.routes.crm.template_service") as mock_template_service, \
         patch("api.routes.crm.create_job", new_callable=AsyncMock, return_value="job-crm"), \
         patch("api.routes.crm._prepare_crm_json_upload", new_callable=AsyncMock) as mock_prepare:
        mock_template_service.get_template = AsyncMock(return_value={
            "id": INSPECTION_TEMPLATE_ID,
            "tenant_id": QUALITY_TENANT_ID,
        })
        mock_prepare.return_value = {
            "file_name": f"{DOCUMENT_ID}.pdf",
            "original_file_name": "crm_url_document.pdf",
            "file_path": str(upload_dir / f"{DOCUMENT_ID}.pdf"),
            "file_size": 2048,
            "file_extension": ".pdf",
            "file_type": "application/pdf",
            "mime_type": "application/pdf",
        }
        mock_svc.create_document = AsyncMock()
        mock_svc.update_document_status = AsyncMock()

        response = client.post(
            "/api/crm/documents/submit",
            headers={"Authorization": "Bearer crm-fixed-token"},
            json={
                "template_id": INSPECTION_TEMPLATE_ID,
                "file": [{"url": "http://crm.example.com/page1.jpg"}],
            },
        )

    assert response.status_code == 202
    created_document = mock_svc.create_document.await_args.args[0]
    assert created_document["user_id"] == CRM_SYSTEM_USER_ID
    assert created_document["tenant_id"] == QUALITY_TENANT_ID
    mock_prepare.assert_awaited_once()


def test_crm_submit_rejects_wrong_fixed_token(tmp_path, monkeypatch):
    """固定 token 配置后，错误 token 不能绕过 CRM 鉴权。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, None)

    monkeypatch.setattr(crm_route.settings, "CRM_API_TOKEN", "crm-fixed-token")

    response = client.post(
        "/api/crm/documents/submit",
        headers={"Authorization": "Bearer wrong-token"},
        json={
            "template_id": INSPECTION_TEMPLATE_ID,
            "file": [{"url": "http://crm.example.com/page1.jpg"}],
        },
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_prepare_crm_json_upload_merges_image_urls_to_single_pdf(tmp_path, monkeypatch):
    """同一份文档的多张图片 URL 应按顺序合成一个 PDF 文件。"""
    import api.routes.crm as crm_route
    from PIL import Image

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))

    async def fake_download(item, destination_dir, index):
        image_path = destination_dir / f"page_{index}.jpg"
        Image.new("RGB", (16, 16), color=(index * 30, 20, 20)).save(image_path, format="JPEG")
        return {
            "path": str(image_path),
            "extension": ".jpg",
            "content_type": "image/jpeg",
            "url": item.url,
        }

    monkeypatch.setattr(crm_route, "_download_crm_file_url", fake_download)

    result = await crm_route._prepare_crm_json_upload(
        DOCUMENT_ID,
        crm_route.CrmSubmitRequest(
            template_id=SAMPLING_TEMPLATE_ID,
            custom_push_name="CRM多页抽样单",
            file=[
                crm_route.CrmSubmitFileItem(url="http://crm.example.com/page1.jpg", type="images"),
                crm_route.CrmSubmitFileItem(url="http://crm.example.com/page2.jpg", type="images"),
            ],
        ),
    )

    assert result["file_name"] == f"{DOCUMENT_ID}.pdf"
    assert result["original_file_name"] == "CRM多页抽样单.pdf"
    assert result["file_extension"] == ".pdf"
    assert result["mime_type"] == "application/pdf"
    assert result["file_size"] > 0
    assert (upload_dir / f"{DOCUMENT_ID}.pdf").exists()


def test_crm_submit_files_rejects_file_and_files_together():
    """JSON 提交时 file 和 files 只能二选一，避免页序歧义。"""
    import api.routes.crm as crm_route

    request = crm_route.CrmSubmitRequest(
        template_id=SAMPLING_TEMPLATE_ID,
        file=[crm_route.CrmSubmitFileItem(url="http://crm.example.com/page1.jpg")],
        files=[crm_route.CrmSubmitFileItem(url="http://crm.example.com/page2.jpg")],
    )

    with pytest.raises(crm_route.ValidationError, match="file和files不能同时传"):
        crm_route._crm_submit_files(request)


def test_crm_file_url_rejects_loopback_address():
    """CRM 文件 URL 不能指向本机地址，避免服务端下载 SSRF。"""
    import api.routes.crm as crm_route

    with pytest.raises(crm_route.ValidationError, match="文件URL不允许指向内网或本机地址"):
        crm_route._validate_crm_file_url("http://127.0.0.1:8099/private.jpg")


@pytest.mark.asyncio
async def test_prepare_crm_json_upload_cleans_partial_pdf_when_merge_fails(tmp_path, monkeypatch):
    """图片合并失败时应清理已写入的目标 PDF 残文件。"""
    import api.routes.crm as crm_route
    from PIL import Image

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(crm_route.settings, "UPLOAD_FOLDER", str(upload_dir))

    async def fake_download(item, destination_dir, index):
        image_path = destination_dir / f"page_{index}.jpg"
        Image.new("RGB", (16, 16), color=(index * 30, 20, 20)).save(image_path, format="JPEG")
        return {
            "path": str(image_path),
            "extension": ".jpg",
            "content_type": "image/jpeg",
            "url": item.url,
        }

    def fail_after_partial_write(_image_paths, destination):
        Path(destination).write_bytes(b"partial")
        raise RuntimeError("merge failed")

    monkeypatch.setattr(crm_route, "_download_crm_file_url", fake_download)
    monkeypatch.setattr(crm_route, "_images_to_pdf_sync", fail_after_partial_write)

    with pytest.raises(RuntimeError, match="merge failed"):
        await crm_route._prepare_crm_json_upload(
            DOCUMENT_ID,
            crm_route.CrmSubmitRequest(
                template_id=SAMPLING_TEMPLATE_ID,
                file=[crm_route.CrmSubmitFileItem(url="http://crm.example.com/page1.jpg")],
            ),
        )

    assert not (upload_dir / f"{DOCUMENT_ID}.pdf").exists()


def test_crm_fixed_token_can_query_job_for_quality_document(monkeypatch):
    """CRM 固定 token 可查询质量中心文档关联的任务。"""
    from api.main import app

    monkeypatch.setattr("api.dependencies.auth.settings.CRM_API_TOKEN", "crm-fixed-token")

    with patch("api.routes.documents.process.get_job", new_callable=AsyncMock, return_value={
        "job_id": "job-crm",
        "status": "completed",
        "stage": "completed",
        "progress": 100,
        "document_ids": [DOCUMENT_ID],
        "items": [],
        "created_by": CRM_SYSTEM_USER_ID,
    }), patch("api.routes.documents.process.supabase_service") as mock_svc:
        mock_svc.get_document = AsyncMock(return_value={
            "id": DOCUMENT_ID,
            "user_id": CRM_SYSTEM_USER_ID,
            "tenant_id": QUALITY_TENANT_ID,
        })
        with TestClient(app) as client:
            response = client.get(
                "/api/documents/jobs/job-crm",
                headers={"Authorization": "Bearer crm-fixed-token"},
            )

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-crm"


def test_crm_fixed_token_cannot_query_other_tenant_job(monkeypatch):
    """CRM 固定 token 不能读取非质量中心文档关联的任务。"""
    from api.main import app

    monkeypatch.setattr("api.dependencies.auth.settings.CRM_API_TOKEN", "crm-fixed-token")

    with patch("api.routes.documents.process.get_job", new_callable=AsyncMock, return_value={
        "job_id": "job-other",
        "status": "completed",
        "stage": "completed",
        "progress": 100,
        "document_ids": [DOCUMENT_ID],
        "items": [],
        "created_by": "99999999-9999-4999-8999-999999999999",
    }), patch("api.routes.documents.process.supabase_service") as mock_svc:
        mock_svc.get_document = AsyncMock(return_value={
            "id": DOCUMENT_ID,
            "user_id": "99999999-9999-4999-8999-999999999999",
            "tenant_id": TENANT_ID,
        })
        with TestClient(app) as client:
            response = client.get(
                "/api/documents/jobs/job-other",
                headers={"Authorization": "Bearer crm-fixed-token"},
            )

    assert response.status_code == 404


def test_crm_fixed_token_can_query_quality_document_status(monkeypatch):
    """CRM 固定 token 可查询质量中心文档状态。"""
    from api.main import app

    monkeypatch.setattr("api.dependencies.auth.settings.CRM_API_TOKEN", "crm-fixed-token")

    with patch("api.routes.documents.query._run_supabase", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = SimpleNamespace(data=[{
            "id": DOCUMENT_ID,
            "user_id": CRM_SYSTEM_USER_ID,
            "tenant_id": QUALITY_TENANT_ID,
            "status": "pending_review",
            "document_type": "sampling",
        }])
        with TestClient(app) as client:
            response = client.get(
                f"/api/documents/{DOCUMENT_ID}/status",
                headers={"Authorization": "Bearer crm-fixed-token"},
            )

    assert response.status_code == 200
    assert response.json()["status"] == "pending_review"


def test_crm_fixed_token_cannot_query_other_tenant_document_status(monkeypatch):
    """CRM 固定 token 查询非质量中心文档状态时按无权访问处理。"""
    from api.main import app

    monkeypatch.setattr("api.dependencies.auth.settings.CRM_API_TOKEN", "crm-fixed-token")

    with patch("api.routes.documents.query._run_supabase", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = SimpleNamespace(data=[{
            "id": DOCUMENT_ID,
            "user_id": USER_ID,
            "tenant_id": TENANT_ID,
            "status": "pending_review",
            "document_type": "sampling",
        }])
        with TestClient(app) as client:
            response = client.get(
                f"/api/documents/{DOCUMENT_ID}/status",
                headers={"Authorization": "Bearer crm-fixed-token"},
            )

    assert response.status_code == 404


def test_crm_submit_rejects_regular_user():
    """普通登录用户不能调用 CRM 集成入口。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_regular_user)

    response = client.post(
        "/api/crm/documents/submit",
        json={
            "template_id": TEMPLATE_ID,
            "file": [{"url": "http://crm.example.com/page1.jpg"}],
        },
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
            json={
                "template_id": TEMPLATE_ID,
                "file": [{"url": "http://crm.example.com/page1.jpg"}],
            },
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
            json={
                "template_id": EXPRESS_TEMPLATE_ID,
                "file": [{"url": "http://crm.example.com/page1.jpg"}],
            },
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
            json=_crm_feishu_push_payload(
                alipay_account="  pay@example.com  ",
                alipay_name=" 张三 ",
                dealer=" 宁波经销商 ",
                dealer_name=" 李四 ",
                contact_phone=" 13800000000 ",
                reviewed_data={
                    "sample_name": "CRM修正值",
                    "alipay_account": "不应作为识别字段保存",
                    "dealer": "不应作为识别字段保存",
                },
                custom_push_name="CRM审核单",
            ),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "pushed"
    mock_push.assert_awaited_once()
    push_kwargs = mock_push.await_args.kwargs
    assert push_kwargs["extraction_data"]["sample_name"] == "CRM修正值"
    assert "alipay_account" not in push_kwargs["extraction_data"]
    assert "dealer" not in push_kwargs["extraction_data"]
    assert "is_validated" not in push_kwargs["extraction_data"]
    assert push_kwargs["extra_data"] == {
        "alipay_account": "pay@example.com",
        "alipay_name": "张三",
        "dealer": "宁波经销商",
        "dealer_name": "李四",
        "contact_phone": "13800000000",
    }
    assert push_kwargs["extra_field_mapping"] == {
        "alipay_account": "支付宝账号",
        "alipay_name": "支付宝姓名",
        "dealer": "经销商",
        "dealer_name": "经销商姓名",
        "contact_phone": "联系号码",
    }
    assert push_kwargs["custom_push_name"] == "CRM审核单"
    assert push_kwargs["dedupe_key"] == "crm-dedupe"
    mock_mark_completed.assert_awaited_once_with(
        "inspection_reports",
        DOCUMENT_ID,
        {"sample_name": "CRM修正值"},
        USER_ID,
    )


def test_crm_feishu_push_requires_manual_fields():
    """CRM 手填字段为飞书推送必填字段。"""
    import api.routes.crm as crm_route

    client = _build_test_app(crm_route, _mock_quality_admin)

    response = client.post(
        f"/api/crm/documents/{DOCUMENT_ID}/feishu/push",
        json=_crm_feishu_push_payload(contact_phone=" "),
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
            json=_crm_feishu_push_payload(),
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
            json=_crm_feishu_push_payload(),
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
            json=_crm_feishu_push_payload(reviewed_data={"unknown_field": "不应允许"}),
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
            json=_crm_feishu_push_payload(),
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
async def test_mark_crm_push_completed_refetches_updated_result(monkeypatch):
    """完成标记应先更新再查询结果，兼容不支持 update().select() 的 Supabase builder。"""
    import api.routes.crm as crm_route

    class FakeUpdateQuery:
        def __init__(self):
            self.update_data = None
            self.eq_args = None
            self.calls = []

        def update(self, data):
            self.calls.append("update")
            self.update_data = data
            return self

        def eq(self, *args):
            self.calls.append("eq")
            self.eq_args = args
            return self

        def select(self, *args):
            raise AttributeError("'SyncFilterRequestBuilder' object has no attribute 'select'")

        def execute(self):
            self.calls.append("execute")
            return SimpleNamespace(data=[])

    class FakeSelectQuery:
        def __init__(self):
            self.eq_args = None
            self.select_args = None
            self.calls = []

        def select(self, *args):
            self.calls.append("select")
            self.select_args = args
            return self

        def eq(self, *args):
            self.calls.append("eq")
            self.eq_args = args
            return self

        def execute(self):
            self.calls.append("execute")
            return SimpleNamespace(data=[{"document_id": DOCUMENT_ID, "is_validated": True}])

    update_query = FakeUpdateQuery()
    select_query = FakeSelectQuery()

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def table(self, table_name):
            assert table_name == "inspection_reports"
            self.calls += 1
            return update_query if self.calls == 1 else select_query

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

    assert update_query.update_data["sample_name"] == "CRM修正值"
    assert update_query.update_data["is_validated"] is True
    assert update_query.update_data["validated_by"] == USER_ID
    assert update_query.update_data["validated_at"]
    assert update_query.eq_args == ("document_id", DOCUMENT_ID)
    assert update_query.calls == ["update", "eq", "execute"]
    assert select_query.select_args == ("*",)
    assert select_query.eq_args == ("document_id", DOCUMENT_ID)
    assert select_query.calls == ["select", "eq", "execute"]
    fake_service.update_document.assert_awaited_once_with(DOCUMENT_ID, {"status": "completed"})


@pytest.mark.asyncio
async def test_mark_crm_push_completed_fails_when_refetch_has_no_validated_row(monkeypatch):
    """完成标记后重新查询不到已审核结果时应失败，避免误报飞书推送完成。"""
    import api.routes.crm as crm_route

    class FakeUpdateQuery:
        def update(self, _data):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class FakeSelectQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def table(self, table_name):
            assert table_name == "inspection_reports"
            self.calls += 1
            return FakeUpdateQuery() if self.calls == 1 else FakeSelectQuery()

    class FakeSupabaseService:
        client = FakeClient()
        update_document = AsyncMock()

    fake_service = FakeSupabaseService()
    monkeypatch.setattr(crm_route, "supabase_service", fake_service)

    with pytest.raises(crm_route.ProcessingError, match="CRM推送成功后更新审核结果失败"):
        await crm_route._mark_crm_push_completed(
            "inspection_reports",
            DOCUMENT_ID,
            {"sample_name": "CRM修正值"},
            USER_ID,
        )

    fake_service.update_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_crm_push_completed_fails_when_refetch_row_is_not_validated(monkeypatch):
    """完成标记后重新查询到未审核结果时应失败，避免状态提前完成。"""
    import api.routes.crm as crm_route

    class FakeUpdateQuery:
        def update(self, _data):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    class FakeSelectQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[{"document_id": DOCUMENT_ID, "is_validated": False}])

    class FakeClient:
        def __init__(self):
            self.calls = 0

        def table(self, table_name):
            assert table_name == "inspection_reports"
            self.calls += 1
            return FakeUpdateQuery() if self.calls == 1 else FakeSelectQuery()

    class FakeSupabaseService:
        client = FakeClient()
        update_document = AsyncMock()

    fake_service = FakeSupabaseService()
    monkeypatch.setattr(crm_route, "supabase_service", fake_service)

    with pytest.raises(crm_route.ProcessingError, match="CRM推送成功后更新审核结果失败"):
        await crm_route._mark_crm_push_completed(
            "inspection_reports",
            DOCUMENT_ID,
            {"sample_name": "CRM修正值"},
            USER_ID,
        )

    fake_service.update_document.assert_not_awaited()
