"""
LLM 调用封装 — 包含三重防线的结构化输出生成器。
"""

from dataclasses import dataclass
import json
import re
import logging
from typing import Any, Generic, Literal, Type, TypeVar, overload

from pydantic import BaseModel, ValidationError
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import llm_config

logger = logging.getLogger("jd_assistent.llm")

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMProviderConfig:
    """单个 LLM provider/model 的运行配置。"""

    provider: str
    model: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class LLMGenerateResult(Generic[T]):
    """带审计元信息的结构化输出结果。"""

    data: T
    audit: dict[str, Any]


def _build_provider_chain() -> list[LLMProviderConfig]:
    """构建主模型 + fallback provider 链。"""

    chain: list[LLMProviderConfig] = []

    for item in llm_config.get_provider_chain():
        runtime_config = llm_config.get_provider_runtime_config(item["provider"])
        chain.append(
            LLMProviderConfig(
                provider=item["provider"],
                model=item["model"],
                api_key=runtime_config["api_key"],
                base_url=runtime_config["base_url"],
            )
        )

    return chain


def _get_llm(provider_config: LLMProviderConfig) -> ChatOpenAI:
    """根据 provider 配置创建 LLM 实例。"""
    kwargs = {
        "model": provider_config.model,
        "api_key": provider_config.api_key,
        "temperature": llm_config.TEMPERATURE,
        "max_tokens": llm_config.MAX_TOKENS,
        "timeout": llm_config.REQUEST_TIMEOUT_SECONDS,
    }
    if provider_config.base_url:
        kwargs["base_url"] = provider_config.base_url

    return ChatOpenAI(**kwargs)


def _should_trigger_fallback(error: Exception) -> bool:
    """判定异常是否满足 R4 的 provider 降级条件。"""

    status_code = getattr(error, "status_code", None)
    message = str(error).lower()

    if isinstance(error, TimeoutError):
        return True

    if status_code == 429:
        return True

    if isinstance(status_code, int) and 500 <= status_code < 600:
        return True

    return any(
        keyword in message
        for keyword in [
            "timeout",
            "timed out",
            "429",
            "rate limit",
            " 500",
            " 502",
            " 503",
            " 504",
        ]
    )


