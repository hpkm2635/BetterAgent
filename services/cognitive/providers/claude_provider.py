import os
import logging
from typing import List, Dict, Any, Optional
from services.cognitive.providers.base import BaseLLMProvider
from shared.config_loader import get_config_val

logger = logging.getLogger("claude_provider")


class ClaudeProvider(BaseLLMProvider):
    """
    Anthropic Claude provider — streaming-first implementation.
    Requires: pip install anthropic
    Env:      CLAUDE_API_KEY
    Config:   llm.claude.model (default: claude-3-5-sonnet-20241022)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("CLAUDE_API_KEY", "")
        self.model_name = get_config_val("llm.claude.model", "claude-3-5-sonnet-20241022")
        self.client = None
        if self.api_key and not self.api_key.startswith("your_"):
            try:
                import anthropic
                self.client = anthropic.AsyncAnthropic(api_key=self.api_key)
                logger.info(f"ClaudeProvider initialized with model: {self.model_name}")
            except ImportError:
                logger.warning(
                    "ClaudeProvider: 'anthropic' package not installed. "
                    "Run: pip install anthropic"
                )
            except Exception as e:
                logger.warning(f"ClaudeProvider init failed: {e}")
        else:
            # NOTICE: Explicit warning so operator knows why Claude is unavailable
            # instead of getting silent stub responses.
            logger.warning(
                "ClaudeProvider: CLAUDE_API_KEY is not set or is a placeholder. "
                "Claude will not be available as a provider."
            )

    def supports_vision(self) -> bool:
        return True  # Claude 3.x supports image input

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delegate to generate_stream() to avoid code duplication."""
        full_text = ""
        tool_calls: List[Dict[str, Any]] = []
        async for event in self.generate_stream(messages, tools_schema, system_prompt):
            if event.get("type") == "text":
                full_text += event.get("delta", "")
            elif event.get("type") == "tool_calls":
                tool_calls.extend(event.get("calls", []))
        return {"text": full_text, "tool_calls": tool_calls, "finish_reason": "end_turn"}

    async def generate_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ):
        if not self.client:
            logger.error(
                "ClaudeProvider.generate_stream() called but client is not initialized. "
                "Check CLAUDE_API_KEY and 'anthropic' package installation."
            )
            yield {"type": "text", "delta": ""}
            return

        anthropic_messages = self._build_messages(messages)
        anthropic_tools = self._build_tools(tools_schema or [])

        try:
            kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "max_tokens": 4096,
                "messages": anthropic_messages,
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            async with self.client.messages.stream(**kwargs) as stream:
                pending_tool_uses: List[Dict[str, Any]] = []
                async for event in stream:
                    if cancel_event and cancel_event.is_set():
                        break
                    ev_type = getattr(event, "type", None)
                    if ev_type == "content_block_delta":
                        delta_obj = getattr(event, "delta", None)
                        delta_type = getattr(delta_obj, "type", None)
                        if delta_type == "input_json_delta":
                            partial_json = getattr(delta_obj, "partial_json", "")
                            if pending_tool_uses and isinstance(partial_json, str) and partial_json:
                                pending_tool_uses[-1]["_input_str"] += partial_json
                        else:
                            delta_text = getattr(delta_obj, "text", "")
                            if isinstance(delta_text, str) and delta_text:
                                yield {"type": "text", "delta": delta_text}

                    elif ev_type == "content_block_start":
                        cb = getattr(event, "content_block", None)
                        if cb and getattr(cb, "type", None) == "tool_use":
                            pending_tool_uses.append({
                                "id": getattr(cb, "id", "toolu_0"),
                                "name": getattr(cb, "name", ""),
                                "args": {},
                                "_input_str": "",
                            })

                    elif ev_type == "content_block_stop":
                        if pending_tool_uses and pending_tool_uses[-1].get("_input_str"):
                            import json as _json
                            try:
                                pending_tool_uses[-1]["args"] = _json.loads(
                                    pending_tool_uses[-1].pop("_input_str", "{}")
                                )
                            except Exception:
                                pending_tool_uses[-1].pop("_input_str", None)

                if pending_tool_uses:
                    yield {
                        "type": "tool_calls",
                        "calls": [{"name": t["name"], "args": t["args"]} for t in pending_tool_uses],
                    }

        except Exception as e:
            logger.error(f"ClaudeProvider streaming error: {e}", exc_info=True)
            yield {"type": "text", "delta": ""}

    @staticmethod
    def _build_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert internal message format to Anthropic API format with unique tool IDs and strict role alternation."""
        raw_blocks = []
        call_counter = 0
        last_tool_id = ""

        for m in messages:
            role = "user" if m.get("role") == "user" else "assistant"
            content = m.get("content", "")
            meta = m.get("metadata", {}) or {}

            if isinstance(meta, dict) and meta.get("function_call"):
                fc = meta["function_call"]
                call_counter += 1
                call_id = fc.get("id") or f"call_{call_counter}"
                last_tool_id = call_id
                raw_blocks.append({
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": call_id,
                                 "name": fc["name"], "input": fc.get("args", {})}],
                })
                continue
            if isinstance(meta, dict) and meta.get("function_response"):
                fr = meta["function_response"]
                tool_id = fr.get("id") or fr.get("tool_use_id") or last_tool_id or f"call_{call_counter}"
                raw_blocks.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                 "content": str(fr.get("response", {}))}],
                })
                continue

            raw_blocks.append({"role": role, "content": content or ""})

        # Merge consecutive same-role messages and enforce initial 'user' role
        result: List[Dict[str, Any]] = []
        for item in raw_blocks:
            role = item["role"]
            content = item["content"]

            if not result:
                if role != "user":
                    result.append({"role": "user", "content": "Hello"})
                result.append({"role": role, "content": content if isinstance(content, list) else content})
            else:
                prev = result[-1]
                if prev["role"] == role:
                    if isinstance(prev["content"], list):
                        if isinstance(content, list):
                            prev["content"].extend(content)
                        elif content:
                            prev["content"].append({"type": "text", "text": str(content)})
                    else:
                        if isinstance(content, list):
                            prev_text = prev["content"]
                            prev["content"] = [{"type": "text", "text": prev_text}] if prev_text else []
                            prev["content"].extend(content)
                        else:
                            prev["content"] = (prev["content"] + "\n" + str(content)).strip()
                else:
                    result.append({"role": role, "content": content if isinstance(content, list) else content})

        return result

    @staticmethod
    def _build_tools(tools_schema: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert internal tool schema to Anthropic tool format."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools_schema
        ]
