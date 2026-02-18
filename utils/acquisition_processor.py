"""
Content acquisition processor for BodhiFlow.

This module provides the unified entry point for processing various content sources
(video, audio, documents, webpages) during Phase 1 acquisition. It handles:
- YouTube videos (transcript download + STT fallback)
- Local video/audio files (audio extraction + STT)
- Teams meeting recordings (download + audio extraction + STT)
- Podcast episodes (audio download + STT)
- Text documents (PDF/Word/webpage via MarkItDown, no STT)

The main function `process_single_video_acquisition` orchestrates acquisition
for a single source, regardless of its type.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import ffmpeg

from utils.logger_config import get_logger
from .metadata import normalize_metadata
from .file_saver import save_raw_transcript, save_metadata_for_transcript
from .input_handler import clean_filename

# Initialize logger for this module
logger = get_logger(__name__)


def _unique_dest_path(intermediate_dir: str, safe_title: str, suffix: str, ext: str) -> Path:
    """Return a path in intermediate_dir that does not yet exist: {safe_title}{suffix}{ext}, or _2, _3, ..."""
    base = f"{safe_title}{suffix}"
    dest = Path(intermediate_dir) / f"{base}{ext}"
    if not dest.exists():
        return dest
    n = 2
    while True:
        dest = Path(intermediate_dir) / f"{base}_{n}{ext}"
        if not dest.exists():
            return dest
        n += 1


def _save_or_remove_audio_after_transcribe(
    audio_path: str,
    intermediate_dir: str,
    safe_title: str,
    save_video: bool,
) -> None:
    """After AI transcription: move audio to intermediate_dir if save_video else remove."""
    if not audio_path or not os.path.exists(audio_path):
        return
    if save_video and intermediate_dir:
        os.makedirs(intermediate_dir, exist_ok=True)
        ext = Path(audio_path).suffix or ".m4a"
        dest = _unique_dest_path(intermediate_dir, safe_title, "_source_audio", ext)
        try:
            shutil.move(audio_path, str(dest))
            logger.info(f"Saved source audio to intermediate folder: {dest}")
        except Exception as e:
            logger.warning(f"Could not move audio to intermediate folder: {e}")
            if os.path.exists(audio_path):
                os.remove(audio_path)
    else:
        os.remove(audio_path)


def _save_or_remove_video_after_transcribe(
    video_path: str,
    intermediate_dir: str,
    safe_title: str,
    save_video: bool,
) -> None:
    """After AI transcription (Teams): move meeting video to intermediate_dir if save_video else remove."""
    if not video_path or not os.path.exists(video_path):
        return
    if save_video and intermediate_dir:
        os.makedirs(intermediate_dir, exist_ok=True)
        ext = Path(video_path).suffix or ".mp4"
        dest = _unique_dest_path(intermediate_dir, safe_title, "_source_video", ext)
        try:
            shutil.move(video_path, str(dest))
            logger.info(f"Saved source video to intermediate folder: {dest}")
        except Exception as e:
            logger.warning(f"Could not move video to intermediate folder: {e}")
            try:
                if os.path.exists(video_path):
                    os.remove(video_path)
            except Exception:
                pass
    else:
        try:
            os.remove(video_path)
        except Exception as exc:
            logger.warning(f"Could not remove meeting video {video_path}: {exc}")


def extract_audio_from_video(video_path: str, output_path: str) -> Optional[str]:
    """
    Extracts audio from a local video file.

    Output format is inferred from output_path extension:
    - .mp3 -> MP3 (for ZAI GLM-ASR; saves space and avoids re-encoding in STT)
    - otherwise -> M4A (AAC)

    Args:
        video_path: Path to the input video file
        output_path: Path where the audio file should be saved (.mp3 or .m4a)

    Returns:
        Path to the extracted audio file if successful, None otherwise
    """
    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return None

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    use_mp3 = output_path.lower().endswith(".mp3")
    if not use_mp3 and not output_path.lower().endswith(".m4a"):
        output_path = os.path.splitext(output_path)[0] + ".m4a"
    elif use_mp3 and not output_path.lower().endswith(".mp3"):
        output_path = os.path.splitext(output_path)[0] + ".mp3"

    try:
        # Use ffmpeg-python to extract audio
        stream = ffmpeg.input(video_path)
        if use_mp3:
            stream = ffmpeg.output(
                stream,
                output_path,
                acodec="libmp3lame",
                audio_bitrate="128k",
                ac=1,
                ar="22050",
            )
        else:
            stream = ffmpeg.output(
                stream,
                output_path,
                acodec="aac",
                audio_bitrate="128k",
                ac=1,
                ar="22050",
            )

        # Overwrite output file if it exists
        stream = ffmpeg.overwrite_output(stream)

        # Run ffmpeg command
        # On Windows, hide the console window
        if sys.platform == "win32":
            # For Windows, we need to use subprocess directly to control window creation
            import subprocess

            # Get the ffmpeg command as a list
            cmd = ffmpeg.compile(stream, overwrite_output=True)

            # Run with CREATE_NO_WINDOW flag, capture as bytes to avoid encoding issues
            result = subprocess.run(
                cmd, creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True
            )

            if result.returncode != 0:
                # Decode stderr with error handling for encoding issues
                try:
                    stderr_text = result.stderr.decode("utf-8", errors="replace")
                except Exception:
                    stderr_text = str(result.stderr)
                logger.error(f"FFmpeg error: {stderr_text}")
                return extract_audio_fallback(video_path, output_path, use_mp3=use_mp3)
        else:
            # On non-Windows platforms, use normal ffmpeg.run
            ffmpeg.run(stream, quiet=True)

        if os.path.exists(output_path):
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                f"Successfully extracted audio to: {output_path} ({file_size_mb:.1f} MB)"
            )
            return output_path
        else:
            logger.error("Audio extraction completed but output file not found")
            return None

    except ffmpeg.Error as e:
        logger.error(f"FFmpeg error during audio extraction: {e.stderr.decode()}")
        return None
    except Exception as e:
        logger.error(f"Error extracting audio: {e}")
        # Try fallback method (preserve requested format from output_path)
        return extract_audio_fallback(
            video_path, output_path, use_mp3=output_path.lower().endswith(".mp3")
        )


def extract_audio_fallback(
    video_path: str, output_path: str, *, use_mp3: bool = False
) -> Optional[str]:
    """
    Fallback method using direct subprocess call to ffmpeg.

    Args:
        video_path: Path to the input video file
        output_path: Path where the audio file should be saved
        use_mp3: If True, output MP3; else M4A (AAC)

    Returns:
        Path to the extracted audio file if successful, None otherwise
    """
    try:
        if use_mp3:
            codec, ext = "libmp3lame", ".mp3"
        else:
            codec, ext = "aac", ".m4a"
        if not output_path.lower().endswith(ext):
            output_path = os.path.splitext(output_path)[0] + ext
        cmd = [
            "ffmpeg",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            codec,
            "-ab",
            "128k",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-y",
            output_path,
        ]

        # Run command
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}

        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        result = subprocess.run(cmd, **kwargs)

        if result.returncode == 0 and os.path.exists(output_path):
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                f"Successfully extracted audio using fallback: {output_path} ({file_size_mb:.1f} MB)"
            )
            return output_path
        else:
            logger.error(
                f"Fallback audio extraction failed with return code: {result.returncode}"
            )
            if result.stderr:
                logger.error(f"Error: {result.stderr.decode()}")
            return None

    except FileNotFoundError:
        logger.error("FFmpeg not found. Please ensure FFmpeg is installed and in PATH")
        return None
    except Exception as e:
        logger.error(f"Fallback extraction error: {e}")
        return None


def get_video_info(video_path: str) -> dict:
    """
    Gets information about a video file.

    Args:
        video_path: Path to the video file

    Returns:
        Dictionary with video information (duration, codec, etc.)
    """
    try:
        probe = ffmpeg.probe(video_path)

        # Extract useful information
        info = {
            "duration": float(probe["format"].get("duration", 0)),
            "size": int(probe["format"].get("size", 0)),
            "bit_rate": int(probe["format"].get("bit_rate", 0)),
            "format_name": probe["format"].get("format_name", "unknown"),
        }

        # Get video stream info
        video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
        if video_streams:
            video_stream = video_streams[0]
            info["video_codec"] = video_stream.get("codec_name", "unknown")
            info["width"] = video_stream.get("width", 0)
            info["height"] = video_stream.get("height", 0)

        # Get audio stream info
        audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
        if audio_streams:
            audio_stream = audio_streams[0]
            info["audio_codec"] = audio_stream.get("codec_name", "unknown")
            info["sample_rate"] = int(audio_stream.get("sample_rate", 0))
            info["channels"] = audio_stream.get("channels", 0)

        return info

    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return {}


def process_single_video_acquisition(video_data: dict, config: dict) -> dict:
    """
    Process a single content source for acquisition (Phase 1).
    This function is designed to be used by multiprocessing pool.

    Handles multiple source types: YouTube videos, local files, Teams meetings,
    podcast episodes, and text documents (PDF/Word/webpage).

    Args:
        video_data (dict): Source information with keys:
            - source_path (str): URL or file path
            - source_type (str): "youtube_url", "local_file", "teams_meeting_url",
                                 "podcast_audio", or "text_document"
            - original_title (str): Title for naming
        config (dict): Processing configuration with keys:
            - temp_dir (str): Temporary directory for audio files
            - intermediate_dir (str): Directory for raw transcript files
            - openai_api_key (str): OpenAI API key for STT
            - cookie_file_path (str|None): Cookie file for YouTube
            - output_language (str): Language for STT
            - disable_ai_transcribe (bool): When True, YouTube videos only use downloaded transcripts, no STT fallback

    Returns:
        dict: Result with keys:
            - status (str): "success" or "failure"
            - video_title (str): Video title
            - transcript_file (str|None): Path to saved transcript file
            - transcript_text (str|None): Raw transcript content
            - error (str|None): Error message if failed
            - job_id (int): Job ID from CSV batch (if applicable)
    """
    import os
    from pathlib import Path

    from .audio_chunker import chunk_audio_on_silence
    from .speech_to_text import transcribe_audio_chunks
    from .transcript_fetcher import download_youtube_transcript
    from .youtube_downloader import download_youtube_audio

    video_title = video_data["original_title"]
    # Ensure a filesystem-safe title is used for any file/dir path
    safe_title = clean_filename(video_title)
    source_path = video_data["source_path"]
    source_type = video_data["source_type"]

    try:
        raw_text = None
        asr_cfg = config.get("asr_config") or {}
        max_chunk_sec = asr_cfg.get("max_chunk_duration_seconds") or 600
        min_chunk_sec = 5.0 if max_chunk_sec <= 30 else 30.0
        chunk_kwargs = {"max_chunk_duration": max_chunk_sec, "min_chunk_duration": min_chunk_sec}
        # Use MP3 from upstream when ZAI GLM-ASR is selected (saves space, no re-encode in STT)
        use_mp3_for_zai = (asr_cfg.get("provider") or "").lower() == "zai"
        audio_ext = ".mp3" if use_mp3_for_zai else ".m4a"

        if source_type == "youtube_url":
            # Try transcript download first
            raw_text = download_youtube_transcript(
                source_path, config.get("cookie_file_path")
            )

            # Check if AI transcription is disabled
            disable_ai_transcribe = config.get("disable_ai_transcribe", False)
            last_error = None  # Capture failure reason for diagnostics

            if raw_text is None and not disable_ai_transcribe:
                # Fallback to audio download + STT only if AI transcription is not disabled
                audio_file_target = (
                    Path(config["temp_dir"]) / f"{safe_title}_audio.m4a"
                )
                audio_path = download_youtube_audio(
                    source_path,
                    str(audio_file_target),
                    config.get("cookie_file_path"),
                )

                if audio_path:
                    # Chunk audio and transcribe
                    chunks_output_dir = (
                        Path(config["temp_dir"]) / f"{safe_title}_chunks"
                    )
                    chunk_paths = chunk_audio_on_silence(
                        audio_path, str(chunks_output_dir), **chunk_kwargs
                    )

                    if chunk_paths:
                        raw_text = transcribe_audio_chunks(
                            chunk_paths,
                            asr_config=config.get("asr_config"),
                            api_key=config.get("openai_api_key"),
                        )
                        if not raw_text or not raw_text.strip():
                            last_error = "ASR returned empty transcript"
                    else:
                        last_error = "No audio chunks produced"
                        logger.warning(f"No audio chunks found for {video_title}")

                    # Clean up: save source audio to intermediate_dir if requested, else remove
                    _save_or_remove_audio_after_transcribe(
                        audio_path,
                        config.get("intermediate_dir", ""),
                        safe_title,
                        config.get("save_video_on_ai_transcribe", False),
                    )
                    for chunk_path in chunk_paths:
                        if os.path.exists(chunk_path):
                            os.remove(chunk_path)
                    if chunks_output_dir.exists():
                        chunks_output_dir.rmdir()
                else:
                    last_error = "Failed to download YouTube audio (video may be private/restricted; try Cookie file)"
            elif raw_text is None and disable_ai_transcribe:
                last_error = "No transcript available and AI transcription is disabled"
                # Log that transcript was not available and AI transcription is disabled
                logger.warning(f"No transcript available for {video_title} and AI transcription is disabled")

        elif source_type == "teams_meeting_url":
            from .teams_meeting import download_teams_meeting_recording

            download_dir = Path(config["temp_dir"]) / "teams_meetings"
            video_filename_stem = safe_title if safe_title else "teams_meeting"
            meeting_video_path = download_teams_meeting_recording(
                source_path,
                str(download_dir),
                video_filename_stem,
                max_retries=config.get("teams_download_retries", 2),
            )

            if meeting_video_path:
                audio_file_target = Path(config["temp_dir"]) / f"{safe_title}_audio{audio_ext}"
                audio_path = extract_audio_from_video(
                    meeting_video_path, str(audio_file_target)
                )

                if audio_path:
                    chunks_output_dir = Path(config["temp_dir"]) / f"{safe_title}_chunks"
                    chunk_paths = chunk_audio_on_silence(
                        audio_path, str(chunks_output_dir), **chunk_kwargs
                    )

                    if chunk_paths:
                        raw_text = transcribe_audio_chunks(
                            chunk_paths,
                            asr_config=config.get("asr_config"),
                            api_key=config.get("openai_api_key"),
                        )
                    else:
                        logger.warning(f"No audio chunks found for {video_title}")

                    _save_or_remove_audio_after_transcribe(
                        audio_path,
                        config.get("intermediate_dir", ""),
                        safe_title,
                        config.get("save_video_on_ai_transcribe", False),
                    )
                    for chunk_path in chunk_paths:
                        if os.path.exists(chunk_path):
                            os.remove(chunk_path)
                    if chunks_output_dir.exists():
                        chunks_output_dir.rmdir()
                else:
                    logger.error(
                        f"Failed to extract audio from downloaded Teams meeting: {video_title}"
                    )

                _save_or_remove_video_after_transcribe(
                    meeting_video_path,
                    config.get("intermediate_dir", ""),
                    safe_title,
                    config.get("save_video_on_ai_transcribe", False),
                )
            else:
                logger.error(f"Failed to download Teams meeting recording for {video_title}")

        elif source_type == "local_file":
            # Extract audio and transcribe (MP3 when ZAI GLM-ASR to avoid re-encode)
            audio_file_target = Path(config["temp_dir"]) / f"{safe_title}_audio{audio_ext}"
            audio_path = extract_audio_from_video(source_path, str(audio_file_target))

            if audio_path:
                # Chunk audio and transcribe
                chunks_output_dir = Path(config["temp_dir"]) / f"{safe_title}_chunks"
                chunk_paths = chunk_audio_on_silence(
                    audio_path, str(chunks_output_dir), **chunk_kwargs
                )

                if chunk_paths:
                    raw_text = transcribe_audio_chunks(
                        chunk_paths,
                        asr_config=config.get("asr_config"),
                        api_key=config.get("openai_api_key"),
                    )
                else:
                    logger.warning(f"No audio chunks found for {video_title}")

                _save_or_remove_audio_after_transcribe(
                    audio_path,
                    config.get("intermediate_dir", ""),
                    safe_title,
                    config.get("save_video_on_ai_transcribe", False),
                )
                for chunk_path in chunk_paths:
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)
                if chunks_output_dir.exists():
                    chunks_output_dir.rmdir()

        elif source_type == "text_document":
            # Text entry: extract text via MarkItDown (no STT)
            from .text_extractor import extract_text_from_file, extract_text_from_url

            if source_path.startswith("http://") or source_path.startswith("https://"):
                raw_text = extract_text_from_url(source_path, config["temp_dir"])
            else:
                raw_text = extract_text_from_file(source_path)

        elif source_type == "podcast_audio":
            # Download podcast audio and transcribe
            from .podcast_parser import download_podcast_audio
            
            audio_file_target = Path(config["temp_dir"]) / f"{video_title}_audio"
            audio_path = download_podcast_audio(
                source_path,  # This is the audio_url from the episode
                config["temp_dir"],
                video_title
            )

            if audio_path:
                # Chunk audio and transcribe
                chunks_output_dir = Path(config["temp_dir"]) / f"{safe_title}_chunks"
                chunk_paths = chunk_audio_on_silence(
                    audio_path, str(chunks_output_dir), **chunk_kwargs
                )

                if chunk_paths:
                    raw_text = transcribe_audio_chunks(
                        chunk_paths,
                        asr_config=config.get("asr_config"),
                        api_key=config.get("openai_api_key"),
                    )
                else:
                    logger.warning(f"No audio chunks found for {video_title}")

                _save_or_remove_audio_after_transcribe(
                    audio_path,
                    config.get("intermediate_dir", ""),
                    safe_title,
                    config.get("save_video_on_ai_transcribe", False),
                )
                for chunk_path in chunk_paths:
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)
                if chunks_output_dir.exists():
                    chunks_output_dir.rmdir()
            else:
                logger.error(f"Failed to download podcast audio for {video_title}")

        if raw_text:
            # Save raw transcript to intermediate directory
            transcript_file = save_raw_transcript(
                raw_text, video_title, config["intermediate_dir"]
            )

            # Build and persist standardized metadata sidecar
            try:
                # prefer values already normalized in queue item
                base = {
                    "title": video_title,
                    "source_url": source_path,
                    "author": video_data.get("channel") or video_data.get("author"),
                    "upload_date": video_data.get("upload_date"),
                    "pub_date": video_data.get("pub_date") or video_data.get("episode_date"),
                    "tags": video_data.get("tags"),
                    "duration": video_data.get("duration") or video_data.get("episode_duration"),
                    "language": config.get("output_language"),
                    "description": video_data.get("description") or video_data.get("episode_description"),
                }
                std_meta = normalize_metadata(source_type, base)
                save_metadata_for_transcript(video_title, std_meta, config["intermediate_dir"])
            except Exception:
                pass

            return {
                "status": "success",
                "video_title": video_title,
                "transcript_file": transcript_file,
                "transcript_text": raw_text,
                "error": None,
                "job_id": video_data.get("job_id", 0),
            }
        else:
            if source_type == "text_document":
                error_msg = "Failed to extract text from document"
            else:
                error_msg = "Failed to extract transcript from source"
                if source_type == "youtube_url" and last_error:
                    error_msg = f"{error_msg}: {last_error}"
            return {
                "status": "failure",
                "video_title": video_title,
                "job_id": video_data.get("job_id", 0),
                "transcript_file": None,
                "transcript_text": None,
                "error": error_msg,
            }

    except Exception as e:
        return {
            "status": "failure",
            "video_title": video_title,
            "job_id": video_data.get("job_id", 0),
            "transcript_file": None,
            "transcript_text": None,
            "error": str(e),
        }


# Test functions if running this module directly
if __name__ == "__main__":
    # Create a test video file (this would normally be an actual video)
    test_video_path = "test_video.mp4"
    test_audio_output = "test_audio_output.m4a"

    logger.info("Content acquisition processor test")
    logger.info("-" * 50)

    # Note: For actual testing, you'd need a real video file
    logger.info("Note: This test requires an actual video file to work properly")
    logger.info(
        "Place a video file named 'test_video.mp4' in the current directory to test"
    )

    if os.path.exists(test_video_path):
        # Test video info extraction
        logger.info(f"\nTesting get_video_info on: {test_video_path}")
        info = get_video_info(test_video_path)
        if info:
            logger.info(f"  Duration: {info.get('duration', 0):.1f} seconds")
            logger.info(f"  Size: {info.get('size', 0) / (1024 * 1024):.1f} MB")
            logger.info(f"  Video codec: {info.get('video_codec', 'unknown')}")
            logger.info(f"  Audio codec: {info.get('audio_codec', 'unknown')}")
            logger.info(f"  Resolution: {info.get('width', 0)}x{info.get('height', 0)}")

        # Test audio extraction
        logger.info("\nTesting extract_audio_from_video:")
        logger.info(f"  Input: {test_video_path}")
        logger.info(f"  Output: {test_audio_output}")

        audio_path = extract_audio_from_video(test_video_path, test_audio_output)
        if audio_path:
            logger.info(f"  Success! Audio extracted to: {audio_path}")

            # Clean up
            if os.path.exists(audio_path):
                os.remove(audio_path)
                logger.info("  Test file cleaned up")
        else:
            logger.error("  Audio extraction failed")
    else:
        logger.warning(f"\nTest video file not found: {test_video_path}")
        logger.info("Please provide a test video file to run the extraction test")
