"""
Load UI configuration from config/ui_config.json.

Provides default values for checkboxes, chunk size, language, and other
UI elements. Edit config/ui_config.json to customize without code changes.
"""

import json
from pathlib import Path
from typing import Any

# Default config when file is missing
_DEFAULT_CONFIG = {
    "chunk_size": {"default": 70000, "min": 5000, "max": 500000},
    "options": {
        "batch_csv": False,
        "resume": True,
        "metadata_enhance": True,
        "disable_ai_transcribe": False,
        "save_video": False,
        "phase2_skip_existing": True,
        "document_folder_recursive": True,
    },
    "default_checked_styles": {
        "Balanced and Detailed": True,
        "Summary": True,
        "Educational": False,
        "Narrative Rewriting": False,
        "Q&A Generation": False,
        "Meeting Minutes (BETA)": False,
    },
    "language": "简体中文",
    "start_index": "1",
    "end_index": "0",
}


def _config_path() -> Path:
    """Path to ui_config.json relative to project root."""
    return Path(__file__).resolve().parent.parent / "config" / "ui_config.json"


def get_ui_config() -> dict[str, Any]:
    """
    Load UI config from config/ui_config.json.
    Falls back to built-in defaults if file is missing or invalid.
    """
    path = _config_path()
    if not path.exists():
        return _DEFAULT_CONFIG.copy()
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        # Merge with defaults so missing keys get defaults
        result = _DEFAULT_CONFIG.copy()
        for key, val in loaded.items():
            if isinstance(val, dict) and key in result and isinstance(result[key], dict):
                result[key] = {**result[key], **val}
            else:
                result[key] = val
        return result
    except (json.JSONDecodeError, OSError):
        return _DEFAULT_CONFIG.copy()
