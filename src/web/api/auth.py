"""认证 API - 多用户 JWT 认证(2026-08-10 阶段1)

- users 表(UUID 主键), role: owner|member
- 兼容旧单用户: 首次启动自动从 AppSettings 迁移旧账号为 owner
- JWT payload: user_id + role + ver(踢人用)
- 权限依赖: get_current_user(登录) / require_owner(仅管理员)
"""
import os
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt

from src.web.database import get_db, SessionLocal
from src.web.models import AppSettings, User

router = APIRouter()
security = HTTPBearer(auto_error=False)

# JWT 配置
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))

# 环境变量配置（Docker 部署用）
ENV_AUTH_USERNAME = os.getenv("AUTH_USERNAME")
ENV_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# 设置项 key(旧单用户兼容)
AUTH_USERNAME_KEY = "auth_username"
PASSWORD_HASH_KEY = "auth_password_hash"
JWT_SECRET_KEY = "jwt_secret"
AUTH_TOKEN_VERSION_KEY = "auth_token_version"

# JWT Secret 缓存
_jwt_secret: str | None = None


def get_jwt_secret() -> str:
    """获取 JWT Secret（持久化到数据库）"""
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret

    # 环境变量优先
    if os.getenv("JWT_SECRET"):
        _jwt_secret = os.getenv("JWT_SECRET")
        return _jwt_secret

    # 从数据库读取或首次生成
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == JWT_SECRET_KEY).first()
        if setting:
            _jwt_secret = setting.value
        else:
            _jwt_secret = secrets.token_hex(32)
            db.add(AppSettings(key=JWT_SECRET_KEY, value=_jwt_secret, description="JWT签名密钥(自动生成)"))
            db.commit()
        return _jwt_secret
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str
    user: Optional[dict] = None


def hash_password(password: str) -> str:
    """使用标准库 scrypt + 随机盐保存密码。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验 scrypt，并兼容旧版 SHA-256 哈希。"""
    if stored.startswith("scrypt$"):
        try:
            _, salt_hex, digest_hex = stored.split("$")
            salt = bytes.fromhex(salt_hex)
            digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
            return hmac.compare_digest(digest.hex(), digest_hex)
        except Exception:
            return False
    # 旧版 SHA-256(无盐, 兼容迁移前数据)
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).hexdigest(), stored)


# ── 用户管理(多用户核心) ──────────────────────────────────────────────

def init_auth_from_env(db: Session) -> bool:
    """兼容旧接口(server.py 引用): 从环境变量初始化认证。

    多用户下由 get_or_create_owner 统一处理, 此函数仅确保 owner 存在。
    """
    owner = get_or_create_owner(db)
    return owner is not None


def get_or_create_owner(db: Session) -> User:
    """确保存在 owner 用户。

    首次启动: 从环境变量或旧单用户(AppSettings)迁移账号为 owner;
    若都没有, 创建默认 admin/admin123。
    """
    owner = db.query(User).filter(User.role == "owner").first()
    if owner:
        return owner

    # 1. 环境变量优先
    if ENV_AUTH_USERNAME and ENV_AUTH_PASSWORD:
        user = User(
            id=str(uuid.uuid4()),
            username=ENV_AUTH_USERNAME,
            password_hash=hash_password(ENV_AUTH_PASSWORD),
            role="owner",
        )
        db.add(user)
        db.commit()
        return user

    # 2. 旧单用户迁移(AppSettings)
    setting_username = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    setting_hash = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    if setting_username and setting_hash and setting_hash.value:
        user = User(
            id=str(uuid.uuid4()),
            username=setting_username.value,
            password_hash=setting_hash.value,  # 复用已有哈希(scrypt或旧sha256均可验)
            role="owner",
        )
        db.add(user)
        db.commit()
        return user

    # 3. 兜底默认账号(首次部署)
    user = User(
        id=str(uuid.uuid4()),
        username="admin",
        password_hash=hash_password("admin123"),
        role="owner",
    )
    db.add(user)
    db.commit()
    return user


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, username: str, password: str, role: str = "member") -> User:
    """创建用户(owner 调用)。"""
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    return user


