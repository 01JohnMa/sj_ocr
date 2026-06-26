import pytest

from sdk.agents.orchestrator import SDKOrchestrator
from sdk.models import (
    ConfirmTemplateRequest,
    ExcelTemplatePlaceholder,
    SDKSession,
    SDKSessionState,
)


@pytest.mark.asyncio
async def test_commit_persists_excel_template_metadata(monkeypatch):
    captured_template_payload = {}

    async def fake_create_template(payload):
        captured_template_payload.update(payload)
        return {"id": "template-1"}

    async def fake_create_field(template_id, payload):
        return {"id": f"field-{payload['sort_order']}"}

    async def fake_create_example(template_id, payload):
        return {"id": f"example-{payload['sort_order']}"}

    monkeypatch.setattr(
        "sdk.agents.orchestrator.template_service.create_template",
        fake_create_template,
    )
    monkeypatch.setattr(
        "sdk.agents.orchestrator.template_service.create_field",
        fake_create_field,
    )
    monkeypatch.setattr(
        "sdk.agents.orchestrator.template_service.create_example",
        fake_create_example,
    )

    session = SDKSession(
        id="session-1",
        file_name="scan.jpg",
        file_path="/tmp/scan.jpg",
        excel_template_file_name="template.xlsx",
        excel_template_path="/tmp/template.xlsx",
        excel_placeholders=[
            ExcelTemplatePlaceholder(
                sheet_name="Report",
                coordinate="B1",
                field_key="order_no",
                raw_value="{{order_no}}",
            )
        ],
        ocr_text="订单号：NOZS0311046",
        ocr_confidence=0.93,
        page_count=1,
        user_id="user-1",
        state=SDKSessionState.TEMPLATE_CONFIRMED,
        created_at=1000,
        updated_at=1000,
        confirmed_template=ConfirmTemplateRequest(
            template_name="出货单",
            template_code="shipment_report",
            tenant_id="tenant-1",
            fields=[
                {
                    "field_key": "order_no",
                    "field_label": "订单号",
                    "field_type": "text",
                    "extraction_hint": "从订单号标签后提取",
                }
            ],
        ),
    )

    result = await SDKOrchestrator().commit(session)

    assert result.template_id == "template-1"
    assert captured_template_payload["output_mode"] == "both"
    assert captured_template_payload["excel_template_file_name"] == "template.xlsx"
    assert captured_template_payload["excel_template_path"] == "/tmp/template.xlsx"
    assert captured_template_payload["excel_template_placeholders"] == [
        {
            "sheet_name": "Report",
            "coordinate": "B1",
            "field_key": "order_no",
            "raw_value": "{{order_no}}",
        }
    ]
