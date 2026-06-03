# services/vlm_service.py
"""VLM 多模态提取服务

使用 qwen3.7-plus（DashScope OpenAI 兼容接口）直接从图片提取结构化字段，
无需先经过 OCR 转文字，适合手写/复杂版式文档。
"""

import base64
import io
import json
import os
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger
from openai import AsyncOpenAI

from config.settings import settings
from services.base import build_field_table


# ── VLM Prompt 模板 ──────────────────────────────────────────────────────────

VLM_EXTRACTION_PROMPT = """你是一个专业的数据提取助手，专门处理{doc_type}。
请从图片中精准提取以下字段。

**目标字段：**
{field_list}

**处理规则：**
1. 日期格式统一为 YYYY-MM-DD
2. 缺失字段值设为空字符串 ""
3. 数值保持原文精度，保留单位
4. 确保 JSON 语法正确（使用英文双引号、英文逗号）
5. 所有字段值必须严格来自当前图片原文，禁止使用示例中的具体数值

{examples_section}
**输出要求：**
- 仅输出扁平的 JSON 对象，只包含上述目标字段，禁止添加任何其他字段
- 不要包含任何解释、引言或 Markdown 代码块标记"""


def _build_vlm_examples_section(examples: List[Dict[str, Any]]) -> str:
    """
    VLM 专用示例段落构建：只展示输出 JSON 的键结构（值用 "..." 占位），
    避免模型将示例中的具体数值照抄到提取结果中。
    """
    if not examples:
        return ""

    section = "**输出格式示例（仅供格式参考，值必须来自图片原文）：**\n"
    for i, ex in enumerate(examples, 1):
        example_output = ex.get("example_output", {})
        if isinstance(example_output, str):
            try:
                example_output = json.loads(example_output)
            except json.JSONDecodeError:
                example_output = {}
        structure = {k: "..." for k in example_output.keys()}
        output_str = json.dumps(structure, ensure_ascii=False)
        section += f"\n示例{i}输出结构：\n{output_str}\n"
    return section


