"""VLM 服务异步行为测试。"""

import inspect
from unittest.mock import MagicMock

import pytest

from services import vlm_service as vlm_module


def test_vlm_call_is_async():
    """VLM 网络请求入口必须是 async，避免堵塞 FastAPI 事件循环。"""
    assert inspect.iscoroutinefunction(vlm_module.VLMService._call_vlm)


def test_image_preparation_is_async():
    """图片/PDF 准备入口必须是 async，避免同步转图和 base64 编码堵塞事件循环。"""
    assert inspect.iscoroutinefunction(vlm_module.VLMService.get_image_base64_list)


@pytest.mark.asyncio
async def test_call_vlm_awaits_async_openai_client(monkeypatch):
    """_call_vlm 应 await AsyncOpenAI 的 chat completion 调用。"""
    service = vlm_module.VLMService()
    service._client = None

    message = MagicMock(content='{"sample_name":"LED"}')
    choice = MagicMock(message=message)
    response = MagicMock(choices=[choice])

    class FakeCompletions:
        async def create(self, **kwargs):
            self.kwargs = kwargs
            return response

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = MagicMock()
            self.chat.completions = FakeCompletions()

    monkeypatch.setattr(vlm_module.settings, "VLM_API_KEY", "test-key")
    monkeypatch.setattr(vlm_module.settings, "VLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(vlm_module.settings, "VLM_MODEL_ID", "test-vlm")
    monkeypatch.setattr(vlm_module, "AsyncOpenAI", FakeClient)

    raw = await service._call_vlm("base64-image", "extract fields")

    assert raw == '{"sample_name":"LED"}'
    assert isinstance(service._client, FakeClient)
    assert service._client.chat.completions.kwargs["model"] == "test-vlm"
