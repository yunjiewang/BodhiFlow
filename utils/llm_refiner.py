"""
Language model refinement utilities for BodhiFlow.

This module provides functions for:
- Refining raw text using language models (Gemini)
- Creating and managing refinement tasks
- Async processing of multiple refinement jobs
- Chunk processing for large content
"""

import re
from pathlib import Path
from typing import Any, Optional

from .call_llm import call_llm
from .file_saver import load_metadata_for_transcript
from .metadata import build_yaml_front_matter
from .meta_infer import enhance_metadata_with_llm
from .logger_config import get_logger

# Initialize logger for this module
logger = get_logger(__name__)


def _call_llm_for_refine(
    prompt: str,
    provider_config: Optional[dict[str, Any]] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """Single point for LLM call: use provider_config when provided, else legacy Gemini args."""
    if provider_config:
        return call_llm(prompt, provider_config=provider_config)
    return call_llm(prompt, model_name=model_name or "gemini-2.5-flash", api_key=api_key)


def refine_text_with_llm(
    text_content: str,
    style_prompt_template: str,
    language: str,
    model_name: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
    chunk_size: Optional[int] = None,
    provider_config: Optional[dict[str, Any]] = None,
) -> str:
    """
    Refines raw text content using an LLM based on a style-specific prompt template.

    When provider_config is provided (provider, model_name, api_key), uses multi-provider call_llm.
    Otherwise uses legacy model_name + api_key (Gemini).
    """
    if "[full_transcript_text]" in style_prompt_template:
        full_prompt = style_prompt_template.replace(
            "[full_transcript_text]", text_content
        )
        logger.info(
            "Processing Meeting Minutes style prompt (BETA: no language replacement and ignored chunk size setting)"
        )
    else:
        prompt_with_language = style_prompt_template.replace("[Language]", language)
        if chunk_size and len(text_content.split()) > chunk_size:
            chunks = split_text_into_chunks(text_content, chunk_size)
            refined_chunks = []
            for i, chunk in enumerate(chunks):
                logger.info(f"Processing chunk {i + 1}/{len(chunks)}...")
                if i == 0:
                    chunk_prompt = prompt_with_language + chunk
                else:
                    continuation_note = "\n\n[This is a continuation of the previous text. Please continue refining in the same style.]\n\n"
                    chunk_prompt = prompt_with_language + continuation_note + chunk
                try:
                    refined_chunk = _call_llm_for_refine(
                        chunk_prompt,
                        provider_config=provider_config,
                        model_name=model_name,
                        api_key=api_key,
                    )
                    refined_chunks.append(refined_chunk)
                except Exception as e:
                    logger.error(f"Error processing chunk {i + 1}: {e}")
                    raise
            return "\n\n".join(refined_chunks)
        full_prompt = prompt_with_language + text_content

    try:
        return _call_llm_for_refine(
            full_prompt,
            provider_config=provider_config,
            model_name=model_name,
            api_key=api_key,
        )
    except Exception as e:
        logger.error(f"Error refining text: {e}")
        raise


def split_text_into_chunks(text: str, chunk_size: int) -> list[str]:
    """
    Splits text into chunks of approximately chunk_size words.

    Tries to split at paragraph boundaries when possible to maintain coherence.

    Args:
        text: The text to split
        chunk_size: Maximum words per chunk

    Returns:
        List of text chunks
    """
    # Split into paragraphs first
    paragraphs = text.split("\n\n")

    chunks = []
    current_chunk = []
    current_word_count = 0

    for paragraph in paragraphs:
        paragraph_word_count = len(paragraph.split())

        # If adding this paragraph would exceed chunk size
        if current_word_count + paragraph_word_count > chunk_size and current_chunk:
            # Save current chunk
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [paragraph]
            current_word_count = paragraph_word_count
        else:
            # Add to current chunk
            current_chunk.append(paragraph)
            current_word_count += paragraph_word_count

    # Don't forget the last chunk
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def create_refinement_tasks(
    transcript_files: list[str],
    styles_data: list[tuple],
    output_dir: str,
    language: str | None = None,
) -> list[dict]:
    """
    Create all combinations of transcript files × refinement styles for Phase 2 processing.

    Args:
        transcript_files (list[str]): Paths to raw transcript files
        styles_data (list[tuple]): List of (style_name, style_prompt_template) tuples
        output_dir (str): Directory for output markdown files
        language (str | None): Optional per-job output language (e.g. from CSV); if set, stored on each task.

    Returns:
        list[dict]: List of refinement tasks, each with:
            - transcript_file, style_name, style_prompt, output_file, video_title
            - language (optional): when provided
    """

    tasks = []
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for transcript_file in transcript_files:
        transcript_path = Path(transcript_file)
        video_title = transcript_path.stem.replace("_raw_transcript", "")

        for style_name, style_prompt_template in styles_data:
            safe_style_name = re.sub(r"[^\w\-]+", "_", style_name)
            output_filename = f"{video_title} [{safe_style_name}].md"
            output_file = output_path / output_filename

            task = {
                "transcript_file": transcript_file,
                "style_name": style_name,
                "style_prompt": style_prompt_template,
                "output_file": str(output_file),
                "video_title": video_title,
            }
            if language is not None:
                task["language"] = language
            tasks.append(task)

    return tasks


async def async_refine_single_task(task: dict, gemini_config: dict) -> dict:
    """
    Async wrapper for single refinement task, used by AsyncRefinementCoordinator.

    Args:
        task (dict): Single refinement task with keys:
            - transcript_file (str): Path to raw transcript file
            - style_name (str): Name of the refinement style
            - style_prompt (str): Prompt template for the style
            - output_file (str): Path for the output markdown file
            - video_title (str): Video title
        gemini_config (dict): Gemini API configuration with keys:
            - api_key (str): Gemini API key
            - model_name (str): Gemini model name
            - chunk_size (int): Text chunk size for processing
            - language (str): Output language

    Returns:
        dict: Result with keys:
            - status (str): "success" or "failure"
            - task_id (str): Unique task identifier
            - output_file (str): Path to output markdown file
            - video_title (str): Video title
            - style_name (str): Style name
            - error (str|None): Error message if failed
    """
    import asyncio

    from .file_saver import load_raw_transcript, save_text_to_file

    task_id = f"{task['video_title']}_{task['style_name']}"

    try:
        # Load raw transcript
        raw_text = load_raw_transcript(task["transcript_file"])

        lang = task.get("language") or gemini_config.get("language", "English")
        # Refine text with LLM (run in thread pool to avoid blocking)
        def sync_refine():
            return refine_text_with_llm(
                raw_text,
                task["style_prompt"],
                lang,
                model_name=gemini_config.get("model_name"),
                api_key=gemini_config.get("api_key"),
                chunk_size=gemini_config["chunk_size"],
                provider_config=gemini_config.get("provider_config"),
            )

        # Run the blocking LLM call in thread pool
        loop = asyncio.get_event_loop()
        refined_text = await loop.run_in_executor(None, sync_refine)

        # Build and inject YAML front matter
        intermediate_dir = gemini_config.get("intermediate_dir") or ""
        meta = load_metadata_for_transcript(task["video_title"], intermediate_dir)
        # Enhance non-factual fields if enabled
        if gemini_config.get("metadata_enhancement_enabled", True):
            try:
                # Ensure OPENAI_API_KEY is available for Responses API
                import os as _os
                if gemini_config.get("openai_api_key") and not _os.environ.get("OPENAI_API_KEY"):
                    _os.environ["OPENAI_API_KEY"] = gemini_config.get("openai_api_key")
                add = enhance_metadata_with_llm(
                    raw_text,
                    lang,
                    model=gemini_config.get("metadata_llm_model", "gpt-5-nano"),
                )
            except ValueError as e:
                if "OPENAI_API_KEY" in str(e):
                    # Log the API key issue and continue without enhancement
                    print(f"⚠️  Metadata enhancement skipped: {e}")
                add = {"description": "", "tags": []}
            except Exception as e:
                # Log other errors and continue without enhancement
                print(f"⚠️  Metadata enhancement failed: {e}")
                add = {"description": "", "tags": []}
            # Only fill when missing
            if not meta.get("description"):
                meta["description"] = add.get("description", "")
            if not meta.get("tags"):
                meta["tags"] = add.get("tags", [])
        # Attach style and model info
        meta["style"] = task["style_name"]
        meta["model_used"] = gemini_config.get("phase2_model_id") or gemini_config.get("model_name")
        fm = build_yaml_front_matter(meta)
        final_md = fm + refined_text

        # Save refined markdown with front matter
        save_text_to_file(final_md, task["output_file"])

        return {
            "status": "success",
            "task_id": task_id,
            "output_file": task["output_file"],
            "video_title": task["video_title"],
            "style_name": task["style_name"],
            "error": None,
        }

    except Exception as e:
        return {
            "status": "failure",
            "task_id": task_id,
            "output_file": task["output_file"],
            "video_title": task["video_title"],
            "style_name": task["style_name"],
            "error": str(e),
        }


# Test function if running this module directly
if __name__ == "__main__":
    # Test refine_text_with_llm
    test_text = """This is a test transcript. It talks about Python programming.
Python is a great language for beginners. It has simple syntax.
You can do many things with Python like web development, data science, and automation.
The community is very helpful and there are lots of libraries available."""

    test_prompt_template = """Please summarize the following text in [Language].
Make it concise but informative. Use bullet points for key points.
Format the output in Markdown.

Text:
"""

    logger.info("Testing refine_text_with_llm:")
    logger.info("Language: English")
    logger.info("Text length: %d words", len(test_text.split()))
    logger.info("-" * 50)

    try:
        # You'll need to set GEMINI_API_KEY environment variable for this to work
        refined = refine_text_with_llm(
            text_content=test_text,
            style_prompt_template=test_prompt_template,
            language="English",
        )
        logger.info("Refined text:")
        logger.info(refined)
    except Exception as e:
        logger.error(f"Test failed: {e}")
        logger.info("Make sure you have set the GEMINI_API_KEY environment variable")
