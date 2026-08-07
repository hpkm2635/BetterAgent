import os
import yaml
import logging
from typing import Dict, Any
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

        persona_path = f"config/persona/{active_id}.yaml"
        if not os.path.exists(persona_path):
            logger.warning(f"Persona config '{persona_path}' not found. Fallback to 'config/persona/catgirl.yaml'")
            persona_path = "config/persona/catgirl.yaml"

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
