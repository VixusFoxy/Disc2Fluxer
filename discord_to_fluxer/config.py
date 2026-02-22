from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "discord-to-fluxer"
CONFIG_FILE = CONFIG_DIR / "config.json"

_DEFAULTS = {
    "discord_token": "",
    "fluxer_token": "",
    "fluxer_base_url": "https://api.fluxer.app/v1",
}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return {**_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


_SAFE_KEYS = {"fluxer_base_url"}


def save(data: dict) -> None:
    # Only persist known-safe keys to disk. Tokens are never written.
    safe = {k: v for k, v in data.items() if k in _SAFE_KEYS}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(safe, indent=2))
