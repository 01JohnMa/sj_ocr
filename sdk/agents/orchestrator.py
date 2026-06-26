"""Orchestration for AI template generation sessions."""

from typing import List

from services.template_service import template_service
from services.tenant_service import tenant_service
from sdk.agents.code_agent import generate_cleaner_code
from sdk.agents.doc_analyzer_agent import analyze_document as run_doc_analyzer
from sdk.agents.prompt_agent import build_fallback_prompt, generate_prompt as run_prompt_agent
from sdk.models import (
    CommitResult,
    DocumentAnalysis,
    SDKSession,
    SDKSessionState,
)


class SDKOrchestrator:
    async def analyze_document(self, session: SDKSession) -> DocumentAnalysis:
        tenants = await tenant_service.get_all_tenants(active_only=True)
        return await run_doc_analyzer(
            ocr_text=session.ocr_text,
            file_name=session.file_name,
            tenants=tenants,
        )

    async def generate_prompt(self, session: SDKSession) -> str:
        if not session.confirmed_template:
            raise ValueError("请先确认模板信息")
        try:
            return await run_prompt_agent(session.confirmed_template)
        except Exception:
            return build_fallback_prompt(session.confirmed_template)

    async def generate_code(self, session: SDKSession) -> str:
        if not session.confirmed_template:
            raise ValueError("请先确认模板信息")
        return await generate_cleaner_code(session.confirmed_template)

    async def commit(self, session: SDKSession) -> CommitResult:
        if not session.confirmed_template:
            raise ValueError("请先确认模板信息")

        confirmed = session.confirmed_template
        tenant_id = confirmed.tenant_id
        if not tenant_id:
            if not confirmed.tenant_name or not confirmed.tenant_code:
                raise ValueError("新建部门需要 tenant_name 和 tenant_code")
            tenant = await tenant_service.create_tenant({
                "name": confirmed.tenant_name,
                "code": confirmed.tenant_code,
                "description": f"由 AI 模板向导创建：{confirmed.tenant_name}",
            })
            tenant_id = tenant["id"]

        prompt = session.prompt or build_fallback_prompt(confirmed)
        excel_placeholders = [
            placeholder.model_dump()
            for placeholder in session.excel_placeholders
        ]
        template = await template_service.create_template({
            "tenant_id": tenant_id,
            "name": confirmed.template_name,
            "code": confirmed.template_code,
            "description": confirmed.description,
            "required_doc_count": 1,
            "extraction_mode": confirmed.extraction_mode,
            "per_page_extraction": confirmed.per_page_extraction,
            "extraction_prompt_template": prompt,
            "cleaner_module": None,
            "output_mode": "both" if session.excel_template_path else "bitable",
            "excel_template_file_name": session.excel_template_file_name,
            "excel_template_path": session.excel_template_path,
            "excel_template_placeholders": excel_placeholders,
        })

        field_ids: List[str] = []
        for index, field in enumerate(confirmed.fields):
            created = await template_service.create_field(
                template["id"],
                {
                    **field.model_dump(),
                    "sort_order": index,
                },
            )
            if created.get("id"):
                field_ids.append(created["id"])

        example_ids: List[str] = []
        for index, example in enumerate(confirmed.examples):
            created = await template_service.create_example(
                template["id"],
                {
                    **example.model_dump(),
                    "sort_order": index,
                    "is_active": True,
                },
            )
            if created.get("id"):
                example_ids.append(created["id"])

        session.state = SDKSessionState.COMMITTED
        return CommitResult(
            tenant_id=tenant_id,
            template_id=template["id"],
            field_ids=field_ids,
            example_ids=example_ids,
            cleaner_module=None,
        )


orchestrator = SDKOrchestrator()
