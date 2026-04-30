"""
=======================================================
  Centralised Logger
=======================================================
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime


def get_logger(name: str, log_file: str = "logs/pipeline_run.log") -> logging.Logger:
    """
    Return a logger with both console and rotating file handlers.
    Creates the log directory automatically if it doesn't exist.
    """
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger   # Already configured in this process

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # Rotating file handler (10 MB × 5 backups)
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger
