"""
File management utilities for BodhiFlow.

This module provides functions for:
- Saving raw transcripts to intermediate storage
- Loading raw transcripts from storage
- Saving refined markdown outputs
- File discovery and management operations
"""

import os
from pathlib import Path
import json


def save_text_to_file(content: str, file_path: str) -> None:
    """
    Saves text content to a file.

    Args:
        content: The text content to save
        file_path: The path where the file should be saved

    Raises:
        OSError: If the file cannot be written
    """
    # Convert to Path object for easier manipulation
    path = Path(file_path)

    # Create parent directories if they don't exist
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write the content to the file
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Successfully saved file: {path}")
    except Exception as e:
        print(f"Error saving file {path}: {e}")
        raise


def save_raw_transcript(content: str, video_title: str, intermediate_dir: str) -> str:
    """
    Save raw transcript text to intermediate directory for Phase 2 processing.

    Args:
        content (str): Raw transcript text
        video_title (str): Video title for filename
        intermediate_dir (str): Directory to save transcript files

    Returns:
        str: Path to saved transcript file

    Raises:
        OSError: If file cannot be written
    """
    # Create intermediate directory if it doesn't exist
    Path(intermediate_dir).mkdir(parents=True, exist_ok=True)

    # Clean video title for filename
    from .youtube_downloader import clean_filename

    safe_title = clean_filename(video_title)

    # Create transcript filename
    transcript_filename = f"{safe_title}_raw_transcript.txt"
    transcript_path = Path(intermediate_dir) / transcript_filename

    # Save transcript
    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(transcript_path)


def load_raw_transcript(file_path: str) -> str:
    """
    Load raw transcript content from file.

    Args:
        file_path (str): Path to raw transcript file

    Returns:
        str: Raw transcript content

    Raises:
        FileNotFoundError: If transcript file doesn't exist
        OSError: If file cannot be read
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Transcript file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def discover_raw_transcript_files(intermediate_dir: str) -> list[str]:
    """
    Find all raw transcript files in intermediate directory.

    Args:
        intermediate_dir (str): Directory containing raw transcript files

    Returns:
        list[str]: List of transcript file paths
    """
    if not os.path.exists(intermediate_dir):
        return []

    transcript_files = []
    intermediate_path = Path(intermediate_dir)

    # Look for files ending with '_raw_transcript.txt'
    for file_path in intermediate_path.glob("*_raw_transcript.txt"):
        if file_path.is_file():
            transcript_files.append(str(file_path))

    return sorted(transcript_files)


def save_metadata_for_transcript(video_title: str, metadata: dict, intermediate_dir: str) -> str:
    """
    Save standardized metadata as a sidecar JSON file next to the raw transcript.

    Returns the path to the saved metadata file.
    """
    Path(intermediate_dir).mkdir(parents=True, exist_ok=True)
    from .youtube_downloader import clean_filename
    safe_title = clean_filename(video_title)
    meta_path = Path(intermediate_dir) / f"{safe_title}.meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return str(meta_path)


def load_metadata_for_transcript(video_title: str, intermediate_dir: str) -> dict:
    """
    Load standardized metadata sidecar JSON if present; otherwise return minimal dict.
    """
    from .youtube_downloader import clean_filename
    safe_title = clean_filename(video_title)
    meta_path = Path(intermediate_dir) / f"{safe_title}.meta.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"title": video_title, "source_type": "unknown"}


# Test function if running this module directly
if __name__ == "__main__":
    # Test save_text_to_file
    test_content = """# Test Document

This is a test document created by the save_text_to_file function.

## Features
- Saves text to file
- Creates parent directories if needed
- Uses UTF-8 encoding

## Example Usage
```python
save_text_to_file("Hello World", "output/test.txt")
```
"""

    test_path = "test_output/test_document.md"
    print(f"Testing save_text_to_file:")
    print(f"  Saving to: {test_path}")

    try:
        save_text_to_file(test_content, test_path)

        # Verify the file was created
        if os.path.exists(test_path):
            with open(test_path, "r", encoding="utf-8") as f:
                saved_content = f.read()
            print(f"  File created successfully!")
            print(f"  File size: {len(saved_content)} characters")

            # Clean up test file
            os.remove(test_path)
            os.rmdir("test_output")
            print(f"  Test file cleaned up")
    except Exception as e:
        print(f"  Test failed: {e}")
