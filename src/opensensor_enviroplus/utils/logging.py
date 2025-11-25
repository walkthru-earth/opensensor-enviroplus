"""
Smart logging with Rich for beautiful console output and easy debugging.
"""

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install as install_rich_traceback

# Install rich traceback handler for better error messages
install_rich_traceback(show_locals=True)

# Global console for rich output
console = Console()


class SafeFileHandler(logging.FileHandler):
    """FileHandler that ensures the log directory exists before every write."""

    def emit(self, record):
        """Emit a record, ensuring the log directory exists first."""
        try:
            # Ensure log directory exists before writing
            log_dir = Path(self.baseFilename).parent
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If directory creation fails, let the parent handler deal with it
            pass
        super().emit(record)


def setup_logging(
    level: str = "INFO", log_file: Path | None = None, json_format: bool = False
) -> logging.Logger:
    """
    Set up logging with Rich handler for beautiful console output.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging
        json_format: Use JSON format for structured logging

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger("opensensor")
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    logger.handlers.clear()

    # Console handler with Rich
    console_handler = RichHandler(
        console=console, rich_tracebacks=True, tracebacks_show_locals=True, markup=True
    )
    console_handler.setLevel(getattr(logging, level.upper()))
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        if json_format:
            # TODO: Add structured JSON logging
            pass
        else:
            # Use SafeFileHandler that recreates the log directory if deleted
            file_handler = SafeFileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

    return logger


def log_sensor_reading(data: dict, logger: logging.Logger) -> None:
    """Log sensor reading with nice formatting."""
    logger.debug(f" Sensor reading: {len(data)} fields")


def log_batch_write(count: int, path: Path, duration: float, logger: logging.Logger) -> None:
    """Log batch write operation."""
    rate = count / duration if duration > 0 else 0
    logger.info(
        f" Wrote [bold]{count}[/bold] records to {path.name} "
        f"([dim]{duration:.2f}s, {rate:.0f} rec/s[/dim])"
    )


def log_error(error: Exception, logger: logging.Logger, context: str = "") -> None:
    """Log error with rich traceback."""
    if context:
        logger.error(f"ERROR: {context}: {error}")
    else:
        logger.error(f"ERROR: Error: {error}")


def log_status(message: str, logger: logging.Logger, emoji: str = "INFO:") -> None:
    """Log status message with emoji."""
    logger.info(f"{emoji} {message}")