# ── Token ─────────────────────────────────────────────────────────────

def create_token(user: User, expires_hours: int = JWT_EXPIRE_HOURS) -> tuple[str, datetime]:
    """创建 JWT token, 含 user_id + role + ver(踢人用)。"""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=expires_hours)
    payload = {
        "exp": expires_at,
        "iat": now,
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "jti": secrets.token_hex(16),
        "ver": user.token_version,
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_token(token: str) -> dict | None:
    """解码 JWT, 失败返回 None。"""
    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── 权限依赖 ──────────────────────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """验证当前用户(用作依赖), 返回 User 对象。"""
    owner = get_or_create_owner(db)  # 确保 owner 存在

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_by_id(db, payload.get("sub", ""))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已禁用")
    if user.token_version != int(payload.get("ver", 0)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效, 请重新登录")

    return user


async def require_owner(user: User = Depends(get_current_user)) -> User:
    """仅 owner 可用(用户管理/系统设置)。"""
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可操作")
    return user


def user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# ── API ───────────────────────────────────────────────────────────────

@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """获取认证状态(前端判断是否需要初始化)。"""
    owner = get_or_create_owner(db)
    return {
        "initialized": True,
        "user": user_to_dict(owner),
        "multi_user": True,
    }


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登录(多用户)。"""
    get_or_create_owner(db)  # 确保 owner 存在(兼容首次部署)
    user = get_user_by_username(db, data.username.strip())
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已禁用")

    token, expires_at = create_token(user)
    return TokenResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        user=user_to_dict(user),
    )


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """获取当前用户信息。"""
    return {"user": user_to_dict(user)}


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改自己的密码, 先校验旧密码, 同时使该用户既有 Token 失效。"""
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(400, "旧密码不正确")
    if len(data.new_password) < 8:
        raise HTTPException(400, "密码长度至少 8 位")

    user.password_hash = hash_password(data.new_password)
    user.token_version += 1  # 踢掉旧 token
    db.commit()

    return {"message": "密码已更新"}


# ── 用户管理 API(仅 owner) ─────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "member"


@router.get("/users")
async def list_users(owner: User = Depends(require_owner), db: Session = Depends(get_db)):
    """用户列表(仅 owner)。"""
    users = db.query(User).order_by(User.created_at).all()
    return {"users": [user_to_dict(u) for u in users]}


@router.post("/users")
async def create_user_api(
    data: UserCreateRequest,
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """创建子账号(仅 owner)。"""
    if len(data.username.strip()) < 2:
        raise HTTPException(400, "用户名长度至少 2 位")
    if len(data.password) < 8:
        raise HTTPException(400, "密码长度至少 8 位")
    if data.role not in ("owner", "member"):
        raise HTTPException(400, "角色必须是 owner 或 member")
    if get_user_by_username(db, data.username.strip()):
        raise HTTPException(400, "用户名已存在")

    user = create_user(db, data.username.strip(), data.password, data.role)
    return {"user": user_to_dict(user)}


class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/users/{user_id}")
async def update_user_api(
    user_id: str,
    data: UserUpdateRequest,
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """修改用户(仅 owner): 改密/改角色/禁用。"""
    target = get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == owner.id and data.is_active is False:
        raise HTTPException(400, "不能禁用自己")

    if data.password:
        if len(data.password) < 8:
            raise HTTPException(400, "密码长度至少 8 位")
        target.password_hash = hash_password(data.password)
        target.token_version += 1  # 踢掉该用户旧 token
    if data.role and data.role in ("owner", "member"):
        target.role = data.role
    if data.is_active is not None:
        target.is_active = data.is_active
    db.commit()

    return {"user": user_to_dict(target)}


@router.delete("/users/{user_id}")
async def delete_user_api(
    user_id: str,
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """删除用户(仅 owner)。不能删自己。"""
    target = get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == owner.id:
        raise HTTPException(400, "不能删除自己")
    if target.role == "owner":
        raise HTTPException(400, "不能删除其他管理员")

    db.delete(target)
    db.commit()
    return {"message": "用户已删除"}
