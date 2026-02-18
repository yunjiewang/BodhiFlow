"""
YouTube downloading utilities for BodhiFlow.

This module provides functions to:
- Get video URLs from YouTube playlists
- Download audio from YouTube videos
"""

import os
from pathlib import Path
from typing import List, Optional, Dict, Any

import yt_dlp
from pytubefix import Playlist, YouTube

from utils.logger_config import get_logger
from utils.input_handler import clean_filename

# Initialize logger for this module
logger = get_logger(__name__)


def get_video_urls_from_playlist(
    playlist_url: str, cookie_path: Optional[str] = None
) -> List[str]:
    """
    Extracts all video URLs from a YouTube playlist.

    Args:
        playlist_url: The YouTube playlist URL
        cookie_path: Optional path to cookie file for authenticated access

    Returns:
        List of video URLs in the playlist
    """
    try:
        # Use pytubefix to get playlist info
        playlist = Playlist(playlist_url)

        # Get all video URLs
        video_urls = list(playlist.video_urls)

        logger.info(f"Found {len(video_urls)} videos in playlist: {playlist.title}")
        return video_urls

    except Exception as e:
        logger.error(f"Error extracting playlist videos: {e}")
        # Try fallback with yt-dlp
        return get_playlist_with_ytdlp(playlist_url, cookie_path)


def get_playlist_with_ytdlp(
    playlist_url: str, cookie_path: Optional[str] = None
) -> List[str]:
    """
    Fallback method to get playlist videos using yt-dlp.

    Args:
        playlist_url: The YouTube playlist URL
        cookie_path: Optional path to cookie file

    Returns:
        List of video URLs
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,  # Don't download, just extract info
    }

    if cookie_path and os.path.exists(cookie_path):
        ydl_opts["cookiefile"] = cookie_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)

            if "entries" in info:
                video_urls = []
                for entry in info["entries"]:
                    if entry and "url" in entry:
                        # Construct full URL
                        video_id = entry.get("id", entry.get("url"))
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        video_urls.append(video_url)

                logger.info(f"Found {len(video_urls)} videos using yt-dlp")
                return video_urls
            else:
                logger.warning("No entries found in playlist")
                return []

    except Exception as e:
        logger.error(f"Error with yt-dlp fallback: {e}")
        return []


def fetch_youtube_metadata(video_url: str, cookie_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch rich metadata for a YouTube video without downloading it.

    Returns a dict with at least: title, source_url. Best-effort on channel, upload_date, tags, duration.
    Never raises; on failure returns minimal fields.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    if cookie_path and os.path.exists(cookie_path):
        ydl_opts["cookiefile"] = cookie_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            title = info.get("title") or "Untitled"
            channel = info.get("uploader") or info.get("channel") or ""
            upload_date = info.get("upload_date") or ""  # e.g., 20240131
            tags = info.get("tags") or []
            duration = info.get("duration")  # seconds
            return {
                "title": title,
                "channel": channel,
                "upload_date": upload_date,
                "tags": tags,
                "duration": duration,
                "source_url": video_url,
            }
    except Exception as e:
        logger.warning(f"fetch_youtube_metadata failed: {e}")
        # Fallback minimal metadata; do not fail the main flow
        try:
            title = get_video_title(video_url)
        except Exception:
            title = "Untitled"
        return {"title": title, "source_url": video_url}


def download_youtube_audio(
    video_url: str, output_path: str, cookie_path: Optional[str] = None
) -> Optional[str]:
    """
    Downloads audio from a YouTube video with optimized format selection.
    
    Prioritizes m4a format to avoid unnecessary transcoding, which can save 70-80% of processing time.

    Args:
        video_url: The YouTube video URL
        output_path: Path where the audio file should be saved
        cookie_path: Optional path to cookie file for authenticated access

    Returns:
        Path to the downloaded audio file if successful, None otherwise
    """
    # Ensure output_path is a string
    if not isinstance(output_path, str):
        output_path = str(output_path)

    # Derive temp_dir and filename_stem from output_path
    # The output_path is the final destination for the audio file
    # yt-dlp will download to temp_dir with filename_stem and its own extension logic,
    # then we move it to output_path.
    path_obj = Path(output_path)
    final_output_dir = path_obj.parent
    # Sanitize stem to avoid Windows-illegal chars and inconsistency with yt-dlp
    filename_stem = clean_filename(path_obj.stem)

    # For yt-dlp's initial download, we'll use the same directory as the final output_path's parent.
    # This simplifies things as yt-dlp will place its output (e.g., filename_stem.m4a) there.
    # We then just ensure the final name matches output_path (if it had a different suffix, though unlikely here).
    temp_dir = final_output_dir

    ydl_opts = {
        # OPTIMIZED: Prioritize m4a format to avoid transcoding
        # 1. bestaudio[ext=m4a] - Direct m4a download (no conversion needed, fastest!)
        # 2. bestaudio[acodec=aac] - AAC audio (container conversion only, fast)
        # 3. bestaudio - Any best audio (may need full transcoding, slowest)
        # 4. best - Fallback to best quality with video if audio-only unavailable
        "format": "bestaudio[ext=m4a]/bestaudio[acodec=aac]/bestaudio/best",
        
        # yt-dlp will download to temp_dir, using filename_stem.%(ext)s
        "outtmpl": os.path.join(temp_dir, f"{filename_stem}.%(ext)s"),
        
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",  # Ensure m4a output
                "preferredquality": "128",
                "nopostoverwrites": False,  # Allow overwriting to avoid issues
            }
        ],
        
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "-tv_simply"]  # 优先 default，失败再试 -tv_simply
            }
        },

        # Processing preferences
        "prefer_ffmpeg": True,  # Prefer ffmpeg over avconv
        "keepvideo": False,     # Don't keep video track if present
        
        # Output options
        "quiet": True,
        "no_warnings": True,
        
        # Keep filenames cross-platform friendly
        "restrictfilenames": True,
        "windowsfilenames": True,
        "extract_audio": True,
    }

    # # Try to enable impersonate if curl_cffi is available
    # try:
    #     import importlib.util
    #     if importlib.util.find_spec("curl_cffi") is not None:
    #         ydl_opts["impersonate"] = "Chrome-116"
    #         logger.debug("Browser impersonation enabled (curl_cffi available)")
    #     else:
    #         logger.debug("Browser impersonation disabled (curl_cffi not available)")
    # except Exception:
    #     logger.debug("Browser impersonation disabled (curl_cffi check failed)")
    
    # Add cookie file if provided
    if cookie_path and os.path.exists(cookie_path):
        ydl_opts["cookiefile"] = cookie_path
        logger.info(f"Using cookie file: {cookie_path}")

    # Construct the expected output path after yt-dlp processing
    # yt-dlp will use the preferredcodec as the extension.
    # This will be in temp_dir (which is final_output_dir here)
    expected_downloaded_path = Path(temp_dir) / f"{filename_stem}.m4a"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Ensure the target directory for yt-dlp exists
            os.makedirs(temp_dir, exist_ok=True)

            # Check if the FINAL output_path already exists. If so, no need to download.
            if os.path.exists(output_path):
                logger.info(
                    f"Audio file already exists at final destination: {output_path}"
                )
                return output_path

            # Download the audio with optimized format selection
            logger.info("Downloading audio with optimized format selection...")
            ydl.extract_info(video_url, download=True)

            # Check if yt-dlp produced the expected file at expected_downloaded_path
            downloaded_file_to_move = None
            if expected_downloaded_path.exists():
                downloaded_file_to_move = expected_downloaded_path
            else:
                # Fallback: yt-dlp might sometimes use a different extension if exact conversion fails
                # or if original was already m4a. Check for common audio extensions.
                # Given we specify m4a, this fallback is less critical but harmless.
                # This check should ideally not be needed if preferredcodec works as expected.
                logger.warning(
                    f"Expected file {expected_downloaded_path} not found. Searching for alternatives..."
                )
                for ext_to_check in [
                    "m4a",
                    "mp3",
                    "opus",
                    "ogg",
                    "wav",
                ]:  # m4a is first
                    potential_path = Path(temp_dir) / f"{filename_stem}.{ext_to_check}"
                    if potential_path.exists():
                        logger.info(
                            f"Found downloaded audio with alternate extension: {potential_path}"
                        )
                        downloaded_file_to_move = potential_path
                        break

                # Last resort: glob most recent m4a that starts with stem
                if not downloaded_file_to_move:
                    try:
                        candidates = sorted(
                            Path(temp_dir).glob(f"{filename_stem}*.m4a"),
                            key=lambda p: p.stat().st_mtime,
                            reverse=True,
                        )
                        if candidates:
                            downloaded_file_to_move = candidates[0]
                    except Exception:
                        pass

            if downloaded_file_to_move:
                # If the downloaded file path is different from the final output_path
                # (e.g., if output_path had a different suffix, or if we used a truly separate temp_dir)
                # we would rename/move. Here, since temp_dir = final_output_dir and filename_stem is aligned,
                # downloaded_file_to_move should be equal to output_path if output_path ends with .m4a

                final_path_obj = Path(output_path)
                if downloaded_file_to_move != final_path_obj:
                    # This might happen if output_path was like "title.audio" and it became "title.m4a"
                    # Or if a different extension was found in the fallback.
                    logger.info(f"Moving {downloaded_file_to_move} to {output_path}")
                    os.makedirs(
                        final_path_obj.parent, exist_ok=True
                    )  # Ensure dir again just in case
                    os.rename(downloaded_file_to_move, output_path)

                logger.info(f"Successfully processed audio to: {output_path}")
                return output_path
            else:
                logger.error("Audio file not found at expected location")
                return None

    except Exception as e:
        # Improved error logging to capture more details
        import traceback
        error_msg = str(e) if str(e) else f"{type(e).__name__}: {repr(e)}"
        logger.error(f"Error downloading audio: {error_msg}")
        logger.debug(f"Exception type: {type(e)}, Exception details: {repr(e)}")
        # Log full traceback for debugging
        logger.debug(f"Full traceback:\n{traceback.format_exc()}")
        return None


def get_video_title(video_url: str) -> str:
    """
    Gets the title of a YouTube video.

    Args:
        video_url: The YouTube video URL

    Returns:
        The video title, or a default title if extraction fails
    """
    try:
        # Try with pytubefix first
        yt = YouTube(video_url)
        title = yt.title

        # Clean the title for use as filename
        title = clean_filename(title)
        return title

    except Exception as e:
        logger.error(f"Error getting video title with pytubefix: {e}")

        # Try with yt-dlp as fallback
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                title = info.get("title", "Untitled")
                title = clean_filename(title)
                return title

        except Exception as e2:
            logger.error(f"Error getting video title with yt-dlp: {e2}")
            return "Untitled_Video"


# Test functions if running this module directly
if __name__ == "__main__":
    # Test playlist extraction
    test_playlist_url = (
        "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
    )
    logger.info("Testing get_video_urls_from_playlist:")
    logger.info(f"  Playlist URL: {test_playlist_url}")

    video_urls = get_video_urls_from_playlist(test_playlist_url)
    logger.info(f"  Found {len(video_urls)} videos")
    for i, url in enumerate(video_urls[:3]):  # Show first 3
        logger.info(f"    {i + 1}. {url}")

    # Test video title extraction
    if video_urls:
        test_video_url = video_urls[0]
        logger.info("\nTesting get_video_title:")
        logger.info(f"  Video URL: {test_video_url}")
        title = get_video_title(test_video_url)
        logger.info(f"  Title: {title}")

        # Test audio download (commented out to avoid actual download)
        # logger.info(f"\nTesting download_youtube_audio:")
        # output_path = f"test_audio/{title}.mp3"
        # audio_file = download_youtube_audio(test_video_url, output_path)
        # if audio_file:
        #     logger.info(f"  Downloaded to: {audio_file}")
        #     # Clean up
        #     os.remove(audio_file)
        #     os.rmdir("test_audio")
