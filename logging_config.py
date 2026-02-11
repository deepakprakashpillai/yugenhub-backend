"""
Centralized logging configuration for YugenHub Backend.
Provides structured JSON logging with context variables for multi-tenant SaaS debugging.
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime, timezone
from contextvars import ContextVar

# --- Context Variables (populated by middleware per-request) ---
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
agency_id_var: ContextVar[str] = ContextVar("agency_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON with context variables."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get("-"),
            "agency_id": agency_id_var.get("-"),
            "user_id": user_id_var.get("-"),
            "message": record.getMessage(),
        }

        # Include extra data if provided via logger.info("msg", extra={"data": {...}})
        if hasattr(record, "data") and record.data:
            log_entry["data"] = record.data

        # Include exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class DevFormatter(logging.Formatter):
    """Colorized, human-readable formatter for local development."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        req_id = request_id_var.get("-")
        agency = agency_id_var.get("-")
        user = user_id_var.get("-")

        prefix = f"{color}{record.levelname:<7}{self.RESET}"
        context = f"[req={req_id} agency={agency} user={user}]"
        msg = f"{prefix} {record.name} {context} {record.getMessage()}"

        # Append extra data
        if hasattr(record, "data") and record.data:
            msg += f"  | data={record.data}"

        # Append exception
        if record.exc_info and record.exc_info[0] is not None:
            msg += f"\n{self.formatException(record.exc_info)}"

        return msg


def setup_logging():
    """Initialize logging for the application."""
    env = os.getenv("ENV", "development").lower()
    log_level = os.getenv("LOG_LEVEL", "DEBUG" if env == "development" else "INFO").upper()

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers (avoids duplicates on reload)
    root_logger.handlers.clear()

    # --- Console Handler ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)

    if env == "production":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(DevFormatter())

    root_logger.addHandler(console_handler)

    # --- File Handler (Rotating) ---
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)  # File always gets INFO+
    file_handler.setFormatter(JSONFormatter())  # File always JSON
    root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("motor").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)

    # Startup log
    app_logger = logging.getLogger("yugenhub")
    app_logger.info(f"Logging initialized | env={env} level={log_level} file={log_file}")


def get_logger(name: str) -> logging.Logger:
    """Get a named logger under the yugenhub namespace."""
    return logging.getLogger(f"yugenhub.{name}")
