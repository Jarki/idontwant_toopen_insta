from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime
import hashlib
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback as traceback_module
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import TracebackType
from typing import Protocol

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import QueuePool

MAX_QUEUE_SIZE = 256
MAX_REQUEST_LINKS = 100
MAX_MESSAGE_SIZE = 8192
MAX_TRACE_SIZE = 65536
MAX_TRACE_FRAMES = 80
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.05
SHUTDOWN_BUDGET_SECONDS = 1.0
FALLBACK_INTERVAL_SECONDS = 30.0
TRUNCATION_MARKER = "\n... [traceback truncated]"

_URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>'\"]+")
_AUTH_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*"
    r"(?:(?:bearer|basic)\s+)?[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_PATH_RE = re.compile(r"(?<![\w.-])/(?:home|root|app|workspace|srv|opt|tmp)/[^\s:'\"]+")
_CREDENTIAL_RE = re.compile(r"(?i)(postgresql(?:\+\w+)?://)[^\s/@:]+(?::[^\s/@]*)?@")
_APP_PACKAGE = "ig_reel_downloader"


@dataclasses.dataclass(frozen=True, slots=True)
class ErrorContext:
    media_request_ids: tuple[int, ...] = ()
    provider: str | None = None
    media_kind: str | None = None
    stage: str | None = None
    release: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ErrorSnapshot:
    fingerprint: str
    display_name: str
    event_code: str
    occurred_at: datetime.datetime
    severity: str
    logger_name: str
    component: str | None
    exception_type: str | None
    message: str
    traceback: str | None
    provider: str | None
    media_kind: str | None
    release: str | None
    media_request_ids: tuple[int, ...]


_context: contextvars.ContextVar[ErrorContext | None] = contextvars.ContextVar(
    "error_reporter_context", default=None
)


def _get_context() -> ErrorContext:
    return _context.get() or ErrorContext()


@contextlib.contextmanager
def bind_context(
    *,
    media_request_ids: Sequence[int] | None = None,
    provider: str | None = None,
    media_kind: str | None = None,
    stage: str | None = None,
    release: str | None = None,
) -> Iterator[None]:
    current = _get_context()
    ids = (
        current.media_request_ids
        if media_request_ids is None
        else tuple(dict.fromkeys(value for value in media_request_ids if value > 0))[
            :MAX_REQUEST_LINKS
        ]
    )
    updated = ErrorContext(
        media_request_ids=ids,
        provider=provider if provider is not None else current.provider,
        media_kind=media_kind if media_kind is not None else current.media_kind,
        stage=stage if stage is not None else current.stage,
        release=release if release is not None else current.release,
    )
    token = _context.set(updated)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> ErrorContext:
    return _get_context()


def _known_secrets() -> tuple[str, ...]:
    names = (
        "BOT_TOKEN",
        "DATABASE_URL",
        "DB_APP_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "DB_ERROR_API_PASSWORD",
        "POSTGRES_PASSWORD",
    )
    return tuple(value for name in names if (value := os.getenv(name)))


def sanitize(value: str, *, secrets: Sequence[str] = ()) -> str:
    sanitized = _CREDENTIAL_RE.sub(r"\1[redacted]@", value)
    sanitized = _URL_RE.sub("[url]", sanitized)
    sanitized = _AUTH_RE.sub(lambda match: f"{match.group(1)}=[redacted]", sanitized)
    sanitized = _BEARER_RE.sub("[authorization]", sanitized)
    sanitized = _PATH_RE.sub("[path]", sanitized)
    for secret in (*_known_secrets(), *secrets):
        if secret:
            sanitized = sanitized.replace(secret, "[redacted]")
    return sanitized


def _qualified_exception_type(exc_type: type[BaseException] | None) -> str | None:
    if exc_type is None:
        return None
    return f"{exc_type.__module__}.{exc_type.__qualname__}"[:256]


def _application_frames(tb: TracebackType | None) -> tuple[str, ...]:
    frames: list[str] = []
    while tb is not None:
        filename = Path(tb.tb_frame.f_code.co_filename)
        parts = filename.parts
        if _APP_PACKAGE in parts:
            package_index = parts.index(_APP_PACKAGE)
            module = ".".join(Path(*parts[package_index:]).with_suffix("").parts)
            frames.append(f"{module}:{tb.tb_frame.f_code.co_name}")
        tb = tb.tb_next
    return tuple(frames[-MAX_TRACE_FRAMES:])


def _traceback_text(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None] | None,
) -> str | None:
    if exc_info is None:
        return None
    exc_type, exc, tb = exc_info
    trace = "".join(traceback_module.format_exception(exc_type, exc, tb))
    trace = sanitize(trace)
    if len(trace) > MAX_TRACE_SIZE:
        trace = trace[: MAX_TRACE_SIZE - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return trace


def _fingerprint(
    event_code: str, exception_type: str | None, frames: Sequence[str]
) -> str:
    shape = "\n".join((event_code, exception_type or "", *frames))
    return hashlib.sha256(shape.encode()).hexdigest()


def snapshot_from_record(record: logging.LogRecord) -> ErrorSnapshot:
    context = _get_context()
    raw_code = getattr(record, "event_code", None)
    event_code = str(raw_code or f"{record.name}.{record.funcName}")[:128]
    exc_info = record.exc_info if record.exc_info and record.exc_info[0] else None
    exception_type = _qualified_exception_type(exc_info[0] if exc_info else None)
    frames = _application_frames(exc_info[2] if exc_info else None)
    message = sanitize(record.getMessage())[:MAX_MESSAGE_SIZE] or event_code
    display_name = sanitize(str(getattr(record, "display_name", event_code)))[:256]
    component = context.stage or getattr(record, "component", None)
    return ErrorSnapshot(
        fingerprint=_fingerprint(event_code, exception_type, frames),
        display_name=display_name,
        event_code=event_code,
        occurred_at=datetime.datetime.fromtimestamp(record.created, datetime.UTC),
        severity=record.levelname
        if record.levelname in {"ERROR", "CRITICAL"}
        else "ERROR",
        logger_name=record.name[:255],
        component=str(component)[:255] if component else None,
        exception_type=exception_type,
        message=message,
        traceback=_traceback_text(exc_info),
        provider=context.provider,
        media_kind=context.media_kind,
        release=context.release,
        media_request_ids=context.media_request_ids,
    )


class SnapshotWriter(Protocol):
    def __call__(self, snapshot: ErrorSnapshot) -> None: ...


class ErrorReporterHandler(logging.Handler):
    def __init__(
        self, target: queue.Queue[ErrorSnapshot], fallback: Callable[[str], None]
    ) -> None:
        super().__init__(logging.ERROR)
        self._target = target
        self._fallback = fallback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._target.put_nowait(snapshot_from_record(record))
        except queue.Full:
            self._fallback("error reporter queue is full; dropping event")
        except Exception:
            self._fallback("error reporter failed to snapshot event")


_RECORD_SQL = text(
    """
SELECT observability.record_error(
    :fingerprint, :display_name, :event_code, :occurred_at, :severity,
    :logger_name, :component, :exception_type, :message, :traceback,
    :provider, :media_kind, :release, CAST(:media_request_ids AS bigint[])
)
"""
)


def record_snapshot(engine: Engine, snapshot: ErrorSnapshot) -> None:
    parameters = dataclasses.asdict(snapshot)
    parameters["media_request_ids"] = list(snapshot.media_request_ids)
    with engine.begin() as connection:
        connection.execute(_RECORD_SQL, parameters)


def create_reporter_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
        connect_args={"connect_timeout": 1, "options": "-c statement_timeout=1000"},
    )


