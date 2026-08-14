"""SIDA 个人微信 iLink 直连客户端(纯 Python, 腾讯官方通道)。

参考实现: Hermes weixin.py(腾讯官方 iLink Bot API)。
能力:
  - fetch_qr(): 获取扫码二维码(供设置页扫码绑定)
  - poll_qr(): 轮询扫码状态, 成功后返回账号凭证
  - send_text(): 向已建立会话的微信用户推送文本消息

凭证结构(存 notify_channels.config): {token, base_url, user_id}
"""
import json
import time
import uuid

import httpx

# ---- iLink 常量(腾讯官方 iLink 协议) ----
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_GET_CONFIG = "ilink/bot/getconfig"
EP_SEND_TYPING = "ilink/bot/sendtyping"

ITEM_TEXT = 1
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2

TYPING_START = 1
TYPING_STOP = 2

API_TIMEOUT = 15.0


def _random_wechat_uin() -> str:
    return str(uuid.uuid4().int % (10**10))


def _headers(token: str | None = None, body: str = "") -> dict:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_qr(bot_type: str = "3") -> dict:
    """获取扫码二维码。返回 {qrcode, qrcode_url}(qrcode_url 为完整可扫链接)。"""
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.get(
            f"{ILINK_BASE_URL}/{EP_GET_BOT_QR}?bot_type={bot_type}",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    qrcode = str(data.get("qrcode") or "")
    qrcode_url = str(data.get("qrcode_img_content") or "") or qrcode
    if not qrcode:
        raise RuntimeError(f"iLink 二维码响应缺少 qrcode: {data}")
    return {"qrcode": qrcode, "qrcode_url": qrcode_url}


async def poll_qr(qrcode: str) -> dict:
    """轮询扫码状态。

    返回:
      {"status": "wait" | "scaned" | "expired" | ...}
      {"status": "success", "account_id", "token", "base_url", "user_id"}  扫码确认后
    """
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.get(
            f"{ILINK_BASE_URL}/{EP_GET_QR_STATUS}?qrcode={qrcode}",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
    status = str(data.get("status") or "wait")
    if status == "confirmed":
        return {
            "status": "success",
            "account_id": str(data.get("ilink_bot_id") or ""),
            "token": str(data.get("bot_token") or ""),
            "base_url": str(data.get("baseurl") or ILINK_BASE_URL),
            "user_id": str(data.get("ilink_user_id") or ""),
        }
    return {"status": status}


async def send_text(account: dict, to: str, text: str, context_token: str | None = None) -> dict:
    """向指定微信用户推送文本消息。

    account: {token, base_url, user_id}
    to: 接收方 peer id(形如 xxx@im.wechat), 必须与 bot 建立过会话
    context_token: iLink 会话 token(来自 getupdates, 外发必须回显最新值)
    """
    if not text or not text.strip():
        raise ValueError("send_text: 消息内容不能为空")
    message = {
        "from_user_id": "",
        "to_user_id": to,
        "client_id": f"sida-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}",
        "message_type": MSG_TYPE_BOT,
        "message_state": MSG_STATE_FINISH,
        "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
    }
    if context_token:
        message["context_token"] = context_token
    body = json.dumps(
        {"msg": message, "base_info": {"channel_version": CHANNEL_VERSION}},
        ensure_ascii=False,
    )
    base_url = (account.get("base_url") or ILINK_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url}/{EP_SEND_MESSAGE}",
            content=body,
            headers=_headers(account.get("token"), body),
        )
        resp.raise_for_status()
        return resp.json()


async def get_updates(account: dict, sync_buf: str = "") -> dict:
    """拉取入站消息(iLink getupdates)。

    返回原始响应: {ret, msgs: [...], get_updates_buf, longpolling_timeout_ms}
    每条 msg 含 from_user_id / context_token 等 —— context_token 是外发推送的必需参数。
    """
    body = json.dumps(
        {"get_updates_buf": sync_buf or "", "base_info": {"channel_version": CHANNEL_VERSION}},
        ensure_ascii=False,
    )
    base_url = (account.get("base_url") or ILINK_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            f"{base_url}/{EP_GET_UPDATES}",
            content=body,
            headers=_headers(account.get("token"), body),
        )
        resp.raise_for_status()
        return resp.json()


async def get_config(account: dict, user_id: str, context_token: str | None = None) -> dict:
    """获取用户会话配置(含 typing_ticket, 用于发送'正在输入'状态)。"""
    payload: dict = {"ilink_user_id": user_id}
    if context_token:
        payload["context_token"] = context_token
    body = json.dumps(
        {**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
        ensure_ascii=False,
    )
    base_url = (account.get("base_url") or ILINK_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{base_url}/{EP_GET_CONFIG}",
            content=body,
            headers=_headers(account.get("token"), body),
        )
        resp.raise_for_status()
        return resp.json()


async def send_typing(
    account: dict, user_id: str, typing_ticket: str, status: int = TYPING_START
) -> None:
    """发送'正在输入/停止输入'状态(TYPING_START=1 开始, TYPING_STOP=2 结束)。"""
    payload = {
        "ilink_user_id": user_id,
        "typing_ticket": typing_ticket,
        "status": status,
    }
    body = json.dumps(
        {**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
        ensure_ascii=False,
    )
    base_url = (account.get("base_url") or ILINK_BASE_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{base_url}/{EP_SEND_TYPING}",
            content=body,
            headers=_headers(account.get("token"), body),
        )
        resp.raise_for_status()
