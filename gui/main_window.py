"""
BodhiFlow - Content to Wisdom Converter

This application extracts content from YouTube videos, local videos, audio files,
and processes them using AI to create well-formatted, insightful documents.

Main features:
- Extract transcripts from YouTube playlists or single videos
- Process local video and audio files
- Transform text with multiple refinement styles (Summary, Educational, etc.)
- Configure chunk size for optimal API processing
- Customize output language
- Select specific video ranges from playlists
- Save outputs to customizable locations

The application uses PocketFlow for sophisticated content processing workflows:
1. Phase 1: Content acquisition (transcript download or STT)
2. Phase 2: Content refinement using advanced language models

Requirements:
- PyQt5 for the user interface
- PocketFlow for workflow orchestration
- OpenAI API for speech-to-text conversion
- Google Gemini API for text processing
"""

import logging
import os

from dotenv import load_dotenv
from PyQt5.QtCore import QFile, Qt, QTextStream, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.prompts import text_refinement_prompts
from utils.logger_config import get_logger
from utils.ui_config import get_ui_config
from utils.models_config import (
    get_asr_models,
    get_phase2_models,
    get_default_asr_id,
    get_default_phase2_id,
    get_phase2_model_max_concurrency,
)

# Initialize logger for this module
gui_logger = get_logger(__name__)


# Import StatusType from shared constants module
from utils.constants import StatusType, STATUS_COLORS, STATUS_TO_LOG_LEVEL

# Alias for backward compatibility (GUI-specific usage)
GUI_STATUS_TO_LOG_LEVEL = STATUS_TO_LOG_LEVEL

load_dotenv(".env")  # This might need adjustment based on BodhiFlow's .env location


