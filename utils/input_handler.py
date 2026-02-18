"""
Input handling utilities for BodhiFlow.

This module provides functions to:
- Determine the type of user input (YouTube URL, local file, folder, document types, etc.)
- List video/audio files or document files in a directory
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from utils.teams_meeting import is_teams_meeting_manifest_url

# Document extensions for text-entry; all must be supported by MarkItDown for extraction
_TEXT_FILE_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".xml"}
_PDF_FILE_EXTENSIONS = {".pdf"}
_WORD_FILE_EXTENSIONS = {".docx", ".doc"}
_HTML_FILE_EXTENSIONS = {".html", ".htm"}
_PPTX_FILE_EXTENSIONS = {".pptx"}
_EXCEL_FILE_EXTENSIONS = {".xlsx", ".xls"}
_EPUB_FILE_EXTENSIONS = {".epub"}
_MSG_FILE_EXTENSIONS = {".msg"}  # Outlook email (MarkItDown outlook extra)
_DOCUMENT_EXTENSIONS = (
    _TEXT_FILE_EXTENSIONS
    | _PDF_FILE_EXTENSIONS
    | _WORD_FILE_EXTENSIONS
    | _HTML_FILE_EXTENSIONS
    | _PPTX_FILE_EXTENSIONS
    | _EXCEL_FILE_EXTENSIONS
    | _EPUB_FILE_EXTENSIONS
    | _MSG_FILE_EXTENSIONS
)


def _url_source_config_path() -> Path:
    """Path to config/url_source_config.json (project root = parent of utils/)."""
    return Path(__file__).resolve().parent.parent / "config" / "url_source_config.json"


def _get_http_url_source_type(url: str) -> str:
    """
    Match an http(s) URL against url_sources in config. First match wins.
    Returns config id (e.g. 'webpage_text', 'bilibili_video') or 'unknown_url'.
    domain_patterns: '*' = match any; else host substring match.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
    except Exception:
        return "unknown_url"

    path = _url_source_config_path()
    if not path.exists():
        return "unknown_url"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "unknown_url"

    sources = data.get("url_sources")
    if not isinstance(sources, list):
        return "unknown_url"

    for entry in sources:
        patterns = entry.get("domain_patterns")
        if not isinstance(patterns, list):
            continue
        for p in patterns:
            if p == "*":
                return entry.get("id", "unknown_url")
            if p and p.lower() in host:
                return entry.get("id", "unknown_url")
    return "unknown_url"


def get_input_type(input_path: str, input_mode_hint: Optional[str] = None) -> str:
    """
    Determines the type of the input path.

    Args:
        input_path: User provided path or URL
        input_mode_hint: When path is a directory, "media_folder" or "document_folder"
                         to disambiguate (e.g. from GUI two-folder buttons). None = treat as media folder.

    Returns:
        One of: "youtube_video_url", "youtube_playlist_url", "teams_meeting_url",
                "podcast_rss_url", "text_file", "pdf_file", "word_file", "webpage_url",
                "document_folder", "file", "folder", "unknown_url"
    """
    s = (input_path or "").strip()
    if not s:
        return "file"

    # Check if it's a YouTube URL
    if re.match(r"https?://(www\.)?(youtube\.com|youtu\.be)", s):
        if "playlist?list=" in s:
            return "youtube_playlist_url"
        return "youtube_video_url"

    if is_teams_meeting_manifest_url(s):
        return "teams_meeting_url"

    if _is_podcast_rss_url(s):
        return "podcast_rss_url"

    # Local path
    if os.path.exists(s):
        if os.path.isfile(s):
            ext = Path(s).suffix.lower()
            if ext in _TEXT_FILE_EXTENSIONS:
                return "text_file"
            if ext in _PDF_FILE_EXTENSIONS:
                return "pdf_file"
            if ext in _WORD_FILE_EXTENSIONS:
                return "word_file"
            if ext in _HTML_FILE_EXTENSIONS:
                return "text_file"  # treat HTML as text for extraction
            if ext in (_PPTX_FILE_EXTENSIONS | _EXCEL_FILE_EXTENSIONS | _EPUB_FILE_EXTENSIONS | _MSG_FILE_EXTENSIONS):
                return "text_file"  # MarkItDown-supported; same pipeline as other documents
            return "file"
        if os.path.isdir(s):
            if input_mode_hint == "document_folder":
                return "document_folder"
            return "folder"

    # http(s) URL not matched above: use whitelist config
    if re.match(r"https?://", s):
        source_id = _get_http_url_source_type(s)
        if source_id == "webpage_text":
            return "webpage_url"
        if source_id == "unknown_url":
            return "unknown_url"
        return source_id  # e.g. bilibili_video for future use

    return "file"


