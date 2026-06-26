
# api/main.py
"""FastAPI 主应用"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from loguru import logger
import asyncio
import os

from api.exceptions import AppException
from config.settings import settings
from services.ocr_service import ocr_service
from services.supabase_service import supabase_service
from api.routes import documents_router, health_router
from api.routes.tenants import router as tenants_router
from api.routes.admin import router as admin_router
from api.routes.sdk import router as sdk_router
from api.routes.crm import router as crm_router


# 配置日志
logger.add(
    settings.LOG_FILE,
    rotation="10 MB",
    retention="7 days",
    level=settings.LOG_LEVEL
)


# ============ 超时恢复任务 ============

STUCK_RECOVERY_INTERVAL_SECONDS = 600  # 每 10 分钟执行一次
STUCK_RECOVERY_TIMEOUT_MINUTES = 30    # processing 超过 30 分钟视为超时


async def _run_stuck_document_recovery() -> None:
    """重置超时的 processing 文档，防止永久卡死。"""
    try:
        count = await supabase_service.reset_stuck_processing_documents(
            timeout_minutes=STUCK_RECOVERY_TIMEOUT_MINUTES
        )
        if count:
            logger.warning(f"[恢复任务] 重置了 {count} 个超时 processing 文档")
        else:
            logger.debug("[恢复任务] 无超时 processing 文档")
    except Exception as e:
        logger.error(f"[恢复任务] 执行失败: {e}")


async def _stuck_recovery_loop() -> None:
    """后台循环，每隔 STUCK_RECOVERY_INTERVAL_SECONDS 秒执行一次恢复任务。"""
    while True:
        await asyncio.sleep(STUCK_RECOVERY_INTERVAL_SECONDS)
        await _run_stuck_document_recovery()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化服务
    logger.info("=" * 50)
    logger.info(f"正在启动 {settings.APP_NAME}...")
    logger.info("=" * 50)
    
    # 创建必要的目录
    os.makedirs(settings.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(os.path.dirname(settings.LOG_FILE), exist_ok=True)
    logger.info(f"✓ 上传目录: {settings.UPLOAD_FOLDER}")
    
    # 初始化OCR服务
    if settings.OCR_ENABLED and settings.OCR_INIT_ON_STARTUP:
        try:
            await ocr_service.initialize()
            logger.info("✓ OCR服务初始化成功")
        except Exception as e:
            logger.opt(exception=e).warning("OCR服务初始化失败，可稍后重试")
    elif settings.OCR_ENABLED:
        logger.warning("⚠ OCR服务启用，但启动时初始化已跳过（OCR_INIT_ON_STARTUP=false）")
    else:
        logger.warning("⚠ OCR服务已禁用（OCR_ENABLED=false）")
    
    # 初始化Supabase服务
    try:
        await supabase_service.initialize()
        logger.info("✓ Supabase服务初始化成功")
    except Exception as e:
        logger.opt(exception=e).warning("Supabase服务初始化失败，请检查配置")

    # 启动超时文档恢复后台任务
    asyncio.create_task(_stuck_recovery_loop())
    logger.info("✓ 超时文档恢复任务已启动（每 10 分钟执行一次）")

    logger.info("=" * 50)
    logger.info(f"✓ {settings.APP_NAME} 启动完成")
    logger.info(f"  API文档: http://{settings.HOST}:{settings.PORT}/docs")
    logger.info(f"  健康检查: http://{settings.HOST}:{settings.PORT}/api/health")
    logger.info("=" * 50)
    
    yield
    
    # 关闭时清理
    logger.info("正在关闭服务...")
    await ocr_service.close()
    logger.info("服务已关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    description="NeoFlow 智能文档处理平台",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health_router, prefix="/api", tags=["健康检查"])
app.include_router(documents_router, prefix="/api/documents", tags=["文档处理"])
app.include_router(tenants_router, prefix="/api", tags=["租户管理"])
app.include_router(admin_router, prefix="/api", tags=["管理员配置"])
app.include_router(sdk_router, prefix="/api", tags=["AI模板生成"])
app.include_router(crm_router, prefix="/api", tags=["CRM集成"])


@app.exception_handler(AppException)
async def app_exception_handler(request, exc: AppException):
    """处理业务异常 - 返回统一格式"""
    logger.bind(app_code=exc.code, status_code=exc.status_code).warning("业务异常")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": exc.code,
            "status_code": exc.status_code
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """处理 HTTP 异常"""
    # 记录 detail 便于生产排障（401/403 仍可能含敏感信息，按需收敛）
    if exc.status_code in (401, 403):
        logger.bind(status_code=exc.status_code).warning("HTTP异常")
    else:
        logger.bind(status_code=exc.status_code).warning(
            "HTTP异常 detail={}", exc.detail
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": "HTTP_ERROR",
            "status_code": exc.status_code
        }
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """处理未捕获异常"""
    logger.opt(exception=exc).error("未处理异常")
    return JSONResponse(
        status_code=500,
        content={
            "error": "内部服务器错误",
            "code": "INTERNAL_ERROR",
        }
    )


# 根路由
@app.get("/")
async def root():
    """根路由 - 显示API信息"""
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "health": "/api/health",
        "endpoints": {
            "upload": "POST /api/documents/upload",
            "process": "POST /api/documents/{document_id}/process",
            "status": "GET /api/documents/{document_id}/status",
            "result": "GET /api/documents/{document_id}/result"
        }
    }


# 启动入口
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
