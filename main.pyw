"""
Main entry point for BodhiFlow - Content to Wisdom converter.

This module provides both GUI and command-line interfaces for the BodhiFlow application,
which converts videos, audio, podcasts, and documents to refined knowledge documents.
"""

import logging
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QFile, QTextStream, QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMainWindow

# Add the current directory to Python path for imports
sys.path.append(str(Path(__file__).parent))

from flow import create_flow_for_phases
from gui.main_window import BodhiFlow_GUI_MainWindow
from utils.constants import StatusType, STATUS_TO_LOG_LEVEL
from utils.logger_config import get_logger

# Initialize logger for this module
logger = get_logger(__name__)

# Mapping from StatusType to logging level (alias for backward compatibility)
STATUS_TYPE_TO_LOG_LEVEL = STATUS_TO_LOG_LEVEL


class PocketFlowRunner(QThread):
    """
    QThread that runs the PocketFlow in the background while keeping GUI responsive.

    Signals:
        status_update: Emitted when a status message needs to be displayed
        progress_update: Emitted when progress percentage changes
        flow_complete: Emitted when the entire flow is finished
    """

    # Define Qt signals for communication with GUI
    status_update = pyqtSignal(str, StatusType)  # (message, type as StatusType enum)
    progress_update = pyqtSignal(int)  # progress_percent
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
            # Initialize shared memory from GUI parameters
            shared_memory = self._initialize_shared_memory()
            
            # Add stop check callback to shared memory
            shared_memory["stop_check_callback"] = self._check_stop_requested

            # Create appropriate flow based on phase flags
            flow = create_flow_for_phases(
                shared_memory["run_phase_1"], shared_memory["run_phase_2"]
            )

            # Run the flow
            flow.run(shared_memory)

            # Signal completion only if not stopped
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

        # Create thread-safe callbacks that emit Qt signals
        def status_callback(message: str, msg_type: StatusType):
            # Emit signal to GUI
            self.status_update.emit(message, msg_type)

            # Also log to file using appropriate logging level
            log_level = STATUS_TYPE_TO_LOG_LEVEL.get(msg_type, logging.INFO)
            logger.log(log_level, f"[GUI] {message}")

        def progress_callback(progress_percent: int):
            self.progress_update.emit(progress_percent)
            # Log progress updates at DEBUG level to avoid noise
            logger.debug(f"[GUI] Progress: {progress_percent}%")

        # CSV batch: parse CSV and populate csv_jobs / job_overrides when csv_path is set
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
                # Leave csv_jobs empty so flow can report or fail

        # Build shared memory from flow_params
        shared_memory = {
            # User inputs from GUI
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
            "disable_ai_transcribe": self.flow_params.get("disable_ai_transcribe", False),
            "selected_gemini_model": self.flow_params.get(
                "selected_gemini_model", "gemini-2.5-flash"
            ),
            # Metadata controls
            "metadata_enhancement_enabled": self.flow_params.get(
                "metadata_enhancement_enabled", True
            ),
            "metadata_llm_model": self.flow_params.get(
                "metadata_llm_model", "gpt-5-nano"
            ),
            # Phase control
            "run_phase_1": self.flow_params.get("run_phase_1", True),
            "run_phase_2": self.flow_params.get("run_phase_2", True),
            "phase_1_only": self.flow_params.get("phase_1_only", False),
            "phase_2_only": self.flow_params.get("phase_2_only", False),
            "max_workers_processes": self.flow_params.get("max_workers_processes", 4),
            "max_workers_async": self.flow_params.get("max_workers_async", 10),
            # GUI callbacks (thread-safe via Qt signals)
            "status_update_callback": status_callback,
            "progress_update_callback": progress_callback,
            # Flow state (initialized empty)
            "video_sources_queue": [],
            "raw_transcript_files": [],
            "phase_1_results": {},
            "refinement_tasks": [],
            "phase_2_results": {},
            "final_outputs_summary": [],
        }

        return shared_memory


def launch_gui():
    """Launch the PyQt5 GUI application."""
    app = QApplication(sys.argv)

    # --- Load Stylesheet --- #
    # Construct path to styles.qss (assuming it's in the gui/ subdirectory)
    script_dir = Path(__file__).parent  # This is BodhiFlow/ directory
    stylesheet_path = script_dir / "gui" / "styles.qss"

    try:
        with open(stylesheet_path, "r") as f:
            app.setStyleSheet(f.read())
            logger.info(f"Stylesheet loaded from: {stylesheet_path}")
    except FileNotFoundError:
        logger.warning(
            f"Stylesheet not found at: {stylesheet_path}. Using default styles."
        )
    except Exception as e:
        logger.error(f"Error loading stylesheet: {e}")
    # --- End Stylesheet Loading --- #

    # Set application properties
    app.setApplicationName("BodhiFlow")
    app.setApplicationDisplayName("BodhiFlow - Content to Wisdom")
    app.setApplicationVersion("2.0")

    # Create and show main window
    main_window = BodhiFlow_GUI_MainWindow()
    main_window.show()

    # Start the application event loop
    sys.exit(app.exec_())


def main_cli():
    """
    Command-line interface for BodhiFlow (future implementation).

    For now, this launches the GUI. CLI support can be added later.
    """
    logger.info("BodhiFlow - Content to Wisdom Converter")
    logger.info("Launching GUI interface...")
    launch_gui()


if __name__ == "__main__":
    # Check if we're in GUI mode or CLI mode
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        main_cli()
    else:
        launch_gui()
