"""
FastAPI 应用入口。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import app_config
from .api.admin_routes import router as admin_router
from .api.auth_routes import router as auth_router
from .api.routes import router as api_router
from .services.task_store import task_store

# 配置日志
logging.basicConfig(
    level=logging.DEBUG if app_config.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("jd_assistent")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期钩子，启动时预热任务持久化基础设施。"""
    await task_store.ensure_ready()
    yield
    await task_store.shutdown()


app = FastAPI(
    title="智能简历优化系统",
    description="基于 Multi-Agent 架构的智能简历优化 API",
    version="0.1.7",
    lifespan=lifespan,
)

# ═══ CORS 中间件 ═══
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══ 全局异常处理器 ═══


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """处理业务逻辑校验错误。"""
    logger.warning("业务校验错误: %s", str(exc))
    return JSONResponse(
        status_code=400,
        content={"error": "请求参数错误", "detail": str(exc)},
    )


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError):
    """处理运行时错误（如 LLM 调用失败）。"""
    logger.error("运行时错误: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "服务内部错误", "detail": str(exc)},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未知异常。"""
    logger.exception("未知异常: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": "服务器内部错误", "detail": "请稍后重试或联系管理员"},
    )


# ═══ 注册路由 ═══
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(api_router)


@app.get("/health")
async def health_check():
    """健康检查。"""
    return {"status": "ok", "version": "0.1.7"}
