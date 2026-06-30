"""共享测试 fixture。"""

import pytest
import importlib.util
import importlib.machinery
import os
import sys
import types
from fastapi.testclient import TestClient

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("VLM_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("OCR_INIT_ON_STARTUP", "false")

if "paddleocr" in sys.modules:
    paddleocr_available = True
else:
    paddleocr_available = importlib.util.find_spec("paddleocr") is not None

if not paddleocr_available:
    paddleocr_stub = types.ModuleType("paddleocr")
    paddleocr_stub.__spec__ = importlib.machinery.ModuleSpec("paddleocr", loader=None)

    class PaddleOCR:
        pass

    paddleocr_stub.PaddleOCR = PaddleOCR
    sys.modules["paddleocr"] = paddleocr_stub


USER_ID = "11111111-1111-4111-8111-111111111111"
TENANT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DOCUMENT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
TEMPLATE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

MOCK_DOCUMENT = {
    "id": DOCUMENT_ID,
    "user_id": USER_ID,
    "tenant_id": TENANT_ID,
    "status": "uploaded",
    "file_path": "/tmp/test.pdf",
    "template_id": TEMPLATE_ID,
    "custom_push_name": None,
}


@pytest.fixture(autouse=True)
def stub_agents_workflow(monkeypatch):
    """路由/worker 单测会 mock OCR workflow，不需要导入真实 LangChain/LangGraph。"""
    fake_workflow = types.ModuleType("agents.workflow")
    fake_workflow.ocr_workflow = object()
    fake_workflow.OCRWorkflow = object
    monkeypatch.setitem(sys.modules, "agents.workflow", fake_workflow)


async def _mock_current_user():
    from api.dependencies.auth import CurrentUser

    return CurrentUser(
        user_id=USER_ID,
        token="test-token",
        tenant_id=TENANT_ID,
        role="user",
    )


@pytest.fixture
def client():
    from api.dependencies.auth import get_current_user, get_crm_current_user
    from api.main import app

    app.dependency_overrides[get_current_user] = _mock_current_user
    app.dependency_overrides[get_crm_current_user] = _mock_current_user
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def unauth_client():
    from api.main import app

    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
