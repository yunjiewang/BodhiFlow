"""
Audio chunking utilities for BodhiFlow.

This module provides functions to split audio files into chunks based on silence detection,
which is useful for processing with speech-to-text APIs that have file size limits.
"""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

import ffmpeg

from utils.logger_config import get_logger

# Initialize logger for this module
logger = get_logger(__name__)

# Constants for fallback chunking
MAX_FILE_SIZE_MB = 25  # OpenAI API limit
FALLBACK_CHUNK_DURATION = 600  # 10 minutes in seconds
TARGET_BITRATE = "128k"  # Target bitrate for chunks
MAX_PARALLEL_WORKERS = 4  # Maximum parallel chunk creation workers


def chunk_audio_on_silence(
    audio_path: str,
    output_dir: str,
    min_silence_len: int = 1000,  # milliseconds
    silence_thresh: int = -30,  # dB
    min_chunk_duration: float = 30.0,  # seconds
    max_chunk_duration: float = 600,  # 10 minutes
) -> List[str]:
    """
    Splits an audio file into chunks based on silence detection with intelligent fallback mechanisms.

    This function attempts silence-based chunking first, then falls back to time-based chunking
    if the file is too large (>25MB) and silence detection fails or produces unusable results.

    Args:
        audio_path: Path to the input audio file
        output_dir: Directory where chunks should be saved
        min_silence_len: Minimum length of silence to be used for a split (ms)
        silence_thresh: Silence threshold in dB
        min_chunk_duration: Minimum duration for each chunk (seconds)
        max_chunk_duration: Maximum duration for each chunk (seconds)

    Returns:
        List of paths to the generated audio chunks
    """
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return []

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Check file size
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    logger.info(f"Audio file size: {file_size_mb:.2f} MB")

    # Try silence-based chunking first
    logger.info("Attempting silence-based audio chunking...")
    silence_timestamps = detect_silence_with_ffmpeg(
        audio_path,
        min_silence_len / 1000.0,  # Convert to seconds
        silence_thresh,
    )

    # Determine if we need fallback chunking
    needs_fallback = False

    # Get audio duration without loading entire file (needed for all paths)
    logger.info(f"Getting audio duration: {audio_path}")
    total_duration = _get_audio_duration_fast(audio_path)
    logger.info(f"Total audio duration: {total_duration:.1f} seconds")

    if not silence_timestamps:
        logger.warning("No silence periods detected")
        if file_size_mb > MAX_FILE_SIZE_MB:
            logger.warning(
                f"File size ({file_size_mb:.2f} MB) exceeds API limit ({MAX_FILE_SIZE_MB} MB)"
            )
            needs_fallback = True
        elif total_duration <= max_chunk_duration:
            # File size and duration are acceptable, create single chunk
            logger.info("File size and duration are acceptable, creating single chunk")
            return _create_single_chunk(audio_path, output_dir)
        else:
            # Duration exceeds max_chunk_duration, need to chunk even if file size is OK
            logger.warning(
                f"Audio duration ({total_duration:.1f}s) exceeds max_chunk_duration ({max_chunk_duration}s), "
                "using fallback time-based chunking"
            )
            needs_fallback = True

    # If we have silence periods, try to create chunks based on them
    if silence_timestamps and not needs_fallback:
        chunk_boundaries = create_chunk_boundaries(
            silence_timestamps, total_duration, min_chunk_duration, max_chunk_duration
        )

        # Check if silence-based chunking would produce chunks that are too large
        estimated_chunks = _estimate_chunk_sizes(
            audio_path, chunk_boundaries, total_duration
        )
        if any(size_mb > MAX_FILE_SIZE_MB for size_mb in estimated_chunks):
            logger.warning(
                "Silence-based chunks would exceed size limit, using fallback"
            )
            needs_fallback = True
        else:
            logger.info(
                f"Silence-based chunking will create {len(chunk_boundaries)} chunks"
            )

    # Use fallback time-based chunking if needed
    if needs_fallback:
        fallback_duration = min(FALLBACK_CHUNK_DURATION, max_chunk_duration)
        logger.info("Using fallback time-based chunking...")
        chunk_boundaries = _create_time_based_boundaries(
            total_duration, fallback_duration, min_chunk_duration
        )
        logger.info(f"Time-based chunking will create {len(chunk_boundaries)} chunks")

    # Create chunks with parallel ffmpeg processing (fast, no memory loading)
    logger.info("Creating audio chunks with parallel ffmpeg processing...")
    chunk_paths = _create_chunks_with_ffmpeg_parallel(
        audio_path, chunk_boundaries, output_dir
    )

    # Validate chunk sizes and durations
    _validate_chunk_sizes(chunk_paths)
    _validate_chunk_durations(chunk_paths, max_chunk_duration)

    logger.info(f"Successfully created {len(chunk_paths)} chunks in {output_dir}")
    return chunk_paths


