"""
PocketFlow nodes for BodhiFlow - Content to Wisdom Converter.

This module contains all the PocketFlow nodes used in the BodhiFlow application
for content acquisition and refinement workflows.

Main node categories:
- Input Processing: InputExpansionNode
- Content Acquisition: ParallelAcquisitionCoordinatorNode
- Content Refinement: RefinementTaskCreatorNode, AsyncRefinementCoordinatorNode
- Cleanup & Completion: TempFileCleanupNode, FlowCompletionNode

Each node follows the PocketFlow pattern with prep(), exec(), and post() methods.
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from pocketflow import Node

from utils.constants import StatusType
from utils.file_saver import discover_raw_transcript_files

# Import utility functions
from utils.input_handler import (
    get_input_type,
    list_video_files_in_folder,
    list_document_files_in_folder,
    clean_filename,
)
from utils.llm_refiner import async_refine_single_task, create_refinement_tasks
from utils.models_config import get_model_by_id
from utils.acquisition_processor import process_single_video_acquisition
from utils.youtube_downloader import (
    get_video_title,
    get_video_urls_from_playlist,
    fetch_youtube_metadata,
)
from utils.podcast_parser import get_podcast_info, parse_podcast_rss
from utils.teams_meeting import derive_meeting_title


def _apply_range(items: list, start_index: int, end_index: int) -> list:
    """
    Apply start/end index slicing to a list of items (e.g. playlist range).

    Args:
        items: List to slice
        start_index: 1-based start index
        end_index: 0 means all remaining items

    Returns:
        Sliced list
    """
    if end_index > 0:
        return items[start_index - 1 : end_index]
    else:
        return items[start_index - 1 :]


def _should_skip_resume(
    safe_title: str,
    existing_titles: set,
    resume_mode: bool,
    title: str,
    status_callback,
) -> bool:
    """
    Check if an item should be skipped in resume mode.

    Args:
        safe_title: Cleaned filename-safe title
        existing_titles: Set of existing transcript titles
        resume_mode: Whether resume mode is enabled
        title: Original title for logging
        status_callback: Callback for status messages

    Returns:
        True if should skip, False otherwise
    """
    if resume_mode and safe_title in existing_titles:
        status_callback(
            f"Skipping (resume mode): {title} - transcript already exists",
            StatusType.INFO,
        )
        return True
    return False


class InputExpansionNode(Node):
    """
    Phase 1: Expand user input into video_sources_queue for processing.
    """

    def prep(self, shared):
        prep_data = {
            "user_input_path": shared["user_input_path"],
            "start_index": shared["start_index"],
            "end_index": shared["end_index"],
            "cookie_file_path": shared["cookie_file_path"],
            "status_callback": shared["status_update_callback"],
            "resume_mode": shared.get("resume_mode", False),
            "intermediate_dir": shared["intermediate_dir"],
            "input_mode_hint": shared.get("input_mode_hint"),
            "document_folder_recursive": shared.get("document_folder_recursive", True),
            "csv_jobs": shared.get("csv_jobs", []),
            "job_overrides": shared.get("job_overrides", {}),
        }

        # If resume mode is enabled, get existing transcript files for filtering
        if prep_data["resume_mode"]:
            existing_transcript_files = discover_raw_transcript_files(
                shared["intermediate_dir"]
            )
            # Extract video titles from transcript file names
            # File pattern: {safe_title}_raw_transcript.txt
            existing_titles = set()
            for file_path in existing_transcript_files:
                filename = Path(file_path).stem  # Remove .txt extension
                # Remove '_raw_transcript' suffix to get the safe title
                if filename.endswith("_raw_transcript"):
                    safe_title = filename[: -len("_raw_transcript")]
                    existing_titles.add(safe_title)
            prep_data["existing_titles"] = existing_titles
            prep_data["status_callback"](
                f"Resume mode: Found {len(existing_titles)} existing transcript files. Will retry source(s) without transcripts.",
                StatusType.INFO,
            )
        else:
            prep_data["existing_titles"] = set()

        return prep_data

    def exec(self, prep_data):
        prep_data["status_callback"]("Expanding input sources...", StatusType.INFO)

        start_index = prep_data["start_index"]
        end_index = prep_data["end_index"]
        cookie_file_path = prep_data["cookie_file_path"]
        resume_mode = prep_data["resume_mode"]
        existing_titles = prep_data["existing_titles"]
        csv_jobs = prep_data.get("csv_jobs") or []

        # Build list of (input_path, job_id). Single run: one item with job_id=0; CSV: one per job.
        if csv_jobs:
            inputs_to_expand = [(j["input"], j["job_id"]) for j in csv_jobs]
        else:
            user_input_path = (prep_data.get("user_input_path") or "").strip()
            if user_input_path == "No Input Allowed":
                prep_data["status_callback"](
                    "Phase 2 Only mode: No input processing needed", StatusType.INFO
                )
                return []
            if not user_input_path:
                inputs_to_expand = []
            else:
                inputs_to_expand = [(user_input_path, 0)]

        video_sources_queue = []
        skipped_count = 0

        for user_input_path, job_id in inputs_to_expand:
            input_mode_hint = prep_data.get("input_mode_hint") if job_id == 0 else None
            input_type = get_input_type(user_input_path, input_mode_hint)

            if input_type == "unknown_url":
                prep_data["status_callback"](
                    f"Unsupported URL type (job {job_id}): {user_input_path[:50]}...",
                    StatusType.ERROR,
                )
                continue

            if input_type == "youtube_playlist_url":
                # Get all video URLs from playlist
                urls = get_video_urls_from_playlist(user_input_path, cookie_file_path)
                if not urls:
                    prep_data["status_callback"](
                        f"Warning: No videos found in playlist (job {job_id})",
                        StatusType.WARNING,
                    )
                    continue
                
                urls = _apply_range(urls, start_index, end_index)
                prep_data["status_callback"](
                    f"Found {len(urls)} videos in playlist (job {job_id}, range {start_index}-{end_index if end_index > 0 else 'end'})",
                    StatusType.INFO,
                )

                # Get titles and create queue entries
                for url in urls:
                    meta = fetch_youtube_metadata(url, cookie_file_path)
                    title = meta.get("title") or get_video_title(url)
                    safe_title = clean_filename(title)

                    if _should_skip_resume(
                        safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                    ):
                        skipped_count += 1
                        continue

                    video_sources_queue.append(
                        {
                            "source_path": url,
                            "source_type": "youtube_url",
                            "original_title": title,
                            "channel": meta.get("channel"),
                            "upload_date": meta.get("upload_date"),
                            "tags": meta.get("tags"),
                            "duration": meta.get("duration"),
                            "job_id": job_id,
                        }
                    )

            elif input_type == "youtube_video_url":
                meta = fetch_youtube_metadata(user_input_path, cookie_file_path)
                title = meta.get("title") or get_video_title(user_input_path)
                safe_title = clean_filename(title)

                if _should_skip_resume(
                    safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                ):
                    skipped_count += 1
                else:
                    video_sources_queue.append(
                        {
                            "source_path": user_input_path,
                            "source_type": "youtube_url",
                            "original_title": title,
                            "channel": meta.get("channel"),
                            "upload_date": meta.get("upload_date"),
                            "tags": meta.get("tags"),
                            "duration": meta.get("duration"),
                            "job_id": job_id,
                        }
                    )

            elif input_type == "teams_meeting_url":
                title = derive_meeting_title(user_input_path)
                safe_title = clean_filename(title)

                if _should_skip_resume(
                    safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                ):
                    skipped_count += 1
                else:
                    prep_data["status_callback"](
                        f"Detected Teams meeting manifest URL", StatusType.INFO
                    )
                    video_sources_queue.append(
                        {
                            "source_path": user_input_path,
                            "source_type": "teams_meeting_url",
                            "original_title": title,
                            "job_id": job_id,
                        }
                    )

            elif input_type == "folder":
                files = list_video_files_in_folder(user_input_path)
                files = _apply_range(files, start_index, end_index)

                for file_path in files:
                    title = Path(file_path).stem
                    safe_title = clean_filename(title)

                    if _should_skip_resume(
                        safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                    ):
                        skipped_count += 1
                        continue

                    video_sources_queue.append(
                        {
                            "source_path": file_path,
                            "source_type": "local_file",
                            "original_title": title,
                            "job_id": job_id,
                        }
                    )

            elif input_type == "file":
                title = Path(user_input_path).stem
                safe_title = clean_filename(title)

                if _should_skip_resume(
                    safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                ):
                    skipped_count += 1
                else:
                    video_sources_queue.append(
                        {
                            "source_path": user_input_path,
                            "source_type": "local_file",
                            "original_title": title,
                            "job_id": job_id,
                        }
                    )

            elif input_type == "podcast_rss_url":
                episodes = parse_podcast_rss(user_input_path, start_index, end_index)

                if not episodes:
                    prep_data["status_callback"](
                        "No episodes found in podcast RSS feed", StatusType.ERROR
                    )
                else:
                    podcast_info = get_podcast_info(user_input_path)
                    prep_data["status_callback"](
                        f"Found {len(episodes)} episodes in podcast: {podcast_info['title']}",
                        StatusType.INFO
                    )

                    for episode in episodes:
                        title = episode["title"]
                        safe_title = clean_filename(title)

                        if _should_skip_resume(
                            safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                        ):
                            skipped_count += 1
                            continue

                        video_sources_queue.append(
                            {
                                "source_path": episode["audio_url"],
                                "source_type": "podcast_audio",
                                "original_title": title,
                                "description": episode.get("description", ""),
                                "upload_date": episode.get("pub_date", ""),
                                "duration": episode.get("duration", ""),
                                "job_id": job_id,
                            }
                        )

            elif input_type in ("text_file", "pdf_file", "word_file", "webpage_url"):
                if input_type == "webpage_url":
                    title = user_input_path.strip("/").split("/")[-1] or "webpage"
                else:
                    title = Path(user_input_path).stem
                safe_title = clean_filename(title)
                if _should_skip_resume(
                    safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                ):
                    skipped_count += 1
                else:
                    video_sources_queue.append(
                        {
                            "source_path": user_input_path,
                            "source_type": "text_document",
                            "original_title": title,
                            "job_id": job_id,
                        }
                    )

            elif input_type == "document_folder":
                recursive = prep_data.get("document_folder_recursive", True)
                files = list_document_files_in_folder(user_input_path, recursive=recursive)
                files = _apply_range(files, start_index, end_index)
                for file_path in files:
                    title = Path(file_path).stem
                    safe_title = clean_filename(title)
                    if _should_skip_resume(
                        safe_title, existing_titles, resume_mode, title, prep_data["status_callback"]
                    ):
                        skipped_count += 1
                        continue
                    video_sources_queue.append(
                        {
                            "source_path": file_path,
                            "source_type": "text_document",
                            "original_title": title,
                            "job_id": job_id,
                        }
                    )

        # Report results
        if resume_mode:
            if skipped_count > 0:
                prep_data["status_callback"](
                    f"Resume mode: Skipped {skipped_count} source(s) with existing transcripts",
                    StatusType.INFO,
                )
            if len(video_sources_queue) == 0:
                prep_data["status_callback"](
                    "Resume mode: No sources to retry. All sources already have transcripts or none found in input.",
                    StatusType.WARNING,
                )
            else:
                prep_data["status_callback"](
                    f"Resume mode: Will retry {len(video_sources_queue)} source(s) without transcripts",
                    StatusType.INFO,
                )

        prep_data["status_callback"](
            f"Found {len(video_sources_queue)} source(s) to process",
            StatusType.SUCCESS,
        )
        return video_sources_queue

    def post(self, shared, prep_res, exec_res):
        shared["video_sources_queue"] = exec_res

        if exec_res:
            return "start_parallel_acquisition"
        else:
            return "phase_1_complete_no_input"


class ParallelAcquisitionCoordinatorNode(Node):
    """
    Phase 1: Coordinate multi-processing (A/V work) and async (API calls) for all videos.
    """

    def prep(self, shared):
        asr_model_id = shared.get("asr_model_id") or "openai/gpt-4o-transcribe"
        asr_entry = get_model_by_id(asr_model_id, "asr")
        asr_config = None
        if asr_entry:
            prov = asr_entry.get("provider", "openai")
            key = shared.get("openai_api_key") if prov == "openai" else shared.get("zai_api_key")
            asr_config = {
                "provider": prov,
                "model_name": asr_entry.get("model_name", "gpt-4o-transcribe"),
                "api_key": key,
            }
            if asr_entry.get("max_chunk_duration_seconds") is not None:
                asr_config["max_chunk_duration_seconds"] = int(asr_entry["max_chunk_duration_seconds"])
        return {
            "video_sources_queue": shared["video_sources_queue"],
            "max_workers_processes": shared["max_workers_processes"],
            "config": {
                "temp_dir": shared["temp_dir"],
                "intermediate_dir": shared["intermediate_dir"],
                "openai_api_key": shared["openai_api_key"],
                "asr_config": asr_config,
                "cookie_file_path": shared["cookie_file_path"],
                "output_language": shared["output_language"],
                "disable_ai_transcribe": shared.get("disable_ai_transcribe", False),
                "save_video_on_ai_transcribe": shared.get("save_video_on_ai_transcribe", False),
            },
            "status_callback": shared["status_update_callback"],
            "progress_callback": shared["progress_update_callback"],
            "stop_check_callback": shared.get("stop_check_callback", lambda: False),
        }

    def exec(self, prep_data):
        video_sources = prep_data["video_sources_queue"]
        max_workers = prep_data["max_workers_processes"]
        config = prep_data["config"]
        status_callback = prep_data["status_callback"]
        progress_callback = prep_data["progress_callback"]
        stop_check = prep_data["stop_check_callback"]

        status_callback("Starting parallel content acquisition...", StatusType.INFO)

        # Check if stop was requested before starting
        if stop_check():
            status_callback("Processing cancelled before starting", StatusType.WARNING)
            return {}

        # Create directories
        Path(config["temp_dir"]).mkdir(parents=True, exist_ok=True)
        Path(config["intermediate_dir"]).mkdir(parents=True, exist_ok=True)

        results = {}
        total_videos = len(video_sources)
        completed = 0

        # Use ProcessPoolExecutor for multiprocessing
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_video = {
                executor.submit(
                    process_single_video_acquisition, video_data, config
                ): video_data
                for video_data in video_sources
            }

            # Process completed tasks
            for future in as_completed(future_to_video):
                # Check if stop was requested
                if stop_check():
                    status_callback("Cancelling remaining tasks...", StatusType.WARNING)
                    # Cancel remaining futures
                    for remaining_future in future_to_video:
                        if not remaining_future.done():
                            remaining_future.cancel()
                    break

                video_data = future_to_video[future]
                video_title = video_data["original_title"]

                try:
                    result = future.result()
                    results[video_title] = result

                    if result["status"] == "success":
                        status_callback(
                            f"✓ {video_title}: Content acquired successfully",
                            StatusType.SUCCESS,
                        )
                    else:
                        status_callback(
                            f"✗ {video_title}: {result['error']}", StatusType.ERROR
                        )

                except Exception as e:
                    results[video_title] = {
                        "status": "failure",
                        "video_title": video_title,
                        "transcript_file": None,
                        "transcript_text": None,
                        "error": str(e),
                    }
                    status_callback(
                        f"✗ {video_title}: Exception - {str(e)}", StatusType.ERROR
                    )

                completed += 1
                progress_percent = int((completed / total_videos) * 100)
                progress_callback(progress_percent)

        # Summary
        if stop_check():
            status_callback(
                f"Phase 1 cancelled: {completed}/{total_videos} source(s) processed before cancellation",
                StatusType.WARNING,
            )
        else:
            successful = sum(1 for r in results.values() if r["status"] == "success")
            status_callback(
                f"Phase 1 complete: {successful}/{total_videos} source(s) processed successfully",
                StatusType.INFO,
            )

        return results

    def post(self, shared, prep_res, exec_res):
        shared["phase_1_results"] = exec_res

        # Collect transcript files and (for CSV) transcript_file -> job_id mapping
        transcript_files = []
        transcript_file_to_job_id = {}
        for result in exec_res.values():
            if result["status"] == "success" and result["transcript_file"]:
                transcript_files.append(result["transcript_file"])
                transcript_file_to_job_id[result["transcript_file"]] = result.get("job_id", 0)

        shared["raw_transcript_files"] = transcript_files
        shared["transcript_file_to_job_id"] = transcript_file_to_job_id
        return "phase_1_complete"


class RefinementTaskCreatorNode(Node):
    """
    Phase 2: Create all combinations of transcript files × refinement styles.
    """

    def prep(self, shared):
        should_discover = shared.get("phase_2_only", False) or shared.get(
            "resume_mode", False
        )

        if should_discover:
            transcript_files = discover_raw_transcript_files(shared["intermediate_dir"])
        else:
            transcript_files = shared.get("raw_transcript_files", [])

        return {
            "transcript_files": transcript_files,
            "selected_styles_data": shared["selected_styles_data"],
            "output_base_dir": shared["output_base_dir"],
            "output_language": shared.get("output_language", "English"),
            "transcript_file_to_job_id": shared.get("transcript_file_to_job_id", {}),
            "job_overrides": shared.get("job_overrides", {}),
            "status_callback": shared["status_update_callback"],
            "phase_2_only": shared.get("phase_2_only", False),
            "resume_mode": shared.get("resume_mode", False),
        }

    def exec(self, prep_data):
        from collections import defaultdict
        import os

        status_callback = prep_data["status_callback"]
        transcript_files = prep_data["transcript_files"]
        output_base_dir = prep_data["output_base_dir"]
        transcript_file_to_job_id = prep_data.get("transcript_file_to_job_id") or {}
        job_overrides = prep_data.get("job_overrides") or {}
        phase_2_only = prep_data["phase_2_only"]
        resume_mode = prep_data["resume_mode"]

        if phase_2_only:
            status_callback(
                "Phase 2 Only: Creating refinement tasks from existing transcripts...",
                StatusType.INFO,
            )
        elif resume_mode:
            status_callback(
                "Resume mode: Creating refinement tasks from existing transcripts...",
                StatusType.INFO,
            )
        else:
            status_callback("Creating refinement tasks...", StatusType.INFO)

        if not transcript_files:
            status_callback(
                "No transcript files found for refinement", StatusType.ERROR
            )
            return []

        # CSV batch: group by job_id and use per-job styles/output_subdir when present
        if transcript_file_to_job_id and job_overrides:
            from prompts import text_refinement_prompts

            by_job = defaultdict(list)
            for tf in transcript_files:
                jid = transcript_file_to_job_id.get(tf, 0)
                by_job[jid].append(tf)

            tasks = []
            default_lang = prep_data.get("output_language") or "English"
            for job_id, files in by_job.items():
                overrides = job_overrides.get(job_id, {})
                style_names = overrides.get("styles")
                if style_names:
                    styles_data = [
                        (n, text_refinement_prompts[n])
                        for n in style_names
                        if n in text_refinement_prompts
                    ]
                else:
                    styles_data = prep_data["selected_styles_data"]
                if not styles_data:
                    continue
                subdir = overrides.get("output_subdir") or ""
                output_dir = os.path.join(output_base_dir, subdir) if subdir else output_base_dir
                job_lang = overrides.get("language") or default_lang
                tasks.extend(
                    create_refinement_tasks(files, styles_data, output_dir, language=job_lang)
                )
        else:
            styles_data = prep_data["selected_styles_data"]
            if not styles_data:
                status_callback("No refinement styles selected", StatusType.ERROR)
                return []
            tasks = create_refinement_tasks(transcript_files, styles_data, output_base_dir)

        status_callback(
            f"Created {len(tasks)} refinement tasks",
            StatusType.SUCCESS,
        )

        return tasks

    def post(self, shared, prep_res, exec_res):
        shared["refinement_tasks"] = exec_res

        if exec_res:
            return "start_async_refinement"
        else:
            return "phase_2_complete_no_tasks"


class AsyncRefinementCoordinatorNode(Node):
    """
    Phase 2: Process all refinement tasks concurrently using async LLM calls.
    """

    def prep(self, shared):
        phase2_model_id = shared.get("phase2_model_id") or shared.get("selected_gemini_model") or "zai/glm-7-flash"
        phase2_entry = get_model_by_id(phase2_model_id, "phase2")
        provider_config = None
        if phase2_entry:
            prov = phase2_entry.get("provider", "zai")
            key_map = {
                "gemini": shared.get("gemini_api_key"),
                "openai": shared.get("openai_api_key"),
                "deepseek": shared.get("deepseek_api_key"),
                "zai": shared.get("zai_api_key"),
            }
            provider_config = {
                "provider": prov,
                "model_name": phase2_entry.get("model_name", "glm-4.7"),
                "api_key": key_map.get(prov),
            }
        return {
            "refinement_tasks": shared["refinement_tasks"],
            "max_workers_async": shared["max_workers_async"],
            "gemini_config": {
                "api_key": shared["gemini_api_key"],
                "model_name": shared.get("selected_gemini_model"),
                "phase2_model_id": phase2_model_id,
                "provider_config": provider_config,
                "chunk_size": shared["llm_chunk_size"],
                "language": shared["output_language"],
                "intermediate_dir": shared["intermediate_dir"],
                "metadata_enhancement_enabled": shared.get("metadata_enhancement_enabled", True),
                "openai_api_key": shared.get("openai_api_key"),
                "metadata_llm_model": shared.get("metadata_llm_model", "gpt-5-nano"),
            },
            "status_callback": shared["status_update_callback"],
            "progress_callback": shared["progress_update_callback"],
            "stop_check_callback": shared.get("stop_check_callback", lambda: False),
        }

    def exec(self, prep_data):
        tasks = prep_data["refinement_tasks"]
        max_workers = prep_data["max_workers_async"]
        gemini_config = prep_data["gemini_config"]
        status_callback = prep_data["status_callback"]
        progress_callback = prep_data["progress_callback"]
        stop_check = prep_data["stop_check_callback"]

        # Check if stop was requested before starting
        if stop_check():
            status_callback("Phase 2 cancelled before starting", StatusType.WARNING)
            return {}

        status_callback("Starting async refinement processing...", StatusType.INFO)

        # Run async processing
        results = asyncio.run(
            self._process_refinement_tasks_async(
                tasks, max_workers, gemini_config, status_callback, progress_callback, stop_check
            )
        )

        # Summary
        if stop_check():
            completed = sum(1 for r in results.values() if r["status"] in ["success", "failure"])
            status_callback(
                f"Phase 2 cancelled: {completed}/{len(tasks)} refinements processed before cancellation",
                StatusType.WARNING,
            )
        else:
            successful = sum(1 for r in results.values() if r["status"] == "success")
            status_callback(
                f"Phase 2 complete: {successful}/{len(tasks)} refinements completed successfully",
                StatusType.INFO,
            )

        return results

    async def _process_refinement_tasks_async(
        self, tasks, max_workers, gemini_config, status_callback, progress_callback, stop_check
    ):
        """Async helper method to process refinement tasks concurrently."""
        semaphore = asyncio.Semaphore(max_workers)
        results = {}
        total_tasks = len(tasks)
        completed = 0

        async def process_single_task(task):
            nonlocal completed
            
            # Check if stop was requested before processing this task
            if stop_check():
                return {
                    "status": "cancelled",
                    "task_id": f"{task['video_title']}_{task['style_name']}",
                    "output_file": task["output_file"],
                    "video_title": task["video_title"],
                    "style_name": task["style_name"],
                    "error": "Cancelled by user",
                }
            
            async with semaphore:
                try:
                    result = await async_refine_single_task(task, gemini_config)

                    if result["status"] == "success":
                        status_callback(
                            f"✓ {result['video_title']} [{result['style_name']}]: Refinement completed",
                            StatusType.SUCCESS,
                        )
                    else:
                        status_callback(
                            f"✗ {result['video_title']} [{result['style_name']}]: {result['error']}",
                            StatusType.ERROR,
                        )

                    completed += 1
                    progress_percent = int((completed / total_tasks) * 100)
                    progress_callback(progress_percent)

                    return result

                except Exception as e:
                    completed += 1
                    progress_percent = int((completed / total_tasks) * 100)
                    progress_callback(progress_percent)

                    error_result = {
                        "status": "failure",
                        "task_id": f"{task['video_title']}_{task['style_name']}",
                        "output_file": task["output_file"],
                        "video_title": task["video_title"],
                        "style_name": task["style_name"],
                        "error": str(e),
                    }
                    status_callback(
                        f"✗ {task['video_title']} [{task['style_name']}]: Exception - {str(e)}",
                        StatusType.ERROR,
                    )
                    return error_result

        # Process all tasks concurrently
        task_futures = [process_single_task(task) for task in tasks]
        task_results = await asyncio.gather(*task_futures)

        # Build results dictionary
        for result in task_results:
            results[result["task_id"]] = result

        return results

    def post(self, shared, prep_res, exec_res):
        shared["phase_2_results"] = exec_res

        # Build final outputs summary
        final_outputs = []
        for result in exec_res.values():
            final_outputs.append(
                {
                    "input": result["video_title"],
                    "style": result["style_name"],
                    "output_md_path": result["output_file"],
                    "status": result["status"],
                }
            )

        shared["final_outputs_summary"] = final_outputs
        return "phase_2_complete"


class TempFileCleanupNode(Node):
    """
    Utility: Clean up temporary audio files and chunks after Phase 1 completion.
    """

    def prep(self, shared):
        return {
            "temp_dir": shared["temp_dir"],
            "status_callback": shared["status_update_callback"],
        }

    def exec(self, prep_data):
        temp_dir = prep_data["temp_dir"]
        status_callback = prep_data["status_callback"]

        if Path(temp_dir).exists():
            import shutil

            shutil.rmtree(temp_dir)
            status_callback("Temporary files cleaned up", StatusType.INFO)

        return "cleanup_complete"

    def post(self, shared, prep_res, exec_res):
        return "cleanup_complete"


class FlowCompletionNode(Node):
    """
    Utility: Generate final summary and complete the flow.
    """

    def prep(self, shared):
        return {
            "final_outputs_summary": shared.get("final_outputs_summary", []),
            "phase_1_results": shared.get("phase_1_results", {}),
            "phase_2_results": shared.get("phase_2_results", {}),
            "run_phase_1": shared.get("run_phase_1", False),
            "run_phase_2": shared.get("run_phase_2", False),
            "status_callback": shared["status_update_callback"],
        }

    def exec(self, prep_data):
        final_outputs = prep_data["final_outputs_summary"]
        phase_1_results = prep_data["phase_1_results"]
        phase_2_results = prep_data["phase_2_results"]
        run_phase_1 = prep_data["run_phase_1"]
        run_phase_2 = prep_data["run_phase_2"]
        status_callback = prep_data["status_callback"]

        # Generate comprehensive summary
        summary_lines = ["=== BodhiFlow Processing Complete ==="]

        if run_phase_1:
            phase_1_successful = sum(
                1 for r in phase_1_results.values() if r["status"] == "success"
            )
            summary_lines.append(
                f"Phase 1 (Content Acquisition): {phase_1_successful}/{len(phase_1_results)} source(s) processed"
            )

        if run_phase_2:
            phase_2_successful = sum(
                1 for r in phase_2_results.values() if r["status"] == "success"
            )
            summary_lines.append(
                f"Phase 2 (Content Refinement): {phase_2_successful}/{len(phase_2_results)} refinements completed"
            )

            # List successful outputs
            successful_outputs = [o for o in final_outputs if o["status"] == "success"]
            if successful_outputs:
                summary_lines.append("<br>Generated Files:")
                for output in successful_outputs:
                    summary_lines.append(
                        f"  • {output['input']} [{output['style']}] → {Path(output['output_md_path']).name}"
                    )

        summary_message = "<br>".join(summary_lines)
        status_callback(summary_message, StatusType.SUCCESS)

        return summary_message

    def post(self, shared, prep_res, exec_res):
        return "flow_complete"
