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
MAX_EXCEPTION_CHAIN = 8
MAX_INPUT_TEXT = MAX_TRACE_SIZE
MAX_FILENAME_SIZE = 1024
MAX_FUNCTION_NAME_SIZE = 256
MAX_PROVIDER_SIZE = 64
MAX_MEDIA_KIND_SIZE = 64
MAX_COMPONENT_SIZE = 255
MAX_RELEASE_SIZE = 128
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.05
SHUTDOWN_BUDGET_SECONDS = 1.0
FALLBACK_INTERVAL_SECONDS = 30.0
TRUNCATION_MARKER = "\n... [traceback truncated]"

_URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>'\"]+")
_AUTH_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*"
    r"(?:\"[^\r\n\"]*\"|'[^\r\n']*'|[^\r\n]+)"
)
_AUTH_MAPPING_RE = re.compile(
    r"(?im)(?P<prefix>['\"](?:authorization|proxy-authorization|cookie|set-cookie)"
    r"['\"]\s*:\s*)(?P<value>\"(?:\\.|[^\"\\\r\n])*\"|"
    r"'(?:\\.|[^'\\\r\n])*')"
)
_BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_PATH_RE = re.compile(r"(?<![\w.-])/(?:home|root|app|workspace|srv|opt|tmp)/[^\s:'\"]+")
_CREDENTIAL_RE = re.compile(r"(?i)(postgresql(?:\+\w+)?://)[^\s/@:]+(?::[^\s/@]*)?@")
_TELEGRAM_STRUCTURED_IDENTITY_RE = re.compile(
    r"(?is)(?<![\w.])(?:telegram\s+)?(?:user|sender|chat)\s*"
    r"\((?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^)'\"\\])*\)"
)
_TELEGRAM_IDENTITY_RE = re.compile(r"(?i)\btelegram\s+(?:user|sender|chat)\b[^\n;]*")
_USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,}")
_LABELED_ID_RE = re.compile(
    r"(?i)\b(?:user|sender|chat|telegram)[_-]?id\s*[:=]\s*-?\d+"
)
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
        provider=(
            provider[:MAX_PROVIDER_SIZE] if provider is not None else current.provider
        ),
        media_kind=(
            media_kind[:MAX_MEDIA_KIND_SIZE]
            if media_kind is not None
            else current.media_kind
        ),
        stage=stage[:MAX_COMPONENT_SIZE] if stage is not None else current.stage,
        release=release[:MAX_RELEASE_SIZE] if release is not None else current.release,
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
    sanitized = _TELEGRAM_STRUCTURED_IDENTITY_RE.sub("[telegram identity]", value)
    sanitized = _TELEGRAM_IDENTITY_RE.sub("[telegram identity]", sanitized)
    sanitized = _LABELED_ID_RE.sub("[telegram identity]", sanitized)
    sanitized = _USERNAME_RE.sub("[telegram username]", sanitized)
    sanitized = _AUTH_MAPPING_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('value')[0]}"
            f"[redacted]{match.group('value')[0]}"
        ),
        sanitized,
    )
    sanitized = _CREDENTIAL_RE.sub(r"\1[redacted]@", sanitized)
    sanitized = _AUTH_RE.sub(lambda match: f"{match.group(1)}=[redacted]", sanitized)
    sanitized = _URL_RE.sub("[url]", sanitized)
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


def _application_frames(tb: TracebackType | None) -> tuple[tuple[str, ...], bool]:
    frames: list[str] = []
    visited = 0
    while tb is not None and visited < MAX_TRACE_FRAMES:
        filename = Path(tb.tb_frame.f_code.co_filename[-MAX_FILENAME_SIZE:])
        parts = filename.parts
        if _APP_PACKAGE in parts:
            package_index = parts.index(_APP_PACKAGE)
            module = ".".join(Path(*parts[package_index:]).with_suffix("").parts)
            function = tb.tb_frame.f_code.co_name[:MAX_FUNCTION_NAME_SIZE]
            frames.append(f"{module}:{function}")
        tb = tb.tb_next
        visited += 1
    return tuple(frames), tb is not None


def _exception_chain(exc: BaseException) -> tuple[tuple[BaseException, ...], bool]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while (
        current is not None
        and id(current) not in seen
        and len(chain) < MAX_EXCEPTION_CHAIN
    ):
        seen.add(id(current))
        chain.append(current)
        current = (
            current.__cause__
            if current.__cause__ is not None
            else current.__context__
            if not current.__suppress_context__
            else None
        )
    truncated = current is not None and id(current) not in seen
    return tuple(reversed(chain)), truncated


def _chain_frames(exc: BaseException) -> tuple[str, ...]:
    chain, chain_truncated = _exception_chain(exc)
    shape: list[str] = ["chain-truncated"] if chain_truncated else []
    for chained in chain:
        shape.append(f"chain:{_qualified_exception_type(type(chained))}")
        frames, frames_truncated = _application_frames(chained.__traceback__)
        shape.extend(frames)
        if frames_truncated:
            shape.append("frames-truncated")
    return tuple(shape[-MAX_TRACE_FRAMES:])


def _bounded_exception_message(exc: BaseException) -> str:
    if not exc.args:
        return ""
    first = exc.args[0]
    if isinstance(first, str):
        return first[:MAX_INPUT_TEXT]
    return f"[{type(first).__name__} value]"


