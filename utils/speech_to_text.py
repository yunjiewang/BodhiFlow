"""
Speech-to-text utilities for BodhiFlow.

Supports OpenAI (gpt-4o-transcribe / whisper-1) and ZAI (glm-asr-2512).
Use transcribe_audio_chunks(..., asr_config=...) for provider dispatch.

ZAI GLM-ASR only accepts .wav or .mp3. When using ZAI, upstream (extract_audio_from_video)
outputs .mp3 so no conversion is needed; otherwise we convert to .mp3 here to save space.
"""

import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional

from openai import OpenAI

from .logger_config import get_logger

logger = get_logger(__name__)

try:
    from zai import ZaiClient
    _ZAI_AVAILABLE = True
except ImportError:
    ZaiClient = None  # type: ignore
    _ZAI_AVAILABLE = False


def _convert_to_mp3_for_zai(source_path: str) -> Optional[str]:
    """
    Convert audio to .mp3 in a temp file for ZAI API (which only accepts .wav or .mp3).
    Returns path to temp .mp3 file, or None on failure. Caller must delete the temp file.
    If source is already .wav or .mp3, returns None (no conversion needed).
    Uses MP3 to save space vs WAV.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext in (".wav", ".mp3"):
        return None
    fd, mp3_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", source_path,
                "-acodec", "libmp3lame", "-ab", "128k", "-ac", "1", "-ar", "22050",
                mp3_path,
            ],
            capture_output=True,
            check=True,
            creationflags=creationflags,
        )
        return mp3_path
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"FFmpeg convert to mp3 for ZAI failed: {e}")
        if os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
            except OSError:
                pass
        return None


def transcribe_audio_chunk_openai(
    audio_chunk_path: str,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-transcribe",
    max_retries: int = 3,
) -> Optional[str]:
    """
    Transcribes an audio chunk using OpenAI's transcription API (supports "gpt-4o-transcribe" or "whisper-1").

    Args:
        audio_chunk_path: Path to the audio file to transcribe
        api_key: Optional OpenAI API key (uses OPENAI_API_KEY env var if not provided)
        model: The model to use for transcription (default: gpt-4o-transcribe)
        max_retries: Maximum number of retry attempts

    Returns:
        The transcribed text if successful, None otherwise
    """
    if not os.path.exists(audio_chunk_path):
        logger.error(f"Audio file not found: {audio_chunk_path}")
        return None

    # Check file size (OpenAI current limit is 25MB for sync transcription)
    file_size_mb = os.path.getsize(audio_chunk_path) / (1024 * 1024)
    if file_size_mb > 25:
        logger.error(f"Audio file too large ({file_size_mb:.1f} MB). Maximum is 25 MB.")
        return None

    # Initialize OpenAI client
    if api_key:
        client = OpenAI(api_key=api_key)
    else:
        # Try to get from environment
        env_key = os.environ.get("OPENAI_API_KEY")
        if env_key:
            client = OpenAI(api_key=env_key)
        else:
            logger.error(
                "No OpenAI API key provided and OPENAI_API_KEY not found in environment"
            )
            return None

    # Attempt transcription with retries
    for attempt in range(max_retries):
        try:
            with open(audio_chunk_path, "rb") as audio_file:
                # Build params for API call. According to the latest OpenAI Audio API
                # documentation (May-2025), both "whisper-1" and "gpt-4o-transcribe"
                # are valid model names for the `/audio/transcriptions` endpoint.
                # If a future error arises due to model name, we gracefully fall back
                # to "whisper-1".

                params = {
                    "model": model,
                    "file": audio_file,
                    "response_format": "text",
                }

                # Call the API
                transcript = client.audio.transcriptions.create(**params)

                # The response is directly the text when response_format is "text"
                if transcript:
                    logger.debug(
                        f"Successfully transcribed: {os.path.basename(audio_chunk_path)}"
                    )
                    return transcript
                else:
                    logger.warning(f"Empty transcript returned for: {audio_chunk_path}")
                    return None

        except Exception as e:
            error_message = str(e)
            logger.warning(
                f"Transcription attempt {attempt + 1} failed: {error_message}"
            )

            # Fallback: if model unsupported, retry with whisper-1 once
            if (
                "model" in error_message.lower()
                or "unsupported" in error_message.lower()
            ) and model != "whisper-1":
                logger.info(
                    "Model not supported for transcription. Falling back to 'whisper-1'."
                )
                model = "whisper-1"
                continue

            # Check if it's a rate limit error
            if "rate" in error_message.lower() and attempt < max_retries - 1:
                import time

                wait_time = (attempt + 1) * 5
                logger.info(f"Rate limit hit, waiting {wait_time} seconds...")
                time.sleep(wait_time)
                continue

            # If it's the last attempt or not a retriable error, return None
            if attempt == max_retries - 1:
                logger.error(f"Failed to transcribe after {max_retries} attempts")
                return None

    return None


def transcribe_audio_chunk_zai(
    audio_chunk_path: str,
    api_key: Optional[str] = None,
    model_name: str = "glm-asr-2512",
    max_retries: int = 3,
) -> Optional[str]:
    """
    Transcribes an audio chunk using ZAI's transcription API (e.g. glm-asr-2512).

    Args:
        audio_chunk_path: Path to the audio file (e.g. .wav, .mp3; ≤25MB, ≤30s per ZAI docs).
        api_key: Optional ZAI API key (uses ZAI_API_KEY env if not provided).
        model_name: Model name (default glm-asr-2512).
        max_retries: Max retry attempts.

    Returns:
        Transcribed text if successful, None otherwise.
    """
    if not _ZAI_AVAILABLE or ZaiClient is None:
        logger.error("ZAI SDK not installed. Install with: pip install zai-sdk")
        return None
    if not os.path.exists(audio_chunk_path):
        logger.error(f"Audio file not found: {audio_chunk_path}")
        return None
    file_size_mb = os.path.getsize(audio_chunk_path) / (1024 * 1024)
    if file_size_mb > 25:
        logger.error(f"Audio file too large ({file_size_mb:.1f} MB). ZAI max 25 MB.")
        return None
    key = api_key or os.environ.get("ZAI_API_KEY")
    if not key:
        logger.error("No ZAI API key provided and ZAI_API_KEY not set")
        return None
    # ZAI only accepts .wav or .mp3; convert .m4a (and other) chunks to .mp3 if needed
    path_to_use = _convert_to_mp3_for_zai(audio_chunk_path) or audio_chunk_path
    client = ZaiClient(api_key=key)
    try:
        for attempt in range(max_retries):
            try:
                with open(path_to_use, "rb") as audio_file:
                    response = client.audio.transcriptions.create(
                        model=model_name,
                        file=audio_file,
                    )
                text = getattr(response, "text", None) or (response if isinstance(response, str) else None)
                if text:
                    logger.debug(f"Successfully transcribed (ZAI): {os.path.basename(audio_chunk_path)}")
                    return text
                logger.warning(f"Empty transcript (ZAI) for: {audio_chunk_path}")
                return None
            except Exception as e:
                logger.warning(f"ZAI transcription attempt {attempt + 1} failed: {e}")
                if "rate" in str(e).lower() and attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 5)
                    continue
                if attempt == max_retries - 1:
                    logger.error(f"Failed to transcribe with ZAI after {max_retries} attempts")
                    return None
        return None
    finally:
        if path_to_use != audio_chunk_path and os.path.exists(path_to_use):
            try:
                os.remove(path_to_use)
            except OSError as e:
                logger.debug(f"Could not remove temp mp3 {path_to_use}: {e}")


def transcribe_audio_chunks(
    chunk_paths: list[str],
    asr_config: Optional[dict[str, Any]] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Transcribes multiple audio chunks and combines them.

    When asr_config is provided, uses it to dispatch by provider:
      asr_config = { "provider": "openai"|"zai", "model_name": "...", "api_key": "..." }
    When asr_config is None, uses OpenAI with optional api_key (backward compatible).

    Args:
        chunk_paths: List of paths to audio chunks.
        asr_config: Optional dict with provider, model_name, api_key.
        api_key: Optional API key (used when asr_config is None for OpenAI).

    Returns:
        Combined transcript text.
    """
    transcripts = []
    provider = None
    model_name = None
    key = None
    if asr_config:
        provider = (asr_config.get("provider") or "").lower()
        model_name = asr_config.get("model_name") or ""
        key = asr_config.get("api_key")

    for i, chunk_path in enumerate(chunk_paths):
        logger.info(f"Transcribing chunk {i + 1}/{len(chunk_paths)}...")
        if asr_config and provider == "zai":
            transcript = transcribe_audio_chunk_zai(chunk_path, api_key=key, model_name=model_name or "glm-asr-2512")
        else:
            # OpenAI (default or from asr_config)
            transcript = transcribe_audio_chunk_openai(
                chunk_path,
                api_key=key if asr_config else api_key,
                model=model_name or "gpt-4o-transcribe",
            )
        if transcript:
            transcripts.append(transcript)
        else:
            logger.warning(f"Warning: Failed to transcribe chunk {i + 1}")

    combined_transcript = " ".join(transcripts)
    combined_transcript = " ".join(combined_transcript.split())
    return combined_transcript