def list_document_files_in_folder(
    folder_path: str, recursive: bool = True
) -> List[str]:
    """
    Lists document files (.txt, .md, .markdown, .pdf, .docx, .doc, .html, .htm, .pptx, .xlsx, .xls, .epub, .msg, .csv, .json, .xml) in the folder.

    Args:
        folder_path: Path to the folder
        recursive: If True (default), include subdirectories; otherwise current dir only.

    Returns:
        List of absolute paths to document files
    """
    out = []
    if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
        return out
    if recursive:
        for root, _dirs, names in os.walk(folder_path):
            for name in names:
                path = os.path.join(root, name)
                if not os.path.isfile(path):
                    continue
                ext = Path(path).suffix.lower()
                if ext in _DOCUMENT_EXTENSIONS:
                    out.append(os.path.abspath(path))
    else:
        for name in os.listdir(folder_path):
            path = os.path.join(folder_path, name)
            if not os.path.isfile(path):
                continue
            ext = Path(path).suffix.lower()
            if ext in _DOCUMENT_EXTENSIONS:
                out.append(os.path.abspath(path))
    out.sort()
    return out


def list_video_files_in_folder(folder_path: str) -> List[str]:
    """
    Lists all supported media files (video or audio) in the specified folder.

    Args:
        folder_path: Path to the folder to search

    Returns:
        List of absolute paths to media files in the folder
    """
    # Common media file extensions (video + audio)
    video_extensions = {
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".flv",
        ".wmv",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".3gp",
        ".webm",
        ".ogv",
    }
    audio_extensions = {
        ".mp3",
        ".wav",
        ".flac",
        ".aac",
        ".ogg",
        ".m4a",
        ".opus",
        ".wma",
        ".aiff",
        ".alac",
    }
    media_extensions = {ext.lower() for ext in video_extensions.union(audio_extensions)}

    media_files = []

    if not os.path.exists(folder_path):
        print(f"Warning: Folder {folder_path} does not exist")
        return media_files

    if not os.path.isdir(folder_path):
        print(f"Warning: {folder_path} is not a directory")
        return media_files

    # Walk through the directory (non-recursive for now)
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)

        # Check if it's a file and has a video extension
        if os.path.isfile(file_path):
            _, ext = os.path.splitext(filename)
            if ext.lower() in media_extensions:
                # Return absolute paths
                media_files.append(os.path.abspath(file_path))

    # Sort for consistent ordering
    media_files.sort()

    return media_files


