"""R4 阶段 LLM fallback 与成本归因测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import BaseModel

from ..services import llm_service
from ..services.cost_calculator import calculate_cost_usd


class DemoSchema(BaseModel):
    title: str


class FakeLLM:
    """用于测试 safe_llm_generate 的最小假 LLM。"""

    def __init__(self, responses):
        self._responses = list(responses)

    async def ainvoke(self, _messages, response_format=None):
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_safe_llm_generate_falls_back_and_returns_audit(monkeypatch):
    """主模型超时后，应切到 fallback 模型并返回 usage 审计信息。"""

    provider_chain = [
        llm_service.LLMProviderConfig(
            provider="openai",
            model="gpt-4o",
            api_key="primary-key",
            base_url="",
        ),
        llm_service.LLMProviderConfig(
            provider="anthropic",
            model="claude-3-5-sonnet",
            api_key="fallback-key",
            base_url="",
        ),
    ]

    fake_llms = {
        "gpt-4o": FakeLLM([TimeoutError("request timed out")]),
        "claude-3-5-sonnet": FakeLLM(
            [
                SimpleNamespace(
                    content='{"title":"R4 fallback ok"}',
                    usage_metadata={
                        "input_tokens": 120,
                        "output_tokens": 45,
                        "total_tokens": 165,
                    },
                    response_metadata={"model_name": "claude-3-5-sonnet"},
                )
            ]
        ),
    }

    monkeypatch.setattr(llm_service, "_build_provider_chain", lambda: provider_chain)
    monkeypatch.setattr(
        llm_service,
        "_get_llm",
        lambda provider_config: fake_llms[provider_config.model],
    )

    raw_result = await llm_service.safe_llm_generate(
        prompt="请返回 JSON",
        schema=DemoSchema,
        include_audit=True,
    )
    result = cast(llm_service.LLMGenerateResult[DemoSchema], raw_result)

    assert result.data.title == "R4 fallback ok"
    assert result.audit["provider"] == "anthropic"
    assert result.audit["model"] == "claude-3-5-sonnet"
    assert result.audit["fallback_used"] is True
    assert result.audit["usage"]["total_tokens"] == 165
    assert result.audit["attempts"][0]["status"] == "fallback"


def test_calculate_cost_usd_uses_known_model_pricing():
    """成本计算器应基于模型单价输出稳定的美元估算值。"""

    cost = calculate_cost_usd(
        "gpt-4o",
        {
            "input_tokens": 2_000,
            "output_tokens": 500,
            "total_tokens": 2_500,
        },
    )

    assert cost == pytest.approx(0.0175)
