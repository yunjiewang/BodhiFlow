"""
YouTube transcript fetching utility for BodhiFlow.

This module provides functions to download transcripts from YouTube videos
using the youtube_transcript_api library.
"""

import os
from typing import Optional, List

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnplayable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import WebshareProxyConfig
from requests import Session

from utils.logger_config import get_logger

# Initialize logger for this module
logger = get_logger(__name__)


# Load environment variables (for proxy credentials)
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # If python-dotenv isn't available at runtime, skip silently
    pass


def _get_webshare_proxy_config_from_env() -> Optional[WebshareProxyConfig]:
    """
    Build a Webshare proxy config from environment variables if available.

    Supported env var names (first match wins):
      - WEBSHARE_PROXY_USERNAME or PROXY_USERNAME
      - WEBSHARE_PROXY_PASSWORD or PROXY_PASSWORD

    Returns:
        WebshareProxyConfig if creds exist, otherwise None.
    """
    username = os.getenv("WEBSHARE_PROXY_USERNAME") or os.getenv("PROXY_USERNAME")
    password = os.getenv("WEBSHARE_PROXY_PASSWORD") or os.getenv("PROXY_PASSWORD")

    if username and password:
        try:
            return WebshareProxyConfig(
                proxy_username=username,
                proxy_password=password,
                filter_ip_locations=["ca", "us"],
            )
        except Exception as e:
            logger.warning(f"Failed to construct WebshareProxyConfig: {e}")

    return None


def _create_http_client(cookies_path: Optional[str]) -> Optional[Session]:
    """
    Create a custom HTTP client session with cookie support.

    Args:
        cookies_path: Path to Netscape format cookies file

    Returns:
        Configured Session object or None
    """
    if not cookies_path or not os.path.exists(cookies_path):
        return None

    try:
        from http.cookiejar import MozillaCookieJar

        http_client = Session()

        # Load cookies from file
        cookie_jar = MozillaCookieJar(cookies_path)
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        http_client.cookies = cookie_jar

        logger.info(f"Loaded cookies from: {cookies_path}")
        return http_client
    except Exception as e:
        logger.warning(f"Failed to load cookies: {e}")
        return None


