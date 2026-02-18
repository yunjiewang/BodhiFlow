"""
GUI and CLI launch for BodhiFlow.

Provides launch_gui() and main_cli() so main.pyw / main.py can be thin entry points.
"""

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

# Project root = parent of core/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def launch_gui():
    """Launch the PyQt5 GUI application."""
    from gui.main_window import BodhiFlow_GUI_MainWindow
    from utils.logger_config import get_logger

    logger = get_logger(__name__)
    app = QApplication(sys.argv)

    stylesheet_path = _PROJECT_ROOT / "gui" / "styles.qss"
    try:
        with open(stylesheet_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
            logger.info(f"Stylesheet loaded from: {stylesheet_path}")
    except FileNotFoundError:
        logger.warning(
            f"Stylesheet not found at: {stylesheet_path}. Using default styles."
        )
    except Exception as e:
        logger.error(f"Error loading stylesheet: {e}")

    app.setApplicationName("BodhiFlow")
    app.setApplicationDisplayName("BodhiFlow - Content to Wisdom")
    app.setApplicationVersion("2.0")

    main_window = BodhiFlow_GUI_MainWindow()
    main_window.show()
    sys.exit(app.exec_())


def main_cli():
    """CLI entry; currently launches GUI."""
    from utils.logger_config import get_logger

    logger = get_logger(__name__)
    logger.info("BodhiFlow - Content to Wisdom Converter")
    logger.info("Launching GUI interface...")
    launch_gui()
