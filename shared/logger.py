import logging
import os
from pathlib import Path


def setup_logger(service_name: str,
                 log_dir: str = "logs",
                 level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(service_name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler
    try:
        logs_path = Path(log_dir)
        logs_path.mkdir(parents=True, exist_ok=True)
        log_file = logs_path / f"{service_name}.log"

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except PermissionError:
        pass  # Already redirected to log file by shell process launcher

    return logger