def _get_audio_duration_fast(audio_path: str) -> float:
    """
    Get audio duration quickly without loading the entire file into memory.
    Uses ffprobe to read metadata.

    Args:
        audio_path: Path to the audio file

    Returns:
        Duration in seconds

    Raises:
        Exception: If ffprobe fails to read the file (e.g., corrupted file, ffmpeg not installed)
    """
    probe = ffmpeg.probe(audio_path)
    duration = float(probe["format"]["duration"])
    return duration


def _create_single_chunk(audio_path: str, output_dir: str) -> List[str]:
    """Create a single chunk by copying the original file, preserving format."""
    import shutil

    # Detect input file format and use same extension for output
    input_ext = os.path.splitext(audio_path)[1].lower()

    # Use the same extension as input, or default to .m4a if unknown
    if input_ext in [".mp3", ".m4a", ".wav", ".aac", ".ogg"]:
        output_ext = input_ext
    else:
        output_ext = ".m4a"

    chunk_path = os.path.join(output_dir, f"chunk_001{output_ext}")
    shutil.copy2(audio_path, chunk_path)
    logger.info(f"Created single chunk (no chunking needed): {output_ext}")
    return [chunk_path]


def _estimate_chunk_sizes(
    audio_path: str, chunk_boundaries: List[Tuple[float, float]], total_duration: float
) -> List[float]:
    """Estimate the file sizes of chunks in MB based on file size and duration ratio."""
    estimated_sizes = []
    total_size = os.path.getsize(audio_path)

    for start_time, end_time in chunk_boundaries:
        duration = end_time - start_time
        # Rough estimation based on duration ratio
        estimated_size_bytes = total_size * (duration / total_duration)
        estimated_size_mb = estimated_size_bytes / (1024 * 1024)
        estimated_sizes.append(estimated_size_mb)

    return estimated_sizes


def _create_time_based_boundaries(
    total_duration: float, chunk_duration: float, min_chunk_duration: float
) -> List[Tuple[float, float]]:
    """
    Create chunk boundaries based on fixed time intervals.
    
    Args:
        total_duration: Total duration of the audio
        chunk_duration: Target chunk duration (should be <= max_chunk_duration)
        min_chunk_duration: Minimum chunk duration
    
    Returns:
        List of (start, end) tuples for chunks
    """
    boundaries = []
    current_start = 0.0

    while current_start < total_duration:
        current_end = min(current_start + chunk_duration, total_duration)

        # Handle the last chunk
        if current_end == total_duration:
            remaining_duration = total_duration - current_start
            if remaining_duration < min_chunk_duration and boundaries:
                # Merge with previous chunk if too short, but check if merged chunk exceeds chunk_duration
                last_start, last_end = boundaries[-1]
                merged_duration = total_duration - last_start
                if merged_duration > chunk_duration:
                    # Can't merge without exceeding chunk_duration, create separate chunk
                    # But if it's too short, we still need to handle it
                    if remaining_duration >= min_chunk_duration:
                        boundaries.append((current_start, current_end))
                    else:
                        # Too short to be standalone, but merging would exceed max
                        # In this case, we still merge but log a warning
                        logger.warning(
                            f"Last chunk ({remaining_duration:.1f}s) is too short but merging would exceed "
                            f"chunk_duration ({chunk_duration}s). Merging anyway."
                        )
                        boundaries[-1] = (last_start, total_duration)
                else:
                    # Safe to merge
                    boundaries[-1] = (last_start, total_duration)
            else:
                boundaries.append((current_start, current_end))
            break
        else:
            boundaries.append((current_start, current_end))
            current_start = current_end

    return boundaries


