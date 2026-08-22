import os
import glob
import logging
from typing import Dict, Any, Optional, List
from services.cognitive.tools.base_tool import BaseTool
from shared.config_loader import get_config_val
from dotenv import load_dotenv

logger = logging.getLogger("image_gen_tool")


class ImageGenTool(BaseTool):

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generates an AI image, character selfie, or POV scene photograph. MUST be called whenever the user "
            "asks for a photo, selfie, picture, visual depiction of the character, or scenery in front of her."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Natural language visual scene or pose description.",
                },
                "category": {
                    "type": "string",
                    "enum": ["selfie", "scenery", "general"],
                    "description": "Image type: 'selfie' (character selfie/portrait), 'scenery' (POV scenery/room/environment in front of her), or 'general' (artwork/object).",
                },
                "style": {
                    "type": "string",
                    "description": "Visual art style (e.g. anime, watercolor, realistic). Defaults to config setting.",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, prompt: str, category: str = "selfie", style: Optional[str] = None) -> Dict[str, Any]:
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY", "")

        # Config-driven persona appearance and settings from active Persona YAML
        from shared.persona_loader import PersonaLoader
        persona_data = PersonaLoader.load_active_persona()
        default_appearance = "a friendly anime-style character"
        appearance = persona_data.get("appearance", default_appearance)
        art_style = style or persona_data.get("art_style", "anime")
        ref_dir = persona_data.get("reference_images_dir", "config/reference_images")

        # Build category-specific prompt
        if category == "scenery":
            full_prompt = (
                f"First-person point-of-view (POV) photograph looking at scenery: {prompt}. "
                f"No selfie face, immersive camera angle showing room/environment. Style: {art_style}."
            )
        elif category == "general":
            full_prompt = f"Detailed {art_style} illustration of: {prompt}."
        else:
            # Default to 'selfie' / character portrait
            full_prompt = (
                f"Detailed {art_style} selfie photograph of the character: {prompt}. "
                f"Character appearance details: {appearance}."
            )

        output_dir = "./temp"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"photo_{abs(hash(prompt + category)) % 100000}.jpg")

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            model_name = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")
            logger.info(f"Generating image ({category}) with model '{model_name}' for prompt: {prompt}")

            # Assemble contents array (with reference image parts if available)
            contents: List[Any] = []

            # Check for reference images in config/reference_images
            if os.path.exists(ref_dir):
                ref_files = []
                for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                    ref_files.extend(glob.glob(os.path.join(ref_dir, ext)))

                # Limit up to 3 reference images for multi-image reference fusion
                for ref_path in ref_files[:3]:
                    try:
                        with open(ref_path, "rb") as f:
                            img_bytes = f.read()
                        ext_lower = os.path.splitext(ref_path)[1].lower()
                        mime = "image/png" if "png" in ext_lower else "image/jpeg"
                        part = types.Part.from_bytes(data=img_bytes, mime_type=mime)
                        contents.append(part)
                        logger.info(f"Loaded reference image for fusion: {ref_path}")
                    except Exception as ref_err:
                        logger.warning(f"Failed to load reference image {ref_path}: {ref_err}")

            # Append the text prompt instruction
            contents.append(f"Generate an image: {full_prompt}")

            import asyncio
            loop = asyncio.get_running_loop()
            res = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.models.generate_content(
                        model=model_name,
                        contents=contents,
                    )
                ),
                timeout=30.0
            )

            image_bytes: Optional[bytes] = None
            if res.candidates and len(res.candidates) > 0:
                for part in res.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        image_bytes = part.inline_data.data
                        break

            if image_bytes:
                with open(output_path, "wb") as f:
                    f.write(image_bytes)
                logger.info(f"Gemini image ({category}) saved successfully: {output_path} ({len(image_bytes)} bytes)")
                return {
                    "status": "success",
                    "photo_path": output_path,
                    "prompt": prompt,
                    "category": category,
                    "style": art_style,
                }
            else:
                raise ValueError("Gemini returned no inline image bytes")

        except Exception as e:
            logger.warning(f"Gemini image generation failed ({e}), falling back to placeholder")
            try:
                from PIL import Image as PILImage
                img = PILImage.new("RGB", (512, 512), color=(200, 150, 220))
                img.save(output_path, "JPEG")
                logger.info(f"Placeholder image saved: {output_path}")
                return {
                    "status": "fallback",
                    "photo_path": output_path,
                    "prompt": prompt,
                    "category": category,
                    "style": art_style,
                }
            except Exception as e2:
                logger.error(f"Placeholder image also failed: {e2}")
                return {
                    "status": "error",
                    "photo_path": None,
                    "prompt": prompt,
                    "category": category,
                    "style": art_style,
                }
