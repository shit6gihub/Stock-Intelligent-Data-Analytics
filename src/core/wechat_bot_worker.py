"""微信数智分析BOT 双向对话 worker。

后台常驻协程: 对每个已绑定 openclaw 渠道(iLink 微信账号)长轮询 getupdates,
收到用户消息 → 调 SIDA 对话助手(8000, 带数据工具)→ sendmessage 回复微信。

由 server.py lifespan 启动(容器内自运行, 零外部依赖)。
"""

import asyncio
import logging
import os
import time
from collections import deque

import httpx

from src.core import wechat_ilink

logger = logging.getLogger(__name__)

# 内部对话助手 API(PANWATCH_URL 默认 127.0.0.1:8000, 容器内即主服务)
PANWATCH_URL = os.getenv("PANWATCH_URL", "http://127.0.0.1:8000").rstrip("/")
POLL_INTERVAL = 1.0  # 两次长轮询之间的间隔(秒)
MAX_MSG_IDS = 500  # 每账号去重窗口
REPLY_TIMEOUT = 90.0  # AI 回复超时(工具调用可能较慢)
MAX_REPLY_LEN = 1500  # 微信回复截断长度


class _AccountState:
    """单账号轮询状态(内存)。"""

    def __init__(self, channel_id: int, user_id: str, cfg: dict):
        self.channel_id = channel_id
        self.user_id = user_id
        self.cfg = cfg
        self.sync_buf = ""
        self.seen: deque[str] = deque(maxlen=MAX_MSG_IDS)
        self.conversation_id: str | None = None
        self.typing_ticket: str | None = None
        self.initialized = False  # 首轮只建游标不回复(避免回复历史消息)


async def _ask_ai(user_text: str, conv_id: str | None, user_id: str) -> tuple[str, str]:
    """调 SIDA 对话助手(容器内自产服务 token), 返回 (回复文本, conversation_id)。"""
    from src.web.api.auth import create_token
    from src.web.database import SessionLocal
    from src.web.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise RuntimeError(f"用户不存在: {user_id}")
        token, _ = create_token(user)
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=REPLY_TIMEOUT) as client:
        if not conv_id:
            r = await client.post(f"{PANWATCH_URL}/api/chat/conversations", json={}, headers=headers)
            r.raise_for_status()
            data = r.json()
            conv_id = str((data.get("data") or data).get("id") or "")
        if not conv_id:
            raise RuntimeError("对话会话创建失败")
        r = await client.post(
            f"{PANWATCH_URL}/api/chat/conversations/{conv_id}/messages",
            json={"content": user_text},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
        inner = data.get("data") or data
        reply = str(
            inner.get("content") or inner.get("reply") or inner.get("message") or ""
        ).strip()
        return reply, conv_id


def _persist_cfg(channel_id: int, **fields):
    """把字段写回 notify_channels.config(供 notifier 推送复用最新 context_token)。"""
    try:
        from src.web.database import SessionLocal
        from src.web.models import NotifyChannel

        db = SessionLocal()
        try:
            row = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
            if row:
                cfg = dict(row.config or {})
                cfg.update(fields)
                row.config = cfg
                db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"渠道 {channel_id} config 持久化失败: {exc}")


def _load_accounts() -> list[tuple[int, str, dict]]:
    """读取所有启用的 openclaw 渠道(扫码绑定的微信账号), 返回 (channel_id, user_id, config)。"""
    try:
        from src.web.database import SessionLocal
        from src.web.models import NotifyChannel

        db = SessionLocal()
        try:
            rows = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.type == "openclaw", NotifyChannel.enabled.is_(True))
                .all()
            )
            result = []
            for row in rows:
                cfg = dict(row.config or {})
                if cfg.get("token") and cfg.get("user_id"):
                    result.append((row.id, str(row.user_id or ""), cfg))
            return result
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"读取微信渠道失败: {exc}")
        return []


async def _account_loop(state: _AccountState):
    """单账号长轮询: 收消息 → AI 回复 → 回微信。"""
    account = {
        "token": state.cfg.get("token"),
        "base_url": state.cfg.get("base_url") or None,
    }
    peer = str(state.cfg.get("user_id") or "")
    if not account["token"] or not peer:
        return

    while True:
        try:
            updates = await wechat_ilink.get_updates(account, state.sync_buf)
            ret = updates.get("ret")
            errcode = updates.get("errcode")
            if ret not in (0, None) or errcode not in (0, None):
                logger.warning(
                    f"微信 getupdates 错误 ret={ret} errcode={errcode} {updates.get('errmsg')}"
                )
                await asyncio.sleep(5)
                continue

            new_buf = str(updates.get("get_updates_buf") or "")
            if new_buf:
                state.sync_buf = new_buf

            msgs = updates.get("msgs") or []
            for msg in msgs:
                from_id = str(msg.get("from_user_id") or "")
                if from_id != peer:
                    continue  # 只处理绑定用户自己的消息
                msg_id = str(msg.get("msg_id") or msg.get("client_id") or "") or f"{time.time()}"
                if msg_id in state.seen:
                    continue
                state.seen.append(msg_id)

                ctx = str(msg.get("context_token") or "").strip()
                if ctx:
                    state.cfg["context_token"] = ctx

                text = _extract_text(msg)
                if not text:
                    continue
                if not state.initialized:
                    continue  # 首轮建游标, 不回复历史消息

                logger.info(f"微信收到用户消息: {text[:40]}")
                # 发送"正在输入"状态(微信侧显示, 需 typing_ticket)
                try:
                    if not state.typing_ticket:
                        cfg_resp = await wechat_ilink.get_config(
                            account, peer, ctx or state.cfg.get("context_token")
                        )
                        state.typing_ticket = str(cfg_resp.get("typing_ticket") or "") or None
                    if state.typing_ticket:
                        await wechat_ilink.send_typing(account, peer, state.typing_ticket, 1)
                except Exception as exc:
                    logger.debug(f"typing 状态发送失败(可忽略): {exc}")
                try:
                    reply, state.conversation_id = await _ask_ai(
                        text, state.conversation_id, state.user_id
                    )
                except Exception as exc:
                    logger.warning(f"AI 回复失败: {exc}")
                    reply = "🤖 数智分析BOT 暂时无法处理, 请稍后重试。"
                # 停止"正在输入"
                try:
                    if state.typing_ticket:
                        await wechat_ilink.send_typing(account, peer, state.typing_ticket, 2)
                except Exception as exc:
                    logger.debug(f"typing 停止发送失败(可忽略): {exc}")
                reply = reply[:MAX_REPLY_LEN]
                try:
                    await wechat_ilink.send_text(
                        account, peer, reply, context_token=ctx or state.cfg.get("context_token")
                    )
                except Exception as exc:
                    logger.warning(f"微信回复发送失败: {exc}")
                    _persist_cfg(state.channel_id, context_token=state.cfg.get("context_token", ""))
            state.initialized = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"微信轮询异常: {exc}")
            await asyncio.sleep(POLL_INTERVAL)


def _extract_text(msg: dict) -> str:
    """从 iLink 消息里提取文本(支持 text_item 与直接文本字段)。"""
    items = msg.get("item_list") or []
    texts = []
    for it in items:
        if isinstance(it, dict):
            t = it.get("text_item") or {}
            if isinstance(t, dict) and t.get("text"):
                texts.append(str(t["text"]))
    if texts:
        return "\n".join(texts)
    return str(msg.get("text") or "").strip()


async def wechat_bot_worker():
    """主 worker: 加载账号并并发轮询。账号变化时自动增删协程。"""
    logger.info("微信数智分析BOT worker 启动")
    tasks: dict[int, asyncio.Task] = {}
    while True:
        accounts = _load_accounts()
        active_ids = {cid for cid, _, _ in accounts}
        # 新增账号
        for cid, uid, cfg in accounts:
            if cid not in tasks or tasks[cid].done():
                state = _AccountState(cid, uid, cfg)
                tasks[cid] = asyncio.create_task(_account_loop(state), name=f"wechat-bot-{cid}")
        # 移除已停用的账号
        for cid in list(tasks):
            if cid not in active_ids and not tasks[cid].done():
                tasks[cid].cancel()
                tasks.pop(cid, None)
        await asyncio.sleep(30)  # 每 30s 刷新账号列表
