"""
Teams meeting download utilities for BodhiFlow.

This module provides helper functions to:
- Detect Microsoft Teams `videomanifest` URLs
- Normalize manifest URLs before download
- Derive a stable meeting title from the URL metadata
- Download the recording with ffmpeg for further processing
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from utils.logger_config import get_logger

# Initialize module logger
logger = get_logger(__name__)

_TEAMS_HOST_KEYWORDS = ("mediap.svc.ms", "teams.microsoft.com")


def is_teams_meeting_manifest_url(url: str) -> bool:
    """
    Check whether a URL appears to be a Microsoft Teams videomanifest.

    Args:
        url: URL string to examine

    Returns:
        True if the URL matches a Teams videomanifest pattern, False otherwise
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http"):
            return False

        host = parsed.netloc.lower()
        path = parsed.path.lower()

        host_match = any(keyword in host for keyword in _TEAMS_HOST_KEYWORDS)
        path_match = "videomanifest" in path

        if not (host_match and path_match):
            return False

        query_params = parse_qs(parsed.query)
        provider = query_params.get("provider", [""])[0].lower()
        return provider in {"spo", "onedrive"}
    except Exception:
        return False


def clean_manifest_url(url: str) -> str:
    """
    Normalize Teams videomanifest URLs by stripping unused metadata and
    forcing `hybridPlayback` to true for better compatibility.

    Args:
        url: Original Teams videomanifest URL

    Returns:
        Cleaned URL safe for use with ffmpeg
    """
    if not url:
        return url

    cleaned = re.sub(
        r"&altManifestMetadata=.*?&pretranscode=0", "&pretranscode=0", url
    )
    cleaned = re.sub(r"&hybridPlayback=false", "&hybridPlayback=true", cleaned)
    return cleaned


def derive_meeting_title(url: str) -> str:
    """
    Generate a deterministic title for a Teams meeting based on URL metadata.

    The function prioritizes `correlationId` for readability and falls back to
    the `docid` parameter before using a generic label.

    Args:
        url: Teams videomanifest URL

    Returns:
        Title string (without filesystem sanitization)
    """
    default_title = "Teams Meeting"

    if not url:
        return default_title

    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        correlation_id = params.get("correlationId") or params.get("correlationid")
        if correlation_id:
            value = correlation_id[0].strip()
            if value:
                return f"Teams Meeting {value[:12]}"

        docid_values = params.get("docid") or params.get("docId")
        if docid_values:
            decoded = unquote(docid_values[0])
            candidate = decoded.split("/")[-1]
            candidate = candidate.replace("?", "_").replace("=", "_")
            candidate = candidate[:60].strip()
            if candidate:
                return candidate
    except Exception:
        pass

    return default_title


def download_teams_meeting_recording(
    manifest_url: str,
    output_dir: str,
    filename_stem: str,
    max_retries: int = 2,
) -> Optional[str]:
    """
    Download a Teams meeting recording using ffmpeg by copying the stream.

    Args:
        manifest_url: Teams videomanifest URL
        output_dir: Directory where the MP4 should be saved
        filename_stem: Desired stem for the output file (without extension)
        max_retries: Number of retry attempts on failure

    Returns:
        Absolute path to the downloaded MP4 file if successful, None otherwise
    """
    if not manifest_url:
        logger.error("No Teams manifest URL provided")
        return None

    cleaned_url = clean_manifest_url(manifest_url)
    output_path = Path(output_dir) / f"{filename_stem}.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        try:
            output_path.unlink()
        except Exception as exc:
            logger.warning(f"Failed to remove existing file {output_path}: {exc}")

    ffmpeg_cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-i",
        cleaned_url,
        "-c",
        "copy",
        str(output_path),
    ]

    for attempt in range(max_retries + 1):
        try:
            kwargs = {
                "check": True,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }

            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            logger.info(
                f"Downloading Teams meeting (attempt {attempt + 1}) "
                f"to {output_path.name}"
            )
            subprocess.run(ffmpeg_cmd, **kwargs)

            if output_path.exists():
                logger.info(f"Teams meeting downloaded successfully: {output_path}")
                return str(output_path.resolve())

            logger.error("FFmpeg completed but output file is missing")
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            logger.warning(
                f"FFmpeg error downloading Teams meeting (attempt {attempt + 1}): "
                f"{stderr or exc}"
            )
        except Exception as exc:
            logger.error(
                f"Unexpected error downloading Teams meeting (attempt {attempt + 1}): {exc}"
            )
            break

    logger.error("Exceeded maximum retries downloading Teams meeting")
    if output_path.exists():
        try:
            output_path.unlink()
        except Exception:
            pass

    return None