def _normalize_usage_metadata(response: Any) -> dict[str, int]:
    """兼容 LangChain usage_metadata / response_metadata.token_usage 结构。"""

    usage = getattr(response, "usage_metadata", None) or {}
    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}

    input_tokens = int(
        usage.get("input_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("output_tokens")
        or 0
    )
    total_tokens = int(
        usage.get("total_tokens")
        or token_usage.get("total_tokens")
        or input_tokens + output_tokens
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _response_model_name(response: Any, fallback_model: str) -> str:
    response_metadata = getattr(response, "response_metadata", None) or {}
    model_name = response_metadata.get("model_name") or response_metadata.get("model")
    return str(model_name or fallback_model)


def _coerce_response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
            for chunk in content
        )
    return str(content or "")


def _extract_json(text: str) -> str | None:
    """
    第二道防线（物理级）：从 LLM 原始输出中提取 JSON 字符串。
    剥离 ```json 等 Markdown 包裹符。
    """
    # 先尝试提取 ```json ... ``` 包裹的内容
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md_match:
        return md_match.group(1).strip()

    # 再尝试匹配裸 JSON 对象或数组
    json_match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()

    return None


@overload
async def safe_llm_generate(
    prompt: str,
    schema: Type[T],
    system_prompt: str = "",
    max_retries: int = 3,
    include_audit: Literal[False] = False,
) -> T: ...


@overload
async def safe_llm_generate(
    prompt: str,
    schema: Type[T],
    system_prompt: str = "",
    max_retries: int = 3,
    include_audit: Literal[True] = True,
) -> LLMGenerateResult[T]: ...


async def safe_llm_generate(
    prompt: str,
    schema: Type[T],
    system_prompt: str = "",
    max_retries: int = 3,
    include_audit: bool = False,
) -> T | LLMGenerateResult[T]:
    """
    三重防线 LLM 结构化输出生成器。

    Args:
        prompt: 用户侧 Prompt（含变量已替换）
        schema: 期望输出的 Pydantic 模型类
        system_prompt: 系统 Prompt（可选）
        max_retries: 最大重试次数

    Returns:
        经过校验的 Pydantic 模型实例

    Raises:
        RuntimeError: 超过最大重试次数仍无法生成有效输出
    """
    provider_chain = _build_provider_chain()
    all_attempts: list[dict[str, Any]] = []
    raw_text = ""

    for provider_index, provider_config in enumerate(provider_chain):
        llm = _get_llm(provider_config)
        current_prompt = prompt
        fallback_reason = ""

        for attempt in range(max_retries + 1):
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=current_prompt))

            try:
                try:
                    response = await llm.ainvoke(
                        messages,
                        response_format={"type": "json_object"},
                    )
                except Exception as json_mode_error:
                    if _should_trigger_fallback(json_mode_error):
                        fallback_reason = str(json_mode_error)
                        all_attempts.append(
                            {
                                "provider": provider_config.provider,
                                "model": provider_config.model,
                                "attempt": attempt + 1,
                                "stage": "invoke",
                                "status": "fallback",
                                "reason": fallback_reason,
                            }
                        )
                        logger.warning(
                            "LLM 调用触发 provider fallback: provider=%s model=%s reason=%s",
                            provider_config.provider,
                            provider_config.model,
                            fallback_reason,
                        )
                        break

                    logger.warning(
                        "API 级 JSON 模式不可用，回退为普通调用: %s",
                        str(json_mode_error),
                    )
                    response = await llm.ainvoke(messages)
            except Exception as invoke_error:
                if _should_trigger_fallback(invoke_error):
                    fallback_reason = str(invoke_error)
                    all_attempts.append(
                        {
                            "provider": provider_config.provider,
                            "model": provider_config.model,
                            "attempt": attempt + 1,
                            "stage": "invoke",
                            "status": "fallback",
                            "reason": fallback_reason,
                        }
                    )
                    logger.warning(
                        "LLM 调用触发 provider fallback: provider=%s model=%s reason=%s",
                        provider_config.provider,
                        provider_config.model,
                        fallback_reason,
                    )
                    break
                raise

            raw_text = _coerce_response_text(getattr(response, "content", ""))
            usage_metadata = _normalize_usage_metadata(response)
            response_model = _response_model_name(response, provider_config.model)
            logger.debug(
                "LLM 原始输出 (provider=%s model=%s attempt %d/%d): %s",
                provider_config.provider,
                response_model,
                attempt + 1,
                max_retries + 1,
                raw_text[:200],
            )

            json_str = _extract_json(raw_text)
            if not json_str:
                error_msg = (
                    "[系统错误] 上次输出未包含有效的 JSON 结构。"
                    "请仅输出符合要求的 JSON，不要包含任何额外文字。"
                )
                logger.warning(
                    "第二道防线拦截: 未找到 JSON 结构 (attempt %d)", attempt + 1
                )
                all_attempts.append(
                    {
                        "provider": provider_config.provider,
                        "model": response_model,
                        "attempt": attempt + 1,
                        "stage": "extract_json",
                        "status": "retry",
                        "reason": "missing_json",
                    }
                )
                current_prompt = prompt + f"\n\n{error_msg}"
                continue

            try:
                data = json.loads(json_str)
                result = schema.model_validate(data)
                audit = {
                    "provider": provider_config.provider,
                    "model": response_model,
                    "usage": usage_metadata,
                    "attempts": [*all_attempts],
                    "fallback_used": provider_index > 0,
                }
                logger.info(
                    "结构化输出生成成功: %s (provider=%s model=%s attempt=%d)",
                    schema.__name__,
                    provider_config.provider,
                    response_model,
                    attempt + 1,
                )
                if include_audit:
                    return LLMGenerateResult(data=result, audit=audit)
                return result

            except json.JSONDecodeError as decode_error:
                error_msg = (
                    f"[系统错误] JSON 解析失败: {str(decode_error)}。"
                    "请检查 JSON 格式是否正确（注意引号、逗号、括号匹配）并重新输出。"
                )
                logger.warning(
                    "第三道防线拦截: JSON 解析错误 (attempt %d): %s",
                    attempt + 1,
                    decode_error,
                )
                all_attempts.append(
                    {
                        "provider": provider_config.provider,
                        "model": response_model,
                        "attempt": attempt + 1,
                        "stage": "parse_json",
                        "status": "retry",
                        "reason": str(decode_error),
                    }
                )
                current_prompt = prompt + f"\n\n{error_msg}"

            except ValidationError as validation_error:
                error_msg = (
                    f"[系统错误] 数据校验失败: {str(validation_error)}。"
                    "请确保输出包含所有必填字段，且字段类型正确，然后重新输出。"
                )
                logger.warning(
                    "第三道防线拦截: Pydantic 校验错误 (attempt %d): %s",
                    attempt + 1,
                    validation_error,
                )
                all_attempts.append(
                    {
                        "provider": provider_config.provider,
                        "model": response_model,
                        "attempt": attempt + 1,
                        "stage": "validate_schema",
                        "status": "retry",
                        "reason": str(validation_error),
                    }
                )
                current_prompt = prompt + f"\n\n{error_msg}"
        else:
            continue

        if fallback_reason:
            continue

    raise RuntimeError(
        f"经过 provider fallback 与 {max_retries} 次重试仍无法生成有效的 {schema.__name__}。"
        f"最后一次 LLM 原始输出: {raw_text[:500]}"
    )


async def llm_chat(prompt: str, system_prompt: str = "") -> str:
    """
    普通 LLM 对话（不要求结构化输出），用于审查员等自由文本节点。
    """
    messages = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    messages.append(HumanMessage(content=prompt))

    last_error: Exception | None = None
    for provider_config in _build_provider_chain():
        llm = _get_llm(provider_config)

        try:
            response = await llm.ainvoke(messages)
            return _coerce_response_text(getattr(response, "content", ""))
        except Exception as error:
            last_error = error
            if _should_trigger_fallback(error):
                logger.warning(
                    "自由文本调用触发 provider fallback: provider=%s model=%s reason=%s",
                    provider_config.provider,
                    provider_config.model,
                    str(error),
                )
                continue
            raise

    raise RuntimeError(f"自由文本 LLM 调用失败: {last_error}")
