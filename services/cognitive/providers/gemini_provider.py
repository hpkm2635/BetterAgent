import os
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from services.cognitive.providers.base import BaseLLMProvider
from shared.config_loader import get_config_val

logger = logging.getLogger("gemini_provider")

# JSON Schema "type" -> Gemini types.Schema "type" mapping. Function parameters
# declared via ToolRegistry/MCP list_tools() come as standard JSON Schema, but
# Gemini's FunctionDeclaration needs its own type enum -- without this map every
# parameter (including ints/bools) gets silently sent to Gemini as STRING.
_JSON_TO_GEMINI_TYPE = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


class GeminiProvider(BaseLLMProvider):

    def __init__(self, api_key: Optional[str] = None):
        load_dotenv()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = get_config_val("llm.gemini.model", "gemini-3.1-flash-lite")
        self.client = None
        if self.api_key and not self.api_key.startswith("your_"):
            try:
                from google import genai
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"GeminiProvider initialized with model: {self.model_name}")
            except Exception as e:
                logger.warning(f"Failed to initialize google.genai Client: {e}")

    def _json_schema_to_gemini_schema(self, schema: Dict[str, Any]):
        from google.genai import types

        if not isinstance(schema, dict):
            return types.Schema(type="STRING")

        json_type = schema.get("type", "string")
        gemini_type = _JSON_TO_GEMINI_TYPE.get(json_type, "STRING")
        kwargs: Dict[str, Any] = {"type": gemini_type}

        desc = schema.get("description")
        if desc:
            kwargs["description"] = desc

        if "enum" in schema:
            kwargs["enum"] = schema["enum"]

        if gemini_type == "OBJECT":
            raw_props = schema.get("properties", {}) or {}
            kwargs["properties"] = {
                p_name: self._json_schema_to_gemini_schema(p_info)
                for p_name, p_info in raw_props.items()
            }
            if schema.get("required"):
                kwargs["required"] = schema["required"]
        elif gemini_type == "ARRAY":
            items_schema = schema.get("items", {}) or {"type": "string"}
            kwargs["items"] = self._json_schema_to_gemini_schema(items_schema)

        return types.Schema(**kwargs)

    def _build_function_declarations(self, tools_schema: Optional[List[Dict[str, Any]]]):
        from google.genai import types

        if not tools_schema:
            return []

        func_decls = []
        for t in tools_schema:
            params = t.get("parameters", {}) or {}
            raw_props = params.get("properties", {}) or {}
            req_fields = params.get("required", [])

            props = {
                p_name: self._json_schema_to_gemini_schema(p_info)
                for p_name, p_info in raw_props.items()
            }

            func_decls.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=types.Schema(
                        type="OBJECT",
                        properties=props,
                        required=req_fields,
                    ),
                )
            )
        return func_decls

    def _messages_to_contents(self, messages: List[Dict[str, Any]]):
        from google.genai import types

        contents = []
        for m in messages:
            meta = m.get("metadata")

            # Synthetic tool-call round-trip turns (see cognitive_engine.stream_reasoning_loop):
            # a "model" turn recording the function call the LLM made, followed by a
            # "user" turn carrying that tool's real result back to the model.
            if isinstance(meta, dict) and meta.get("function_call"):
                fc = meta["function_call"]
                thought_sig = fc.get("thought_signature")
                parts = [
                    types.Part(
                        function_call=types.FunctionCall(name=fc["name"], args=fc.get("args") or {}),
                        thought_signature=thought_sig,
                    )
                ]
                contents.append(types.Content(role="model", parts=parts))
                continue
            if isinstance(meta, dict) and meta.get("function_response"):
                fr = meta["function_response"]
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=fr["name"], response=fr.get("response") or {})],
                ))
                continue

            role = "user" if m.get("role") == "user" else "model"
            content_text = m.get("content", "")
            parts = []

            # Extract photo_path or vision_frame base64 from metadata
            photo_path = None
            vision_frame_bytes = None
            if isinstance(meta, dict):
                if meta.get("photo_path"):
                    photo_path = meta.get("photo_path")
                if meta.get("vision_frame") and isinstance(meta["vision_frame"], dict):
                    vf = meta["vision_frame"]
                    b64_str = vf.get("image_base64", "")
                    if b64_str:
                        import base64
                        try:
                            vision_frame_bytes = base64.b64decode(b64_str)
                        except Exception as b_err:
                            logger.warning(f"Failed to decode vision_frame base64: {b_err}")
            elif "[猫娘已发送照片:" in content_text:
                try:
                    start = content_text.find("[猫娘已发送照片:") + len("[猫娘已发送照片:")
                    end = content_text.find("]", start)
                    if start != -1 and end != -1:
                        photo_path = content_text[start:end].strip()
                except Exception:
                    pass
            elif "[主人发送了一张照片:" in content_text:
                try:
                    start = content_text.find("[主人发送了一张照片:") + len("[主人发送了一张照片:")
                    end = content_text.find("]", start)
                    if start != -1 and end != -1:
                        photo_path = content_text[start:end].strip()
                except Exception:
                    pass

            # Attach raw vision frame bytes if available
            if vision_frame_bytes:
                try:
                    parts.append(types.Part.from_bytes(data=vision_frame_bytes, mime_type="image/jpeg"))
                    logger.info(f"👁️ Attached real-time vision frame image ({len(vision_frame_bytes)} bytes) to Gemini Multimodal Context!")
                except Exception as v_err:
                    logger.warning(f"Failed to attach vision_frame_bytes to Gemini context: {v_err}")

            # Attach compressed image Part if photo file exists locally
            elif photo_path and os.path.exists(photo_path):
                try:
                    img_bytes = None
                    try:
                        import io
                        from PIL import Image
                        with Image.open(photo_path) as img:
                            if img.mode in ("RGBA", "P", "CMYK"):
                                img = img.convert("RGB")
                            w, h = img.size
                            max_dim = 1024
                            if w > max_dim or h > max_dim:
                                if w >= h:
                                    new_w = max_dim
                                    new_h = int(h * (max_dim / float(w)))
                                else:
                                    new_h = max_dim
                                    new_w = int(w * (max_dim / float(h)))
                                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=85, optimize=True)
                            img_bytes = buf.getvalue()
                    except Exception as c_err:
                        logger.warning(f"Image compression fallback to raw read: {c_err}")
                        with open(photo_path, "rb") as pf:
                            img_bytes = pf.read()

                    parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
                    logger.info(f"Attached compressed vision photo to Gemini LLM context: {photo_path} ({len(img_bytes)} bytes)")
                except Exception as p_err:
                    logger.warning(f"Failed to attach photo {photo_path} to Gemini context: {p_err}")

            parts.append(types.Part.from_text(text=content_text))
            contents.append(types.Content(role=role, parts=parts))

        return contents

    async def generate(self,
                       messages: List[Dict[str, Any]],
                       tools_schema: Optional[List[Dict[str, Any]]] = None,
                       system_prompt: Optional[str] = None) -> Dict[str, Any]:
        last_msg = messages[-1]["content"] if messages else ""

        if self.client:
            try:
                from google.genai import types

                config_kwargs = {}
                if system_prompt:
                    config_kwargs["system_instruction"] = system_prompt

                func_decls = self._build_function_declarations(tools_schema)
                if func_decls:
                    config_kwargs["tools"] = [types.Tool(function_declarations=func_decls)]

                config = types.GenerateContentConfig(**config_kwargs)
                contents = self._messages_to_contents(messages)

                import asyncio
                loop = asyncio.get_running_loop()
                response = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self.client.models.generate_content(
                            model=self.model_name,
                            contents=contents if contents else last_msg,
                            config=config,
                        )
                    ),
                    timeout=35.0
                )

                tool_calls = []
                if hasattr(response, "function_calls") and response.function_calls:
                    for fc in response.function_calls:
                        tool_calls.append({
                            "name": fc.name,
                            "args": dict(fc.args or {})
                        })

                res_text = response.text or ""
                return {
                    "text": res_text,
                    "tool_calls": tool_calls,
                    "finish_reason": "STOP"
                }
            except Exception as e:
                logger.error(f"Gemini API generation error: {e}")

        # Fallback catgirl response
        reply_text = f"喵~ 收到主人的消息了：{last_msg}"
        return {
            "text": reply_text,
            "tool_calls": [],
            "finish_reason": "STOP"
        }

    async def generate_stream(
        self,
        messages: List[Dict[str, Any]],
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        cancel_event: Optional[Any] = None,
    ):
        """
        Pure async streaming generator using google-genai client.aio.models.generate_content_stream.

        Yields tagged-union dicts so callers can tell text deltas apart from
        tool calls the model wants executed:
          {"type": "text", "delta": str}
          {"type": "tool_calls", "calls": [{"name": str, "args": dict}, ...]}
        """
        last_msg = messages[-1]["content"] if messages else ""

        if not self.client:
            yield {"type": "text", "delta": f"喵~ 收到主人的消息了：{last_msg}"}
            return

        try:
            from google.genai import types

            config_kwargs = {}
            if system_prompt:
                config_kwargs["system_instruction"] = system_prompt

            func_decls = self._build_function_declarations(tools_schema)
            if func_decls:
                config_kwargs["tools"] = [types.Tool(function_declarations=func_decls)]

            config = types.GenerateContentConfig(**config_kwargs)
            contents = self._messages_to_contents(messages)

            # ⚡ Pure Async Generator Call using self.client.aio
            response_stream = await self.client.aio.models.generate_content_stream(
                model=self.model_name,
                contents=contents if contents else last_msg,
                config=config,
            )

            # Function calls arrive as whole parts (not token-streamed), so they're
            # collected across the stream and surfaced once it drains -- the caller
            # (CognitiveEngine.stream_reasoning_loop) executes them and starts a new
            # generate_stream() round with the results appended to `messages`.
            pending_calls: List[Dict[str, Any]] = []
            current_thought_sig: Optional[bytes] = None

            async for chunk in response_stream:
                if cancel_event and cancel_event.is_set():
                    logger.info("⚡ Gemini stream cancelled via cancel_event")
                    break

                # Read part.text directly instead of the chunk.text shorthand:
                # a chunk carrying both a text part and a function_call part
                # (routine once tool use is involved) makes that shorthand
                # print an SDK-level "non-text parts in the response" warning
                # to stderr on every such chunk even though nothing is wrong.
                text_delta = ""
                if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                    for part in chunk.candidates[0].content.parts:
                        if getattr(part, "thought_signature", None):
                            current_thought_sig = part.thought_signature
                        if getattr(part, "text", None):
                            text_delta += part.text

                if text_delta:
                    yield {"type": "text", "delta": text_delta}
                if getattr(chunk, "function_calls", None):
                    for fc in chunk.function_calls:
                        call_dict: Dict[str, Any] = {"name": fc.name, "args": dict(fc.args or {})}
                        if current_thought_sig:
                            call_dict["thought_signature"] = current_thought_sig
                        pending_calls.append(call_dict)

            if pending_calls:
                yield {"type": "tool_calls", "calls": pending_calls}

        except Exception as e:
            logger.error(f"Gemini API streaming error: {e}")
            yield {"type": "text", "delta": f"喵~ 收到主人的消息了：{last_msg}"}