def download_youtube_transcript(
    video_url: str,
    cookies_path: Optional[str] = None,
    max_retries: int = 3,
    preferred_languages: Optional[List[str]] = ["en", "zh-CN", "zh"],
) -> Optional[str]:
    """
    Downloads the transcript for a YouTube video with retry mechanism and language detection.

    Uses the latest youtube-transcript-api (as of 2025) with proper API initialization.

    Args:
        video_url: The YouTube video URL
        cookies_path: Optional path to a Netscape format cookies file.
        max_retries: Maximum number of retry attempts (default: 3)
        preferred_languages: List of preferred language codes (e.g., ['en', 'zh-CN', 'zh'])
                           Defaults to English first, then Chinese variants.

    Returns:
        The transcript text if available, None if no transcript found

    Note:
        Returns None for videos that:
        - Have transcripts disabled
        - Have no transcript available
        - Are unplayable
        This allows the caller to fall back to STT if needed.

        Retries are attempted for network-related errors but not for
        definitive "no transcript" errors.

    Language Priority:
        1. If preferred_languages specified, tries each in order
        2. Automatically falls back to any available transcript
        3. Logs which language was successfully retrieved

    API Version:
        Uses youtube-transcript-api latest API (2025):
        - ytt_api.fetch(video_id, languages=[...])
        - ytt_api.list(video_id) for detailed transcript info
    """
    import time

    # Extract video ID from URL first (no need to retry this)
    video_id = extract_video_id(video_url)
    if not video_id:
        logger.error(f"Could not extract video ID from URL: {video_url}")
        return None

    # Set default preferred languages if none provided
    if preferred_languages is None:
        preferred_languages = ["en"]

    # Attempt transcript download with retries
    for attempt in range(max_retries):
        try:
            # Configure API client
            api_init_kwargs = {}

            # Add cookie support via http_client
            http_client = _create_http_client(cookies_path)
            if http_client:
                api_init_kwargs["http_client"] = http_client

            # Add proxy support
            proxy_config = _get_webshare_proxy_config_from_env()
            if proxy_config:
                api_init_kwargs["proxy_config"] = proxy_config
                logger.info("Using Webshare proxy for YouTubeTranscriptApi")

            # Initialize API with proper configuration
            ytt_api = YouTubeTranscriptApi(**api_init_kwargs)

            # METHOD 1: Try simple fetch() with language preferences (recommended by latest API)
            try:
                logger.info(
                    f"Attempting to fetch transcript with preferred languages: {preferred_languages}"
                )
                fetched_transcript = ytt_api.fetch(
                    video_id, languages=preferred_languages, preserve_formatting=False
                )

                # Extract transcript data using attribute access (not dict subscript)
                full_transcript = " ".join(
                    [snippet.text for snippet in fetched_transcript]
                )
                full_transcript = " ".join(full_transcript.split())  # Clean whitespace

                logger.info(
                    f"Successfully downloaded transcript for {video_id} "
                    f"(language: {fetched_transcript.language_code})"
                )
                return full_transcript

            except NoTranscriptFound:
                # If preferred languages don't work, try METHOD 2: list all and pick any
                logger.info(
                    "Preferred languages not found, trying to get any available transcript..."
                )

                transcript_list = ytt_api.list(video_id)

                # Log available transcripts
                available_languages = []
                for transcript in transcript_list:
                    lang_info = f"{transcript.language} ({transcript.language_code})"
                    if transcript.is_generated:
                        lang_info += " [auto-generated]"
                    available_languages.append(lang_info)

                logger.info(f"Available transcripts: {', '.join(available_languages)}")

                # Try to find ANY transcript (prioritize manually created)
                transcript_obj = None
                try:
                    # Try to find manually created transcript first
                    transcript_obj = transcript_list.find_manually_created_transcript(
                        [t.language_code for t in transcript_list]
                    )
                    logger.info(
                        f"Using manually created transcript: {transcript_obj.language}"
                    )
                except NoTranscriptFound:
                    # Fall back to any transcript (including auto-generated)
                    try:
                        transcript_obj = transcript_list.find_transcript(
                            [t.language_code for t in transcript_list]
                        )
                        logger.info(
                            f"Using available transcript: {transcript_obj.language}"
                        )
                    except NoTranscriptFound:
                        logger.warning(
                            f"No usable transcript found for video: {video_url}"
                        )
                        return None

                if transcript_obj:
                    # Fetch the transcript data
                    fetched_transcript = transcript_obj.fetch()
                    # Use attribute access (snippet.text) instead of dict subscript
                    full_transcript = " ".join(
                        [snippet.text for snippet in fetched_transcript]
                    )
                    full_transcript = " ".join(full_transcript.split())

                    logger.info(
                        f"Successfully downloaded transcript for {video_id} "
                        f"(language: {transcript_obj.language_code})"
                    )
                    return full_transcript

        except TranscriptsDisabled:
            # This is a definitive error - don't retry
            logger.warning(f"Transcripts are disabled for video: {video_url}")
            return None

        except NoTranscriptFound:
            # This is a definitive error - don't retry
            logger.warning(f"No transcript found for video: {video_url}")
            return None

        except VideoUnplayable:
            # This is a definitive error - don't retry
            logger.error(f"Video is unplayable: {video_url}")
            return None

        except Exception as e:
            error_message = str(e)

            # Check if this is a potentially retriable error
            retriable_errors = [
                "network",
                "timeout",
                "connection",
                "http",
                "request",
                "rate",
                "limit",
                "temporary",
                "unavailable",
                "502",
                "503",
                "504",
                "no element found",
                "parse",
                "xml",
                "json",
                "encoding",
            ]
            is_retriable = any(
                keyword in error_message.lower() for keyword in retriable_errors
            )

            # If it's the last attempt or not a retriable error, log as error and return
            if attempt == max_retries - 1 or not is_retriable:
                if is_retriable:
                    logger.error(
                        f"Failed to download transcript after {max_retries} attempts "
                        f"for video: {video_url}. Last error: {error_message}"
                    )
                else:
                    logger.error(
                        f"Non-retriable error downloading transcript for video: {video_url}. "
                        f"Error: {error_message}"
                    )
                return None

            # Log the retry attempt
            logger.warning(
                f"Transcript download attempt {attempt + 1}/{max_retries} failed "
                f"for {video_url}: {error_message}"
            )

            # Wait before retrying (exponential backoff: 2s, 6s, 18s)
            wait_time = 2 * (3**attempt)
            logger.info(f"Retrying transcript download in {wait_time} seconds...")
            time.sleep(wait_time)

    # This shouldn't be reached, but just in case
    return None


def extract_video_id(video_url: str) -> Optional[str]:
    """
    Extracts the video ID from a YouTube URL.

    Supports formats:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/watch?v=VIDEO_ID&t=123s

    Args:
        video_url: The YouTube video URL

    Returns:
        The video ID if found, None otherwise
    """
    import re

    # Pattern for standard youtube.com URLs
    standard_pattern = (
        r"(?:youtube\.com\/watch\?v=|youtube\.com\/watch\?.*&v=)([a-zA-Z0-9_-]{11})"
    )
    match = re.search(standard_pattern, video_url)
    if match:
        return match.group(1)

    # Pattern for youtu.be URLs
    short_pattern = r"youtu\.be\/([a-zA-Z0-9_-]{11})"
    match = re.search(short_pattern, video_url)
    if match:
        return match.group(1)

    return None


# Test function if running this module directly
if __name__ == "__main__":
    # # Test extract_video_id
    # test_urls = [
    #     "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    #     "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    #     "https://youtu.be/dQw4w9WgXcQ",
    #     "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
    #     "invalid_url",
    # ]

    # logger.info("Testing extract_video_id:")
    # for url in test_urls:
    #     video_id = extract_video_id(url)
    #     logger.info(f"  {url} -> {video_id}")

    logger.info("\nTesting download_youtube_transcript:")
    # This is a popular video that should have transcripts
    test_video_url = "https://www.youtube.com/watch?v=wpVQ5nHdVr8"
    test_cookies_path = None  # Set to a valid path if testing with cookies

    logger.info(f"  Attempting to download transcript for: {test_video_url}")
    if test_cookies_path:
        logger.info(f"  Using cookies from: {test_cookies_path}")

    # Test with English and Chinese preferences
    transcript = download_youtube_transcript(
        test_video_url,
        cookies_path=test_cookies_path,
        preferred_languages=["en", "zh-CN", "zh"],
    )
    if transcript:
        logger.info("  Transcript downloaded successfully!")
        logger.info(f"  Length: {len(transcript)} characters")
        logger.info(f"  Preview: {transcript[:200]}...")
    else:
        logger.warning("  No transcript available")
