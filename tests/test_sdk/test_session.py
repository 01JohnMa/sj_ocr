from sdk.models import SDKSessionState
from sdk.session import SDKSessionStore


def test_session_store_expires_old_sessions(monkeypatch):
    now = {"value": 1000.0}
    store = SDKSessionStore(ttl_seconds=10, now=lambda: now["value"])

    session = store.create(
        file_name="report.pdf",
        file_path="/tmp/report.pdf",
        ocr_text="样品名称：小型断路器",
        ocr_confidence=0.91,
        page_count=1,
        user_id="user-1",
    )

    assert store.get(session.id).state == SDKSessionState.OCR_COMPLETED

    now["value"] = 1011.0

    assert store.get(session.id) is None


def test_session_store_delete_removes_session():
    store = SDKSessionStore(ttl_seconds=10)
    session = store.create(
        file_name="report.pdf",
        file_path="/tmp/report.pdf",
        ocr_text="样品名称：小型断路器",
        ocr_confidence=0.91,
        page_count=1,
        user_id="user-1",
    )

    assert store.delete(session.id) is True
    assert store.get(session.id) is None
