"""Paths and user settings.

Everything the app stores lives in the ``data/`` folder next to the code:

    data/settings.json        your settings (including your Groq API key)
    data/projects/<id>/       one folder per project
    data/cache/               ESP-IDF example index, function-name index
    data/workspace/           the shared ESP-IDF build folder (fast rebuilds)

``data/`` is git-ignored, so your key and projects never end up on GitHub.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

APP_NAME = "AI-Driven Embedded Programmer"
APP_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("ADEP_DATA_DIR", ROOT / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"
WORKSPACE_DIR = DATA_DIR / "workspace"
SETTINGS_FILE = DATA_DIR / "settings.json"
WEB_DIR = ROOT / "web"
FIRMWARE_TEMPLATE = ROOT / "firmware_template"

GROQ_MODELS = [
    {"id": "openai/gpt-oss-120b", "label": "GPT-OSS 120B (best, default)"},
    {"id": "openai/gpt-oss-20b", "label": "GPT-OSS 20B (faster)"},
    {"id": "qwen/qwen3.8-27b", "label": "Qwen 3.8 27B (preview)"},
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "groq",                    # "groq" or "ollama"
    "groq_api_key": "",
    "groq_model": "openai/gpt-oss-120b",
    "ollama_host": "http://localhost:11434",
    "ollama_model": "qwen2.5-coder:7b",
    "temperature": 0.2,
    "auto_build": True,                    # build after every AI change
    "auto_fix": True,                      # send build errors back to the AI
    "max_fix_attempts": 3,
    "examples_in_prompt": 2,               # ESP-IDF examples given to the AI
    "serial_baud": 115200,
    "flash_baud": 460800,
    "sim_seconds": 10,
    # Only needed if ESP-IDF is not found automatically:
    "idf_path": "",
    "idf_tools_path": "",
    "idf_python": "",
}

SECRET_KEYS = ("groq_api_key",)

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR, CACHE_DIR, WORKSPACE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    """Settings from data/settings.json, with defaults for anything missing.

    The GROQ_API_KEY environment variable, if set, is used when the file has no key.
    """
    settings = deepcopy(DEFAULT_SETTINGS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    except (OSError, json.JSONDecodeError):
        pass
    if not settings["groq_api_key"] and os.environ.get("GROQ_API_KEY"):
        settings["groq_api_key"] = os.environ["GROQ_API_KEY"].strip()
    return settings


def save_settings(changes: dict[str, Any]) -> dict[str, Any]:
    """Merge ``changes`` into the stored settings. Unknown keys are ignored.

    An empty or masked API key in ``changes`` keeps the stored key, so the
    settings page never has to send the real key back.
    """
    with _lock:
        ensure_dirs()
        current = load_settings()
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key in SECRET_KEYS and (not value or "•" in str(value)):
                continue
            default = DEFAULT_SETTINGS[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int) and not isinstance(default, bool):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    continue
            elif isinstance(default, float):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            else:
                value = str(value).strip()
            current[key] = value
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
        return current


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Settings safe to send to the browser: the API key is masked."""
    s = deepcopy(settings or load_settings())
    for key in SECRET_KEYS:
        value = s.get(key) or ""
        s[key] = ("•" * 8 + value[-4:]) if value else ""
        s[f"{key}_set"] = bool(value)
    return s
