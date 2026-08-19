from email import contentmanager
import os
import re
import json
import logging
import asyncio
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from services.cognitive.providers.base import BaseLLMProvider
from shared.config_loader import get_config_val
from shared.logger import setup_logger

load_dotenv()

logger = logging.getLogger("openai_provider")
qwen_raw_logger = setup_logger("qwen_raw_json")


class OpenAIProvider(BaseLLMProvider):

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        context_window: Optional[int] = None,
        provider_name: str = "openai",
    ):
        self.provider_name = provider_name
        self.api_key = (
            api_key
            or os.getenv(f"{provider_name.upper()}_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or get_config_val(f"llm.{provider_name}.api_key", "")
            or get_config_val("llm.openai.api_key", "sk-placeholder")
        )
        self.base_url = (
            base_url
            or os.getenv(f"{provider_name.upper()}_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or get_config_val(f"llm.{provider_name}.base_url", None)
            or get_config_val("llm.openai.base_url", None)
        )
        self.model = (
            model
            or get_config_val(f"llm.{provider_name}.model", None)
            or get_config_val("llm.openai.model", "gpt-4o")
        )
        self.temperature = temperature if temperature is not None else get_config_val(f"llm.{provider_name}.temperature", 0.7)
        self.max_tokens = max_tokens or get_config_val(f"llm.{provider_name}.max_tokens", 2048)
        self._context_window = context_window or get_config_val(f"llm.{provider_name}.context_window", 128000)

        # Log security notice if using non-official third-party relay proxies
        official_domains = ["api.openai.com", "api.deepseek.com", "dashscope.aliyuncs.com", "aliyuncs.com", "api.moonshot.cn"]
        if self.base_url:
            self._check_and_log_security_notice(self.provider_name, self.base_url, official_domains)

        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                kwargs = {"api_key": self.api_key}
                if self.base_url:
                    kwargs["base_url"] = self.base_url
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                logger.error("OpenAI Python SDK (`openai>=1.0.0`) is not installed. Please run `pip install openai`.")
                raise ImportError("OpenAI SDK is required for OpenAIProvider.")
        return self._client

    def get_context_window(self) -> int:
        return self._context_window

    def supports_vision(self) -> bool:
        model_lower = self.model.lower()
        provider_lower = self.provider_name.lower()
        return provider_lower in ["qwen", "gemini"] or any(k in model_lower for k in ["gpt-4o", "gpt-4-turbo", "vision", "vl", "qwen", "claude", "gemini"])

    def supports_tool_calling(self) -> bool:
        # Most modern OpenAI models support tool calling; DeepSeek-R1 pure reasoning can disable if needed
        return True

    async def health_check(self) -> bool:
        """Lightweight API probe to verify connectivity and key validity."""
        try:
            client = self._get_client()
            await client.models.list()
            return True
        except Exception as err:
            logger.warning(f"[{self.provider_name}] Health check failed for model '{self.model}': {err}")
            return False

    def _build_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Converts internal message format to OpenAI Chat Completion format.
        Includes Vision base64 conversion and strict Tool Call ID history matching.
        """
        openai_msgs: List[Dict[str, Any]] = []

        if system_prompt:
            openai_msgs.append({"role": "system", "content": system_prompt})

        # Track active tool call IDs to ensure role: "tool" responses strictly match tool_call_id
        pending_tool_call_ids: List[str] = []
        call_counter = 1

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            metadata = msg.get("metadata", {}) or {}

            if role == "system":
                openai_msgs.append({"role": "system", "content": content})
                continue

            if role == "user":
                # Handle multimodal vision_frame if present
                vision_frame = metadata.get("vision_frame")
                if vision_frame and isinstance(vision_frame, dict):
                    b64_data = vision_frame.get("image_base64", "")
                    fmt = vision_frame.get("format", "jpeg")
                    if b64_data:
                        openai_msgs.append({
                            "role": "user",
                            "content": [
                                {"type": "text", "text": content or "Please analyze this image."},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/{fmt};base64,{b64_data}"},
                                },
                            ],
                        })
                        continue
                    else:
                            # 2. 优雅降级（不支持视觉的模型，如 DeepSeek）：降级为纯文本提示，防止 API 报错 400
                            fallback_text = (
                                f"{content}\n"
                                f"[系统提示: 用户提供了图片/视觉画面，但当前 LLM 节点 ('{self.model}') "
                                f"不支持视觉输入。请礼貌告知对方你看不到图片具体内容喵~]"
                            ).strip()
                            openai_msgs.append({"role": "user", "content": fallback_text})
                            continue
                openai_msgs.append({"role": "user", "content": content})

            elif role == "assistant":
                msg_obj: Dict[str, Any] = {"role": "assistant"}
                if content:
                    msg_obj["content"] = content

                # If metadata contains assistant tool calls, format tool_calls array with unique IDs
                tool_calls_meta = metadata.get("tool_calls") or msg.get("tool_calls")
                if tool_calls_meta and isinstance(tool_calls_meta, list):
                    formatted_calls = []
                    for call in tool_calls_meta:
                        call_id = call.get("id") or f"call_{call_counter}"
                        call_counter += 1
                        pending_tool_call_ids.append(call_id)

                        fn_name = call.get("name", "")
                        fn_args = call.get("args", {})
                        args_str = json.dumps(fn_args, ensure_ascii=False) if isinstance(fn_args, dict) else str(fn_args)

                        formatted_calls.append({
                            "id": call_id,
                            "type": "function",
                            "function": {"name": fn_name, "arguments": args_str},
                        })
                    msg_obj["tool_calls"] = formatted_calls

                openai_msgs.append(msg_obj)

            elif role == "tool":
                # OpenAI requires role: "tool" with explicit tool_call_id matching preceding assistant call
                matched_id = msg.get("tool_call_id")
                if not matched_id and pending_tool_call_ids:
                    matched_id = pending_tool_call_ids.pop(0)
                elif not matched_id:
                    matched_id = f"call_{call_counter}"
                    call_counter += 1

                openai_msgs.append({
                    "role": "tool",
                    "tool_call_id": matched_id,
                    "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                })

        return openai_msgs

    def _build_tools(self, tools_schema: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        if not tools_schema:
            return None

        openai_tools = []
        for t in tools_schema:
            name = t.get("name", "")
            desc = t.get("description", "")
            params = t.get("parameters", {"type": "object", "properties": {}})

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": params,
                },
            })
        return openai_tools if openai_tools else None

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Single-shot generation delegating to stream aggregation."""
        full_text = ""
        tool_calls = []

        async for chunk in self.generate_stream(messages, tools_schema, system_prompt):
            chunk_type = chunk.get("type")
            if chunk_type == "text":
                full_text += chunk.get("delta", "")
            elif chunk_type == "tool_calls":
                tool_calls.extend(chunk.get("calls", []))

        return {
            "text": full_text,
            "tool_calls": tool_calls,
            "finish_reason": "stop" if not tool_calls else "tool_calls",
        }

    @staticmethod
    def _extract_text_tool_calls(text: str) -> tuple[str, List[Dict[str, Any]]]:
        """
        Robustly extracts embedded tool call blocks from model content streams
        (common in Qwen, DeepSeek, and Ollama OpenAI-compatible APIs).
        Supports:
        1. <tool_call>{"name": "func", "arguments": {...}}</tool_call>
        2. ```json\n{"tool": "func", ...}\n```
        3. ```json{ "tool": "func", ... }```
        4. {"tool": "func", "parameters": {...}}
        5. 👉 func(arg=val) or Action: func(arg=val)
        Returns (cleaned_user_text, list_of_parsed_tool_calls).
        """
        if not text:
            return text, []

        tool_calls: List[Dict[str, Any]] = []

        pattern_xml = r'<tool_call>\s*(.*?)\s*(?:</tool_call>|$)'
        pattern_json_block = r'(?:```(?:json)?\s*)?({(?:\s*"tool"\s*|\s*"tool_use"\s*|\s*"tool_call"\s*|\s*"name"\s*|\s*"function"\s*|\s*"action"\s*):[\s\S]*?})(?:\s*```)?'

        def normalize_tool_name(raw_name: str) -> str:
            name = raw_name.strip()
            if name.startswith("sts2_"):
                name = name[5:]
            if name in ("get_game_state", "play_card", "end_turn", "use_potion", "discard_potion", "claim_reward", "select_card_reward", "skip_card_reward", "combat_select_card", "combat_confirm_selection", "combat_play_card", "combat_end_turn"):
                return name
            return name

        def process_json_block(raw_json: str) -> tuple[bool, str]:
            s = raw_json.strip()
            # Balance unclosed curly braces for nested JSON parameters
            open_count = s.count('{')
            close_count = s.count('}')
            if open_count > close_count:
                s += '}' * (open_count - close_count)

            try:
                parsed = json.loads(s)
                if not isinstance(parsed, dict):
                    return False, ""
                
                # Check for tool_use or tool_call nested dictionary structure
                tool_use_obj = parsed.get("tool_use") or parsed.get("tool_call")
                if isinstance(tool_use_obj, dict):
                    raw_name = tool_use_obj.get("name") or tool_use_obj.get("tool") or ""
                    args = tool_use_obj.get("input") or tool_use_obj.get("parameters") or tool_use_obj.get("args") or {}
                else:
                    raw_name = parsed.get("name") or parsed.get("tool") or parsed.get("function") or parsed.get("action") or ""
                    args = parsed.get("arguments") or parsed.get("args") or parsed.get("parameters") or parsed.get("action_input") or {}

                if not raw_name:
                    return False, ""

                name = normalize_tool_name(str(raw_name))

                if not args and isinstance(parsed, dict):
                    args = {k: v for k, v in parsed.items() if k not in ("tool", "tool_use", "tool_call", "name", "function", "action", "type", "parameters", "text", "content")}

                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass

                extracted_text = parsed.get("text") or parsed.get("content") or ""

                if name:
                    tool_calls.append({
                        "id": f"call_text_{len(tool_calls)}",
                        "name": name,
                        "args": args if isinstance(args, dict) else {"raw": args},
                    })
                    try:
                        import time
                        log_entry = {
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "source": "text_based_json",
                            "raw_json": s,
                            "extracted_name": name,
                            "extracted_args": args,
                        }
                        qwen_raw_logger.info(json.dumps(log_entry, ensure_ascii=False))
                    except Exception:
                        pass
                    return True, str(extracted_text)
            except Exception as e:
                logger.warning(f"Failed to parse text-based tool call JSON: {raw_json} ({e})")
            return False, ""

        def replacer_xml(match):
            success, speech_text = process_json_block(match.group(1))
            return speech_text

        def replacer_json(match):
            success, speech_text = process_json_block(match.group(1))
            return speech_text

        cleaned_text = re.sub(pattern_xml, replacer_xml, text, flags=re.DOTALL)
        cleaned_text = re.sub(pattern_json_block, replacer_json, cleaned_text, flags=re.DOTALL)

        # 4. Prose Tool Call Recommendations Interception with Full Argument Parsing
        pattern_prose = r'(?:👉|Action:)\s*`?([a-zA-Z0-9_]+)`?(?:\((.*?)\))?'

        def _parse_prose_args(args_str: Optional[str]) -> Dict[str, Any]:
            if not args_str or not args_str.strip():
                return {}
            s = args_str.strip()
            # Try JSON loads
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                try:
                    val = json.loads(s)
                    return val if isinstance(val, dict) else {"raw": val}
                except Exception:
                    pass
            # Try kwarg key=val parsing
            res: Dict[str, Any] = {}
            kwarg_matches = re.findall(r'([a-zA-Z0-9_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s,]+))', s)
            if kwarg_matches:
                for key, v_dq, v_sq, v_raw in kwarg_matches:
                    val_str = v_dq if v_dq != '' else (v_sq if v_sq != '' else v_raw)
                    if val_str.lower() == "true":
                        val = True
                    elif val_str.lower() == "false":
                        val = False
                    elif val_str.isdigit():
                        val = int(val_str)
                    else:
                        try:
                            val = float(val_str)
                        except ValueError:
                            val = val_str
                    res[key] = val
                return res
            # Fallback for positional raw string/int
            try:
                val = json.loads(s)
                return {"arg": val}
            except Exception:
                return {"arg": s.strip('"\'')}

        def replacer_prose(match):
            raw_t_name = match.group(1)
            t_name = normalize_tool_name(raw_t_name)
            raw_args_str = match.group(2)
            parsed_args = _parse_prose_args(raw_args_str)

            # Default format="json" for get_game_state ONLY when no explicit args were passed
            if t_name == "get_game_state" and not parsed_args:
                parsed_args = {"format": "json"}

            tool_calls.append({
                "id": f"call_prose_{len(tool_calls)}",
                "name": t_name,
                "args": parsed_args,
            })
            return ""

        cleaned_text = re.sub(pattern_prose, replacer_prose, cleaned_text, flags=re.IGNORECASE)

        # 5. Clean up & silently discard any leftover malformed JSON blocks
        pattern_malformed_json = r'\{\s*"(?:tool|tool_use|tool_call|name|function|action)"\s*:[\s\S]*?\}'
        cleaned_text = re.sub(pattern_malformed_json, "", cleaned_text).strip()

        return cleaned_text, tool_calls

    async def generate_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ):
        """
        Asynchronously streams completion tokens and tool calls.
        Robustly handles non-JSON / 502 HTML proxy responses and text-based <tool_call> blocks.
        """
        client = self._get_client()
        built_msgs = self._build_messages(messages, system_prompt)
        built_tools = self._build_tools(tools_schema)

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": built_msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if built_tools:
            kwargs["tools"] = built_tools

        tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}
        yielded_something = False
        text_buffer = ""

        try:
            stream = await client.chat.completions.create(**kwargs)

            try:
                async for chunk in stream:
                    if cancel_event and getattr(cancel_event, "is_set", lambda: False)():
                        logger.info(f"[{self.provider_name}] Stream cancelled by cancel_event.")
                        break

                    if not chunk.choices or len(chunk.choices) == 0:
                        continue

                    delta = chunk.choices[0].delta

                    # 1. Thinking Delta Isolation (DeepSeek-R1 / Qwen-Thought reasoning_content)
                    reasoning_content = getattr(delta, "reasoning_content", None)
                    if reasoning_content:
                        yielded_something = True
                        yield {"type": "thinking_delta", "text": reasoning_content}

                    # 2. Text Delta (with full JSON & pseudo-tool call buffering)
                    content_delta = getattr(delta, "content", None)
                    if content_delta:
                        text_buffer += content_delta

                        # Check for start of tool_call, json codeblock, JSON object, or prose tool call indicators
                        xml_idx = text_buffer.find("<tool_call")
                        codeblock_idx = text_buffer.find("```")
                        json_match = re.search(r'\{\s*"(?:tool|tool_use|tool_call|name|function|action)"', text_buffer)
                        json_idx = json_match.start() if json_match else -1
                        prose_idx = text_buffer.find("👉")
                        action_idx = text_buffer.find("Action:")

                        indices = [i for i in (xml_idx, codeblock_idx, json_idx, prose_idx, action_idx) if i != -1]
                        if indices:
                            min_idx = min(indices)
                            if min_idx > 0:
                                clean_prefix = text_buffer[:min_idx]
                                text_buffer = text_buffer[min_idx:]
                                yielded_something = True
                                yield {"type": "text", "delta": clean_prefix}

                            # Check if the tool call block starting at index 0 has completed
                            has_closed_block = (
                                ("</tool_call>" in text_buffer) or
                                (text_buffer.startswith("```") and text_buffer.count("```") >= 2) or
                                (text_buffer.startswith("{") and text_buffer.count("{") <= text_buffer.count("}")) or
                                (text_buffer.startswith("👉") and ("\n" in text_buffer or ")" in text_buffer)) or
                                (text_buffer.startswith("Action:") and ("\n" in text_buffer or ")" in text_buffer))
                            )
                            if has_closed_block:
                                cleaned_text, text_tool_calls = self._extract_text_tool_calls(text_buffer)
                                text_buffer = ""
                                if cleaned_text:
                                    yielded_something = True
                                    yield {"type": "text", "delta": cleaned_text}
                                if text_tool_calls:
                                    for tc in text_tool_calls:
                                        idx = len(tool_calls_accumulator)
                                        tool_calls_accumulator[idx] = {
                                            "id": tc["id"],
                                            "name": tc["name"],
                                            "args_str": json.dumps(tc["args"], ensure_ascii=False),
                                        }
                        else:
                            json_starts = (
                                "{", "{\n", "{\r\n", "{\t", "{\"",
                                "{\"t", "{\"to", "{\"too", "{\"tool", "{\"tool_", "{\"tool_u", "{\"tool_us", "{\"tool_use", "{\"tool_c", "{\"tool_ca", "{\"tool_cal", "{\"tool_call",
                                "{\"n", "{\"na", "{\"nam", "{\"name",
                                "{\"f", "{\"fu", "{\"fun", "{\"func", "{\"funct", "{\"functi", "{\"function",
                                "{\"a", "{\"ac", "{\"act", "{\"acti", "{\"action"
                            )
                            tag_starts = ("<", "<t", "<to", "<too", "<tool", "<tool_", "`", "``", "👉", "A", "Ac", "Act", "Acti", "Action", "Action:")
                            if text_buffer.endswith(json_starts) or text_buffer.endswith(tag_starts):
                                pass
                            else:
                                yielded_something = True
                                yield {"type": "text", "delta": text_buffer}
                                text_buffer = ""

                    # 3. Tool Calls Delta Accumulation (Native API)
                    delta_tool_calls = getattr(delta, "tool_calls", None)
                    if delta_tool_calls:
                        yielded_something = True
                        for tool_call_delta in delta_tool_calls:
                            idx = tool_call_delta.index
                            if idx not in tool_calls_accumulator:
                                tool_calls_accumulator[idx] = {
                                    "id": tool_call_delta.id or f"call_{idx}",
                                    "name": tool_call_delta.function.name or "",
                                    "args_str": tool_call_delta.function.arguments or "",
                                }
                            else:
                                if tool_call_delta.function.name:
                                    tool_calls_accumulator[idx]["name"] += tool_call_delta.function.name
                                if tool_call_delta.function.arguments:
                                    tool_calls_accumulator[idx]["args_str"] += tool_call_delta.function.arguments
            finally:
                if hasattr(stream, "response") and hasattr(stream.response, "aclose"):
                    try:
                        await stream.response.aclose()
                    except Exception:
                        pass
                elif hasattr(stream, "aclose"):
                    try:
                        await stream.aclose()
                    except Exception:
                        pass

            # Flush remaining text in text_buffer and extract any embedded <tool_call> blocks
            if text_buffer:
                cleaned_text, text_tool_calls = self._extract_text_tool_calls(text_buffer)
                if cleaned_text:
                    yielded_something = True
                    yield {"type": "text", "delta": cleaned_text}
                if text_tool_calls:
                    for tc in text_tool_calls:
                        idx = len(tool_calls_accumulator)
                        tool_calls_accumulator[idx] = {
                            "id": tc["id"],
                            "name": tc["name"],
                            "args_str": json.dumps(tc["args"], ensure_ascii=False),
                        }

            # Flush accumulated tool calls if present
            if tool_calls_accumulator:
                parsed_calls = []
                for _, call_data in sorted(tool_calls_accumulator.items()):
                    args_dict = {}
                    args_str = call_data["args_str"].strip()
                    if args_str:
                        try:
                            args_dict = json.loads(args_str)
                        except json.JSONDecodeError:
                            logger.warning(f"[{self.provider_name}] Failed to parse tool call args JSON: {args_str}")
                            args_dict = {"raw_args": args_str}

                    parsed_calls.append({
                        "id": call_data["id"],
                        "name": call_data["name"],
                        "args": args_dict,
                    })

                try:
                    import time
                    log_entry = {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "provider": self.provider_name,
                        "model": self.model,
                        "parsed_calls": parsed_calls,
                    }
                    qwen_raw_logger.info(json.dumps(log_entry, ensure_ascii=False))
                except Exception:
                    pass

                yield {"type": "tool_calls", "calls": parsed_calls}

        except Exception as err:
            err_str = str(err)
            logger.error(f"[{self.provider_name}] Stream error for model '{self.model}': {err_str}")

            # Non-JSON HTML 502/504 Proxy Error Fallback
            fallback_msg = f"⚠️ [API 节点响应故障] {self.provider_name} ({self.model}) 返回异常: {err_str[:120]}"
            if "502" in err_str or "504" in err_str or "html" in err_str.lower():
                fallback_msg = f"⚠️ [中转站 502/504 网页报错] 节点 '{self.base_url or 'default'}' 暂时掉线，请检查中转站服务状态。"

            yield {"type": "text", "delta": fallback_msg}

        if not yielded_something:
            yield {"type": "text", "delta": ""}
