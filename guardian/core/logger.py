"""
Structured JSON logger using structlog with rotating file handler.
Usage:
    from guardian.core.logger import get_logger
    log = get_logger(__name__)
    log.info("container_restarted", container="open-webui", reason="unhealthy")
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from guardian.core.config import cfg


def _setup_stdlib_handler(log_dir: str, log_level: str) -> None:
    """Configure stdlib root logger with rotating JSON file + stderr."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # ── Rotating file handler ────────────────────────────────
    log_file = Path(log_dir) / "guardian.log"
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=cfg.maintenance.log_rotation.max_size_mb * 1024 * 1024,
        backupCount=cfg.maintenance.log_rotation.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))  # structlog owns formatting
    root.addHandler(file_handler)

    # ── Stderr handler ───────────────────────────────────────
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stderr_handler)


def setup_logging() -> None:
    """One-time logging setup.  Call from main before anything else."""
    _setup_stdlib_handler(cfg.service.log_dir, cfg.service.log_level)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Tell stdlib to format via structlog JSON renderer
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
