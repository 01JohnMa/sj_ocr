from pathlib import Path
from io import BytesIO
import importlib.machinery
import importlib.util
import sys
import types

import pytest
from openpyxl import Workbook
from fastapi import FastAPI

from api.dependencies.auth import CurrentUser, get_current_user

if "paddleocr" not in sys.modules:
    paddleocr_stub = types.ModuleType("paddleocr")
    paddleocr_stub.__spec__ = importlib.machinery.ModuleSpec("paddleocr", loader=None)

    class PaddleOCR:
        pass

    paddleocr_stub.PaddleOCR = PaddleOCR
    sys.modules["paddleocr"] = paddleocr_stub


USER_ID = "11111111-1111-4111-8111-111111111111"
TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _load_sdk_route_module():
    module_path = Path(__file__).resolve().parents[2] / "api" / "routes" / "sdk.py"
    spec = importlib.util.spec_from_file_location("sdk_route_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


sdk_route = _load_sdk_route_module()
sdk_router = sdk_route.router


async def _tenant_admin_user():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
        role="tenant_admin",
    )


async def _other_tenant_admin_user():
    return CurrentUser(
        user_id="22222222-2222-4222-8222-222222222222",
        token="test-token",
        tenant_id=TENANT_ID,
        role="tenant_admin",
    )


async def _regular_user():
    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
        role="user",
    )


@pytest.fixture
def admin_client():
    test_app = FastAPI()
    test_app.include_router(sdk_router, prefix="/api")
    test_app.dependency_overrides[get_current_user] = _tenant_admin_user
    try:
        from fastapi.testclient import TestClient

        with TestClient(test_app) as test_client:
            yield test_client
    finally:
        test_app.dependency_overrides.clear()


@pytest.fixture
def client():
    test_app = FastAPI()
    test_app.include_router(sdk_router, prefix="/api")
    test_app.dependency_overrides[get_current_user] = _regular_user
    try:
        from fastapi.testclient import TestClient

        with TestClient(test_app) as test_client:
            yield test_client
    finally:
        test_app.dependency_overrides.clear()


