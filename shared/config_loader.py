import os
import yaml
from typing import Dict, Any, Optional

_cached_config: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    # Find root dir
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)

    yaml_path = os.path.join(root_dir, "config", "config.yaml")
    example_path = os.path.join(root_dir, "config", "config.yaml.example")

    target_path = yaml_path if os.path.exists(yaml_path) else example_path

    if os.path.exists(target_path):
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                _cached_config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Warning: Failed to parse {target_path}: {e}")
            _cached_config = {}
    else:
        _cached_config = {}

    return _cached_config


def get_config_val(path: str, default: Any = None) -> Any:
    cfg = load_config()
    keys = path.split(".")
    curr = cfg
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return default
    return curr


def invalidate_cache() -> None:
    """Force the next load_config()/get_config_val() call to re-read config.yaml
    from disk. Needed after anything rewrites config.yaml out-of-process (e.g.
    admin backend's persona-activate endpoint flipping persona.active) --
    otherwise this process-wide cache never sees the change."""
    global _cached_config
    _cached_config = None
