"""
=======================================================
  Centralised Logger — Azure Functions compatible
  
  Azure Functions does not allow creating folders
  in /home/site/wwwroot/ — so we use console only.
  Logs appear in Azure Portal → Function → Logs tab.
=======================================================
"""

import logging
import os


def get_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Returns a logger that works on Azure Functions.
    Uses console (stdout) only — no file writing.
    File writing causes PermissionError on Azure.
    """
    logger = logging.getLogger(name)

    # Already configured — return as is
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler only — always works on Azure Functions
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File handler — only if running locally (not on Azure)
    if log_file and not _is_azure():
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except (PermissionError, OSError):
            pass  # Silently skip file logging if not allowed

    return logger


def _is_azure() -> bool:
    """Check if running on Azure Functions."""
    return (
        os.environ.get("FUNCTIONS_WORKER_RUNTIME") is not None or
        os.environ.get("WEBSITE_SITE_NAME") is not None or
        os.path.exists("/home/site/wwwroot")
    )