def _is_podcast_rss_url(url: str) -> bool:
    """
    Check if URL appears to be a podcast RSS feed.
    
    Args:
        url: URL to check
        
    Returns:
        True if URL appears to be an RSS feed, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # Must be a URL
    if not re.match(r"https?://", url):
        return False
    
    url_lower = url.lower()
    
    # Common RSS feed indicators
    rss_indicators = [
        '/rss',
        '/feed',
        '.rss',
        '.xml',
        'feeds.',
        '/podcast',
        'rss.xml',
        'feed.xml',
        'podcast.xml',
        'feeds.feedburner.com',
        'feeds.simplecast.com',
        'feeds.megaphone.fm',
        'feeds.npr.org',
        'feeds.99percentinvisible.org',
        'rss.cnn.com',
        'feeds.thisamericanlife.org'
    ]
    
    # Check for RSS indicators in URL
    for indicator in rss_indicators:
        if indicator in url_lower:
            return True
    
    # Additional check: if URL ends with common RSS file extensions
    parsed_url = url_lower.split('?')[0]  # Remove query parameters
    if parsed_url.endswith(('.rss', '.xml', '/feed', '/rss')):
        return True
    
    return False


def clean_filename(filename: str) -> str:
    """
    Cleans a string to make it safe for use as a filename across different operating systems.

    Args:
        filename: The original filename or title

    Returns:
        A cleaned filename safe for filesystem use
    """
    if not filename:
        return "unnamed"
    
    # Remove characters that are invalid in filenames (Windows is most restrictive)
    # Invalid characters: < > : " | ? * \ /
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", filename)
    
    # Remove control characters and other problematic characters
    cleaned = re.sub(r'[^\w\s\-_\.]', '', cleaned)
    
    # Normalize whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Remove leading/trailing dots and spaces (problematic on Windows)
    cleaned = cleaned.strip('. ')
    
    # Handle edge cases
    if not cleaned:
        return "unnamed"
    
    # Avoid reserved names on Windows
    reserved_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    
    if cleaned.upper() in reserved_names:
        cleaned = f"{cleaned}_file"
    
    # Limit length to avoid filesystem issues (leave room for suffixes like _source_audio.ext)
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip()
    
    return cleaned


def is_audio_url(url: str) -> bool:
    """
    Check if URL appears to be an audio file based on file extension.
    
    Args:
        url: URL to check
        
    Returns:
        True if URL appears to be an audio file, False otherwise
    """
    if not url:
        return False
    
    audio_extensions = ['.mp3', '.m4a', '.wav', '.aac', '.ogg', '.flac', '.mp4']
    url_lower = url.lower()
    
    return any(ext in url_lower for ext in audio_extensions)


# Test functions if running this module directly
if __name__ == "__main__":
    # Test get_input_type
    test_inputs = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://feeds.simplecast.com/BqbsxVfO",
        "https://feeds.npr.org/510289/podcast.xml",
        "https://rss.cnn.com/rss/cnn_topstories.rss",
        "/path/to/video.mp4",
        "/path/to/folder",
        "C:\\Videos\\my_video.avi",
    ]

    print("Testing get_input_type:")
    for test_input in test_inputs:
        result = get_input_type(test_input)
        print(f"  {test_input} -> {result}")

    # Test list_video_files_in_folder
    print("\nTesting list_video_files_in_folder:")
    test_folder = "."  # Current directory
    videos = list_video_files_in_folder(test_folder)
    print(f"  Found {len(videos)} video files in {test_folder}")
    for video in videos[:5]:  # Show first 5
        print(f"    - {os.path.basename(video)}")

    # Test clean_filename
    print("\nTesting clean_filename:")
    test_filenames = [
        "My Great Video: The Adventure!",
        "File<with>invalid|chars?",
        "CON",  # Reserved name
        "Video with / slash \\ backslash",
        "   Leading and trailing spaces   ",
        "Multiple    spaces",
        "Very long filename that exceeds typical filesystem limits and needs to be truncated to ensure compatibility across different operating systems and file systems",
        "",  # Empty string
        "Normal_filename-123.txt"
    ]
    
    for test_name in test_filenames:
        cleaned = clean_filename(test_name)
        print(f"  '{test_name}' -> '{cleaned}'")

    # Test is_audio_url
    print("\nTesting is_audio_url:")
    test_urls = [
        "https://example.com/audio.mp3",
        "https://feeds.example.com/episode1.m4a",
        "https://cdn.example.com/podcast/audio.wav",
        "https://example.com/video.mp4",  # Video file but also audio
        "https://example.com/document.pdf",  # Not audio
        "https://example.com/page.html",  # Not audio
        "",  # Empty string
        "not-a-url.mp3"  # No http/https
    ]
    
    for test_url in test_urls:
        result = is_audio_url(test_url)
        print(f"  '{test_url}' -> {result}")
