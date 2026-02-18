"""
Metadata enhancement via OpenAI Responses API (gpt-5-nano).

# Migrated to Responses API – see docs 2025-03-11
"""
import os
import json
from typing import Dict

from openai import OpenAI
from prompts import utility_prompts


def enhance_metadata_with_llm(
    text: str,
    language: str = "English",
    model: str = "gpt-5-nano",
) -> Dict[str, object]:
    """
    Returns: {"description": str, "tags": [str, ...]}
    Uses OpenAI Responses API to enrich missing non-factual fields only.

    Args:
        text: Source text to infer metadata from
        language: Output language
        model: OpenAI model for Responses API (default "gpt-5-nano")
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is required for metadata enhancement")
    
    client = OpenAI(api_key=api_key)
    tmpl = utility_prompts["Metadata Enhancement"]
    # IMPORTANT: Avoid Python str.format interpreting JSON braces in the template
    # Only substitute our two placeholders; leave any other braces intact
    prompt = tmpl.replace("{language}", language).replace("{text}", text)

    resp = client.responses.create(
        model=model,
        input=[
            {"role": "user", "content": prompt}
        ],
        max_output_tokens=200,
        text={
            "format": {
                "type": "json_schema",
                "name": "metadata_enhancement_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "maxLength": 140},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                            "minItems": 3,
                            "maxItems": 5
                        }
                    },
                    "required": ["description", "tags"],
                    "additionalProperties": False
                },
                "strict": True
            }
        },
    )

    # Structured outputs: parse output_text directly
    raw = (getattr(resp, "output_text", "") or "").strip()

    try:
        data = json.loads(raw)
    except Exception:
        data = None

    if not isinstance(data, dict):
        return {"description": "", "tags": []}

    desc = str(data.get("description", "")).strip()[:140]
    tags = [t.strip() for t in (data.get("tags") or []) if isinstance(t, str)]
    return {"description": desc, "tags": tags[:5]}


