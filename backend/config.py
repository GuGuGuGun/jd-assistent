"""
配置管理模块 — 统一管理所有环境变量与系统配置。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

_default_database_url = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./jd_assistent.db",
)
_default_alembic_url = _default_database_url.replace("+aiosqlite", "").replace(
    "+asyncpg", ""
)


class LLMConfig:
    """LLM 相关配置。"""

    PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")
    MODEL: str = os.getenv("LLM_MODEL", "gpt-4o")
    API_KEY: str = os.getenv("LLM_API_KEY", "")
    BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))
    REQUEST_TIMEOUT_SECONDS: float = float(
        os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "30")
    )
    FALLBACK_PROVIDERS_RAW: str = os.getenv(
        "LLM_FALLBACK_PROVIDERS",
        "anthropic:claude-3-5-sonnet,google:gemini-1.5-pro",
    )

    @classmethod
    def get_provider_chain(cls) -> list[dict[str, str]]:
        """返回按优先级排序的 provider/model 链。"""

        chain: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def append_provider(provider: str, model: str):
            normalized_provider = provider.strip().lower()
            normalized_model = model.strip()
            if not normalized_provider or not normalized_model:
                return

            key = (normalized_provider, normalized_model)
            if key in seen:
                return

            seen.add(key)
            chain.append(
                {
                    "provider": normalized_provider,
                    "model": normalized_model,
                }
            )

        append_provider(cls.PROVIDER, cls.MODEL)

        for item in cls.FALLBACK_PROVIDERS_RAW.split(","):
            raw_item = item.strip()
            if not raw_item:
                continue

            if ":" in raw_item:
                provider, model = raw_item.split(":", 1)
            else:
                provider, model = cls.PROVIDER, raw_item

            append_provider(provider, model)

        return chain

    @classmethod
    def get_provider_runtime_config(cls, provider: str) -> dict[str, str]:
        """读取 provider 级别的运行配置，未单独配置时回退到全局配置。"""

        env_prefix = provider.strip().upper().replace("-", "_")
        api_key = os.getenv(f"{env_prefix}_API_KEY", "") or cls.API_KEY
        base_url = os.getenv(f"{env_prefix}_BASE_URL", "") or cls.BASE_URL

        return {
            "api_key": api_key,
            "base_url": base_url,
        }


class AppConfig:
    """应用服务配置。"""

    HOST: str = os.getenv("API_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("API_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"


class TaskDispatcherConfig:
    """任务派发配置。"""

    MODE: str = os.getenv("TASK_DISPATCHER_MODE", "local")


class CeleryConfig:
    """Celery 运行配置。"""

    BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
    RESULT_BACKEND: str = os.getenv(
        "CELERY_RESULT_BACKEND",
        "redis://127.0.0.1:6379/2",
    )


class EventBusConfig:
    """SSE 事件传输配置。"""

    BACKEND: str = os.getenv("SSE_EVENT_BUS_BACKEND", "memory")
    # 设计意图：Redis 事件总线与 Celery 常常共用同一个 Redis 实例。
    # 当未单独提供 REDIS_URL 时，复用 broker 地址可以减少 compose / 部署配置重复；
    # 若 Redis 实际不可达，事件总线层仍会安全回退到内存实现，不影响默认本地模式。
    REDIS_URL: str = os.getenv("REDIS_URL", "") or CeleryConfig.BROKER_URL
    REDIS_CHANNEL_PREFIX: str = os.getenv(
        "SSE_REDIS_CHANNEL_PREFIX",
        "jd-assistent:sse",
    )


class CheckpointConfig:
    """LangGraph checkpoint 配置。"""

    BACKEND: str = os.getenv("LANGGRAPH_CHECKPOINT_BACKEND", "memory")
    # 设计意图：checkpoint 与 SSE/Celery 都可复用现有 Redis 基础设施，避免额外引入新中间件。
    # 默认仍走 memory，确保本地零配置模式不变；当显式切到 redis 时，再复用现有 Redis 地址。
    REDIS_URL: str = (
        os.getenv("LANGGRAPH_CHECKPOINT_REDIS_URL", "")
        or os.getenv("REDIS_URL", "")
        or CeleryConfig.BROKER_URL
    )
    REDIS_KEY_PREFIX: str = os.getenv(
        "LANGGRAPH_CHECKPOINT_REDIS_KEY_PREFIX",
        "jd-assistent:checkpoint",
    )


class DatabaseConfig:
    """数据库相关配置。"""

    DATABASE_URL: str = _default_database_url
    ALEMBIC_DATABASE_URL: str = os.getenv(
        "ALEMBIC_DATABASE_URL",
        _default_alembic_url,
    )


class AuthConfig:
    """认证与权限相关配置。"""

    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-jwt-secret-change-me")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))
    ADMIN_EMAILS: tuple[str, ...] = tuple(
        email.strip().lower()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    )


# 全局配置实例
llm_config = LLMConfig()
app_config = AppConfig()
task_dispatcher_config = TaskDispatcherConfig()
celery_config = CeleryConfig()
database_config = DatabaseConfig()
event_bus_config = EventBusConfig()
checkpoint_config = CheckpointConfig()
auth_config = AuthConfig()