def _bounded_trace(exc: BaseException, *, redact_exception_message: bool) -> str:
    sections: list[str] = []
    remaining_frames = MAX_TRACE_FRAMES
    chain, chain_truncated = _exception_chain(exc)
    trace_truncated = chain_truncated
    for chained in chain:
        if sections:
            sections.append(
                "\nThe above exception was the direct cause of the following "
                "exception:\n\n"
            )
        sections.append("Traceback (most recent call last):\n")
        frames: list[str] = []
        tb = chained.__traceback__
        while tb is not None and len(frames) < remaining_frames:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename[-MAX_FILENAME_SIZE:]
            function = frame.f_code.co_name[:MAX_FUNCTION_NAME_SIZE]
            frames.append(f'  File "{filename}", line {tb.tb_lineno}, in {function}\n')
            tb = tb.tb_next
        if tb is not None:
            trace_truncated = True
        sections.extend(frames)
        remaining_frames -= len(frames)
        message = (
            "[message redacted]"
            if redact_exception_message
            else _bounded_exception_message(chained)
        )
        sections.append(f"{_qualified_exception_type(type(chained))}: {message}\n")
    if trace_truncated:
        sections.append(TRUNCATION_MARKER)
    return "".join(sections)


def _traceback_text(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None] | None,
    *,
    redact_exception_message: bool = False,
) -> str | None:
    if exc_info is None:
        return None
    _, exc, _ = exc_info
    trace = _bounded_trace(
        exc,
        redact_exception_message=redact_exception_message,
    )
    trace = sanitize(trace)
    if len(trace) > MAX_TRACE_SIZE:
        trace = trace[: MAX_TRACE_SIZE - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return trace


def _fingerprint(
    event_code: str, exception_type: str | None, frames: Sequence[str]
) -> str:
    shape = "\n".join((event_code, exception_type or "", *frames))
    return hashlib.sha256(shape.encode()).hexdigest()


def _bounded_record_message(record: logging.LogRecord) -> str:
    if not isinstance(record.msg, str):
        return f"[{type(record.msg).__name__} log message]"
    # Interpolated arguments can contain unbounded or expensive values.
    return record.msg[:MAX_INPUT_TEXT]


def _bounded_record_text(value: object, fallback: str, limit: int) -> str:
    selected = value if isinstance(value, str) else fallback
    return selected[:limit]


def snapshot_from_record(record: logging.LogRecord) -> ErrorSnapshot:
    context = _get_context()
    raw_code = getattr(record, "event_code", None)
    if isinstance(raw_code, str) and raw_code:
        event_code = raw_code[:128]
    else:
        logger_name = _bounded_record_text(record.name, "logger", 64)
        function_name = _bounded_record_text(record.funcName, "unknown", 63)
        event_code = f"{logger_name}.{function_name}"[:128]
    exc_info = record.exc_info if record.exc_info and record.exc_info[0] else None
    exception_type = _qualified_exception_type(exc_info[0] if exc_info else None)
    frames = _chain_frames(exc_info[1]) if exc_info else ()
    message = sanitize(_bounded_record_message(record))[:MAX_MESSAGE_SIZE] or event_code
    display_name = sanitize(
        _bounded_record_text(getattr(record, "display_name", None), event_code, 256)
    )[:256]
    component = context.stage or _bounded_record_text(
        getattr(record, "component", None), "", 255
    )
    return ErrorSnapshot(
        fingerprint=_fingerprint(event_code, exception_type, frames),
        display_name=display_name,
        event_code=event_code,
        occurred_at=datetime.datetime.fromtimestamp(record.created, datetime.UTC),
        severity=record.levelname
        if record.levelname in {"ERROR", "CRITICAL"}
        else "ERROR",
        logger_name=record.name[:255],
        component=component or None,
        exception_type=exception_type,
        message=message,
        traceback=_traceback_text(
            exc_info,
            redact_exception_message=bool(
                getattr(record, "redact_exception_message", False)
            ),
        ),
        provider=context.provider,
        media_kind=context.media_kind,
        release=context.release,
        media_request_ids=context.media_request_ids,
    )


class SnapshotWriter(Protocol):
    def __call__(self, snapshot: ErrorSnapshot) -> None: ...


class ErrorReporterHandler(logging.Handler):
    def __init__(
        self,
        target: queue.Queue[ErrorSnapshot],
        capacity: threading.BoundedSemaphore,
    ) -> None:
        super().__init__(logging.ERROR)
        self._target = target
        self._capacity = capacity

    def emit(self, record: logging.LogRecord) -> None:
        if not self._capacity.acquire(blocking=False):
            return
        inserted = False
        try:
            snapshot = snapshot_from_record(record)
            self._target.put_nowait(snapshot)
            inserted = True
        except Exception:
            pass
        finally:
            if not inserted:
                self._capacity.release()

    def release_capacity(self) -> None:
        self._capacity.release()


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
        bounded_queue_size = max(1, min(queue_size, MAX_QUEUE_SIZE))
        self._queue: queue.Queue[ErrorSnapshot] = queue.Queue(
            maxsize=bounded_queue_size
        )
        self._writer = writer
        self._retries = max(0, min(retries, MAX_RETRIES))
        self._retry_backoff = max(0.0, min(retry_backoff, RETRY_BACKOFF_SECONDS))
        self._fallback = fallback or _Fallback()
        capacity = threading.BoundedSemaphore(bounded_queue_size)
        self.handler = ErrorReporterHandler(self._queue, capacity)
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
            self.handler.release_capacity()
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