class _Fallback:
    def __init__(self, interval: float = FALLBACK_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._last = 0.0
        self._lock = threading.Lock()
        self._active = threading.local()

    def __call__(self, message: str) -> None:
        if getattr(self._active, "value", False):
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last < self._interval:
                return
            self._last = now
        self._active.value = True
        try:
            print(f"error reporter: {message}", file=sys.stderr)
        except Exception:
            pass
        finally:
            self._active.value = False


class ErrorReporter:
    def __init__(
        self,
        writer: SnapshotWriter,
        *,
        queue_size: int = MAX_QUEUE_SIZE,
        retries: int = MAX_RETRIES,
        retry_backoff: float = RETRY_BACKOFF_SECONDS,
        fallback: Callable[[str], None] | None = None,
    ) -> None:
        self._queue: queue.Queue[ErrorSnapshot] = queue.Queue(maxsize=queue_size)
        self._writer = writer
        self._retries = max(0, min(retries, MAX_RETRIES))
        self._retry_backoff = max(0.0, min(retry_backoff, RETRY_BACKOFF_SECONDS))
        self._fallback = fallback or _Fallback()
        self.handler = ErrorReporterHandler(self._queue, self._fallback)
        self._installed_logger: logging.Logger | None = None
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="error-reporter", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stopping.is_set() or not self._queue.empty():
            try:
                snapshot = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                for attempt in range(self._retries + 1):
                    try:
                        self._writer(snapshot)
                        break
                    except Exception:
                        if attempt == self._retries:
                            self._fallback("writer failed; dropping event")
                        elif self._retry_backoff:
                            time.sleep(self._retry_backoff)
            finally:
                self._queue.task_done()

    def stop(self, timeout: float = SHUTDOWN_BUDGET_SECONDS) -> None:
        installed_logger = getattr(self, "_installed_logger", None)
        if isinstance(installed_logger, logging.Logger):
            installed_logger.removeHandler(self.handler)
            self._installed_logger = None
        self._stopping.set()
        self._thread.join(max(0.0, min(timeout, SHUTDOWN_BUDGET_SECONDS)))


class DatabaseErrorReporter(ErrorReporter):
    def __init__(
        self,
        database_url: str,
        *,
        queue_size: int = MAX_QUEUE_SIZE,
        retries: int = MAX_RETRIES,
        retry_backoff: float = RETRY_BACKOFF_SECONDS,
        fallback: Callable[[str], None] | None = None,
    ) -> None:
        self.engine = create_reporter_engine(database_url)
        super().__init__(
            lambda snapshot: record_snapshot(self.engine, snapshot),
            queue_size=queue_size,
            retries=retries,
            retry_backoff=retry_backoff,
            fallback=fallback,
        )

    def stop(self, timeout: float = SHUTDOWN_BUDGET_SECONDS) -> None:
        super().stop(timeout)
        self.engine.dispose(close=False)


def install_reporter(
    database_url: str,
    *,
    release: str | None = None,
    logger: logging.Logger | None = None,
) -> DatabaseErrorReporter:
    reporter = DatabaseErrorReporter(database_url)
    target_logger = logger or logging.getLogger()
    target_logger.addHandler(reporter.handler)
    if release is not None:
        _context.set(dataclasses.replace(_get_context(), release=release[:128]))
    reporter._installed_logger = target_logger
    return reporter
