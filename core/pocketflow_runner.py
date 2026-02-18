"""
PocketFlow runner thread for BodhiFlow.

Runs the PocketFlow in a QThread so the GUI stays responsive.
Import from here instead of main.pyw for cross-platform stability (.pyw is not
importable as a module on many platforms).
"""

import logging
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal

# Allow imports from project root when run as script or from main.pyw
if __name__ != "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from .flow import create_flow_for_phases
from utils.constants import StatusType, STATUS_TO_LOG_LEVEL
from utils.logger_config import get_logger
from utils.models_config import get_asr_model_max_concurrency

logger = get_logger(__name__)


def _capped_phase1_workers(requested: int, asr_model_id: str | None) -> int:
    """Cap Phase 1 parallel workers by ASR model max_concurrency (e.g. ZAI GLM-ASR-2512 allows 5)."""
    cap = get_asr_model_max_concurrency(asr_model_id or "")
    if cap is None:
        return requested
    return min(requested, cap)


class PocketFlowRunner(QThread):
    """
    QThread that runs the PocketFlow in the background while keeping GUI responsive.

    Signals:
        status_update: Emitted when a status message needs to be displayed
        progress_update: Emitted when progress percentage changes
        flow_complete: Emitted when the entire flow is finished
    """

    status_update = pyqtSignal(str, object)  # (message, StatusType)
    progress_update = pyqtSignal(int)
    flow_complete = pyqtSignal()

    def __init__(self, flow_params):
        super().__init__()
        self.flow_params = flow_params
        self._stop_requested = False

    def stop(self):
        """Request the thread to stop processing."""
        self._stop_requested = True
        self.status_update.emit("Cancellation requested...", StatusType.WARNING)

    def run(self):
        """Execute the PocketFlow in this worker thread."""
        try:
            shared_memory = self._initialize_shared_memory()
            shared_memory["stop_check_callback"] = self._check_stop_requested

            flow = create_flow_for_phases(
                shared_memory["run_phase_1"], shared_memory["run_phase_2"]
            )
            flow.run(shared_memory)

            if not self._stop_requested:
                self.flow_complete.emit()
            else:
                self.status_update.emit("Processing cancelled by user", StatusType.WARNING)

        except Exception as e:
            if not self._stop_requested:
                self.status_update.emit(f"Flow execution error: {str(e)}", StatusType.ERROR)

    def _check_stop_requested(self):
        """Check if stop has been requested. Returns True if should stop."""
        return self._stop_requested

    def _initialize_shared_memory(self):
        """Initialize shared memory structure from GUI flow_params."""
        status_type_to_log_level = STATUS_TO_LOG_LEVEL

        def status_callback(message: str, msg_type: StatusType):
            self.status_update.emit(message, msg_type)
            log_level = status_type_to_log_level.get(msg_type, logging.INFO)
            logger.log(log_level, f"[GUI] {message}")

        def progress_callback(progress_percent: int):
            self.progress_update.emit(progress_percent)
            logger.debug(f"[GUI] Progress: {progress_percent}%")

        csv_path = self.flow_params.get("csv_path")
        csv_jobs = []
        job_overrides = {}
        if csv_path and os.path.isfile(csv_path):
            try:
                from utils.csv_batch import parse_bodhiflow_csv
                csv_jobs = parse_bodhiflow_csv(csv_path)
                job_overrides = {
                    j["job_id"]: {
                        "styles": j["styles"],
                        "language": j["language"],
                        "output_subdir": j["output_subdir"],
                    }
                    for j in csv_jobs
                }
            except (ValueError, FileNotFoundError) as e:
                logger.error(f"CSV parse failed: {e}")

        shared_memory = {
            "user_input_path": self.flow_params.get("user_input_path"),
            "input_mode_hint": self.flow_params.get("input_mode_hint"),
            "document_folder_recursive": self.flow_params.get("document_folder_recursive", True),
            "csv_path": csv_path,
            "csv_jobs": csv_jobs,
            "job_overrides": job_overrides,
            "cookie_file_path": self.flow_params.get("cookie_file_path"),
            "selected_styles_data": self.flow_params.get("selected_styles_data", []),
            "output_language": self.flow_params.get("output_language", "English"),
            "gemini_api_key": self.flow_params.get("gemini_api_key"),
            "openai_api_key": self.flow_params.get("openai_api_key"),
            "zai_api_key": self.flow_params.get("zai_api_key"),
            "deepseek_api_key": self.flow_params.get("deepseek_api_key"),
            "asr_model_id": self.flow_params.get("asr_model_id"),
            "phase2_model_id": self.flow_params.get("phase2_model_id"),
            "output_base_dir": self.flow_params.get("output_base_dir", "./output"),
            "intermediate_dir": self.flow_params.get(
                "intermediate_dir", "./intermediate_transcripts"
            ),
            "temp_dir": self.flow_params.get("temp_dir", "./temp_bodhiflow"),
            "start_index": self.flow_params.get("start_index", 1),
            "end_index": self.flow_params.get("end_index", 0),
            "llm_chunk_size": self.flow_params.get("llm_chunk_size", 70000),
            "resume_mode": self.flow_params.get("resume_mode", False),
            "phase2_skip_existing": self.flow_params.get("phase2_skip_existing", False),
            "disable_ai_transcribe": self.flow_params.get("disable_ai_transcribe", False),
            "save_video_on_ai_transcribe": self.flow_params.get("save_video_on_ai_transcribe", False),
            "selected_gemini_model": self.flow_params.get(
                "selected_gemini_model", "gemini-2.5-flash"
            ),
            "metadata_enhancement_enabled": self.flow_params.get(
                "metadata_enhancement_enabled", True
            ),
            "metadata_llm_model": self.flow_params.get(
                "metadata_llm_model", "gpt-5-nano"
            ),
            "run_phase_1": self.flow_params.get("run_phase_1", True),
            "run_phase_2": self.flow_params.get("run_phase_2", True),
            "phase_1_only": self.flow_params.get("phase_1_only", False),
            "phase_2_only": self.flow_params.get("phase_2_only", False),
            "max_workers_processes": _capped_phase1_workers(
                self.flow_params.get("max_workers_processes", 4),
                self.flow_params.get("asr_model_id"),
            ),
            "max_workers_async": self.flow_params.get("max_workers_async", 10),
            "status_update_callback": status_callback,
            "progress_update_callback": progress_callback,
            "video_sources_queue": [],
            "raw_transcript_files": [],
            "phase_1_results": {},
            "refinement_tasks": [],
            "phase_2_results": {},
            "final_outputs_summary": [],
        }
        return shared_memory