def estimate_transcription_cost(audio_duration_seconds: float) -> float:
    """
    Estimates the cost of transcribing audio using OpenAI Whisper.

    Args:
        audio_duration_seconds: Duration of audio in seconds

    Returns:
        Estimated cost in USD
    """
    # OpenAI Whisper pricing as of 2024: $0.006 per minute
    cost_per_minute = 0.006
    duration_minutes = audio_duration_seconds / 60.0
    estimated_cost = duration_minutes * cost_per_minute

    return estimated_cost


# Test function if running this module directly
if __name__ == "__main__":
    test_audio_path = "test_audio_chunk.mp3"

    logger.info("Speech-to-text utilities test")
    logger.info("-" * 50)

    # Test cost estimation
    test_duration = 300  # 5 minutes
    cost = estimate_transcription_cost(test_duration)
    logger.info(f"Cost estimation for {test_duration / 60:.1f} minutes: ${cost:.3f}")

    # Test transcription (requires actual audio file and API key)
    if os.path.exists(test_audio_path):
        logger.info(f"\nTesting transcribe_audio_chunk_openai:")
        logger.info(f"  Input: {test_audio_path}")

        # Check file size
        file_size_mb = os.path.getsize(test_audio_path) / (1024 * 1024)
        logger.info(f"  File size: {file_size_mb:.2f} MB")

        # Attempt transcription
        transcript = transcribe_audio_chunk_openai(test_audio_path)

        if transcript:
            logger.info(f"  Transcription successful!")
            logger.info(f"  Length: {len(transcript)} characters")
            logger.info(f"  Preview: {transcript[:200]}...")
        else:
            logger.error(f"  Transcription failed")
            logger.info(
                f"  Make sure you have set the OPENAI_API_KEY environment variable"
            )
    else:
        logger.warning(f"\nTest audio file not found: {test_audio_path}")
        logger.info("Please provide a test audio file to run the transcription test")
        logger.info("Note: This test requires a valid OpenAI API key")
