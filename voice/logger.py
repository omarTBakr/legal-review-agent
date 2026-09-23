"""Logging for the voice service, configured once from VOICE_LOG_LEVEL."""

import logging
import os

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging() -> None:
    logging.basicConfig(level=os.getenv("VOICE_LOG_LEVEL", "INFO").upper(), format=FORMAT)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