def _create_single_chunk_worker(
    chunk_info: Tuple[int, Tuple[float, float]], audio_path: str, output_dir: str
) -> Tuple[int, str]:
    """
    Worker function to create a single chunk (for parallel processing).

    This function intelligently handles format conversion:
    - MP3 input -> MP3 output (uses stream copy, no re-encoding, fast)
    - M4A/AAC input -> M4A output (uses stream copy, no re-encoding, fast)
    - Other formats -> M4A/AAC output (re-encodes for OpenAI API compatibility)

    Args:
        chunk_info: Tuple of (index, (start_time, end_time))
        audio_path: Path to the source audio file
        output_dir: Directory to save the chunk

    Returns:
        Tuple of (chunk_num, chunk_path) or (chunk_num, None) if failed
    """
    i, (start_time, end_time) = chunk_info
    chunk_num = i + 1

    duration = end_time - start_time

    try:
        # Detect input file format to determine output format and codec
        input_ext = os.path.splitext(audio_path)[1].lower()

        # For MP3 input: output MP3 (can use stream copy)
        # For M4A/AAC input: output M4A (can use stream copy)
        # For other formats: convert to M4A/AAC (better compatibility with OpenAI API)
        if input_ext == ".mp3":
            chunk_filename = f"chunk_{chunk_num:03d}.mp3"
            chunk_path = os.path.join(output_dir, chunk_filename)

            # MP3 -> MP3: use stream copy (fast)
            stream = ffmpeg.input(audio_path, ss=start_time, t=duration)
            stream = ffmpeg.output(
                stream,
                chunk_path,
                acodec="copy",  # Stream copy - no re-encoding!
                format="mp3",
            )
        elif input_ext in [".m4a", ".aac"]:
            # M4A/AAC -> M4A: use stream copy (fast)
            chunk_filename = f"chunk_{chunk_num:03d}.m4a"
            chunk_path = os.path.join(output_dir, chunk_filename)

            stream = ffmpeg.input(audio_path, ss=start_time, t=duration)
            stream = ffmpeg.output(
                stream,
                chunk_path,
                acodec="copy",  # Stream copy - no re-encoding!
                format="ipod",
            )
        else:
            # Other formats -> M4A/AAC: need to re-encode
            chunk_filename = f"chunk_{chunk_num:03d}.m4a"
            chunk_path = os.path.join(output_dir, chunk_filename)

            stream = ffmpeg.input(audio_path, ss=start_time, t=duration)
            stream = ffmpeg.output(
                stream,
                chunk_path,
                acodec="aac",  # Re-encode to AAC for M4A container
                audio_bitrate=TARGET_BITRATE,
                format="ipod",
            )

        # Run ffmpeg with window hidden on Windows
        if sys.platform == "win32":
            cmd = ffmpeg.compile(stream, overwrite_output=True)
            result = subprocess.run(
                cmd, creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True
            )
            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace")
                logger.error(f"FFmpeg chunk {chunk_num} creation failed: {stderr_text}")
                return (chunk_num, None)
        else:
            ffmpeg.run(stream, overwrite_output=True, quiet=True)

        logger.info(f"Created chunk {chunk_num}: {duration:.1f}s")
        return (chunk_num, chunk_path)

    except Exception as e:
        logger.error(f"Error creating chunk {chunk_num}: {e}")
        return (chunk_num, None)


def _create_chunks_with_ffmpeg_parallel(
    audio_path: str, chunk_boundaries: List[Tuple[float, float]], output_dir: str
) -> List[str]:
    """
    Create audio chunks directly using ffmpeg with parallel processing.
    Uses stream copy to avoid re-encoding, which is 10-20x faster than re-encoding.

    Args:
        audio_path: Path to the source audio file
        chunk_boundaries: List of (start_time, end_time) tuples
        output_dir: Directory to save chunks

    Returns:
        List of paths to created chunks (sorted by chunk number)
    """
    num_chunks = len(chunk_boundaries)
    max_workers = min(MAX_PARALLEL_WORKERS, num_chunks)

    logger.info(
        f"Processing {num_chunks} chunks in parallel (max {max_workers} workers)..."
    )

    # Dictionary to store results by chunk number
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all chunk creation tasks
        future_to_chunk = {
            executor.submit(
                _create_single_chunk_worker, (i, boundary), audio_path, output_dir
            ): i
            for i, boundary in enumerate(chunk_boundaries)
        }

        # Collect results as they complete
        for future in as_completed(future_to_chunk):
            chunk_num, chunk_path = future.result()
            if chunk_path:
                results[chunk_num] = chunk_path

    # Sort by chunk number and return only successful chunks
    chunk_paths = [results[i] for i in sorted(results.keys())]

    logger.info(f"Successfully created {len(chunk_paths)}/{num_chunks} chunks")
    return chunk_paths


