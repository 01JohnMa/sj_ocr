"""批量处理入队行为测试。"""

from unittest.mock import AsyncMock, patch

from tests.conftest import DOCUMENT_ID, TENANT_ID, TEMPLATE_ID


def test_batch_process_persists_executable_items_and_does_not_start_in_api(client):
    """批量处理接口只入队，并把 worker 执行所需模板参数持久化到 job items。"""
    paired_document_id = "22222222-2222-4222-8222-222222222222"
    paired_template_id = "33333333-3333-4333-8333-333333333333"

    with patch("api.routes.documents.batch.template_service") as mock_templates, \
         patch("api.routes.documents.batch.supabase_service") as mock_supabase, \
         patch("api.routes.documents.batch.create_job", new_callable=AsyncMock, return_value="job-batch") as mock_create_job, \
         patch("api.routes.documents.batch.find_job_by_dedupe_key", new_callable=AsyncMock, return_value=None), \
         patch("api.routes.documents.batch._run_batch_job", new_callable=AsyncMock) as mock_run_batch:
        mock_templates.get_template = AsyncMock(side_effect=[
            {"id": TEMPLATE_ID, "tenant_id": TENANT_ID},
        ])
        mock_supabase.get_document = AsyncMock(side_effect=[
            {"id": DOCUMENT_ID, "tenant_id": TENANT_ID, "user_id": "user-1"},
            {"id": paired_document_id, "tenant_id": TENANT_ID, "user_id": "user-1"},
        ])
        mock_supabase.update_document_status = AsyncMock()

        resp = client.post(
            "/api/documents/batch-process",
            json={
                "items": [
                    {
                        "document_id": DOCUMENT_ID,
                        "template_id": TEMPLATE_ID,
                        "paired_document_id": paired_document_id,
                        "paired_template_id": paired_template_id,
                        "custom_push_name": "推送名",
                    }
                ]
            },
        )

    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-batch", "status": "queued"}
    mock_run_batch.assert_not_called()

    batch_items = mock_create_job.call_args.kwargs["batch_items"]
    assert batch_items == [
        {
            "index": 0,
            "type": "merge",
            "document_ids": [DOCUMENT_ID, paired_document_id],
            "document_id": DOCUMENT_ID,
            "template_id": TEMPLATE_ID,
            "paired_document_id": paired_document_id,
            "paired_template_id": paired_template_id,
            "custom_push_name": "推送名",
            "status": "queued",
        }
    ]
