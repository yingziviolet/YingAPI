"""难度感知路由(P4):启发式判定"简单请求",自动降级到便宜模型。

启发式(全部满足才降级):
- 该模型在 GW_DOWNGRADE_MAP 里配了降级目标
- 无 tools / tool_choice / response_format(结构化任务不降)
- 轮数 <= 上限,纯文本(不含图片等多模态段)
- 文本总长 < 阈值,且不含代码块(``` 视为编码任务)
判定是确定性的:同一请求永远同一路由,缓存不会串。
质量回评(抽样用强模型复评降级答案)在路线图 P4 后段。
"""
from app.config import Settings


def downgrade_target(body: dict, settings: Settings) -> str | None:
    """返回降级目标对外模型名;不满足条件返回 None。"""
    if not settings.downgrade_enabled:
        return None
    model = body.get("model")
    target = settings.downgrade_map.get(model or "")
    if not target or target == model:
        return None
    if body.get("tools") or body.get("tool_choice") or body.get("response_format"):
        return None
    messages = body.get("messages") or []
    if len(messages) > settings.downgrade_max_messages:
        return None
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            return None
        content = message.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    return None  # 含图片等多模态段:不降级
                parts.append(part.get("text", ""))
            text = " ".join(parts)
        elif content is None:
            text = ""
        else:
            return None
        if "```" in text:
            return None
        total_chars += len(text)
    if total_chars >= settings.downgrade_max_chars:
        return None
    return target
