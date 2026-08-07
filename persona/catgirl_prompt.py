"""
DEPRECATED: Persona configuration has been modularized to config/persona/*.yaml.
Use shared.persona_loader.PersonaLoader.load_active_persona() instead.
"""
from shared.persona_loader import PersonaLoader

_data = PersonaLoader.load_active_persona()
CATGIRL_BASE_PROMPT = _data.get("base_prompt", "")


def build_sleepy_prompt() -> str:
    return _data.get("sleepy_prompt", "")

