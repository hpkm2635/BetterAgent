import os
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from services.cognitive.providers.base import BaseLLMProvider
from shared.config_loader import get_config_val

logger = logging.getLogger("gemini_provider")


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

                if tools_schema:
                    func_decls = []
                    for t in tools_schema:
                        props = {}
                        params = t.get("parameters", {})
                        raw_props = params.get("properties", {})
                        req_fields = params.get("required", [])

                        for p_name, p_info in raw_props.items():
                            desc = p_info.get("description", "") if isinstance(p_info, dict) else ""
                            props[p_name] = types.Schema(
                                type="STRING",
                                description=desc
                            )

                        decl = types.FunctionDeclaration(
                            name=t["name"],
                            description=t.get("description", ""),
                            parameters=types.Schema(
                                type="OBJECT",
                                properties=props,
                                required=req_fields
                            )
                        )
                        func_decls.append(decl)

                    if func_decls:
                        config_kwargs["tools"] = [types.Tool(function_declarations=func_decls)]

                config = types.GenerateContentConfig(**config_kwargs)

                contents = []
                for m in messages:
                    role = "user" if m.get("role") == "user" else "model"
                    content_text = m.get("content", "")
                    parts = []

                    # Extract photo_path or vision_frame base64 from metadata
                    photo_path = None
                    vision_frame_bytes = None
                    meta = m.get("metadata")
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
                    contents.append(
                        types.Content(
                            role=role,
                            parts=parts
                        )
                    )

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
