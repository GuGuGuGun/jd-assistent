"""LLM Token 成本核算工具。"""

from __future__ import annotations

from collections.abc import Mapping

# 设计意图：R4 先提供稳定、可审计的估算能力，避免把成本计算散落到各节点。
# 单价允许后续通过配置中心或数据库替换；当前实现先覆盖计划中的主链模型。
MODEL_PRICING_USD_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0},
}


def _pick_pricing(model_name: str) -> dict[str, float] | None:
    normalized_model = (model_name or "").strip().lower()

    if not normalized_model:
        return None

    if normalized_model in MODEL_PRICING_USD_PER_MILLION_TOKENS:
        return MODEL_PRICING_USD_PER_MILLION_TOKENS[normalized_model]

    for candidate, pricing in MODEL_PRICING_USD_PER_MILLION_TOKENS.items():
        if normalized_model.startswith(candidate):
            return pricing

    return None


def calculate_cost_usd(model_name: str, usage: Mapping[str, int] | None) -> float:
    """根据模型与 token usage 估算美元成本。"""

    if not usage:
        return 0.0

    pricing = _pick_pricing(model_name)
    if pricing is None:
        return 0.0

    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input_token_count")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output_token_count")
        or 0
    )

    cost = (input_tokens / 1_000_000) * pricing["input"] + (
        output_tokens / 1_000_000
    ) * pricing["output"]
    return round(cost, 8)
