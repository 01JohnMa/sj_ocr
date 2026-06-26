"""Cleaner code preview agent.

The first implementation only returns code for administrator preview. It is not
imported or executed by the runtime pipeline.
"""

from pydantic import BaseModel

from sdk.models import ConfirmTemplateRequest
from sdk.openai_agents_runtime import run_structured_agent


class CleanerCodeOutput(BaseModel):
    code: str


CODE_AGENT_INSTRUCTIONS = """你是 Python 数据清洗函数生成助手。
只生成字段级 clean_<field_key>(value: str) -> str 函数，代码必须自包含。
不要包含文件读写、网络请求、eval、exec、importlib、subprocess 或数据库操作。"""


async def generate_cleaner_code(confirmed: ConfirmTemplateRequest) -> str:
    prompt = (
        f"模板名称: {confirmed.template_name}\n"
        f"字段: {[field.model_dump() for field in confirmed.fields]}\n"
    )
    result = await run_structured_agent(
        name="code_agent",
        instructions=CODE_AGENT_INSTRUCTIONS,
        prompt=prompt,
        output_type=CleanerCodeOutput,
    )
    output = result if isinstance(result, CleanerCodeOutput) else CleanerCodeOutput.model_validate(result)
    return output.code
