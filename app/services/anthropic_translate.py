"""Anthropic Messages API <-> OpenAI Chat Completions 协议翻译。

网关内部统一走 OpenAI 协议(渠道都是 OpenAI 兼容端点);本模块把 Anthropic
客户端(Claude Code 等,配自有 key)的请求翻译进来、把响应翻译回去。
三层翻译:请求体、非流式响应体、SSE 事件流(OpenAI chunk -> Anthropic 事件序列)。
"""
import json
import logging
import secrets

logger = logging.getLogger("gateway.anthropic")


class TranslateError(ValueError):
    """请求体无法翻译(客户端错误,400)。"""


# ---------- 请求翻译:Anthropic -> OpenAI ----------


def _system_text(system) -> str | None:
    if isinstance(system, str):
        return system
    if isinstance(system, list):  # [{type: "text", text: ...}, ...]
        parts = [b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def _image_part(block: dict) -> dict | None:
    source = block.get("source") or {}
    if source.get("type") == "base64":
        uri = f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"
        return {"type": "image_url", "image_url": {"url": uri}}
    if source.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": source.get("url", "")}}
    return None


def _user_blocks_to_openai(blocks: list) -> tuple[list[dict], list | str | None]:
    """把 user turn 的内容块拆成 (OpenAI tool 消息列表, user content)。

    Anthropic 的 tool_result 出现在 user turn 里;OpenAI 要求它们是独立的
    role=tool 消息且排在后续 user 文本之前。tool_result 里的图片块转成紧随
    其后的 user 图片段(OpenAI 的 tool 消息不能带图,user 可以)。
    """
    tool_messages: list[dict] = []
    parts: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_result":
            text = _tool_result_text(block.get("content"))
            images = []
            if isinstance(block.get("content"), list):
                for inner in block["content"]:
                    if isinstance(inner, dict) and inner.get("type") == "image":
                        part = _image_part(inner)
                        if part:
                            images.append(part)
            if images:
                note = f"[tool returned {len(images)} image(s); attached in the following user message]"
                text = f"{text}\n{note}" if text else note
                parts.extend(images)
            if block.get("is_error"):
                text = f"[tool error] {text}"
            tool_messages.append(
                {"role": "tool", "tool_call_id": block.get("tool_use_id", ""), "content": text}
            )
        elif btype == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            part = _image_part(block)
            if part:
                parts.append(part)
        # thinking / document 等其余块类型:丢弃(网关不透传推理内容)

    if not parts:
        return tool_messages, None
    if all(p["type"] == "text" for p in parts):
        return tool_messages, "\n".join(p["text"] for p in parts)
    return tool_messages, parts


def _assistant_blocks_to_openai(blocks: list) -> dict:
    texts: list[str] = []
    tool_calls: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id") or f"call_{secrets.token_hex(8)}",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
        # thinking 块:丢弃
    message: dict = {"role": "assistant", "content": "\n".join(texts) if texts else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def anthropic_to_openai_request(body: dict) -> dict:
    """翻译请求体。抛 TranslateError 表示客户端请求不合法。"""
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise TranslateError("'model' is required and must be a string")
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens < 1:
        raise TranslateError("'max_tokens' is required and must be a positive integer")
    in_messages = body.get("messages")
    if not isinstance(in_messages, list) or not in_messages:
        raise TranslateError("'messages' is required and must be a non-empty list")

    out_messages: list[dict] = []
    system = _system_text(body.get("system"))
    if system:
        out_messages.append({"role": "system", "content": system})

    for message in in_messages:
        if not isinstance(message, dict):
            raise TranslateError("each message must be an object")
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            if isinstance(content, str):
                out_messages.append({"role": "user", "content": content})
            elif isinstance(content, list):
                tool_messages, user_content = _user_blocks_to_openai(content)
                out_messages.extend(tool_messages)
                if user_content is not None:
                    out_messages.append({"role": "user", "content": user_content})
            else:
                raise TranslateError("user message content must be a string or a list of blocks")
        elif role == "assistant":
            if isinstance(content, str):
                out_messages.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                out_messages.append(_assistant_blocks_to_openai(content))
            else:
                raise TranslateError("assistant message content must be a string or a list of blocks")
        elif role == "system":
            # Anthropic 支持 messages 里的 system turn(mid-conversation system message)
            text = _system_text(content)
            if text is None:
                raise TranslateError("system message content must be a string or a list of text blocks")
            out_messages.append({"role": "system", "content": text})
        else:
            raise TranslateError(f"unsupported message role: {role!r}")

    out: dict = {"model": model, "max_tokens": max_tokens, "messages": out_messages}

    if isinstance(body.get("temperature"), (int, float)):
        out["temperature"] = body["temperature"]
    if isinstance(body.get("top_p"), (int, float)):
        out["top_p"] = body["top_p"]
    stop_sequences = body.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        out["stop"] = stop_sequences
    if body.get("stream"):
        out["stream"] = True

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        out_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            out_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema") or {"type": "object"},
                    },
                }
            )
        if out_tools:
            out["tools"] = out_tools

    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type")
        if tc_type == "auto":
            out["tool_choice"] = "auto"
        elif tc_type == "any":
            out["tool_choice"] = "required"
        elif tc_type == "tool" and tool_choice.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tool_choice["name"]}}
        elif tc_type == "none":
            out["tool_choice"] = "none"
        if tool_choice.get("disable_parallel_tool_use"):
            out["parallel_tool_calls"] = False

    # thinking / metadata / service_tier / top_k 等 Anthropic 专有字段:上游不认,丢弃
    return out