def _excel_template_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Report"
    sheet["A1"] = "订单号"
    sheet["B1"] = "{{order_no}}"
    sheet["A2"] = "数量"
    sheet["B2"] = "{{quantity}} 件"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_create_session_uploads_file_and_runs_ocr(admin_client, monkeypatch, tmp_path):
    async def fake_process_document(file_path: str):
        assert Path(file_path).exists()
        return {
            "text": "样品名称：小型断路器\n检验结论：合格",
            "confidence": 0.93,
            "lines": [],
            "total_lines": 2,
        }

    monkeypatch.setattr(sdk_route.ocr_service, "process_document", fake_process_document)
    monkeypatch.setattr(sdk_route.settings, "UPLOAD_FOLDER", str(tmp_path))

    response = admin_client.post(
        "/api/sdk/sessions",
        files={"file": ("report.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["file_name"] == "report.pdf"
    assert data["state"] == "ocr_completed"
    assert data["ocr_text"] == "样品名称：小型断路器\n检验结论：合格"
    assert data["ocr_confidence"] == 0.93


def test_create_session_accepts_empty_excel_template_and_returns_slots(
    admin_client,
    monkeypatch,
    tmp_path,
):
    async def fake_process_document(file_path: str):
        assert Path(file_path).exists()
        return {
            "text": "订单号：NOZS0311046\n数量：12",
            "confidence": 0.93,
            "lines": [],
            "total_lines": 2,
        }

    monkeypatch.setattr(sdk_route.ocr_service, "process_document", fake_process_document)
    monkeypatch.setattr(sdk_route.settings, "UPLOAD_FOLDER", str(tmp_path))

    response = admin_client.post(
        "/api/sdk/sessions",
        files={
            "file": ("scan.jpg", b"image bytes", "image/jpeg"),
            "excel_template": (
                "template.xlsx",
                _excel_template_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["file_name"] == "scan.jpg"
    assert data["excel_template_file_name"] == "template.xlsx"
    assert [
        (item["sheet_name"], item["coordinate"], item["field_key"])
        for item in data["excel_placeholders"]
    ] == [
        ("Report", "B1", "order_no"),
        ("Report", "B2", "quantity"),
    ]


def test_analyze_adds_excel_slots_to_field_draft(admin_client, monkeypatch, tmp_path):
    async def fake_process_document(file_path: str):
        return {
            "text": "订单号：NOZS0311046\n数量：12",
            "confidence": 0.93,
            "lines": [],
            "total_lines": 2,
        }

    analysis_payload = {
        "recommended_doc_type": "出货单",
        "recommended_doc_code": "shipment_report",
        "confidence": 0.95,
        "recommended_tenant": {
            "suggest_name": "品质部",
            "suggest_code": "quality",
            "reason": "文档中出现品质管理部",
            "match_existing_tenant_id": TENANT_ID,
        },
        "detected_fields": [
            {
                "field_key": "order_no",
                "field_label": "订单号",
                "field_type": "text",
                "extraction_hint": "从订单号标签后提取",
                "review_enforced": False,
                "review_allowed_values": None,
                "sample_value": "NOZS0311046",
            }
        ],
        "suggested_examples": [],
    }

    async def fake_analyze(session):
        return analysis_payload

    monkeypatch.setattr(sdk_route.ocr_service, "process_document", fake_process_document)
    monkeypatch.setattr(sdk_route.settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(sdk_route.orchestrator, "analyze_document", fake_analyze)

    create_response = admin_client.post(
        "/api/sdk/sessions",
        files={
            "file": ("scan.jpg", b"image bytes", "image/jpeg"),
            "excel_template": (
                "template.xlsx",
                _excel_template_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
    )

    session_id = create_response.json()["id"]
    analyze_response = admin_client.post(f"/api/sdk/sessions/{session_id}/analyze")

    assert analyze_response.status_code == 200
    fields = analyze_response.json()["analysis"]["detected_fields"]
    assert [field["field_key"] for field in fields] == ["order_no", "quantity"]
    assert fields[1]["field_label"] == "quantity"
    assert "Excel" in fields[1]["extraction_hint"]


def test_sdk_session_flow_analyze_prompt_and_commit(admin_client, monkeypatch, tmp_path):
    async def fake_process_document(file_path: str):
        return {
            "text": "样品名称：小型断路器\n检验结论：合格",
            "confidence": 0.93,
            "lines": [],
            "total_lines": 2,
        }

    analysis_payload = {
        "recommended_doc_type": "检测报告",
        "recommended_doc_code": "inspection_report",
        "confidence": 0.95,
        "recommended_tenant": {
            "suggest_name": "品质部",
            "suggest_code": "quality",
            "reason": "文档中出现品质管理部",
            "match_existing_tenant_id": TENANT_ID,
        },
        "detected_fields": [
            {
                "field_key": "sample_name",
                "field_label": "样品名称",
                "field_type": "text",
                "extraction_hint": "位于样品名称标签后",
                "review_enforced": False,
                "review_allowed_values": None,
                "sample_value": "小型断路器",
            }
        ],
        "suggested_examples": [
            {
                "example_input": "样品名称：小型断路器",
                "example_output": {"sample_name": "小型断路器"},
            }
        ],
    }

    async def fake_analyze(session):
        return analysis_payload

    async def fake_generate_prompt(session):
        return "请提取字段：sample_name\n{ocr_text}"

    commit_session_payload = {}

    async def fake_commit(session):
        commit_session_payload["prompt"] = session.prompt
        commit_session_payload["cleaner_code"] = session.cleaner_code
        return {
            "tenant_id": TENANT_ID,
            "template_id": "template-1",
            "field_ids": ["field-1"],
            "example_ids": ["example-1"],
            "cleaner_module": None,
        }

    monkeypatch.setattr(sdk_route.ocr_service, "process_document", fake_process_document)
    monkeypatch.setattr(sdk_route.settings, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(sdk_route.orchestrator, "analyze_document", fake_analyze)
    monkeypatch.setattr(sdk_route.orchestrator, "generate_prompt", fake_generate_prompt)
    monkeypatch.setattr(sdk_route.orchestrator, "commit", fake_commit)

    create_response = admin_client.post(
        "/api/sdk/sessions",
        files={"file": ("report.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )
    session_id = create_response.json()["id"]

    analyze_response = admin_client.post(f"/api/sdk/sessions/{session_id}/analyze")
    assert analyze_response.status_code == 200
    assert analyze_response.json()["analysis"]["recommended_doc_type"] == "检测报告"

    confirm_response = admin_client.post(
        f"/api/sdk/sessions/{session_id}/confirm-template",
        json={
            "template_name": "检测报告",
            "template_code": "inspection_report",
            "description": "AI 生成模板",
            "tenant_id": TENANT_ID,
            "tenant_name": None,
            "tenant_code": None,
            "extraction_mode": "ocr_llm",
            "per_page_extraction": False,
            "fields": analysis_payload["detected_fields"],
            "examples": analysis_payload["suggested_examples"],
        },
    )
    assert confirm_response.status_code == 200

    prompt_response = admin_client.post(f"/api/sdk/sessions/{session_id}/prompt")
    assert prompt_response.status_code == 200
    assert "{ocr_text}" in prompt_response.json()["prompt"]

    commit_response = admin_client.post(
        f"/api/sdk/sessions/{session_id}/commit",
        json={
            "prompt": "管理员最终确认的 prompt\n{ocr_text}",
            "cleaner_code": "def clean_sample_name(value: str) -> str:\n    return value.strip()",
        },
    )
    assert commit_response.status_code == 200
    assert commit_response.json()["commit_result"]["template_id"] == "template-1"
    assert commit_session_payload == {
        "prompt": "管理员最终确认的 prompt\n{ocr_text}",
        "cleaner_code": "def clean_sample_name(value: str) -> str:\n    return value.strip()",
    }


def test_sdk_routes_require_admin(client):
    response = client.post(
        "/api/sdk/sessions",
        files={"file": ("report.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )

    assert response.status_code == 403


def test_sdk_session_owner_is_enforced(admin_client, monkeypatch, tmp_path):
    async def fake_process_document(file_path: str):
        return {
            "text": "样品名称：小型断路器",
            "confidence": 0.93,
            "lines": [],
            "total_lines": 1,
        }

    monkeypatch.setattr(sdk_route.ocr_service, "process_document", fake_process_document)
    monkeypatch.setattr(sdk_route.settings, "UPLOAD_FOLDER", str(tmp_path))

    create_response = admin_client.post(
        "/api/sdk/sessions",
        files={"file": ("report.pdf", b"%PDF-1.4 sample", "application/pdf")},
    )
    session_id = create_response.json()["id"]

    test_app = FastAPI()
    test_app.include_router(sdk_router, prefix="/api")
    test_app.dependency_overrides[get_current_user] = _other_tenant_admin_user
    try:
        from fastapi.testclient import TestClient

        with TestClient(test_app) as other_client:
            response = other_client.get(f"/api/sdk/sessions/{session_id}")
    finally:
        test_app.dependency_overrides.clear()

    assert response.status_code == 403
