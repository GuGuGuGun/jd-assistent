"""
配置模块测试。
"""

from pathlib import Path

from ..config import checkpoint_config, database_config, task_dispatcher_config


def test_database_url_is_exposed():
    """应暴露数据库连接配置，供 R1 基础设施使用。"""
    assert isinstance(database_config.DATABASE_URL, str)
    assert database_config.DATABASE_URL


def test_task_dispatcher_mode_defaults_to_local():
    """默认任务派发模式应保持 local，避免本地开发强依赖 Celery。"""
    assert task_dispatcher_config.MODE == "local"


def test_checkpoint_backend_defaults_to_memory():
    """默认 checkpoint 后端应保持 memory，避免本地开发强依赖 Redis。"""
    assert checkpoint_config.BACKEND == "memory"


def test_docker_compose_declares_api_redis_and_worker_services():
    """Docker Compose 运行面应至少包含 api、redis 与 worker 三个服务。"""
    compose_file = Path(__file__).resolve().parents[2] / "docker-compose.yml"

    assert compose_file.exists()

    content = compose_file.read_text(encoding="utf-8")
    assert "api:" in content
    assert "redis:" in content
    assert "worker:" in content
