"""SIDA 扫码绑定个人微信(OpenClaw 桥接) API。

宿主机微信桥接服务(容器内经 172.17.0.1:8001 可达, 地址可用环境变量
SIDA_WECHAT_BRIDGE 覆盖)提供:
- POST /start {"bind_id": ...} -> {"qrcode_url": "..."}
- GET  /status?bind=<bind_id> -> {"status": "waiting"}
                             | {"status": "success", "account_id": "...", "user_id": "..."}
- POST /send {"account_id", "to", "message", "idempotency_key"} -> {"ok": true, "message_id": "..."}

绑定关系直接落 notify_channels(type=openclaw), 不建新表。
"""

import logging
import os
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.web.api.auth import get_current_user
from src.web.database import get_db
from src.web.models import NotifyChannel, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notify/wechat-bind")

# 宿主机微信桥接服务地址(容器内经 docker0 网关 172.17.0.1 可达)
BRIDGE_BASE = os.getenv("SIDA_WECHAT_BRIDGE", "http://172.17.0.1:8001").rstrip("/")
BRIDGE_TIMEOUT = 30.0

CHANNEL_TYPE = "openclaw"
CHANNEL_NAME = "个人微信(扫码绑定)"
# 桥接地址固定存进 config.webhook_url, _send_openclaw 会往 {webhook_url}/send 推消息
BRIDGE_WEBHOOK_URL = "http://172.17.0.1:8001"


def _bridge_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="微信桥接服务不可用")


def _find_bound_channel(db: Session, user: User) -> NotifyChannel | None:
    """当前用户扫码绑定的 openclaw 渠道。

    识别方式: name 为「个人微信(扫码绑定)」或 config 中带 account_id+user_id,
    避免误删/误读用户手动添加的 openclaw(webhook_url+secret)渠道。
    """
    rows = (
        db.query(NotifyChannel)
        .filter(
            NotifyChannel.user_id == user.id,
            NotifyChannel.type == CHANNEL_TYPE,
        )
        .all()
    )
    for row in rows:
        cfg = row.config or {}
        if row.name == CHANNEL_NAME or (cfg.get("account_id") and cfg.get("user_id")):
            return row
    return None


@router.post("/start")
async def start_bind(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """生成 bind_id 并请求桥接服务创建扫码会话, 返回二维码 URL。

    桥接服务不可达时返回 503(微信桥接服务不可用), 前端可据此提示降级。
    """
    bind_id = uuid.uuid4().hex[:12]
    try:
        async with httpx.AsyncClient(timeout=BRIDGE_TIMEOUT) as client:
            resp = await client.post(f"{BRIDGE_BASE}/start", json={"bind_id": bind_id})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error(f"微信桥接 /start 失败(bind={bind_id}): {exc}")
        raise _bridge_unavailable()

    qrcode_url = str((data or {}).get("qrcode_url") or "").strip()
    if not qrcode_url:
        logger.error(f"微信桥接 /start 未返回 qrcode_url: {data}")
        raise _bridge_unavailable()
    logger.info(f"用户 {user.username} 发起微信扫码绑定, bind_id={bind_id}")
    return {"bind_id": bind_id, "qrcode_url": qrcode_url}


@router.get("/status")
async def status_bind(
    bind: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """轮询扫码状态。扫码成功后把 account_id/user_id 落为当前用户的 openclaw 渠道。"""
    try:
        async with httpx.AsyncClient(timeout=BRIDGE_TIMEOUT) as client:
            resp = await client.get(f"{BRIDGE_BASE}/status", params={"bind": bind})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error(f"微信桥接 /status 失败(bind={bind}): {exc}")
        raise _bridge_unavailable()

    data = data or {}
    status = str(data.get("status") or "waiting")
    if status == "success":
        account_id = str(data.get("account_id") or "").strip()
        wechat_user_id = str(data.get("user_id") or "").strip()
        if not account_id or not wechat_user_id:
            logger.error(f"微信桥接 /status success 但缺 account_id/user_id: {data}")
            raise _bridge_unavailable()
        channel = _find_bound_channel(db, user)
        config = {
            "account_id": account_id,
            "user_id": wechat_user_id,
            "webhook_url": BRIDGE_WEBHOOK_URL,
            "secret": "",
        }
        if channel is None:
            channel = NotifyChannel(
                user_id=user.id,
                name=CHANNEL_NAME,
                type=CHANNEL_TYPE,
                config=config,
                enabled=True,
                is_default=False,
            )
            db.add(channel)
        else:
            channel.name = CHANNEL_NAME
            channel.type = CHANNEL_TYPE
            channel.config = config
            channel.enabled = True
        db.commit()
        logger.info(
            f"用户 {user.username} 微信扫码绑定成功: account={account_id} user={wechat_user_id}"
        )
        return {
            "status": "success",
            "account_id": account_id,
            "user_id": wechat_user_id,
        }
    return {"status": status}


@router.delete("")
async def unbind(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """解除绑定: 删除当前用户的扫码绑定 openclaw 渠道。"""
    channel = _find_bound_channel(db, user)
    if channel is None:
        return {"ok": True, "unbound": False}
    db.delete(channel)
    db.commit()
    logger.info(f"用户 {user.username} 已解除微信绑定")
    return {"ok": True, "unbound": True}


@router.get("")
async def bind_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户的 openclaw 绑定状态。"""
    channel = _find_bound_channel(db, user)
    if channel is None:
        return {"bound": False, "account_id": None, "user_id": None}
    cfg = channel.config or {}
    return {
        "bound": True,
        "account_id": cfg.get("account_id") or None,
        "user_id": cfg.get("user_id") or None,
    }
