import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.metrics import record_request

conversation_id_var: ContextVar[str] = ContextVar("conversation_id", default="")
plan_id_var: ContextVar[str] = ContextVar("plan_id", default="")

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "kind": "system",
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_") and value is not None:
                payload[key] = value
        cid = conversation_id_var.get()
        if cid and "conversation_id" not in payload:
            payload["conversation_id"] = cid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: str, msg: str, *, kind: str = "system", **fields) -> None:
    extra = {"kind": kind}
    extra.update(fields)
    logger.log(logging.getLevelName(level.upper()), msg, extra=extra)


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("X-Conversation-Id") or str(uuid.uuid4())
        token = conversation_id_var.set(cid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            record_request(int((time.perf_counter() - start) * 1000), is_error=True)
            raise
        finally:
            conversation_id_var.reset(token)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        record_request(elapsed_ms, is_error=response.status_code >= 500)
        if "x-conversation-id" not in response.headers:
            response.headers["X-Conversation-Id"] = cid
        access_logger = get_logger("app.access")
        log_event(
            access_logger,
            "INFO",
            f"{request.method} {request.url.path}",
            kind="system",
            conversation_id=response.headers.get("X-Conversation-Id") or cid,
            elapsed_ms=elapsed_ms,
            status=response.status_code,
        )
        return response