"""Lazy OpenAI Agents SDK loader.

The project already owns a top-level ``agents`` package for the document
workflow, while the OpenAI Agents SDK also imports as ``agents``. This loader
keeps production imports lazy and gives a clear error instead of breaking the
existing package at application startup.
"""

import importlib.metadata
import importlib.util
import sys
from pathlib import Path


class OpenAIAgentsSDKUnavailable(RuntimeError):
    pass


def load_agents_sdk():
    existing = sys.modules.get("_openai_agents_sdk")
    if existing:
        return existing

    module = _load_agents_sdk_from_distribution()
    required = ("Agent", "Runner", "AsyncOpenAI", "OpenAIChatCompletionsModel")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise OpenAIAgentsSDKUnavailable(
            "当前仓库的本地 agents 包遮蔽了 OpenAI Agents SDK；"
            "需要先解决 import name 冲突或在隔离环境中加载 SDK"
        )
    return module


def _load_agents_sdk_from_distribution():
    try:
        dist = importlib.metadata.distribution("openai-agents")
    except importlib.metadata.PackageNotFoundError as exc:
        raise OpenAIAgentsSDKUnavailable("OpenAI Agents SDK 未安装") from exc

    package_dir = Path(dist.locate_file("agents"))
    init_file = package_dir / "__init__.py"
    if not init_file.exists():
        raise OpenAIAgentsSDKUnavailable("OpenAI Agents SDK 包结构异常，找不到 agents/__init__.py")

    spec = importlib.util.spec_from_file_location(
        "_openai_agents_sdk",
        init_file,
        submodule_search_locations=[str(package_dir)],
    )
    if not spec or not spec.loader:
        raise OpenAIAgentsSDKUnavailable("OpenAI Agents SDK 加载失败")

    module = importlib.util.module_from_spec(spec)
    sys.modules["_openai_agents_sdk"] = module
    spec.loader.exec_module(module)
    return module


async def run_structured_agent(*, name: str, instructions: str, prompt: str, output_type):
    agents_sdk = load_agents_sdk()
    config = get_model_config()
    client = agents_sdk.AsyncOpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    model = agents_sdk.OpenAIChatCompletionsModel(
        model=config["model"],
        openai_client=client,
    )
    agent = agents_sdk.Agent(
        name=name,
        instructions=instructions,
        model=model,
        output_type=output_type,
    )
    result = await agents_sdk.Runner.run(agent, prompt)
    return result.final_output


def get_model_config():
    from sdk.config import get_sdk_model_config

    return get_sdk_model_config()
