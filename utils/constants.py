"""
Common constants for BodhiFlow.

This module provides shared constants used across the application,
including status types and their associated colors/logging levels.
"""

import enum
import logging


class StatusType(enum.Enum):
    """Enumeration for different types of status messages."""

    SUCCESS = 1
    ERROR = 2
    WARNING = 3
    INFO = 4
    PROGRESS = 5
    SKIP = 6
    START = 7
    FINISH = 8
    DEBUG = 9  # Optional: for less prominent info


# Color mapping for GUI status display
STATUS_COLORS = {
    StatusType.SUCCESS: "#27ae60",  # Green
    StatusType.ERROR: "#e74c3c",  # Red
    StatusType.WARNING: "#f39c12",  # Orange
    StatusType.INFO: "#333333",  # Dark Gray (default)
    StatusType.PROGRESS: "#2980b9",  # Blue
    StatusType.SKIP: "#888888",  # Gray
    StatusType.START: "#2980b9",  # Blue (for start messages)
    StatusType.FINISH: "#27ae60",  # Green (for final success)
    StatusType.DEBUG: "#888888",  # Gray
}

# Mapping from StatusType to logging level
STATUS_TO_LOG_LEVEL = {
    StatusType.SUCCESS: logging.INFO,
    StatusType.ERROR: logging.ERROR,
    StatusType.WARNING: logging.WARNING,
    StatusType.INFO: logging.INFO,
    StatusType.PROGRESS: logging.DEBUG,  # Use DEBUG for progress to reduce noise
    StatusType.SKIP: logging.INFO,
    StatusType.START: logging.INFO,
    StatusType.FINISH: logging.INFO,
    StatusType.DEBUG: logging.DEBUG,
}
