"""Prompt generation agent."""

from pydantic import BaseModel

from sdk.models import ConfirmTemplateRequest, SuggestedExample
from sdk.openai_agents_runtime import run_structured_agent


class PromptOutput(BaseModel):
    prompt: str


PROMPT_AGENT_INSTRUCTIONS = """你是 NeoFlow 文档提取 Prompt 生成助手。
基于已确认的模板字段和 few-shot 示例，生成一个完整 extraction prompt。
输出必须保留 {ocr_text} 占位符，要求模型仅输出扁平 JSON，不输出 Markdown。"""


def build_fallback_prompt(confirmed: ConfirmTemplateRequest) -> str:
    field_lines = []
    for index, field in enumerate(confirmed.fields, start=1):
        hint = f"；提示：{field.extraction_hint}" if field.extraction_hint else ""
        field_lines.append(f"{index}. {field.field_label} -> {field.field_key} ({field.field_type}){hint}")

    example_lines = []
    for example in confirmed.examples:
        example_lines.append(
            f"输入片段：{example.example_input}\n期望输出：{example.example_output}"
        )
    examples_section = "\n\n".join(example_lines) if example_lines else "无"

    return f"""你是一个专业的数据提取助手，专门处理{confirmed.template_name}的OCR识别文本。请从用户提供的文本中精准提取以下字段。

目标字段：
{chr(10).join(field_lines)}

few-shot 示例：
{examples_section}

输出要求：
- 仅输出扁平 JSON 对象
- 缺失字段值设为空字符串 ""
- 不要包含解释、引言或 Markdown 代码块

OCR文本：
{{ocr_text}}"""


async def generate_prompt(confirmed: ConfirmTemplateRequest) -> str:
    prompt = (
        f"模板名称: {confirmed.template_name}\n"
        f"模板 code: {confirmed.template_code}\n"
        f"字段: {[field.model_dump() for field in confirmed.fields]}\n"
        f"示例: {[example.model_dump() for example in confirmed.examples]}\n"
    )
    result = await run_structured_agent(
        name="prompt_agent",
        instructions=PROMPT_AGENT_INSTRUCTIONS,
        prompt=prompt,
        output_type=PromptOutput,
    )
    output = result if isinstance(result, PromptOutput) else PromptOutput.model_validate(result)
    if "{ocr_text}" not in output.prompt:
        return build_fallback_prompt(confirmed)
    return output.prompt
