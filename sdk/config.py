"""OpenAI Agents SDK configuration helpers."""

from config.settings import settings


def get_sdk_model_config() -> dict:
    return {
        "model": settings.SDK_MODEL_ID or settings.LLM_MODEL_ID,
        "api_key": settings.SDK_API_KEY or settings.LLM_API_KEY,
        "base_url": settings.SDK_BASE_URL or settings.LLM_BASE_URL,
        "temperature": settings.SDK_TEMPERATURE,
    }
