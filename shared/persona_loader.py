import os
import tempfile
import yaml
import logging
from typing import Dict, Any
from shared import config_loader
from shared.config_loader import get_config_val

logger = logging.getLogger("persona_loader")


class PersonaLoader:
    _cached_persona: Dict[str, Any] = {}
    _last_active_id: str = ""

    @classmethod
    def load_active_persona(cls, force_reload: bool = False) -> Dict[str, Any]:
        """
        Loads active persona dict from config/persona/<active_id>.yaml.
        Allows instant persona switching in 1 second by changing persona.active in config/config.yaml.
        """
        active_id = get_config_val("persona.active", "catgirl")

        if not force_reload and cls._cached_persona and cls._last_active_id == active_id:
            return cls._cached_persona

        persona_path = cls._persona_path(active_id)

        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                cls._cached_persona = data
                cls._last_active_id = active_id
                logger.info(f"Loaded active persona: '{data.get('name', active_id)}' (id={active_id}) from {persona_path}")
                return data
        except Exception as err:
            logger.error(f"Failed to load persona YAML from {persona_path}: {err}")
            return {
                "id": "catgirl",
                "name": "Camelia",
                "base_prompt": "你叫 Camelia，是一个猫娘喵~",
                "appearance": "a cute anime catgirl",
                "art_style": "anime",
                "reference_images_dir": "config/reference_images/catgirl",
            }

    @classmethod
    def invalidate_cache(cls) -> None:
        """Call when persona configuration is updated or hot-reloaded."""
        cls._cached_persona = {}
        cls._last_active_id = ""

    @classmethod
    async def handle_persona_update(cls, raw: bytes) -> None:
        """
        NATS message handler for 'agent.persona.update'.
        Updates persona YAML on disk in-place (if a field patch is present)
        and always invalidates both this class's persona cache and
        config_loader's config.yaml cache -- a message with no field patch
        still means "persona.active changed, re-read everything", which is
        exactly what admin/backend/main.py's activate-persona endpoint sends.
        """
        import json
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and "payload" in payload:
                payload = payload["payload"]

            persona_id = payload.get("persona_id") or "catgirl"
            allowed = {"name", "appearance", "base_prompt", "sleepy_prompt", "knowledge_scope", "forbidden_topics"}
            tts_allowed = {"prompt_audio", "prompt_text", "prompt_lang", "text_lang"}
            patch: Dict[str, Any] = {k: v for k, v in payload.items() if k in allowed and isinstance(v, str)}
            tts_patch = payload.get("tts")
            if isinstance(tts_patch, dict):
                filtered_tts = {k: v for k, v in tts_patch.items() if k in tts_allowed and isinstance(v, str)}
                if filtered_tts:
                    patch["tts"] = filtered_tts

            if patch:
                cls._patch_yaml(persona_id, patch)

            config_loader.invalidate_cache()
            cls.invalidate_cache()
            logger.info(f"Persona '{persona_id}' hot-reloaded and in-memory cache invalidated. Fields updated: {list(patch.keys()) if patch else '(activation only)'}")
        except Exception as err:
            logger.error(f"Failed to handle persona update NATS message: {err}", exc_info=True)

    @classmethod
    def _persona_path(cls, persona_id: str) -> str:
        path = f"config/persona/{persona_id}.yaml"
        if not os.path.exists(path):
            return "config/persona/catgirl.yaml"
        return path

    @staticmethod
    def _merge_patch(data: Dict[str, Any], patch: Dict[str, Any]) -> None:
        """Applies `patch` onto `data` in place. `tts` is merged key-by-key
        into the existing sub-object instead of replacing it wholesale --
        the patch only ever carries the hot-reloadable subfields (see
        handle_persona_update's tts_allowed), so a plain top-level
        `data.update(patch)` would silently wipe out provider/voice_id."""
        for key, value in patch.items():
            if key == "tts" and isinstance(value, dict):
                if not isinstance(data.get("tts"), dict):
                    data["tts"] = {}
                data["tts"].update(value)
            else:
                data[key] = value

    @classmethod
    def _patch_yaml(cls, persona_id: str, patch: Dict[str, Any]) -> None:
        """In-place update of persona YAML using ruamel.yaml (or pyyaml fallback).

        Writes to a temp file in the same directory and os.replace()s it into
        place, so a crash mid-write can never leave a half-written YAML file
        on disk (same pattern as admin/backend/main.py's patch_persona).
        """
        persona_path = cls._persona_path(persona_id)

        try:
            try:
                from ruamel.yaml import YAML
                ryaml = YAML()
                ryaml.preserve_quotes = True
                with open(persona_path, "r", encoding="utf-8") as f:
                    data = ryaml.load(f) or {}
                cls._merge_patch(data, patch)
                dump = lambda f: ryaml.dump(data, f)
            except ImportError:
                with open(persona_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                cls._merge_patch(data, patch)
                dump = lambda f: yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            persona_dir = os.path.dirname(persona_path) or "."
            fd, tmp_path = tempfile.mkstemp(
                dir=persona_dir, prefix=f".{os.path.basename(persona_path)}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    dump(f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, persona_path)
            except Exception:
                os.unlink(tmp_path)
                raise
        except Exception as err:
            logger.error(f"Failed to patch YAML file {persona_path}: {err}")

