"""用户管理 API — SIDA 权限体系管理端(2026-08-15)。

模型授权: owner 给每个用户分配可用模型, 存 users.permissions JSON 列的 model_access 字段:
    {"mode": "inherit" | "granted" | "deny_all", "model_ids": [int]}
- inherit: 继承全部平台模型(未设置 = inherit)
- granted: 仅使用 model_ids 勾选的模型(空数组 = 显式全禁)
- deny_all: 全部禁用

读写容错: permissions 可能是 None / list(旧预留格式) / dict / 非法 JSON 字符串,
统一归一为 dict; 写回时保留原有其他键。

基础用户 CRUD 与 auth.router(/api/auth/users)并存: 本 router 挂在 /api 前缀下
由 app.py include(prefix="/api"), 提供 /api/users 路径, 功能一致, 互不冲突。
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.web.database import get_db
from src.web.api.auth import (
    create_user,
    get_user_by_id,
    get_user_by_username,
    require_owner,
    user_to_dict,
)
from src.web.models import AIModel, AIService, User

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL_ACCESS_MODES = ("inherit", "granted", "deny_all")


# ── permissions 读写容错 ───────────────────────────────────────────────

def _load_permissions(user: User) -> dict:
    """读取用户 permissions(JSON 列), 容错归一为 dict。

    SQLAlchemy JSON 列在库中可能为 None / list(旧预留格式) / dict;
    若该列以字符串形态存在(手工迁移/外部写入), 也尝试 json.loads 解析。
    """
    raw = getattr(user, "permissions", None)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


def _get_model_access(user: User) -> dict:
    """读取用户的 model_access, 结构恒定为 {"mode": str, "model_ids": [int]}。"""
    perms = _load_permissions(user)
    ma = perms.get("model_access")
    if not isinstance(ma, dict):
        return {"mode": "inherit", "model_ids": []}
    mode = ma.get("mode", "inherit")
    if mode not in MODEL_ACCESS_MODES:
        mode = "inherit"
    ids = ma.get("model_ids")
    if not isinstance(ids, list):
        ids = []
    clean = []
    for i in ids:
        if isinstance(i, bool) or not isinstance(i, (int, float)):
            continue
        clean.append(int(i))
    return {"mode": mode, "model_ids": sorted(set(clean))}


def _set_model_access(user: User, mode: str, model_ids: list[int]) -> None:
    """写入 model_access 到 permissions, 保留其他原有键。"""
    perms = _load_permissions(user)
    perms["model_access"] = {"mode": mode, "model_ids": list(model_ids)}
    user.permissions = perms


def _list_all_models(db: Session) -> list[dict]:
    """全局模型列表: AIModel join AIService。"""
    rows = (
        db.query(AIModel)
        .join(AIService)
        .order_by(AIModel.id.asc())
        .all()
    )
    return [
        {
            "id": m.id,
            "name": m.name,
            "model": m.model,
            "service_name": m.service.name if m.service else None,
        }
        for m in rows
    ]


# ── 基础用户 CRUD(与 auth.router 并存) ────────────────────────────────

class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "member"


class UserUpdateRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


# ── 模型授权(权限体系管理端) ──────────────────────────────────────────
# 注: 用户 CRUD 在 auth.py(/api/auth/users), 本文件只负责 model-access

class ModelAccessUpdate(BaseModel):
    mode: str
    # mode=granted 时必填(可为空数组=显式全禁); inherit/deny_all 时忽略
    model_ids: Optional[list[int]] = None


@router.get("/{uid}/model-access")
def get_user_model_access(
    uid: str,
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """获取某用户的模型授权(仅 owner)。未设置 = inherit(继承全部平台模型)。"""
    target = get_user_by_id(db, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    access = _get_model_access(target)
    return {
        "mode": access["mode"],
        "model_ids": access["model_ids"],
        "all_models": _list_all_models(db),
        "user_role": target.role,
        "username": target.username,
    }


@router.put("/{uid}/model-access")
def update_user_model_access(
    uid: str,
    data: ModelAccessUpdate,
    owner: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    """写入用户的模型授权(仅 owner)。保留 permissions 其他键。"""
    target = get_user_by_id(db, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if data.mode not in MODEL_ACCESS_MODES:
        raise HTTPException(400, f"mode 必须是 {', '.join(MODEL_ACCESS_MODES)} 之一")
    if data.mode == "granted":
        if data.model_ids is None:
            raise HTTPException(400, "mode=granted 时必须提供 model_ids(空数组=显式全禁)")
        model_ids = sorted(set(data.model_ids))
        if model_ids:
            rows = db.query(AIModel.id).filter(AIModel.id.in_(model_ids)).all()
            existing = {r[0] for r in rows}
            missing = [i for i in model_ids if i not in existing]
            if missing:
                raise HTTPException(400, f"模型不存在: {missing}")
    else:
        # inherit / deny_all 忽略 model_ids, 统一写空数组保证结构恒定
        model_ids = []

    _set_model_access(target, data.mode, model_ids)
    db.commit()
    logger.info(f"更新用户 {target.username}({target.id}) 模型授权: {data.mode} {model_ids}")
    return {"mode": data.mode, "model_ids": model_ids}
