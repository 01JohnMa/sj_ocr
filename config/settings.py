# config/settings.py
"""应用配置管理"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List
import os


class Settings(BaseSettings):
    """应用配置类"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ============ 基础配置 ============
    APP_NAME: str = "NeoFlow"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8080

    # ============ 安全配置 ============
    SECRET_KEY: str = "your-secret-key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CRM_API_TOKEN: str = ""

    # ============ Supabase配置 (本地部署) ============
    SUPABASE_URL: str = "http://localhost:8000"
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    DATABASE_URL: Optional[str] = None

    # ============ LLM配置 ============
    LLM_MODEL_ID: str = "deepseek-v4-flash"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"
    LLM_TEMPERATURE: float = 0.5

    # ============ OpenAI Agents SDK 配置 ============
    SDK_MODEL_ID: str = ""
    SDK_API_KEY: str = ""
    SDK_BASE_URL: str = ""
    SDK_TEMPERATURE: float = 0.2

    # ============ OCR模型路径 ============
    OCR_DET_MODEL_PATH: str = "./model/PP-OCRv5_server_det_infer"
    OCR_REC_MODEL_PATH: str = "./model/PP-OCRv5_server_rec_infer"
    OCR_ORI_MODEL_PATH: str = "./model/PP-LCNet_x1_0_textline_ori_infer"
    OCR_DOC_MODEL_PATH: str = "./model/PP-LCNet_x1_0_doc_ori_infer"
    OCR_ENABLED: bool = True
    OCR_INIT_ON_STARTUP: bool = True
    OCR_IR_OPTIM: bool = False
    OCR_USE_MKLDNN: bool = False

    # ============ 文件存储 ============
    UPLOAD_FOLDER: str = "./uploads"
    MAX_FILE_SIZE: int = 20971520  # 20MB
    ALLOWED_EXTENSIONS: str = ".pdf,.png,.jpg,.jpeg,.tiff,.bmp"

    # ============ 日志配置 ============
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "./logs/app.log"

    # ============ CORS配置 ============
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:3001,http://localhost:8080"
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"

    # ============ 飞书配置 ============
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    # 以下两项已废弃，运行时推送目标统一从 document_templates
    # 的 feishu_bitable_token / feishu_table_id 字段读取，不再使用环境变量。
    FEISHU_BITABLE_APP_TOKEN: str = ""  # 已废弃，保留供参考
    FEISHU_BITABLE_TABLE_ID: str = ""   # 已废弃，保留供参考
    FEISHU_PUSH_ENABLED: bool = False

    # ============ 文档处理模式 ============
    # "ocr_llm" : PaddleOCR + LLM（印刷体快、成本低）
    # "vlm"     : 多模态 VLM 直接看图提取（手写/复杂版式更准）
    DOC_PROCESS_MODE: str = "ocr_llm"

    # VLM 配置（DOC_PROCESS_MODE="vlm" 时生效）
    VLM_MODEL_ID: str = "qwen3.7-plus"
    VLM_API_KEY: str = ""
    VLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VLM_TEMPERATURE: float = 0.1

    # ============ 文档处理并发控制 ============
    DOC_PROCESS_MAX_CONCURRENCY: int = 2
    DOC_WORKER_POLL_INTERVAL_SECONDS: float = 2.0
    DOC_WORKER_STALE_LOCK_SECONDS: int = 1800
    DOC_WORKER_ID: str = ""

    @property
    def allowed_extensions_list(self) -> List[str]:
        return [ext.strip() for ext in self.ALLOWED_EXTENSIONS.split(",")]

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def allowed_hosts_list(self) -> List[str]:
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",")]

    def validate_ocr_models(self) -> bool:
        """验证OCR模型路径是否存在"""
        paths = [
            self.OCR_DET_MODEL_PATH,
            self.OCR_REC_MODEL_PATH,
            self.OCR_ORI_MODEL_PATH,
            self.OCR_DOC_MODEL_PATH,
        ]
        return all(os.path.exists(p) for p in paths)


# 单例实例
settings = Settings()