# ---------- 响应翻译:OpenAI -> Anthropic ----------

# 注:OpenAI 命中 stop 序列时只返回 finish_reason="stop" 且不告知命中项,
# 故本网关不会产生 Anthropic 的 "stop_sequence" 值(恒 end_turn / stop_sequence=null)
_STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


def map_stop_reason(finish_reason: str | None) -> str:
    return _STOP_REASON.get(finish_reason or "", "end_turn")


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def openai_to_anthropic_response(data: dict, model: str) -> dict:
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}

    content: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for tc in message.get("tool_calls") or []:
        function = tc.get("function") or {}
        content.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{secrets.token_hex(8)}",
                "name": function.get("name", ""),
                "input": _parse_arguments(function.get("arguments")),
            }
        )

    usage = data.get("usage") or {}
    return {
        "id": f"msg_{data.get('id', secrets.token_hex(12))}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": map_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    }


# ---------- 流式翻译:OpenAI chunk 流 -> Anthropic SSE 事件序列 ----------


def _event(name: str, obj: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


class AnthropicStreamTranslator:
    """状态机:消费 OpenAI chunk(已解析的 dict),产出 Anthropic SSE 事件字节。

    事件序列:message_start -> [content_block_start/delta/stop]* -> message_delta -> message_stop
    """

    def __init__(self, model: str, input_tokens_estimate: int = 0):
        self.model = model
        self.message_id = f"msg_{secrets.token_hex(12)}"
        self._started = False
        self._block_index = -1
        self._block_type: str | None = None  # "text" | "tool_use"
        self._openai_tool_index: int | None = None
        self._tool_id: str | None = None
        self.finish_reason: str | None = None
        self.usage: dict | None = None
        self.output_chars = 0
        self._input_tokens_estimate = input_tokens_estimate

    def start(self) -> list[bytes]:
        """立即产出 message_start(幂等):不必等上游首 chunk。"""
        out: list[bytes] = []
        self._ensure_started(out)
        return out

    def _ensure_started(self, out: list[bytes]) -> None:
        if self._started:
            return
        self._started = True
        out.append(
            _event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": self._input_tokens_estimate, "output_tokens": 0},
                    },
                },
            )
        )

    def _close_block(self, out: list[bytes]) -> None:
        if self._block_index >= 0 and self._block_type is not None:
            out.append(
                _event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": self._block_index},
                )
            )
            self._block_type = None
            self._openai_tool_index = None
            self._tool_id = None

    def _open_text_block(self, out: list[bytes]) -> None:
        self._close_block(out)
        self._block_index += 1
        self._block_type = "text"
        out.append(
            _event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self._block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        )

    def _open_tool_block(self, out: list[bytes], tool_id: str, name: str, openai_index: int) -> None:
        self._close_block(out)
        self._block_index += 1
        self._block_type = "tool_use"
        self._openai_tool_index = openai_index
        self._tool_id = tool_id
        out.append(
            _event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self._block_index,
                    "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}},
                },
            )
        )

    def feed(self, chunk: dict) -> list[bytes]:
        out: list[bytes] = []
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            self.usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            return out
        self._ensure_started(out)
        choice = choices[0]
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if isinstance(content, str) and content:
            if self._block_type != "text":
                self._open_text_block(out)
            self.output_chars += len(content)
            out.append(
                _event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self._block_index,
                        "delta": {"type": "text_delta", "text": content},
                    },
                )
            )

        for tc in delta.get("tool_calls") or []:
            openai_index = tc.get("index", 0)
            function = tc.get("function") or {}
            # 切块条件除 index 外还看 id:部分上游并行工具调用全用 index 0,靠新 id 区分
            if (
                self._block_type != "tool_use"
                or self._openai_tool_index != openai_index
                or (tc.get("id") and tc.get("id") != self._tool_id)
            ):
                self._open_tool_block(
                    out,
                    tool_id=tc.get("id") or f"toolu_{secrets.token_hex(8)}",
                    name=function.get("name", ""),
                    openai_index=openai_index,
                )
            fragment = function.get("arguments")
            if isinstance(fragment, str) and fragment:
                self.output_chars += len(fragment)
                out.append(
                    _event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": self._block_index,
                            "delta": {"type": "input_json_delta", "partial_json": fragment},
                        },
                    )
                )

        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        return out

    def finish(self) -> list[bytes]:
        out: list[bytes] = []
        self._ensure_started(out)  # 空响应也要有完整事件序列
        self._close_block(out)
        upstream = self.usage or {}
        ct = upstream.get("completion_tokens")
        output_tokens = ct if isinstance(ct, int) else max(1, self.output_chars // 4)
        usage_out: dict = {"output_tokens": output_tokens}
        pt = upstream.get("prompt_tokens")
        if isinstance(pt, int):  # 上游给了准确 input_tokens 就回填(替代 message_start 里的估算)
            usage_out["input_tokens"] = pt
        out.append(
            _event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": map_stop_reason(self.finish_reason), "stop_sequence": None},
                    "usage": usage_out,
                },
            )
        )
        out.append(_event("message_stop", {"type": "message_stop"}))
        return out

    def error_event(self, message: str) -> bytes:
        return _event(
            "error", {"type": "error", "error": {"type": "api_error", "message": message}}
        )