def _validate_chunk_sizes(chunk_paths: List[str]) -> None:
    """Validate that all chunks are within the size limit."""
    for chunk_path in chunk_paths:
        if os.path.exists(chunk_path):
            size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                logger.warning(
                    f"Chunk {chunk_path} exceeds size limit: {size_mb:.2f} MB"
                )
            else:
                logger.debug(f"Chunk {chunk_path}: {size_mb:.2f} MB")


def _validate_chunk_durations(chunk_paths: List[str], max_chunk_duration: float) -> None:
    """Validate that all chunks are within the duration limit."""
    for chunk_path in chunk_paths:
        if os.path.exists(chunk_path):
            try:
                duration = _get_audio_duration_fast(chunk_path)
                if duration > max_chunk_duration:
                    logger.error(
                        f"Chunk {chunk_path} exceeds duration limit: {duration:.1f}s > {max_chunk_duration}s"
                    )
                else:
                    logger.debug(f"Chunk {chunk_path}: {duration:.1f}s")
            except Exception as e:
                logger.warning(f"Could not validate duration for {chunk_path}: {e}")


def detect_silence_with_ffmpeg(
    audio_path: str, min_silence_duration: float, silence_threshold: int
) -> List[Tuple[float, float]]:
    """
    Detects silence periods in an audio file using ffmpeg.

    Args:
        audio_path: Path to the audio file
        min_silence_duration: Minimum silence duration in seconds
        silence_threshold: Silence threshold in dB

    Returns:
        List of (start, end) tuples for silence periods
    """
    try:
        # Build ffmpeg command to detect silence
        stream = ffmpeg.input(audio_path)
        stream = ffmpeg.filter(
            stream,
            "silencedetect",
            noise=f"{silence_threshold}dB",
            duration=min_silence_duration,
        )
        stream = ffmpeg.output(stream, "-", format="null")

        # Run ffmpeg and capture output
        if sys.platform == "win32":
            # For Windows, we need to use subprocess directly to control window creation
            import subprocess

            # Get the ffmpeg command as a list
            cmd = ffmpeg.compile(stream)

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
                logger.error(f"FFmpeg silence detection failed: {stderr_text}")
                return []

            # Parse from stderr (ffmpeg outputs filter info to stderr)
            # Decode with error handling for encoding issues
            try:
                stderr_text = result.stderr.decode("utf-8", errors="replace")
            except Exception:
                stderr_text = str(result.stderr)
        else:
            # On non-Windows platforms, use normal ffmpeg.run
            kwargs = {"capture_stdout": True, "capture_stderr": True}
            out, err = ffmpeg.run(stream, **kwargs)
            stderr_text = err.decode("utf-8", errors="replace")

        # Parse silence periods from stderr
        silence_periods = []
        silence_start_pattern = r"silence_start: ([\d.]+)"
        silence_end_pattern = r"silence_end: ([\d.]+)"

        starts = re.findall(silence_start_pattern, stderr_text)
        ends = re.findall(silence_end_pattern, stderr_text)

        # Pair up starts and ends
        for start, end in zip(starts, ends):
            silence_periods.append((float(start), float(end)))

        logger.info(f"Detected {len(silence_periods)} silence periods")
        return silence_periods

    except Exception as e:
        logger.error(f"Error detecting silence: {e}")
        return []


