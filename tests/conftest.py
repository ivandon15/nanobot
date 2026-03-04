"""Pytest configuration and shared fixtures."""
import logging
import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def propagate_loguru_to_caplog(caplog):
    """Bridge loguru to pytest's caplog fixture."""
    class PropagateHandler(logging.Handler):
        def emit(self, record):
            logging.getLogger(record.name).handle(record)

    handler_id = logger.add(PropagateHandler(), format="{message}")
    yield
    logger.remove(handler_id)
