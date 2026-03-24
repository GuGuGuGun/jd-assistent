"""
Celery 应用工厂。

设计意图：
1. Celery 为可选能力，未安装依赖时不能影响默认 local 模式启动。
2. Worker 配置集中在这里，避免业务层直接依赖 Celery 对象。
"""

from __future__ import annotations

import logging
from importlib import import_module
from types import SimpleNamespace

from ..config import celery_config

logger = logging.getLogger("jd_assistent.worker.celery")

try:
    celery_module = import_module("celery")
    Celery = getattr(celery_module, "Celery")
    CELERY_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    celery_module = SimpleNamespace(Celery=None)
    Celery = None
    CELERY_AVAILABLE = False


if CELERY_AVAILABLE and Celery is not None:
    celery_app = Celery(
        "jd_assistent",
        broker=celery_config.BROKER_URL,
        backend=celery_config.RESULT_BACKEND,
        include=["backend.worker.tasks"],
    )
    celery_app.conf.update(
        task_ignore_result=True,
        task_track_started=False,
    )
else:  # pragma: no cover
    celery_app = None


def configure_celery_app(
    broker_url: str | None = None,
    result_backend: str | None = None,
):
    """按当前环境配置刷新 Celery app。"""
    if celery_app is None:
        logger.warning("当前环境未安装 Celery，celery 调度模式将不可用")
        return None

    celery_app.conf.broker_url = broker_url or celery_config.BROKER_URL
    celery_app.conf.result_backend = result_backend or celery_config.RESULT_BACKEND
    return celery_app
