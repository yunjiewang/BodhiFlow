"""
Generic LLM calling utility for BodhiFlow.

Supports OpenAI, Gemini, DeepSeek, and ZAI via provider_config.
Legacy Gemini-only: call_llm(prompt, model_name=..., api_key=...).
"""

import os
import time
from typing import Any, Optional

import google.generativeai as genai
from openai import OpenAI

from .logger_config import get_logger

logger = get_logger(__name__)

# Optional ZAI SDK (add to requirements)
try:
    from zai import ZaiClient
    _ZAI_AVAILABLE = True
except ImportError:
    ZaiClient = None  # type: ignore
    _ZAI_AVAILABLE = False


def _call_gemini(prompt: str, model_name: str, api_key: str, max_retries: int) -> str:
    if api_key:
        genai.configure(api_key=api_key)
    else:
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            genai.configure(api_key=env_key)
        else:
            raise ValueError("No API key provided and GEMINI_API_KEY not set")
    model = genai.GenerativeModel(model_name)
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            if response.text:
                return response.text
            if hasattr(response, "prompt_feedback"):
                raise Exception(f"Content was blocked: {response.prompt_feedback}")
            raise Exception("No text in response")
        except Exception as e:
            _handle_retry("gemini", model_name, e, attempt, max_retries)
    raise Exception(f"LLM call failed after {max_retries} attempts")


def _call_openai(prompt: str, model_name: str, api_key: str, max_retries: int) -> str:
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("No OpenAI API key provided and OPENAI_API_KEY not set")
    client = OpenAI(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(max_retries):
        try:
            r = client.chat.completions.create(model=model_name, messages=messages)
            if r.choices and r.choices[0].message.content:
                return r.choices[0].message.content
            raise Exception("Empty response from OpenAI")
        except Exception as e:
            _handle_retry("openai", model_name, e, attempt, max_retries)
    raise Exception(f"LLM call failed after {max_retries} attempts")


def _call_deepseek(prompt: str, model_name: str, api_key: str, max_retries: int) -> str:
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("No DeepSeek API key provided and DEEPSEEK_API_KEY not set")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(max_retries):
        try:
            r = client.chat.completions.create(model=model_name, messages=messages)
            if r.choices and r.choices[0].message.content:
                return r.choices[0].message.content
            raise Exception("Empty response from DeepSeek")
        except Exception as e:
            _handle_retry("deepseek", model_name, e, attempt, max_retries)
    raise Exception(f"LLM call failed after {max_retries} attempts")


def _call_zai(prompt: str, model_name: str, api_key: str, max_retries: int) -> str:
    if not _ZAI_AVAILABLE or ZaiClient is None:
        raise ImportError("ZAI SDK not installed. Install with: pip install zai-sdk")
    if not api_key:
        api_key = os.environ.get("ZAI_API_KEY")
    if not api_key:
        raise ValueError("No ZAI API key provided and ZAI_API_KEY not set")
    client = ZaiClient(api_key=api_key)
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(max_retries):
        try:
            r = client.chat.completions.create(model=model_name, messages=messages)
            if r.choices and r.choices[0].message.content:
                return r.choices[0].message.content
            raise Exception("Empty response from ZAI")
        except Exception as e:
            _handle_retry("zai", model_name, e, attempt, max_retries)
    raise Exception(f"LLM call failed after {max_retries} attempts")


def _handle_retry(provider: str, model_name: str, e: Exception, attempt: int, max_retries: int) -> None:
    msg = str(e).lower()
    if attempt >= max_retries - 1:
        logger.error(f"LLM call failed after {max_retries} attempts [{provider}/{model_name}]: {e}")
        raise
    if "quota" in msg or "rate" in msg:
        wait = (attempt + 1) * 5
        logger.info(f"Rate limit hit [{provider}], waiting {wait}s...")
        time.sleep(wait)
        return
    if any(t in msg for t in ["timeout", "temporary", "unavailable"]):
        wait = (attempt + 1) * 2
        logger.info(f"Transient error [{provider}], retrying in {wait}s...")
        time.sleep(wait)
        return
    logger.error(f"LLM call failed [{provider}/{model_name}]: {e}")
    raise


def call_llm(
    prompt: str,
    provider_config: Optional[dict[str, Any]] = None,
    *,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = 3,
) -> str:
    """
    Call an LLM with the given prompt.

    New (multi-provider): pass provider_config with keys:
      provider ("gemini"|"openai"|"deepseek"|"zai"), model_name, api_key.

    Legacy (Gemini only): pass model_name and optionally api_key;
    api_key defaults to GEMINI_API_KEY env.

    Args:
        prompt: Input prompt.
        provider_config: Optional dict with provider, model_name, api_key.
        model_name: Legacy Gemini model name (used when provider_config is None).
        api_key: Legacy Gemini API key (used when provider_config is None).
        max_retries: Max retries for transient errors.

    Returns:
        Response text from the LLM.
    """
    if provider_config:
        provider = (provider_config.get("provider") or "").lower()
        model = provider_config.get("model_name") or ""
        key = provider_config.get("api_key") or ""
        if not provider or not model:
            raise ValueError("provider_config must include provider and model_name")
        if provider == "gemini":
            return _call_gemini(prompt, model, key, max_retries)
        if provider == "openai":
            return _call_openai(prompt, model, key, max_retries)
        if provider == "deepseek":
            return _call_deepseek(prompt, model, key, max_retries)
        if provider == "zai":
            return _call_zai(prompt, model, key, max_retries)
        raise ValueError(f"Unknown provider in provider_config: {provider}")

    # Legacy Gemini path
    model = model_name or "gemini-2.5-flash"
    return _call_gemini(prompt, model, api_key or "", max_retries)


if __name__ == "__main__":
    test_prompt = "Please write a one-line greeting in Python."
    print("Testing call_llm (Gemini legacy):")
    try:
        out = call_llm(test_prompt)
        print("Response:", out)
    except Exception as e:
        print("Failed:", e)
        print("Set GEMINI_API_KEY or pass provider_config for other providers.")