def create_chunk_boundaries(
    silence_periods: List[Tuple[float, float]],
    total_duration: float,
    min_chunk_duration: float,
    max_chunk_duration: float,
) -> List[Tuple[float, float]]:
    """
    Creates chunk boundaries based on silence periods.

    Args:
        silence_periods: List of (start, end) tuples for silence
        total_duration: Total duration of the audio
        min_chunk_duration: Minimum chunk duration
        max_chunk_duration: Maximum chunk duration

    Returns:
        List of (start, end) tuples for chunks
    """
    if not silence_periods:
        return [(0, total_duration)]

    chunks = []
    current_start = 0

    for silence_start, silence_end in silence_periods:
        # Use the middle of silence as split point
        split_point = (silence_start + silence_end) / 2

        # Check if this would create a valid chunk
        chunk_duration = split_point - current_start

        if chunk_duration >= min_chunk_duration:
            # Check if we need to split due to max duration
            if chunk_duration > max_chunk_duration:
                # Split into multiple chunks
                temp_start = current_start
                while temp_start < split_point:
                    temp_end = min(temp_start + max_chunk_duration, split_point)
                    chunks.append((temp_start, temp_end))
                    temp_start = temp_end
            else:
                chunks.append((current_start, split_point))
            current_start = split_point

    # Handle the last chunk
    if current_start < total_duration:
        remaining_duration = total_duration - current_start
        if remaining_duration >= min_chunk_duration or not chunks:
            # Check if remaining duration exceeds max_chunk_duration
            if remaining_duration > max_chunk_duration:
                # Split the remaining duration into multiple chunks
                temp_start = current_start
                while temp_start < total_duration:
                    temp_end = min(temp_start + max_chunk_duration, total_duration)
                    chunks.append((temp_start, temp_end))
                    temp_start = temp_end
            else:
                chunks.append((current_start, total_duration))
        else:
            # Merge with previous chunk if too short, but check if merged chunk exceeds max
            if chunks:
                last_start, last_end = chunks[-1]
                merged_duration = total_duration - last_start
                if merged_duration > max_chunk_duration:
                    # Can't merge, need to split
                    # First, ensure the previous chunk doesn't exceed max
                    if last_end - last_start > max_chunk_duration:
                        # Previous chunk already exceeds max, split it first
                        chunks.pop()
                        temp_start = last_start
                        while temp_start < last_end:
                            temp_end = min(temp_start + max_chunk_duration, last_end)
                            chunks.append((temp_start, temp_end))
                            temp_start = temp_end
                    # Now handle the remaining part
                    temp_start = last_end if chunks else current_start
                    while temp_start < total_duration:
                        temp_end = min(temp_start + max_chunk_duration, total_duration)
                        chunks.append((temp_start, temp_end))
                        temp_start = temp_end
                else:
                    # Safe to merge
                    chunks[-1] = (last_start, total_duration)

    return chunks


# Test functions if running this module directly
if __name__ == "__main__":
    test_audio_path = "test_audio.mp3"
    test_output_dir = "test_chunks"

    logger.info("Audio chunking utilities test")
    logger.info("-" * 50)

    if os.path.exists(test_audio_path):
        logger.info("Testing chunk_audio_on_silence:")
        logger.info(f"  Input: {test_audio_path}")
        logger.info(f"  Output directory: {test_output_dir}")

        # Clean up previous test run
        if os.path.exists(test_output_dir):
            import shutil

            shutil.rmtree(test_output_dir)
        os.makedirs(test_output_dir, exist_ok=True)

        chunk_paths = chunk_audio_on_silence(
            test_audio_path,
            test_output_dir,
            min_silence_len=1000,
            silence_thresh=-40,
            min_chunk_duration=30.0,
            max_chunk_duration=300,  # 5 minutes for testing
        )

        logger.info(f"\nCreated {len(chunk_paths)} chunks:")
        for i, chunk_path in enumerate(chunk_paths):
            if os.path.exists(chunk_path):
                size_mb = os.path.getsize(chunk_path) / (1024 * 1024)
                logger.info(f"  Chunk {i + 1}: {chunk_path} ({size_mb:.2f} MB)")
            else:
                logger.error(f"  Chunk {i + 1}: {chunk_path} (Missing!)")

        if chunk_paths and all(os.path.exists(p) for p in chunk_paths):
            logger.info("Test completed successfully: All chunk files were created")
        else:
            logger.error("Test failed: Some chunks are missing or were not created")

    else:
        logger.warning(f"Test audio file not found: {test_audio_path}")
        logger.info("Please provide a test audio file to run the chunking test")
