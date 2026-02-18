"""
Main entry point for BodhiFlow - Content to Wisdom converter.

Launches the PyQt5 GUI (no console on Windows when run as .pyw).
All logic lives in core/ for stable imports.
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.launch import launch_gui, main_cli

if __name__ == "__main__":
    # Check if we're in CLI mode or GUI mode
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        main_cli()
    else:
        launch_gui()
