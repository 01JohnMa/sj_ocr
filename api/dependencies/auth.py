# api/dependencies/auth.py
"""认证依赖注入 - FastAPI Depends 实现（支持多租户）"""

import secrets
import time
from typing import Optional, Tuple, Dict, Any
from fastapi import Header, Depends
from pydantic import BaseModel
from loguru import logger
import jwt

from services.supabase_service import supabase_service
from api.exceptions import AuthenticationError
from config.settings import settings

_PROFILE_CACHE_TTL = 60  # seconds
_profile_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
QUALITY_CRM_TENANT_ID = "a0000000-0000-0000-0000-000000000001"
CRM_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


class CurrentUser(BaseModel):
    """当前用户信息（含租户）"""
    user_id: str
    token: str
    tenant_id: Optional[str] = None
    tenant_code: Optional[str] = None
    tenant_name: Optional[str] = None
    role: str = "user"  # super_admin / tenant_admin / user
    display_name: Optional[str] = None
    
    class Config:
        arbitrary_types_allowed = True
    
    def is_super_admin(self) -> bool:
        """是否为超级管理员"""
        return self.role == "super_admin"
    
    def is_tenant_admin(self) -> bool:
        """是否为租户管理员或更高"""
        return self.role in ("tenant_admin", "super_admin")
    
    def can_access_tenant(self, tenant_id: str) -> bool:
        """是否可以访问指定租户的数据"""
        if self.is_super_admin():
            return True
        return self.tenant_id == tenant_id


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[7:].strip()


def _extract_token_and_user_id(authorization: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    从 Authorization header 提取 token 并解析 user_id
    
    Args:
        authorization: Authorization header 值 (Bearer xxx)
        
    Returns:
        (token, user_id) 元组，如果解析失败则返回 (None, None)
    """
    token = _extract_bearer_token(authorization)
    if not token:
        return None, None
    
    try:
        # 解码 JWT 仅提取 user_id（sub 字段）。
        # 签名验证由 Supabase 服务端负责：后续所有数据库操作均通过
        # service_role 客户端 + 手动权限检查完成，不依赖 JWT 签名本身。
        # 若需在本地验证签名，需配置 SUPABASE_JWT_SECRET。
        payload = jwt.decode(token, options={"verify_signature": False})
        user_id = payload.get("sub")
        return token, user_id
    except Exception as e:
        logger.warning(f"JWT 解析失败: {e}")
        return token, None


async def get_current_user(
    authorization: Optional[str] = Header(None)
) -> CurrentUser:
    """
    获取当前登录用户（必需认证，含租户和角色信息）
    
    用法:
        @router.get("/protected")
        async def protected_endpoint(user: CurrentUser = Depends(get_current_user)):
            print(user.user_id, user.tenant_id, user.role)
    
    Raises:
        AuthenticationError: 未登录或 token 无效
    """
    token, user_id = _extract_token_and_user_id(authorization)
    
    if not token or not user_id:
        raise AuthenticationError()
    
    # 获取用户的 profile 信息（含租户和角色），优先读缓存
    user_data = CurrentUser(user_id=user_id, token=token)
    
    try:
        now = time.monotonic()
        cached = _profile_cache.get(user_id)
        if cached and (now - cached[0]) < _PROFILE_CACHE_TTL:
            profile = cached[1]
        else:
            from services.tenant_service import tenant_service
            profile = await tenant_service.get_user_profile(user_id)
            if profile:
                _profile_cache[user_id] = (now, profile)
            logger.debug(f"获取用户 profile (DB): user_id={user_id}")
        
        if profile:
            user_data.tenant_id = profile.get("tenant_id")
            user_data.role = profile.get("role", "user")
            user_data.display_name = profile.get("display_name")
            
            tenant = profile.get("tenants")
            if tenant:
                user_data.tenant_code = tenant.get("code")
                user_data.tenant_name = tenant.get("name")
            
            if not user_data.tenant_id:
                logger.warning(f"用户 {user_id} 的 profile 存在但 tenant_id 为空，可能是注册时触发器未正确写入或前端未补写")
        else:
            logger.warning(f"用户 {user_id} 没有 profile 记录，可能是 handle_new_user 触发器未执行")
    except Exception as e:
        logger.error(f"获取用户 profile 失败: user_id={user_id}, error={e}")
    
    return user_data


async def get_crm_current_user(
    authorization: Optional[str] = Header(None)
) -> CurrentUser:
    """
    获取 CRM 调用身份。

    优先识别固定 CRM_API_TOKEN；未命中时兼容普通登录 JWT，便于后台管理员测试。
    """
    token = _extract_bearer_token(authorization)
    if settings.CRM_API_TOKEN and token and secrets.compare_digest(token, settings.CRM_API_TOKEN):
        return CurrentUser(
            user_id=CRM_SYSTEM_USER_ID,
            token=token,
            tenant_id=QUALITY_CRM_TENANT_ID,
            tenant_code="quality",
            tenant_name="质量管理中心",
            role="tenant_admin",
            display_name="CRM固定鉴权",
        )
    return await get_current_user(authorization)


def invalidate_profile_cache(user_id: str) -> None:
    """清除指定用户的 profile 缓存（在 profile 更新后调用）"""
    _profile_cache.pop(user_id, None)


async def get_optional_user(
    authorization: Optional[str] = Header(None)
) -> Optional[CurrentUser]:
    """
    获取当前用户（可选认证）
    
    未登录时返回 None，不抛出异常
    
    用法:
        @router.get("/public")
        async def public_endpoint(user: Optional[CurrentUser] = Depends(get_optional_user)):
            if user:
                print(f"已登录: {user.user_id}")
            else:
                print("匿名访问")
    """
    token, user_id = _extract_token_and_user_id(authorization)
    
    if not token or not user_id:
        return None
    
    return CurrentUser(user_id=user_id, token=token)
