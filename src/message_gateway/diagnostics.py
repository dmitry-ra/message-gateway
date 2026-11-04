"""Structured operational events without message bodies or RPC envelopes."""

import json
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def emit(event: str, **fields: object) -> None:
    logger.info(
        json.dumps({"at": datetime.now(UTC).isoformat(), "event": event, **fields}, sort_keys=True)
    )
