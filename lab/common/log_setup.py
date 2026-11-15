"""JSON logging shared by every lab service.

Each log line is one JSON object. Later phases ship these lines to Loki, and the triage agent
reads them back through a tool, so the field names here are a contract:
``ts``, ``level``, ``service``, ``logger``, ``msg``, plus any structured fields passed with
``extra={"fields": {...}}``.
"""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                entry.setdefault(key, value)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(service: str, level: str = "INFO") -> None:
    """Send every log record (ours and the libraries') to stdout as JSON."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Our middleware logs each request with more detail than uvicorn's access log.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs every request at INFO; that is noise for the agent.
    logging.getLogger("httpx").setLevel(logging.WARNING)