class BodhiFlow_GUI_MainWindow(QMainWindow):  # Renamed class
    """
    Main application window for BodhiFlow - Content to Wisdom converter.

    This class provides the user interface for the application, allowing users to:
    - Input a YouTube playlist/video URL or local file/folder path
    - Specify language and processing preferences
    - Select refinement styles for text processing
    - Configure output settings
    - View progress and status updates during processing

    The application uses PocketFlow to manage:
    - Content acquisition (transcript download or STT for various sources)
    - Audio extraction and STT for local files
    - Text refinement using advanced language models
    """

    def __init__(self):
        super().__init__()
        self.ui_config = get_ui_config()
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.prompts = text_refinement_prompts  # From prompts.py at project root
        self.extraction_thread = None  # To be replaced by PocketFlow
        self.gemini_thread = None  # To be replaced by PocketFlow
        self.is_processing = False

        # These model selection parts might be simplified or driven by PocketFlow config
        self.available_models = [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ]
        self.selected_model_name = "gemini-2.5-flash"

        self.initUI()

    @pyqtSlot(int)
    def update_chunk_size_label(self, value):
        """
        Updates the displayed chunk size value when the slider is moved.

        Args:
            value (int): The new chunk size value from the slider
        """
        self.chunk_size_value_label.setText(str(value))

    @pyqtSlot(bool)
    def on_phase_1_only_toggled(self, checked):
        """
        Handle Phase 1 Only checkbox toggle.
        """
        if checked:
            # Uncheck Phase 2 Only (mutual exclusion)
            self.phase_2_only_checkbox.setChecked(False)
            # Reset URL input to normal state if it was disabled
            if self.url_input.text() == "No Input Allowed":
                self.url_input.clear()
                self.url_input.setPlaceholderText(
                    "Enter YouTube URL (playlist/video), Podcast RSS URL, or local video/folder path"
                )
            self.url_input.setEnabled(True)

    @pyqtSlot(bool)
    def on_phase_2_only_toggled(self, checked):
        """
        Handle Phase 2 Only checkbox toggle.
        """
        if checked:
            # Uncheck Phase 1 Only (mutual exclusion)
            self.phase_1_only_checkbox.setChecked(False)
            # Disable and clear URL input
            self.url_input.setText("No Input Allowed")
            self.url_input.setEnabled(False)
        else:
            # Re-enable URL input if unchecked
            if self.url_input.text() == "No Input Allowed":
                self.url_input.clear()
                self.url_input.setPlaceholderText(
                    "Enter YouTube URL (playlist/video), Podcast RSS URL, or local video/folder path"
                )
            self.url_input.setEnabled(True)

    def initUI(self):
        """
        Initializes the user interface of the application.

        Creates and configures all UI components including:
        - Input fields for content sources, language, and API settings
        - Refinement style selection checkboxes
        - Chunk size slider for controlling text processing
        - File input/output selection fields
        - Progress display and status windows
        - Control buttons for starting/canceling operations

        Also applies style settings and layouts to create a modern UI appearance.
        """
        self.setWindowTitle("BodhiFlow: Transform Content into Wisdom")
        self.setMinimumSize(900, 850)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Title Section
        title_label = QLabel("BodhiFlow: Content to Wisdom Converter")
        title_label.setObjectName("TitleLabel")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # Input Container
        input_container = QWidget()
        input_container.setObjectName("InputContainer")
        input_layout = QVBoxLayout(input_container)
        input_layout.setSpacing(8)

        # URL/Path Input with Phase Controls
        url_and_phase_layout = QHBoxLayout()
        url_and_phase_layout.setSpacing(15)

        # Left side: URL Input + folder type buttons (takes most space)
        url_layout = QVBoxLayout()
        url_label = QLabel(
            "Input Source (YouTube | Teams Recording | Podcast | Local File/Folder):"
        )
        url_label.setObjectName("UrlLabel")
        url_label.setToolTip("URL or path: YouTube video/playlist, Teams manifest, Podcast RSS, or local file/folder. Use folder buttons for media or document folders.")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Enter YouTube URL (playlist/video), Teams videomanifest URL, Podcast RSS URL, or local video/folder path"
        )
        self.url_input.setToolTip("Paste a URL or path. For batch mode, use the CSV file option instead.")
        self.input_mode_hint = None  # "media_folder" | "document_folder" when set by folder buttons
        self.url_input.textChanged.connect(self._on_url_input_changed)

        self.batch_checkbox = QCheckBox("Batch CSV")
        self.batch_checkbox.setChecked(self.ui_config["options"]["batch_csv"])
        self.batch_checkbox.setToolTip("Run multiple jobs from a CSV file; check to show CSV path and select file.")
        self.batch_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.batch_checkbox.toggled.connect(self._on_batch_checkbox_changed)

        self.csv_path = None  # set when user selects CSV file
        self.csv_path_display = QLineEdit()
        self.csv_path_display.setReadOnly(True)
        self.csv_path_display.setPlaceholderText("No CSV selected")
        self.csv_path_display.setToolTip("Path to the selected CSV file. Columns: url/path, optional style_ids, language, output_subdir.")
        self.btn_select_csv = QPushButton("Select CSV File")
        self.btn_select_csv.setToolTip("Choose a CSV file that lists multiple inputs and optional per-row overrides (styles, language, output subdir).")
        self.btn_select_csv.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.btn_select_csv.clicked.connect(self._on_select_csv_file)

        # First row: Input path + Recursive (above folder buttons) + folder buttons
        url_row = QHBoxLayout()
        url_row.addWidget(self.url_input)
        self.document_folder_recursive_checkbox = QCheckBox("Recursive📂🔍")
        self.document_folder_recursive_checkbox.setChecked(
            self.ui_config["options"]["document_folder_recursive"]
        )
        self.document_folder_recursive_checkbox.setToolTip("When selecting a folder (media or documents), include subdirectories (default: on).")
        self.document_folder_recursive_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.document_folder_recursive_checkbox.setVisible(False)  # Shown when media or document folder selected
        url_row.addWidget(self.document_folder_recursive_checkbox)
        self.btn_media_folder = QPushButton(r"📁⟩🎥")
        self.btn_media_folder.setToolTip("Pick a folder containing video/audio files to process")
        self.btn_media_folder.setFixedWidth(55)
        self.btn_media_folder.setFixedHeight(30)
        self.btn_media_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px; }")  # Light gray
        self.btn_media_folder.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.btn_media_folder.clicked.connect(self._on_select_media_folder)
        self.btn_doc_folder = QPushButton(r"📁⟩📖")
        self.btn_doc_folder.setToolTip("Pick a folder containing PDF/Word/TXT documents for text extraction")
        self.btn_doc_folder.setFixedWidth(55)
        self.btn_doc_folder.setFixedHeight(30)
        self.btn_doc_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px; }")  # Light gray
        self.btn_doc_folder.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.btn_doc_folder.clicked.connect(self._on_select_document_folder)
        url_row.addWidget(self.btn_media_folder)
        url_row.addWidget(self.btn_doc_folder)

        csv_row_layout = QHBoxLayout()
        csv_file_label = QLabel("CSV file:")
        csv_file_label.setToolTip("Batch input: one row per job. Required columns: url or path. Optional: style_ids, language, output_subdir.")
        csv_row_layout.addWidget(csv_file_label)
        csv_row_layout.addWidget(self.csv_path_display)
        csv_row_layout.addWidget(self.btn_select_csv)
        csv_row_container = QWidget()
        csv_row_container.setLayout(csv_row_layout)
        csv_row_container.setVisible(False)
        self.csv_row_container = csv_row_container

        url_layout.addWidget(url_label)
        url_layout.addSpacing(3)
        url_layout.addLayout(url_row)
        url_layout.addWidget(csv_row_container)
        url_and_phase_layout.addLayout(url_layout, 3)  # Takes 3/4 of space

        # Right side: Phase Control Checkboxes
        phase_control_layout = QVBoxLayout()
        phase_control_layout.setSpacing(6)

        phase_label = QLabel("Phase Control:")
        phase_label.setObjectName("PhaseControlLabel")
        phase_label.setToolTip("Run only Phase 1 (transcripts) or only Phase 2 (refinement from existing transcripts). Default: both.")
        phase_control_layout.addWidget(phase_label)

        self.phase_1_only_checkbox = QCheckBox("Phase 1: Get Transcripts Only")
        self.phase_1_only_checkbox.setObjectName("Phase1OnlyCheckbox")
        self.phase_1_only_checkbox.setToolTip("Acquire transcripts only; skip refinement. Useful for building a transcript library.")
        self.phase_1_only_checkbox.toggled.connect(self.on_phase_1_only_toggled)
        phase_control_layout.addWidget(self.phase_1_only_checkbox)

        self.phase_2_only_checkbox = QCheckBox(
            "Phase 2: Refine Downloaded Transcripts Only"
        )
        self.phase_2_only_checkbox.setObjectName("Phase2OnlyCheckbox")
        self.phase_2_only_checkbox.setToolTip("Refine existing transcripts in the Intermediate folder only; skip Phase 1.")
        self.phase_2_only_checkbox.toggled.connect(self.on_phase_2_only_toggled)
        phase_control_layout.addWidget(self.phase_2_only_checkbox)

        url_and_phase_layout.addLayout(phase_control_layout, 1)  # Takes 1/4 of space

        input_layout.addLayout(url_and_phase_layout)

        # Language, Video Range, and Options in one row - three independent sections
        lang_range_options_layout = QHBoxLayout()
        lang_range_options_layout.setSpacing(10)

        # Section 1: Output Language (left side)
        language_layout = QVBoxLayout()
        language_layout.setSpacing(6)
        language_label = QLabel("Output Language:")
        language_label.setObjectName("LanguageLabel")
        language_label.setToolTip("Target language for refined output (e.g. English, 简体中文). Used by refinement prompts.")
        language_label.setAlignment(Qt.AlignTop)  # Align to top for consistent baseline
        self.language_input = QLineEdit()
        self.language_input.setPlaceholderText("e.g., English, Spanish, French")
        self.language_input.setToolTip("Type the desired output language name. Can be overridden per job in CSV batch mode.")
        # Consider how .env is handled in BodhiFlow project structure
        self.language_input.setText(self.ui_config["language"])
        self.language_input.setFixedHeight(32)  # Set fixed height for alignment
        language_layout.addWidget(language_label)
        language_layout.addSpacing(3)
        language_layout.addWidget(self.language_input)
        lang_range_options_layout.addLayout(language_layout)

        # Section 2: Video Range (middle)
        index_container = QVBoxLayout()
        index_container.setSpacing(6)
        index_label = QLabel("Video Range (for Playlists/Folders):")
        index_label.setObjectName("IndexLabel")
        index_label.setToolTip("For playlists or media folders: process only items from Start to End index. 0 = all.")
        index_label.setAlignment(Qt.AlignTop)  # Align to top for consistent baseline
        index_container.addWidget(index_label)
        index_container.addSpacing(3)

        # Start/End inputs in a horizontal layout
        start_end_layout = QHBoxLayout()
        start_end_layout.setSpacing(5)
        start_label = QLabel("Start:")
        start_label.setObjectName("StartLabel")
        start_label.setToolTip("First item index (1-based).")
        self.start_index_input = QLineEdit()
        self.start_index_input.setPlaceholderText("1")
        self.start_index_input.setText(self.ui_config["start_index"])
        self.start_index_input.setFixedWidth(60)
        self.start_index_input.setFixedHeight(32)  # Set fixed height for alignment
        end_label = QLabel("End (0 for all):")
        end_label.setObjectName("EndLabel")
        end_label.setToolTip("Last item index (1-based). Use 0 to process all items.")
        self.end_index_input = QLineEdit()
        self.end_index_input.setPlaceholderText("0")
        self.end_index_input.setText(self.ui_config["end_index"])
        self.end_index_input.setFixedWidth(60)
        self.end_index_input.setFixedHeight(32)  # Set fixed height for alignment
        start_end_layout.addWidget(start_label)
        start_end_layout.addWidget(self.start_index_input)
        start_end_layout.addWidget(end_label)
        start_end_layout.addWidget(self.end_index_input)
        start_end_layout.addStretch(1)
        index_container.addLayout(start_end_layout)
        lang_range_options_layout.addLayout(index_container)

        # Section 3: Options
        options_container = QVBoxLayout()
        options_container.setSpacing(6)
        options_label = QLabel("Options:")
        options_label.setObjectName("PhaseControlLabel")  # Use same ObjectName as Phase Control for consistent styling
        options_label.setToolTip("Batch CSV, resume, save video, metadata enhancement, and ASR/recursive options.")
        options_label.setAlignment(Qt.AlignTop)  # Align to top for consistent baseline
        options_container.addWidget(options_label)
        options_container.addSpacing(3)

        # 2x3 grid layout for checkboxes (2 rows, 3 columns)
        options_grid = QGridLayout()
        options_grid.setSpacing(5)
        options_grid.setColumnStretch(0, 1)
        options_grid.setColumnStretch(1, 1)
        options_grid.setColumnStretch(2, 1)
        
        self.metadata_enhance_checkbox = QCheckBox("Metadata")
        self.metadata_enhance_checkbox.setChecked(
            self.ui_config["options"]["metadata_enhance"]
        )
        self.metadata_enhance_checkbox.setToolTip("Enhance missing description/tags via gpt-5-nano")
        self.metadata_enhance_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        
        self.resume_checkbox = QCheckBox("Resume from Last Run")
        self.resume_checkbox.setObjectName("ResumeCheckbox")
        self.resume_checkbox.setChecked(self.ui_config["options"]["resume"])
        self.resume_checkbox.setToolTip("Skip items that already have a transcript in the Intermediate folder; retry only failed or new items.")
        self.resume_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        
        self.disable_ai_transcribe_checkbox = QCheckBox("Disable AI Audio Transcribe")
        self.disable_ai_transcribe_checkbox.setObjectName("DisableAITranscribeCheckbox")
        self.disable_ai_transcribe_checkbox.setChecked(
            self.ui_config["options"]["disable_ai_transcribe"]
        )
        self.disable_ai_transcribe_checkbox.setToolTip(
            "When enabled, YouTube videos will only use downloaded transcripts.\n"
            "No AI audio transcription fallback will be used, saving costs."
        )
        self.disable_ai_transcribe_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        
        self.save_video_checkbox = QCheckBox("Save Video")
        self.save_video_checkbox.setObjectName("SaveVideoCheckbox")
        self.save_video_checkbox.setChecked(
            self.ui_config["options"]["save_video"]
        )
        self.save_video_checkbox.setToolTip(
            "When enabled, if fallback to AI transcription is used, the downloaded video or audio file "
            "is moved from temp to the Intermediate Transcript Folder for preservation."
        )
        self.save_video_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        
        self.phase2_skip_existing_checkbox = QCheckBox("🔒Existing .md")
        self.phase2_skip_existing_checkbox.setChecked(
            self.ui_config["options"]["phase2_skip_existing"]
        )
        self.phase2_skip_existing_checkbox.setToolTip("When re-running: skip Phase 2 refinement for outputs that already exist; do not overwrite.")
        self.phase2_skip_existing_checkbox.setAttribute(Qt.WA_AlwaysShowToolTips, True)

        # Arrange in 2x3 grid:
        # Row 0: Batch CSV, Resume from Last Run, Save Video
        # Row 1: Metadata, Disable AI Audio Transcribe, 🔒Existing .md
        # Set alignment to ensure vertical alignment within columns
        options_grid.addWidget(self.batch_checkbox, 0, 0, Qt.AlignLeft | Qt.AlignTop)
        options_grid.addWidget(self.resume_checkbox, 0, 1, Qt.AlignLeft | Qt.AlignTop)
        options_grid.addWidget(self.save_video_checkbox, 0, 2, Qt.AlignLeft | Qt.AlignTop)
        options_grid.addWidget(self.metadata_enhance_checkbox, 1, 0, Qt.AlignLeft | Qt.AlignTop)
        options_grid.addWidget(self.disable_ai_transcribe_checkbox, 1, 1, Qt.AlignLeft | Qt.AlignTop)
        options_grid.addWidget(self.phase2_skip_existing_checkbox, 1, 2, Qt.AlignLeft | Qt.AlignTop)

        options_container.addLayout(options_grid)
        lang_range_options_layout.addLayout(options_container, 1)  # Options section takes remaining space and aligns right
        
        # Set stretch factors: Language=1, Video Range=2, Options=1 (with stretch inside)
        lang_range_options_layout.setStretch(0, 1)
        lang_range_options_layout.setStretch(1, 2)
        lang_range_options_layout.setStretch(2, 1)
        
        input_layout.addLayout(lang_range_options_layout)

        # --- Refinement Styles in horizontal layout ---
        style_groupbox = QGroupBox("Refinement Styles (Select one or more)")
        style_groupbox.setToolTip("Choose one or more styles; each produces a separate output file per source (e.g. Summary, Educational, Q&A).")
        style_layout = QGridLayout()
        style_layout.setSpacing(0)

        # Store checkboxes in a dict for easy reference
        self.style_checkboxes = {}
        style_keys = list(self.prompts.keys())

        # Arrange checkboxes in a grid layout - 3 columns
        row, col = 0, 0
        columns = 3
        style_defaults = self.ui_config["default_checked_styles"]
        for style_name in style_keys:
            cb = QCheckBox(style_name)
            checked = (
                style_defaults.get(style_name, False)
                if isinstance(style_defaults, dict)
                else style_name in (style_defaults or [])
            )
            if checked:
                cb.setChecked(True)
            style_layout.addWidget(cb, row, col)
            self.style_checkboxes[style_name] = cb
            col += 1
            if col >= columns:
                col = 0
                row += 1

        style_groupbox.setLayout(style_layout)
        input_layout.addWidget(style_groupbox)

        # Chunk Size with label on the same line as slider
        # This might be removed or become an "advanced" setting if Gemini 1.5 Pro context is large enough
        chunk_size_container = QWidget()
        chunk_size_container.setObjectName("ChunkSizeContainer")
        chunk_size_layout = QVBoxLayout(chunk_size_container)
        chunk_size_layout.setContentsMargins(5, 5, 5, 5)
        chunk_size_layout.setSpacing(3)

        # Header with value
        chunk_header_layout = QHBoxLayout()
        chunk_size_label = QLabel("LLM Chunk Size (Advanced):")  # Label updated
        chunk_size_label.setObjectName("ChunkSizeLabel")
        chunk_size_label.setToolTip("Max words per chunk sent to the Phase 2 LLM. Lower = more API calls but safer for context limits.")
        chunk_cfg = self.ui_config["chunk_size"]
        default_chunk = chunk_cfg["default"]
        self.chunk_size_value_label = QLabel(str(default_chunk))
        self.chunk_size_value_label.setObjectName("ChunkSizeValueLabel")

        chunk_header_layout.addWidget(chunk_size_label)
        chunk_header_layout.addWidget(self.chunk_size_value_label)
        chunk_header_layout.addStretch(1)
        chunk_size_layout.addLayout(chunk_header_layout)

        # Slider
        self.chunk_size_slider = QSlider(Qt.Horizontal)
        self.chunk_size_slider.setObjectName("ChunkSlider")
        self.chunk_size_slider.setToolTip(
            f"Drag to set max words per refinement chunk. Default {default_chunk}."
        )
        self.chunk_size_slider.setMinimum(chunk_cfg["min"])
        self.chunk_size_slider.setMaximum(chunk_cfg["max"])
        self.chunk_size_slider.setValue(default_chunk)
        self.chunk_size_slider.valueChanged.connect(self.update_chunk_size_label)
        chunk_size_layout.addWidget(self.chunk_size_slider)

        # Description
        self.chunk_size_description = QLabel(
            f"(Max words for LLM refinement stage. Default: {default_chunk}. Adjust if facing LLM context limits.)"
        )
        self.chunk_size_description.setObjectName("ChunkSizeDescriptionLabel")
        self.chunk_size_description.setToolTip("Long transcripts are split into chunks; each chunk is refined separately. Increase for fewer API calls; decrease if you hit token limits.")
        self.chunk_size_description.setWordWrap(True)
        chunk_size_layout.addWidget(self.chunk_size_description)

        input_layout.addWidget(chunk_size_container)

        # --- File Inputs in Horizontal Layout ---
        file_inputs_layout = QHBoxLayout()
        file_inputs_layout.setSpacing(15)  # Add some space between the two inputs

        # Create Intermediate Transcript Folder Input
        transcript_widget = self.create_directory_input_widget(
            "Intermediate Transcript Folder (Optional):",  # Label updated
            "Choose Folder",
            "intermediate_dir_input",
            self.select_intermediate_transcript_directory,
            label_object_name="IntermediateDirLabel",
        )
        transcript_widget.setToolTip("Folder for raw transcripts and metadata. Leave blank for default. Used by Phase 2 Only and Resume.")
        file_inputs_layout.addWidget(transcript_widget)  # Add the first widget

        # Create Cookie File Input (will be added to file_inputs_layout)
        cookie_widget = self.create_file_input_widget(
            "Cookie File (Optional):",  # Label updated
            "Choose File",
            "cookie_file_input",
            self.select_cookie_file,
            label_object_name="CookieFileLabel",
        )
        cookie_widget.setToolTip("Netscape-format cookie file for YouTube (e.g. member-only or age-restricted videos). Optional.")
        file_inputs_layout.addWidget(cookie_widget)  # Add the second widget

        # Add the horizontal layout containing both file inputs to the main input layout
        input_layout.addLayout(file_inputs_layout)

        # --- Directory Input (remains below) ---
        main_output_layout = QVBoxLayout()
        main_output_layout.setSpacing(6)
        main_output_label = QLabel("Main Output Folder:")
        main_output_label.setObjectName("SummaryOutputDirLabel")
        main_output_label.setToolTip("Where refined Markdown files are saved. One file per (source, style) combination.")
        main_output_layout.addWidget(main_output_label)
        main_output_layout.addSpacing(3)
        main_output_row = QHBoxLayout()
        self.summary_output_dir_input = QLineEdit()
        self.summary_output_dir_input.setObjectName("summary_output_dir_input")
        self.summary_output_dir_input.setReadOnly(True)
        self.summary_output_dir_input.setPlaceholderText("Select main output folder")
        main_output_btn = QPushButton("Choose Folder")
        main_output_btn.setObjectName("DirectoryButton")
        main_output_btn.setToolTip("Choose where refined Markdown files will be saved.")
        main_output_btn.clicked.connect(self.select_summary_output_directory)
        main_output_btn.setFixedWidth(140)
        main_output_row.addWidget(self.summary_output_dir_input)
        main_output_row.addWidget(main_output_btn)
        main_output_layout.addLayout(main_output_row)
        input_layout.addLayout(main_output_layout)

        # API Key Inputs
        api_keys_layout = QHBoxLayout()  # Use QHBoxLayout for side-by-side
        api_keys_layout.setSpacing(10)

        # Gemini API Key Input
        gemini_api_key_layout = QVBoxLayout()
        gemini_api_key_layout.setSpacing(6)
        gemini_api_key_label = QLabel("Gemini API Key:")
        gemini_api_key_label.setObjectName("ApiKeyLabel")  # Generic, or make specific
        gemini_api_key_label.setToolTip("Google Gemini API key for Phase 2 refinement. Get one at Google AI Studio.")
        self.gemini_api_key_input = QLineEdit()
        self.gemini_api_key_input.setPlaceholderText("Enter your Gemini API key")
        self.gemini_api_key_input.setToolTip("Paste your Gemini API key. Can also be set via GEMINI_API_KEY env var.")
        self.gemini_api_key_input.setEchoMode(QLineEdit.Password)
        self.gemini_api_key_input.setText(os.environ.get("GEMINI_API_KEY", ""))
        gemini_api_key_layout.addWidget(gemini_api_key_label)
        gemini_api_key_layout.addSpacing(3)
        gemini_api_key_layout.addWidget(self.gemini_api_key_input)
        api_keys_layout.addLayout(gemini_api_key_layout)

        # ZAI API Key Input
        zai_api_key_layout = QVBoxLayout()
        zai_api_key_layout.setSpacing(6)
        zai_api_key_label = QLabel("ZAI API Key:")
        zai_api_key_label.setObjectName("ApiKeyLabel")
        zai_api_key_label.setToolTip("ZAI (Zhipu) API key for Phase 2 refinement. Optional if using Gemini.")
        self.zai_api_key_input = QLineEdit()
        self.zai_api_key_input.setPlaceholderText("Enter your ZAI API key")
        self.zai_api_key_input.setToolTip("Paste your ZAI API key. Can also be set via ZAI_API_KEY env var.")
        self.zai_api_key_input.setEchoMode(QLineEdit.Password)
        self.zai_api_key_input.setText(os.environ.get("ZAI_API_KEY", ""))
        zai_api_key_layout.addWidget(zai_api_key_label)
        zai_api_key_layout.addSpacing(3)
        zai_api_key_layout.addWidget(self.zai_api_key_input)
        api_keys_layout.addLayout(zai_api_key_layout)

        # OpenAI API Key Input
        openai_api_key_layout = QVBoxLayout()
        openai_api_key_layout.setSpacing(6)
        openai_api_key_label = QLabel("OpenAI API Key (for STT):")
        openai_api_key_label.setObjectName("OpenAiApiKeyLabel")
        openai_api_key_label.setToolTip("OpenAI API key for speech-to-text (ASR) when captions are unavailable. Optional.")
        self.openai_api_key_input = QLineEdit()
        self.openai_api_key_input.setPlaceholderText(
            "Enter your OpenAI API key for STT"
        )
        self.openai_api_key_input.setToolTip("Paste your OpenAI API key. Can also be set via OPENAI_API_KEY env var.")
        self.openai_api_key_input.setEchoMode(QLineEdit.Password)
        self.openai_api_key_input.setText(os.environ.get("OPENAI_API_KEY", ""))
        openai_api_key_layout.addWidget(openai_api_key_label)
        openai_api_key_layout.addSpacing(3)
        openai_api_key_layout.addWidget(self.openai_api_key_input)
        api_keys_layout.addLayout(openai_api_key_layout)

        # DeepSeek API Key Input
        deepseek_api_key_layout = QVBoxLayout()
        deepseek_api_key_layout.setSpacing(6)
        deepseek_api_key_label = QLabel("DeepSeek API Key:")
        deepseek_api_key_label.setObjectName("ApiKeyLabel")
        deepseek_api_key_label.setToolTip("DeepSeek API key for Phase 2 refinement. Optional if using Gemini or ZAI.")
        self.deepseek_api_key_input = QLineEdit()
        self.deepseek_api_key_input.setPlaceholderText("Enter your DeepSeek API key")
        self.deepseek_api_key_input.setToolTip("Paste your DeepSeek API key. Can also be set via DEEPSEEK_API_KEY env var.")
        self.deepseek_api_key_input.setEchoMode(QLineEdit.Password)
        self.deepseek_api_key_input.setText(os.environ.get("DEEPSEEK_API_KEY", ""))
        deepseek_api_key_layout.addWidget(deepseek_api_key_label)
        deepseek_api_key_layout.addSpacing(3)
        deepseek_api_key_layout.addWidget(self.deepseek_api_key_input)
        api_keys_layout.addLayout(deepseek_api_key_layout)

        input_layout.addLayout(api_keys_layout)

        # ASR and Phase 2 model selection (one row, two combos)
        model_select_layout = QHBoxLayout()
        model_select_layout.setSpacing(10)
        asr_layout = QVBoxLayout()
        asr_layout.setSpacing(6)
        asr_label = QLabel("ASR Model:")
        asr_label.setToolTip("Speech-to-text model used when captions are missing (e.g. YouTube no-caption, local audio).")
        self.asr_model_combo = QComboBox()
        self.asr_model_combo.setToolTip("Choose ASR provider/model. Requires corresponding API key if not built-in.")
        self._asr_models = get_asr_models()
        for m in self._asr_models:
            self.asr_model_combo.addItem(m.get("label", m.get("id", "")), m.get("id"))
        default_asr = get_default_asr_id()
        idx = next((i for i, m in enumerate(self._asr_models) if m.get("id") == default_asr), 0)
        self.asr_model_combo.setCurrentIndex(idx)
        asr_layout.addWidget(asr_label)
        asr_layout.addWidget(self.asr_model_combo)
        model_select_layout.addLayout(asr_layout)
        phase2_layout = QVBoxLayout()
        phase2_layout.setSpacing(6)
        phase2_label = QLabel("Phase 2 Model:")
        phase2_label.setToolTip("LLM used to refine transcripts into formatted documents. Requires Gemini, ZAI, or DeepSeek API key.")
        self.phase2_model_combo = QComboBox()
        self.phase2_model_combo.setToolTip("Select provider and model for refinement. Must match an API key you entered.")
        self._phase2_models = get_phase2_models()
        for m in self._phase2_models:
            self.phase2_model_combo.addItem(m.get("label", m.get("id", "")), m.get("id"))
        default_phase2 = get_default_phase2_id()
        idx2 = next((i for i, m in enumerate(self._phase2_models) if m.get("id") == default_phase2), 0)
        self.phase2_model_combo.setCurrentIndex(idx2)
        phase2_layout.addWidget(phase2_label)
        phase2_layout.addWidget(self.phase2_model_combo)
        model_select_layout.addLayout(phase2_layout)
        input_layout.addLayout(model_select_layout)

        main_layout.addWidget(input_container)

        # Progress Section
        progress_container = QWidget()
        progress_container.setObjectName("ProgressContainer")
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setSpacing(10)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("ProgressBar")
        self.progress_bar.setMaximum(100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setToolTip("Overall progress for the current run (transcript + refinement).")
        progress_layout.addWidget(self.progress_bar)

        # Status Display
        self.status_display = QTextEdit()
        self.status_display.setObjectName("StatusDisplay")
        self.status_display.setReadOnly(True)
        self.status_display.setToolTip("Live log: current item, phase, and any errors.")
        progress_layout.addWidget(self.status_display)
        main_layout.addWidget(progress_container)

        # Control Buttons
        control_layout = QHBoxLayout()
        control_layout.setSpacing(20)

        self.start_button = QPushButton("Start Processing")  # Renamed
        self.start_button.setObjectName(
            "ExtractButton"
        )  # Keep object name for styling if desired
        self.start_button.setToolTip("Start transcript extraction and refinement with current settings.")
        self.start_button.clicked.connect(
            self.start_processing_flow
        )  # Updated method call

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("CancelButton")
        self.cancel_button.setToolTip("Stop the current run after the current item finishes.")
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setEnabled(False)

        control_layout.addStretch(1)
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.cancel_button)
        control_layout.addStretch(1)
        main_layout.addLayout(control_layout)

        self.central_widget.setLayout(main_layout)
        self.setMinimumHeight(900)
        self.resize(1000, 1000)
        self.center()
        self.setAttribute(Qt.WA_AlwaysShowToolTips)

    def create_file_input_widget(
        self, label_text, button_text, field_name, handler, label_object_name=""
    ):
        """
        Creates a file input component widget with label, text field, and button.

        Args:
            label_text (str): The label text to display above the input field
            button_text (str): The text to display on the select button
            field_name (str): The object name for the input field (for referencing later)
            handler: The function to call when the select button is clicked
            label_object_name (str): Object name for the label for styling.

        Returns:
            QWidget: A widget containing the label, input field, and button.
        """
        container_widget = QWidget()  # Create a container widget
        layout = QVBoxLayout(container_widget)  # Set layout on the container
        layout.setContentsMargins(0, 0, 0, 0)  # Remove extra margins
        layout.setSpacing(6)

        label = QLabel(label_text)
        if label_object_name:
            label.setObjectName(label_object_name)
        layout.addWidget(label)
        layout.addSpacing(3)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        input_field = QLineEdit()
        input_field.setObjectName(field_name)
        input_field.setReadOnly(True)
        input_field.setPlaceholderText("Optional: Select file or leave blank")

        button = QPushButton(button_text)
        button.setObjectName("FileButton")
        button.clicked.connect(handler)
        button.setFixedWidth(120)

        input_row.addWidget(input_field)
        input_row.addWidget(button)
        layout.addLayout(input_row)

        setattr(self, field_name, input_field)
        return container_widget

    def create_directory_input_widget(
        self, label_text, button_text, field_name, handler, label_object_name=""
    ):
        """
        Creates a directory input component widget with label, text field, and button.
        Reuses create_directory_input logic but returns a widget instead of adding to layout.

        Args:
            label_text (str): The label text to display above the input field
            button_text (str): The text to display on the select button
            field_name (str): The object name for the input field (for referencing later)
            handler: The function to call when the select button is clicked
            label_object_name (str): Object name for the label for styling.

        Returns:
            QWidget: A widget containing the label, input field, and button.
        """
        container_widget = QWidget()
        container_layout = QVBoxLayout(container_widget)
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Reuse the create_directory_input logic
        self.create_directory_input(
            container_layout,
            label_text,
            button_text,
            field_name,
            handler,
            label_object_name,
        )

        return container_widget

    def create_directory_input(
        self,
        parent_layout,
        label_text,
        button_text,
        field_name,
        handler,
        label_object_name="",
    ):
        """
        Creates a directory input component with label, text field, and button.
        Adds it directly to the parent layout.
        """
        layout = QVBoxLayout()
        layout.setSpacing(6)

        label = QLabel(label_text)
        if label_object_name:
            label.setObjectName(label_object_name)
        layout.addWidget(label)
        layout.addSpacing(3)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        input_field = QLineEdit()
        input_field.setObjectName(field_name)
        input_field.setReadOnly(True)
        # Set appropriate placeholder text
        if "Optional" in label_text:
            input_field.setPlaceholderText(
                "Optional: Select folder or leave blank for default"
            )
        else:
            input_field.setPlaceholderText(
                f"Select {label_text.split(':')[0].strip()} folder"
            )

        button = QPushButton(button_text)
        button.setObjectName("DirectoryButton")
        button.clicked.connect(handler)
        button.setFixedWidth(140)

        input_row.addWidget(input_field)
        input_row.addWidget(button)
        layout.addLayout(input_row)

        parent_layout.addLayout(layout)
        setattr(self, field_name, input_field)

    def center(self):
        frame = self.frameGeometry()
        center_point = QApplication.primaryScreen().availableGeometry().center()
        frame.moveCenter(center_point)
        self.move(frame.topLeft())

    def validate_inputs(self):
        """
        Validates all user inputs before starting the processing.
        This will be adapted for PocketFlow.
        """
        is_csv_mode = self.batch_checkbox.isChecked()
        if is_csv_mode:
            if not self.csv_path or not os.path.isfile(self.csv_path):
                self._show_warning_message(
                    "Batch CSV",
                    "Please select a valid CSV file.",
                )
                return False
            try:
                from utils.csv_batch import parse_bodhiflow_csv
                parse_bodhiflow_csv(self.csv_path)
            except (ValueError, FileNotFoundError) as e:
                self._show_warning_message(
                    "CSV validation failed",
                    f"CSV format or content is invalid. Please fix and retry.\n\n{e}",
                )
                return False
            # Skip URL/path validation; CSV will be parsed in main

        # Handle Phase 2 Only mode - "No Input Allowed" is valid (single-input mode only)
        if not is_csv_mode and self.url_input.text().strip() == "No Input Allowed":
            # This is valid for Phase 2 Only mode, skip URL validation
            pass
        elif not is_csv_mode:
            url_text = self.url_input.text().strip()
            # Import input handler to check input type (pass hint for folder: media vs document)
            from utils.input_handler import get_input_type

            input_mode_hint = getattr(self, "input_mode_hint", None)
            input_type = get_input_type(url_text, input_mode_hint)

            if input_type == "unknown_url":
                self._show_warning_message(
                    "Unsupported URL Type",
                    "This URL is not supported. For webpage text extraction, add the domain to config/url_source_config.json.",
                )
                return False

            valid_input_types = [
                "youtube_video_url",
                "youtube_playlist_url",
                "teams_meeting_url",
                "podcast_rss_url",
                "file",
                "folder",
                "text_file",
                "pdf_file",
                "word_file",
                "webpage_url",
                "document_folder",
            ]

            if input_type not in valid_input_types:
                self._show_warning_message(
                    "Invalid Input Source",
                    "Please enter a valid YouTube URL (playlist/video), Teams videomanifest URL, Podcast RSS URL, webpage URL, or an existing local file/folder path.",
                )
                return False

            # Additional validation for local paths
            is_local_path = input_type in ["file", "folder", "text_file", "pdf_file", "word_file", "document_folder"]

            if is_local_path and not (
                os.path.isfile(url_text) or os.path.isdir(url_text)
            ):
                self._show_warning_message(
                    "Invalid Local Path",
                    "The provided local path is not a valid file or folder.",
                )
                return False

        # Validate Intermediate directory only if provided
        intermediate_dir_path = self.intermediate_dir_input.text().strip()
        if intermediate_dir_path and not os.path.isdir(intermediate_dir_path):
            self._show_warning_message(
                "Invalid Directory",
                "If specified, Intermediate Transcript folder must be a valid directory path",
            )
            return False

        # Validate Output directory
        output_dir = (
            self.summary_output_dir_input.text().strip()
        )  # Renamed summary_output_dir_input
        if not output_dir:
            self._show_warning_message(
                "Output Folder Required", "Please select an Output Folder."
            )
            return False
        # We can create the dir if it doesn't exist, so check if it's a valid *potential* path later
        # For now, just ensure it's not empty.

        phase_1_only = self.phase_1_only_checkbox.isChecked()
        phase_2_only = self.phase_2_only_checkbox.isChecked()
        run_phase_1 = not phase_2_only
        run_phase_2 = not phase_1_only

        asr_id = self.asr_model_combo.currentData() or get_default_asr_id()
        phase2_id = self.phase2_model_combo.currentData() or get_default_phase2_id()
        asr_entry = next((m for m in self._asr_models if m.get("id") == asr_id), None)
        phase2_entry = next((m for m in self._phase2_models if m.get("id") == phase2_id), None)
        asr_provider = asr_entry.get("provider", "openai") if asr_entry else "openai"
        phase2_provider = phase2_entry.get("provider", "zai") if phase2_entry else "zai"

        # Only require ASR key when Phase 1 will run
        if run_phase_1:
            if asr_provider == "openai" and not self.openai_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "OpenAI API key is required for the selected ASR model."
                )
                return False
            if asr_provider == "zai" and not self.zai_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "ZAI API key is required for the selected ASR model."
                )
                return False

        # Only require Phase 2 model key when Phase 2 will run
        if run_phase_2:
            if phase2_provider == "gemini" and not self.gemini_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "Gemini API key is required for the selected Phase 2 model."
                )
                return False
            if phase2_provider == "openai" and not self.openai_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "OpenAI API key is required for the selected Phase 2 model."
                )
                return False
            if phase2_provider == "deepseek" and not self.deepseek_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "DeepSeek API key is required for the selected Phase 2 model."
                )
                return False
            if phase2_provider == "zai" and not self.zai_api_key_input.text().strip():
                self._show_warning_message(
                    "API Key Required", "ZAI API key is required for the selected Phase 2 model."
                )
                return False

        if not self.language_input.text().strip():
            self._show_warning_message(
                "Language Required", "Please specify the output language"
            )
            return False

        # Only require at least one refinement style when Phase 2 will run
        if run_phase_2 and not any(cb.isChecked() for cb in self.style_checkboxes.values()):
            self._show_warning_message(
                "No Style Selected", "Please select at least one Refinement Style."
            )
            return False

        try:
            start_index_str = self.start_index_input.text().strip()
            self.start_index = int(start_index_str) if start_index_str else 1
            if self.start_index < 1:
                raise ValueError("Start index must be 1 or greater.")

            end_index_str = self.end_index_input.text().strip()
            self.end_index = int(end_index_str) if end_index_str else 0
            if self.end_index != 0 and self.end_index < self.start_index:
                raise ValueError("End index must be 0 (for all) or >= start index.")
        except ValueError as e:
            self._show_warning_message(
                "Invalid Index", f"Invalid Start/End Index for playlists/folders: {e}"
            )
            return False

        return True

    def _show_warning_message(self, title, text):
        """Helper to show a QMessageBox for warnings/errors."""
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.exec_()

    def set_processing_state(self, processing):
        """
        Updates the UI state based on whether processing is active.
        """
        self.is_processing = processing
        self.start_button.setEnabled(not processing)  # Updated button name
        self.cancel_button.setEnabled(processing)

        inputs = [
            self.url_input,
            self.intermediate_dir_input,
            self.cookie_file_input,
            self.summary_output_dir_input,
            self.gemini_api_key_input,
            self.zai_api_key_input,
            self.openai_api_key_input,
            self.deepseek_api_key_input,
            self.asr_model_combo,
            self.phase2_model_combo,
            self.language_input,
            self.start_index_input,
            self.end_index_input,
            self.chunk_size_slider,
            self.resume_checkbox,
            self.disable_ai_transcribe_checkbox,
            self.save_video_checkbox,
            self.metadata_enhance_checkbox,
            self.phase_1_only_checkbox,  # Added phase control checkboxes
            self.phase_2_only_checkbox,
            self.batch_checkbox,  # Added batch checkbox
            self.document_folder_recursive_checkbox,
            self.phase2_skip_existing_checkbox,
        ]
        for style_cb in self.style_checkboxes.values():
            style_cb.setEnabled(not processing)

        for input_field in inputs:
            if isinstance(input_field, (QLineEdit, QTextEdit)):
                input_field.setReadOnly(processing)
            elif isinstance(input_field, QCheckBox):
                input_field.setEnabled(not processing)
            else:
                input_field.setEnabled(not processing)

        # Disable folder selection buttons and CSV button during processing
        self.btn_media_folder.setEnabled(not processing)
        self.btn_doc_folder.setEnabled(not processing)
        self.btn_select_csv.setEnabled(not processing)

        # Disable file/directory selection buttons (Choose File, Choose Folder)
        for button in self.findChildren(QPushButton):
            if button.objectName() in ["FileButton", "DirectoryButton"]:
                button.setEnabled(not processing)

    def select_gemini_model(self):
        """Optional model selector; you can remove or adapt this as needed."""
        # Option 1: For brevity, we assume you always return the default
        return self.selected_model_name

        # Option 2: Build a minimal model selection UI in a message box
        # msg_box = QMessageBox()
        # msg_box.setStyleSheet("color: #333333; background-color: white;")
        # msg_box.setWindowTitle("Select Gemini Model")
        # msg_box.setText("Choose a Gemini model for refinement:")

        # model_combo = QComboBox()
        # model_combo.addItems(self.available_models)
        # model_combo.setCurrentText(self.selected_model_name)

        # layout = QVBoxLayout()
        # layout.addWidget(model_combo)
        # widget = QWidget()
        # widget.setLayout(layout)
        # msg_box.layout().addWidget(widget, 1, 0, msg_box.layout().rowCount(), 1)

        # ok_button = msg_box.addButton(QMessageBox.Ok)
        # cancel_button = msg_box.addButton(QMessageBox.Cancel)

        # msg_box.exec_()

        # if msg_box.clickedButton() == ok_button:
        #     return model_combo.currentText()
        # else:
        #     return None

    def get_selected_styles(self):
        """
        Retrieves all selected refinement styles and their prompt templates.
        """
        selected = []
        for style_name, cb in self.style_checkboxes.items():
            if cb.isChecked():
                selected.append((style_name, self.prompts[style_name]))
        return selected

    def start_processing_flow(self):  # Renamed from start_extraction_and_refinement
        """
        Starts the main PocketFlow processing.
        This will be the primary integration point with PocketFlow.
        """
        if not self.validate_inputs():
            return

        asr_model_id = self.asr_model_combo.currentData() or get_default_asr_id()
        phase2_model_id = self.phase2_model_combo.currentData() or get_default_phase2_id()
        self.selected_model_name = phase2_model_id  # kept for any legacy reference

        self.set_processing_state(True)
        self.progress_bar.setValue(0)
        self.status_display.clear()

        # --- Collect all parameters for PocketFlow ---
        is_csv_mode = self.batch_checkbox.isChecked()
        if is_csv_mode:
            phase_1_only = False
            phase_2_only = False
            run_phase_1 = True
            run_phase_2 = True
        else:
            phase_1_only = self.phase_1_only_checkbox.isChecked()
            phase_2_only = self.phase_2_only_checkbox.isChecked()
            run_phase_1 = not phase_2_only
            run_phase_2 = not phase_1_only

        # Get intermediate_dir from GUI or use default relative to output_base_dir
        output_base_dir = self.summary_output_dir_input.text().strip()
        intermediate_dir_input = self.intermediate_dir_input.text().strip()
        if intermediate_dir_input:
            intermediate_dir = intermediate_dir_input
        else:
            # Default: create intermediate_transcripts subfolder in output directory
            intermediate_dir = os.path.join(output_base_dir, "intermediate_transcripts")

        flow_params = {
            "user_input_path": "" if is_csv_mode else self.url_input.text().strip(),
            "input_mode_hint": None if is_csv_mode else getattr(self, "input_mode_hint", None),
            "cookie_file_path": self.cookie_file_input.text().strip() or None,
            "selected_styles_data": self.get_selected_styles(),  # List of (name, prompt_text)
            "output_language": self.language_input.text().strip(),
            "gemini_api_key": self.gemini_api_key_input.text().strip(),
            "openai_api_key": self.openai_api_key_input.text().strip(),
            "zai_api_key": self.zai_api_key_input.text().strip(),
            "deepseek_api_key": self.deepseek_api_key_input.text().strip(),
            "asr_model_id": asr_model_id,
            "phase2_model_id": phase2_model_id,
            "output_base_dir": output_base_dir,
            "intermediate_dir": intermediate_dir,
            "temp_dir": "./temp_bodhiflow",
            "start_index": self.start_index,
            "end_index": self.end_index,
            "llm_chunk_size": self.chunk_size_slider.value(),
            "resume_mode": self.resume_checkbox.isChecked(),
            "phase2_skip_existing": self.phase2_skip_existing_checkbox.isChecked(),
            "document_folder_recursive": self.document_folder_recursive_checkbox.isChecked(),
            "disable_ai_transcribe": self.disable_ai_transcribe_checkbox.isChecked(),
            "save_video_on_ai_transcribe": self.save_video_checkbox.isChecked(),
            "selected_gemini_model": self.selected_model_name,
            "metadata_enhancement_enabled": self.metadata_enhance_checkbox.isChecked(),
            "metadata_llm_model": "gpt-5-nano",
            "run_phase_1": run_phase_1,
            "run_phase_2": run_phase_2,
            "phase_1_only": phase_1_only,
            "phase_2_only": phase_2_only,
            "csv_path": self.csv_path if is_csv_mode else None,
            "max_workers_processes": 4,
            "max_workers_async": get_phase2_model_max_concurrency(phase2_model_id) or 10,
            # Callbacks are handled by PocketFlowRunner in core.pocketflow_runner
        }

        # Ensure output directory exists
        output_dir_path = flow_params["output_base_dir"]
        try:
            if not os.path.exists(output_dir_path):
                os.makedirs(output_dir_path)
                self.update_status(
                    f"Created output directory: {output_dir_path}", StatusType.INFO
                )
        except OSError as e:
            self.handle_error(
                f"Could not create output directory: {output_dir_path} - {e}"
            )
            self.set_processing_state(False)
            return

        # Ensure intermediate directory exists
        intermediate_dir_path = flow_params["intermediate_dir"]
        try:
            if not os.path.exists(intermediate_dir_path):
                os.makedirs(intermediate_dir_path)
                self.update_status(
                    f"Created intermediate directory: {intermediate_dir_path}",
                    StatusType.INFO,
                )
        except OSError as e:
            self.handle_error(
                f"Could not create intermediate directory: {intermediate_dir_path} - {e}"
            )
            self.set_processing_state(False)
            return

        # Start PocketFlow execution in background thread
        start_msg = (
            f"Starting PocketFlow (CSV): {flow_params['csv_path']}"
            if flow_params.get("csv_path")
            else f"Starting PocketFlow: {flow_params['user_input_path']}"
        )
        self.update_status(start_msg, StatusType.START)

        from core.pocketflow_runner import PocketFlowRunner

        # Create and start PocketFlow runner thread
        self.pocketflow_runner = PocketFlowRunner(flow_params)
        self.pocketflow_runner.status_update.connect(self.update_status)
        self.pocketflow_runner.progress_update.connect(self.update_gui_progress)
        self.pocketflow_runner.flow_complete.connect(
            lambda: self.handle_success(flow_params["output_base_dir"])
        )
        self.pocketflow_runner.start()

    @pyqtSlot(int)  # Add this decorator if not already present
    def update_gui_progress(self, progress_percent):
        self.progress_bar.setValue(progress_percent)

    @pyqtSlot(str, StatusType)
    def update_status(self, message, msg_type=StatusType.INFO):
        """Updates the status display with color-coded messages based on type and logs to file."""
        # Update GUI display
        color = STATUS_COLORS.get(msg_type, STATUS_COLORS[StatusType.INFO])
        # Ensure message is safely representable in HTML and terminal
        safe_message = message
        try:
            safe_message.encode('utf-8')
        except Exception:
            safe_message = str(message).encode('utf-8', errors='replace').decode('utf-8')
        self.status_display.append(f"<font color='{color}'>{safe_message}</font>")

        # Also log to file using appropriate logging level
        log_level = GUI_STATUS_TO_LOG_LEVEL.get(msg_type, logging.INFO)
        gui_logger.log(log_level, f"[GUI Status] {message}")

    def handle_success(self, output_path):
        self.set_processing_state(False)
        self.update_status(
            f"Processing complete! Output files should be in folder: {output_path}",
            StatusType.FINISH,
        )
        self.progress_bar.setValue(100)

    def handle_error(self, error_message):
        self.set_processing_state(False)
        self.update_status(f"Error occurred: {error_message}", StatusType.ERROR)
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setText(str(error_message))  # Ensure error is string
        msg_box.setWindowTitle("Error")
        msg_box.exec_()
        self.progress_bar.setValue(0)

    def cancel_processing(self):
        """Cancel the currently running PocketFlow processing."""
        if hasattr(self, 'pocketflow_runner') and self.pocketflow_runner.isRunning():
            # Request the thread to stop
            self.pocketflow_runner.stop()
            
            # Give the thread some time to stop gracefully
            if not self.pocketflow_runner.wait(5000):  # Wait up to 5 seconds
                # If it doesn't stop gracefully, terminate it
                self.pocketflow_runner.terminate()
                self.pocketflow_runner.wait()
                self.update_status("Processing forcefully terminated", StatusType.WARNING)
            else:
                self.update_status("Processing cancelled by user", StatusType.WARNING)
        else:
            self.update_status("No active processing to cancel", StatusType.INFO)

        self.set_processing_state(False)
        self.progress_bar.setValue(0)

    def select_intermediate_transcript_directory(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ShowDirsOnly
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Intermediate Transcript Folder",
            "",
            options=options,
        )
        if dir_path:
            self.intermediate_dir_input.setText(dir_path)

    def select_cookie_file(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Cookie File",
            "",
            "Text Files (*.txt);;All Files (*)",
            options=options,
        )
        if file_path:
            self.cookie_file_input.setText(file_path)

    def _on_url_input_changed(self):
        """Clear folder-type hint when user edits the input path manually."""
        self.input_mode_hint = None
        self._update_folder_button_styles()

    def _on_select_media_folder(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ShowDirsOnly
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Media/Video Folder", "", options=options
        )
        if dir_path:
            self.url_input.setText(dir_path)
            self.input_mode_hint = "media_folder"
            self._update_folder_button_styles()

    def _on_select_document_folder(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ShowDirsOnly
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Document Folder (PDF/Word/TXT)", "", options=options
        )
        if dir_path:
            self.url_input.setText(dir_path)
            self.input_mode_hint = "document_folder"
            self._update_folder_button_styles()

    def _on_batch_checkbox_changed(self, checked):
        is_csv = checked
        self.url_input.setVisible(not is_csv)
        self.btn_media_folder.setVisible(not is_csv)
        self.btn_doc_folder.setVisible(not is_csv)
        self.csv_row_container.setVisible(is_csv)
        self.phase_1_only_checkbox.setEnabled(not is_csv)
        self.phase_2_only_checkbox.setEnabled(not is_csv)
        if is_csv:
            self.url_input.clear()
            self.input_mode_hint = None
            self._update_folder_button_styles()
        else:
            self.csv_path = None
            self.csv_path_display.clear()
        # Batch checkbox is always visible in Options section, no need to change visibility

    def _update_folder_button_styles(self):
        """Set folder buttons to green when that folder type was selected; otherwise default light gray.
        Also show/hide Recursive checkbox based on folder selection."""
        if self.input_mode_hint == "media_folder":
            self.btn_media_folder.setStyleSheet("QPushButton { background-color: #008000; padding: 2px;}")
            self.btn_doc_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px;}")
            self.document_folder_recursive_checkbox.setVisible(True)  # Show for media folder (recursive supported)
        elif self.input_mode_hint == "document_folder":
            self.btn_media_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px;}")
            self.btn_doc_folder.setStyleSheet("QPushButton { background-color: #008000; padding: 2px;}")
            self.document_folder_recursive_checkbox.setVisible(True)  # Show only when document folder is selected
        else:
            self.btn_media_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px;}")
            self.btn_doc_folder.setStyleSheet("QPushButton { background-color: #D3D3D3; padding: 2px;}")
            self.document_folder_recursive_checkbox.setVisible(False)  # Hide when no folder selected

    def _on_select_csv_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select CSV File", "", "CSV (*.csv);;All Files (*)"
        )
        if path:
            self.csv_path = path
            self.csv_path_display.setText(path)

    def select_summary_output_directory(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ShowDirsOnly
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Main Output Folder",
            "",
            options=options,  # Updated title
        )
        if dir_path:
            self.summary_output_dir_input.setText(dir_path)  # Updated field name

    def select_output_file(self, title, field, is_save=True):
        options = QFileDialog.Options()
        if is_save:
            file_path, _ = QFileDialog.getSaveFileName(
                self, title, "", "Text Files (*.txt);;All Files (*)", options=options
            )
        else:
            file_path, _ = QFileDialog.getOpenFileName(
                self, title, "", "Text Files (*.txt);;All Files (*)", options=options
            )

        if file_path:
            if is_save and not file_path.endswith(".txt"):
                file_path += ".txt"  # Ensure .txt for save dialogs
            field.setText(file_path)
