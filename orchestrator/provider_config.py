"""
orchestrator/provider_config.py
"""
import os
from pathlib import Path
import yaml

_KNOWN_KEYS: dict[str, list[str]] = {
    "script_generation": ["api_key", "model_id"],
    "voice_synthesis": ["api_key", "model_id", "voice_id_override"],
    "avatar_render": ["api_key", "presenter_id", "base_url"],
    "caption_generation": ["api_key", "model_size", "device"],
}

def load_provider_config(capability: str) -> dict:
    config: dict = {}
    config_path = Path("configs/providers") / f"{capability}.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    prefix = capability.upper().replace("-", "_")
    keys_to_check = set(config.keys()) | set(_KNOWN_KEYS.get(capability, []))
    for key in keys_to_check:
        env_key = f"{prefix}_{key.upper()}"
        if env_key in os.environ:
            config[key] = os.environ[env_key]
    return config