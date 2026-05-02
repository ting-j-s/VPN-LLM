"""VPN Tunnel Logging Utilities."""

import logging
import sys
from typing import Optional


# Global default level
_DEFAULT_LEVEL = "INFO"


def set_default_level(level: str) -> None:
    """Set global default log level.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
    """
    global _DEFAULT_LEVEL
    _DEFAULT_LEVEL = level.upper()


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name, typically module name or __name__.
        level: Optional log level override. Defaults to global default.

    Returns:
        Configured logger instance.
    """
    log_level = (level or _DEFAULT_LEVEL).upper()

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False

    # Add handler if not already present
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# Alias for backward compatibility
setup_logger = get_logger