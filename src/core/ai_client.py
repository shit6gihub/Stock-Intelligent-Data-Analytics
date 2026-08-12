import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class AIClient:
    """OpenAI 协议兼容的 AI 客户端"""

    def __init__(self, base_url: str, api_key: str, model: str = "", proxy: str = ""):
        kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }
        if proxy:
            kwargs["http_client"] = None  # TODO: 如需代理，用 httpx 配置
        self.client = AsyncOpenAI(**kwargs)
        # 保留原始配置作为实例属性,供需要桥接到第三方 LLM 框架的 agent 使用
        # (e.g. TradingAgents 需要 base_url+api_key 重新构造 langchain 的 LLM)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.total_tokens_used = 0

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        """
        调用 LLM 获取文本回复。

        Args:
            system_prompt: 系统提示词
            user_content: 用户输入内容
            images: 图片路径列表（用于多模态，可选）
            temperature: 生成温度
        """
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 构建 user message
        if images:
            content_parts = [{"type": "text", "text": user_content}]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_data}"}
                    })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        try:
            create_kwargs = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            response = await self.client.chat.completions.create(**create_kwargs)
            # 记录 token 用量
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
                logger.debug(
                    f"Token usage: {response.usage.prompt_tokens} + "
                    f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float = 0.4,
    ) -> str:
        """
        多轮对话：传入完整 messages 列表。

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: 生成温度
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
                logger.debug(
                    f"Token usage: {response.usage.prompt_tokens} + "
                    f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"AI 多轮对话调用失败: {e}")
            raise

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.4,
    ):
        """带 tool use 的对话调用，返回原始 message 对象。"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=temperature,
            )
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool use 调用失败: {e}")
            raise

    async def list_models(self) -> list[str]:
        """通过 OpenAI 兼容的 /v1/models 拉取可用模型 id 列表。"""
        resp = await self.client.models.list()
        return sorted(m.id for m in resp.data)

    def _encode_image(self, image_path: str) -> str | None:
        """将图片文件编码为 base64"""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"图片不存在: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


# ──────────────── 统一 LLM 配置中心 (2026-08-12) ────────────────
# 所有 AI 使用点(对话/Agent/裁判/报告/自检)统一经 get_model_for_scene 解析模型、
# build_system_prompt 组装 system prompt(含用户交易风格画像注入)。

# 画像注入节流(与 src/web/api/chat.py 口径一致, 独立实现避免循环依赖):
# profile_text 截断 + rules 只取前 N 条, 避免每次调用占过多 token
_SHADOW_PROFILE_TEXT_MAX = 300
_SHADOW_PROFILE_RULES_MAX = 3


def _build_shadow_profile_block(profile_json) -> str:
    """从 users.shadow_profile_json 构建精简版画像注入文本(无画像返回空串)。

    格式与 chat.py 的 _build_shadow_profile_block 保持一致:
    profile_text 截断300字 + 规则前3条 human_text + 偏好市场 + 典型持仓中位/P75。
    """
    if not profile_json or not isinstance(profile_json, dict):
        return ""
    parts: list[str] = []

    profile_text = (profile_json.get("profile_text") or "").strip()
    if profile_text:
        if len(profile_text) > _SHADOW_PROFILE_TEXT_MAX:
            profile_text = profile_text[:_SHADOW_PROFILE_TEXT_MAX] + "…"
        parts.append(f"画像: {profile_text}")

    rules = profile_json.get("rules") or []
    if rules:
        rule_lines = []
        for rule in rules[:_SHADOW_PROFILE_RULES_MAX]:
            if isinstance(rule, dict) and rule.get("human_text"):
                rule_lines.append(f"- {rule['human_text']}")
        if rule_lines:
            parts.append("交易规则:\n" + "\n".join(rule_lines))

    preferred_markets = profile_json.get("preferred_markets") or []
    if preferred_markets:
        parts.append("偏好市场: " + ", ".join(str(m) for m in preferred_markets))

    holding_days = profile_json.get("typical_holding_days")
    if holding_days:
        if isinstance(holding_days, (list, tuple)) and len(holding_days) == 2:
            parts.append(
                f"典型持仓天数: 中位 {holding_days[0]} 天 / P75 {holding_days[1]} 天"
            )
        else:
            parts.append(f"典型持仓天数: {holding_days} 天")

    if not parts:
        return ""
    return "以下是用户交易风格画像(AI 参考, 用于给出更贴合的建议):\n" + "\n".join(parts)


def get_model_for_scene(db, scene: str):
    """按场景解析应使用的模型(统一 LLM 配置中心入口)。

    解析顺序: 场景绑定(ai_scene_bindings) → 默认模型(is_default) → 模型池第一个。

    Returns:
        AIModel | None(模型池为空时返回 None)
    """
    from src.web.models import AISceneBinding, AIModel

    # 1. 场景显式绑定
    binding = (
        db.query(AISceneBinding)
        .filter(AISceneBinding.scene == scene)
        .first()
    )
    if binding and binding.model_id is not None:
        model = db.query(AIModel).filter(AIModel.id == binding.model_id).first()
        if model:
            return model
        logger.warning(
            f"场景 {scene} 绑定的模型(id={binding.model_id})已不存在, 回落默认模型"
        )

    # 2. 默认模型
    default = (
        db.query(AIModel)
        .filter(AIModel.is_default == True)  # noqa: E712
        .order_by(AIModel.id)
        .first()
    )
    if default:
        return default

    # 3. 模型池第一个(兜底)
    return db.query(AIModel).order_by(AIModel.id).first()


def build_system_prompt(db, scene: str, base_prompt: str, user) -> str:
    """组装最终 system prompt(统一 LLM 配置中心入口)。

    - 先 base_prompt;
    - 若 user.shadow_profile_json 非空(用户上传过交割单), 追加用户交易风格画像段;
    - 无画像(或 user 为 None)时原样返回 base_prompt, 完全向后兼容。
    """
    if not base_prompt:
        return base_prompt

    profile_json = getattr(user, "shadow_profile_json", None) if user is not None else None
    if not profile_json:
        return base_prompt

    block = _build_shadow_profile_block(profile_json)
    if not block:
        return base_prompt

    logger.debug(f"场景 {scene}: 已注入用户交易风格画像")
    return base_prompt + "\n\n--- 用户交易风格画像 ---\n" + block
