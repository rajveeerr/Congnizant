"""Structured JSON logging for both server and worker.

One JSON object per line, easy to grep/ship to CloudWatch later. Pass
extra kwargs via `log.info("...", extra={"job_id": ...})` and they
appear at the top level of the JSON.
"""

import json
import logging
import sys


_RESERVED_LOGRECORD_FIELDS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Surface anything passed via `extra={...}`
        for k, v in record.__dict__.items():
            if k not in _RESERVED_LOGRECORD_FIELDS:
                payload[k] = v
        return json.dumps(payload, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