class VLMService:
    """多模态 VLM 提取服务（单例）"""

    _instance: Optional["VLMService"] = None
    _client: Optional[AsyncOpenAI] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not settings.VLM_API_KEY:
                raise RuntimeError(
                    "VLM_API_KEY 未配置，请在 .env 中设置 VLM_API_KEY"
                )
            self._client = AsyncOpenAI(
                api_key=settings.VLM_API_KEY,
                base_url=settings.VLM_BASE_URL,
            )
        return self._client

    # ── 图片处理 ──────────────────────────────────────────────────────────────

    @staticmethod
    def encode_image(image_path: str) -> str:
        """将本地图片文件编码为 base64 字符串"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def pil_image_to_base64(pil_image) -> str:
        """将 PIL Image 对象转为 base64 字符串（JPEG 格式）"""
        buf = io.BytesIO()
        pil_image.save(buf, format="JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    async def get_image_base64_list(self, file_path: str) -> List[str]:
        """异步获取文件的 base64 图片列表，避免同步 I/O 和 PDF 转图阻塞事件循环。"""
        return await asyncio.to_thread(self._get_image_base64_list_sync, file_path)

    def _get_image_base64_list_sync(self, file_path: str) -> List[str]:
        """
        获取文件的 base64 图片列表。

        - 图片文件（jpg/png/bmp/tiff）：直接编码，返回单元素列表
        - PDF 文件：用 pdf2image 逐页转图，返回每页 base64 列表
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._pdf_to_base64_list(file_path)
        else:
            return [self.encode_image(file_path)]

    @staticmethod
    def _pdf_to_base64_list(pdf_path: str) -> List[str]:
        """PDF 逐页转图片，返回 base64 列表（依赖 pdf2image + poppler）"""
        try:
            from pdf2image import convert_from_path
        except ImportError:
            raise RuntimeError(
                "pdf2image 未安装，请执行 pip install pdf2image 并确保系统已安装 poppler-utils"
            )

        pages = convert_from_path(pdf_path, dpi=200)
        result = []
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format="JPEG", quality=95)
            result.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
        logger.debug(f"PDF 转图完成: {pdf_path}，共 {len(result)} 页")
        return result

    # ── Prompt 构建 ───────────────────────────────────────────────────────────

    @staticmethod
    def build_vlm_prompt(template: Dict[str, Any]) -> str:
        """
        根据模板字段配置构建 VLM 提取 prompt（纯字段提取，不含分类）。

        示例仅展示输出 JSON 的键结构，不暴露具体值，避免模型照抄示例数据。
        """
        fields = template.get("template_fields", [])
        field_list = build_field_table(fields)

        examples = template.get("template_examples", [])
        examples_section = _build_vlm_examples_section(examples)

        return VLM_EXTRACTION_PROMPT.format(
            doc_type=template.get("name", "文档"),
            field_list=field_list,
            examples_section=examples_section,
        )

    # ── VLM 调用 ──────────────────────────────────────────────────────────────

    async def _call_vlm(self, b64_image: str, prompt: str) -> str:
        """对单张图片调用 VLM，返回原始文本响应"""
        client = self._get_client()
        response = await client.chat.completions.create(
            model=settings.VLM_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_image}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            temperature=settings.VLM_TEMPERATURE,
        )
        return response.choices[0].message.content or ""

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def extract_from_image(
        self, file_path: str, template: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        从图片/PDF 中提取结构化字段。

        - 图片：单次 VLM 调用
        - PDF：逐页调用，后页非空值覆盖前页空值（合并策略）

        Returns:
            与 OCR+LLM 路径 extraction_data 格式一致的 dict
        """
        from agents.json_cleaner import parse_llm_json

        prompt = self.build_vlm_prompt(template)
        b64_list = await self.get_image_base64_list(file_path)

        logger.info(
            f"VLM 提取开始: {file_path}，共 {len(b64_list)} 页，"
            f"模板: {template.get('name')}"
        )

        merged: Dict[str, Any] = {}

        for page_idx, b64 in enumerate(b64_list, 1):
            try:
                raw = await self._call_vlm(b64, prompt)
                page_data = parse_llm_json(raw)

                if "raw_response" in page_data:
                    logger.warning(
                        f"VLM 第 {page_idx} 页返回非 JSON，原始内容: {raw[:200]}"
                    )
                    continue

                # 合并：后页非空值覆盖前页空值
                for k, v in page_data.items():
                    if v not in (None, "", []) or k not in merged:
                        merged[k] = v

                logger.debug(
                    f"VLM 第 {page_idx}/{len(b64_list)} 页提取完成，"
                    f"{len(page_data)} 个字段"
                )
            except Exception as e:
                logger.error(f"VLM 第 {page_idx} 页处理失败: {e}")
                # 单页失败不中断，继续处理其他页

        logger.info(f"VLM 提取完成，合并后共 {len(merged)} 个字段")
        return merged

    async def extract_per_page(
        self, file_path: str, template: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        逐页提取，每页独立返回一个结果（用于 merge 模式的多样品场景）。

        与 extract_from_image 的区别：不做跨页合并，每页作为独立样品。

        Returns:
            [{"page": 1, "data": {...}}, {"page": 2, "data": {...}}, ...]
        """
        from agents.json_cleaner import parse_llm_json

        prompt = self.build_vlm_prompt(template)
        b64_list = await self.get_image_base64_list(file_path)

        logger.info(
            f"VLM 逐页提取开始: {file_path}，共 {len(b64_list)} 页，"
            f"模板: {template.get('name')}"
        )

        results: List[Dict[str, Any]] = []
        for page_idx, b64 in enumerate(b64_list, 1):
            try:
                raw = await self._call_vlm(b64, prompt)
                page_data = parse_llm_json(raw)

                if "raw_response" in page_data:
                    logger.warning(
                        f"VLM 第 {page_idx} 页返回非 JSON，跳过"
                    )
                    continue

                results.append({"page": page_idx, "data": page_data})
                logger.debug(
                    f"VLM 逐页提取 第 {page_idx}/{len(b64_list)} 页完成，"
                    f"{len(page_data)} 个字段"
                )
            except Exception as e:
                logger.error(f"VLM 逐页提取 第 {page_idx} 页失败: {e}")

        logger.info(f"VLM 逐页提取完成，共 {len(results)} 页有效结果")
        return results


# 单例实例
vlm_service = VLMService()
