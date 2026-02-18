"""
Load ASR and Phase2 model options from config/models_config.json.

Falls back to built-in defaults when the file is missing.
Config path: project_root / "config" / "models_config.json"
(project_root = Path(__file__).parent.parent for this utils package).
"""

import json
from pathlib import Path
from typing import Any

# Built-in defaults (match plan section 2)
_DEFAULT_ASR_MODELS = [
    {"id": "openai/gpt-4o-transcribe", "label": "OpenAI gpt-4o-transcribe", "provider": "openai", "model_name": "gpt-4o-transcribe"},
    {"id": "zai/glm-asr-2512", "label": "ZAI GLM-ASR-2512", "provider": "zai", "model_name": "glm-asr-2512", "max_chunk_duration_seconds": 30},
]
_DEFAULT_PHASE2_MODELS = [
    { "id": "zai/glm-4.7-flash", "label": "ZAI GLM-4.7 Flash", "provider": "zai", "model_name": "glm-4.7-flash", "default": True, "max_concurrency": 1 },
    { "id": "gemini/gemini-2.5-flash", "label": "Gemini 2.5 Flash", "provider": "gemini", "model_name": "gemini-2.5-flash" },
    { "id": "deepseek/deepseek-v3.2", "label": "DeepSeek V3.2", "provider": "deepseek", "model_name": "deepseek-chat" },
    { "id": "openai/gpt-5-mini", "label": "OpenAI gpt-5-mini (medium)", "provider": "openai", "model_name": "gpt-5-mini" }
]


def _config_path() -> Path:
    """Project root is parent of utils/."""
    return Path(__file__).resolve().parent.parent / "config" / "models_config.json"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_asr_models() -> list[dict[str, Any]]:
    """Return list of ASR model entries (id, label, provider, model_name)."""
    data = _load_config()
    models = data.get("asr_models")
    if isinstance(models, list) and models:
        return models
    return _DEFAULT_ASR_MODELS.copy()


def get_phase2_models() -> list[dict[str, Any]]:
    """Return list of Phase2 model entries (id, label, provider, model_name, optional default)."""
    data = _load_config()
    models = data.get("phase2_models")
    if isinstance(models, list) and models:
        return models
    return _DEFAULT_PHASE2_MODELS.copy()


def get_default_asr_id() -> str:
    """Return the default ASR model id (first item if no 'default' key)."""
    models = get_asr_models()
    for m in models:
        if m.get("default") is True:
            return m.get("id", "")
    return models[0].get("id", "openai/gpt-4o-transcribe") if models else "openai/gpt-4o-transcribe"


def get_default_phase2_id() -> str:
    """Return the default Phase2 model id (first item with default=True, else first)."""
    models = get_phase2_models()
    for m in models:
        if m.get("default") is True:
            return m.get("id", "")
    return models[0].get("id", "zai/glm-7-flash") if models else "zai/glm-7-flash"


def get_model_by_id(model_id: str, kind: str) -> dict[str, Any] | None:
    """Return the model entry for the given id. kind is 'asr' or 'phase2'."""
    if kind == "asr":
        models = get_asr_models()
    else:
        models = get_phase2_models()
    for m in models:
        if m.get("id") == model_id:
            return m
    return None


def get_phase2_model_max_concurrency(phase2_model_id: str) -> int | None:
    """
    Return the max_concurrency limit for the given Phase2 model, if set.
    Used e.g. for ZAI GLM-4.7-Flash (concurrency 1) to avoid rate-limit / stuck behavior.
    """
    entry = get_model_by_id(phase2_model_id, "phase2")
    if not entry:
        return None
    val = entry.get("max_concurrency")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None
