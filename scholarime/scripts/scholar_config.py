"""Local Scholarime configuration and Semantic Scholar credential handling."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def config_path() -> Path:
    override = os.environ.get("SCHOLARIME_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Scholarime" / "config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Scholarime" / "config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "scholarime" / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def semantic_scholar_key() -> str:
    environment_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if environment_key:
        return environment_key
    value = load_config().get("semantic_scholar_api_key", "")
    return str(value).strip() if value else ""


def save_semantic_scholar_key(api_key: str) -> Path:
    cleaned = api_key.strip()
    if not cleaned:
        raise ValueError("Semantic Scholar API key must not be empty.")
    config = load_config()
    config["semantic_scholar_api_key"] = cleaned
    config["semantic_scholar_configured_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    return save_config(config)


def remove_semantic_scholar_key() -> bool:
    config = load_config()
    if "semantic_scholar_api_key" not in config:
        return False
    config.pop("semantic_scholar_api_key", None)
    config.pop("semantic_scholar_configured_at", None)
    save_config(config)
    return True
