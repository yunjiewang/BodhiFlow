"""
Unified logging configuration for BodhiFlow.

This module provides a centralized logging setup that outputs to both console
and log files with appropriate formatting and rotation.
"""

import logging
import logging.handlers
import os
import sys
import io
from datetime import datetime
from pathlib import Path


def setup_logger(name: str = "bodhiflow", log_level: str = "INFO") -> logging.Logger:
    """
    Set up a logger with both console and file handlers.

    Args:
        name (str): Logger name
        log_level (str): Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        logging.Logger: Configured logger instance
    """

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Avoid adding multiple handlers if logger already exists
    if logger.handlers:
        return logger

    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Create formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    simple_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
    )

    # Console handler with UTF-8 and safe replacement for unsupported glyphs
    console_stream = sys.stdout
    try:
        if hasattr(console_stream, "buffer"):
            console_stream = io.TextIOWrapper(
                console_stream.buffer, encoding="utf-8", errors="replace"
            )
    except Exception:
        # Fallback to default stream
        console_stream = sys.stdout
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_formatter)

    # File handler with rotation
    log_filename = log_dir / f"bodhiflow_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_filename,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)

    # Error file handler
    error_log_filename = (
        log_dir / f"bodhiflow_errors_{datetime.now().strftime('%Y%m%d')}.log"
    )
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_filename,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(detailed_formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)

    return logger


def get_logger(module_name: str = None) -> logging.Logger:
    """
    Get a logger instance for a specific module.

    Args:
        module_name (str): Name of the module (usually __name__)

    Returns:
        logging.Logger: Logger instance
    """
    if module_name:
        # Use the module name as logger name
        logger_name = f"bodhiflow.{module_name.split('.')[-1]}"
    else:
        logger_name = "bodhiflow"

    return setup_logger(logger_name)


# Initialize the main logger when module is imported
main_logger = setup_logger("bodhiflow")
