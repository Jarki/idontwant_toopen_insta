from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

from ig_reel_downloader import error_reporter


def _record(
    *,
    message: str = "failure",
    event_code: str = "test.failure",
    exc_info: tuple[type[BaseException], BaseException, object] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        "test.logger",
        logging.ERROR,
        __file__,
        1,
        message,
        (),
        exc_info,  # type: ignore[arg-type]
    )
    record.event_code = event_code
    return record


def _captured_exception(
    source: str, filename: str = "/srv/app/ig_reel_downloader/work.py"
):
    namespace: dict[str, object] = {}
    try:
        exec(compile(source, filename, "exec"), namespace)
    except Exception as exc:
        return type(exc), exc, exc.__traceback__
    raise AssertionError("source did not raise")


def test_snapshot_sanitizes_secrets_urls_credentials_authorization_and_paths(
    monkeypatch,
) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-secret")
    exc_info = _captured_exception(
        "raise RuntimeError('https://user:pass@example.com/a?token=x '"
        "+ 'Authorization: \"Bearer TOPSECRET\" Authorization: Bearer abc.def '"
        "+ 'bot-secret /home/alice/project/file.py')"
    )
    record = _record(
        message=(
            "postgresql+psycopg://user:pass@db/name "
            "https://example.com/private Authorization=token bot-secret "
            "/workspace/repo/file.py"
        ),
        exc_info=exc_info,
    )

    snapshot = error_reporter.snapshot_from_record(record)
    persisted = f"{snapshot.message}\n{snapshot.traceback}"

    for sensitive in (
        "user:pass",
        "example.com",
        "token",
        "abc.def",
        "TOPSECRET",
        "bot-secret",
        "/home/alice",
        "/workspace/repo",
    ):
        assert sensitive not in persisted
    assert "[url]" in persisted
    assert "[redacted]" in persisted


def test_parameterized_authorization_header_is_fully_redacted() -> None:
    value = "Authorization: Digest username=alice, response=TOPSECRET"
    exc_info = _captured_exception(f"raise RuntimeError({value!r})")

    snapshot = error_reporter.snapshot_from_record(
        _record(message=value, exc_info=exc_info)
    )
    persisted = f"{snapshot.message}\n{snapshot.traceback}"

    assert error_reporter.sanitize(value) == "Authorization=[redacted]"
    for secret in ("Digest", "username=alice", "response=TOPSECRET"):
        assert secret not in persisted


def test_authorization_mapping_values_are_fully_redacted() -> None:
    values = (
        "{'Authorization': 'Digest username=alice, response=TOPSECRET'}",
        '{"Authorization": "Digest username=alice, response=TOPSECRET"}',
    )

    for value in values:
        sanitized = error_reporter.sanitize(value)
        assert "TOPSECRET" not in sanitized
        assert "username=alice" not in sanitized
        assert "[redacted]" in sanitized


def test_trace_preserves_chaining_without_locals() -> None:
    exc_info = _captured_exception(
        "secret_local = 'must-not-leak'\n"
        "try:\n    raise ValueError('inner')\n"
        "except ValueError as exc:\n    raise RuntimeError('outer') from exc"
    )

    snapshot = error_reporter.snapshot_from_record(_record(exc_info=exc_info))

    assert snapshot.traceback is not None
    assert "direct cause" in snapshot.traceback
    assert "ValueError: inner" in snapshot.traceback
    assert "RuntimeError: outer" in snapshot.traceback
    assert "must-not-leak" not in snapshot.traceback


def test_trace_truncation_has_explicit_marker() -> None:
    exc_info = _captured_exception(f"raise RuntimeError({'x' * 70000!r})")

    snapshot = error_reporter.snapshot_from_record(_record(exc_info=exc_info))

    assert snapshot.traceback is not None
    assert len(snapshot.traceback) == error_reporter.MAX_TRACE_SIZE
    assert snapshot.traceback.endswith(error_reporter.TRUNCATION_MARKER)


def test_error_without_exception_has_no_fabricated_traceback() -> None:
    snapshot = error_reporter.snapshot_from_record(_record())

    assert snapshot.exception_type is None
    assert snapshot.traceback is None


def test_fingerprint_ignores_dynamic_messages_but_distinguishes_shape_and_code() -> (
    None
):
    first = error_reporter.snapshot_from_record(
        _record(
            message="item 123", exc_info=_captured_exception("raise ValueError('one')")
        )
    )
    equivalent = error_reporter.snapshot_from_record(
        _record(
            message="item 999", exc_info=_captured_exception("raise ValueError('two')")
        )
    )
    other_code = error_reporter.snapshot_from_record(
        _record(
            event_code="test.other", exc_info=_captured_exception("raise ValueError()")
        )
    )
    other_shape = error_reporter.snapshot_from_record(
        _record(
            exc_info=_captured_exception(
                "def different():\n    raise ValueError()\ndifferent()"
            )
        )
    )

    assert first.fingerprint == equivalent.fingerprint
    assert first.fingerprint != other_code.fingerprint
    assert first.fingerprint != other_shape.fingerprint


def test_fingerprint_includes_each_chained_exception_stack() -> None:
    first = error_reporter.snapshot_from_record(
        _record(
            exc_info=_captured_exception(
                "def inner_a():\n    raise ValueError('dynamic one')\n"
                "def wrapper():\n"
                "    try:\n        inner_a()\n"
                "    except ValueError as exc:\n        raise RuntimeError('outer') from exc\n"
                "wrapper()"
            )
        )
    )
    second = error_reporter.snapshot_from_record(
        _record(
            exc_info=_captured_exception(
                "def inner_b():\n    raise ValueError('dynamic two')\n"
                "def wrapper():\n"
                "    try:\n        inner_b()\n"
                "    except ValueError as exc:\n        raise RuntimeError('outer') from exc\n"
                "wrapper()"
            )
        )
    )

    assert first.fingerprint != second.fingerprint


def test_telegram_identity_is_removed_from_message_and_trace() -> None:
    exc_info = _captured_exception(
        "raise RuntimeError('telegram user Alice (@alice), id=123456 failed')"
    )
    snapshot = error_reporter.snapshot_from_record(
        _record(
            message="telegram user Alice (@alice), id=123456 failed",
            exc_info=exc_info,
        )
    )
    persisted = f"{snapshot.message}\n{snapshot.traceback}"

    for identity in ("Alice", "@alice", "123456"):
        assert identity not in persisted


def test_structured_telegram_identity_is_fully_redacted() -> None:
    identity = "Telegram User(id=123456, first_name=Alice, username=alice)"
    exc_info = _captured_exception(f"raise RuntimeError({identity!r})")

    snapshot = error_reporter.snapshot_from_record(
        _record(message=identity, exc_info=exc_info)
    )
    persisted = f"{snapshot.message}\n{snapshot.traceback}"

    for value in ("123456", "Alice", "alice"):
        assert value not in persisted


def test_native_telegram_identity_representations_are_fully_redacted() -> None:
    identities = (
        "User(first_name='Alice', id=123456, username='alice')",
        "Chat(id=-987654, title='Secret Group', username='secretchat')",
    )

    for identity in identities:
        sanitized = error_reporter.sanitize(identity)
        assert sanitized == "[telegram identity]"


def test_traceback_filename_is_bounded_before_path_processing(monkeypatch) -> None:
    exc_info = _captured_exception(
        "raise RuntimeError('failure')",
        "/srv/app/ig_reel_downloader/" + "x" * 20_000_000,
    )
    real_path = error_reporter.Path
    input_lengths: list[int] = []

    def bounded_path(*parts: str):
        input_lengths.extend(len(part) for part in parts)
        return real_path(*parts)

    monkeypatch.setattr(error_reporter, "Path", bounded_path)
    snapshot = error_reporter.snapshot_from_record(_record(exc_info=exc_info))

    assert max(input_lengths) <= error_reporter.MAX_FILENAME_SIZE
    assert snapshot.traceback is not None
    assert "x" * (error_reporter.MAX_FILENAME_SIZE + 1) not in snapshot.traceback


def test_frame_and_exception_chain_truncation_are_marked() -> None:
    deep_trace = _captured_exception(
        "def recurse(depth):\n"
        "    if depth == 0:\n        raise RuntimeError('deep')\n"
        "    recurse(depth - 1)\n"
        "recurse(100)"
    )
    frame_snapshot = error_reporter.snapshot_from_record(_record(exc_info=deep_trace))

    cause: BaseException = ValueError("root")
    for index in range(error_reporter.MAX_EXCEPTION_CHAIN + 2):
        outer = RuntimeError(f"layer {index}")
        outer.__cause__ = cause
        cause = outer
    chain_snapshot = error_reporter.snapshot_from_record(
        _record(exc_info=(type(cause), cause, None))
    )

    assert frame_snapshot.traceback is not None
    assert frame_snapshot.traceback.endswith(error_reporter.TRUNCATION_MARKER)
    assert chain_snapshot.traceback is not None
    assert chain_snapshot.traceback.endswith(error_reporter.TRUNCATION_MARKER)


def test_large_inputs_are_bounded_without_stringifying_arbitrary_values() -> None:
    class BlockingValue:
        def __str__(self) -> str:
            raise AssertionError("unbounded value was stringified")

    error = RuntimeError(BlockingValue())
    record = logging.LogRecord(
        "test.logger",
        logging.ERROR,
        __file__,
        1,
        BlockingValue(),
        (),
        (RuntimeError, error, None),
    )

    snapshot = error_reporter.snapshot_from_record(record)
    huge = error_reporter.snapshot_from_record(_record(message="x" * 20_000_000))

    assert snapshot.message == "[BlockingValue log message]"
    assert "[BlockingValue value]" in (snapshot.traceback or "")
    assert len(huge.message) == error_reporter.MAX_MESSAGE_SIZE


def test_identity_free_trace_redacts_arbitrary_global_handler_message() -> None:
    exc_info = _captured_exception("raise RuntimeError('Alice 123456 @alice')")
    record = _record(exc_info=exc_info)
    record.redact_exception_message = True

    snapshot = error_reporter.snapshot_from_record(record)

    assert snapshot.traceback is not None
    assert "RuntimeError" in snapshot.traceback
    assert "Alice" not in snapshot.traceback
    assert "123456" not in snapshot.traceback
    assert "@alice" not in snapshot.traceback


def test_context_is_immutable_bounded_and_copied_before_enqueue() -> None:
    target: list[error_reporter.ErrorSnapshot] = []
    reporter = error_reporter.ErrorReporter(target.append)
    try:
        ids = list(range(1, 150))
        with error_reporter.bind_context(
            media_request_ids=ids,
            provider="instagram",
            media_kind="reel",
            stage="download",
            release="abc",
        ):
            reporter.handler.emit(_record())
            ids.clear()
        deadline = time.monotonic() + 1
        while not target and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        reporter.stop()

    assert len(target[0].media_request_ids) == error_reporter.MAX_REQUEST_LINKS
    assert target[0].media_request_ids[0] == 1
    assert target[0].provider == "instagram"
    assert target[0].component == "download"
    assert target[0].release == "abc"


def test_blocked_writer_and_full_queue_never_builds_dropped_snapshot(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    fallback_entered = threading.Event()

    def blocked_writer(snapshot: error_reporter.ErrorSnapshot) -> None:
        del snapshot
        entered.set()
        release.wait()

    def blocking_fallback(message: str) -> None:
        del message
        fallback_entered.set()
        release.wait()

    reporter = error_reporter.ErrorReporter(
        blocked_writer, queue_size=1, fallback=blocking_fallback
    )
    try:
        reporter.handler.emit(_record(message="first"))
        assert entered.wait(1)
        reporter.handler.emit(_record(message="queued"))
        original_snapshot = error_reporter.snapshot_from_record
        snapshot_calls = 0

        def counted_snapshot(record: logging.LogRecord) -> error_reporter.ErrorSnapshot:
            nonlocal snapshot_calls
            snapshot_calls += 1
            return original_snapshot(record)

        monkeypatch.setattr(error_reporter, "snapshot_from_record", counted_snapshot)
        reporter.handler.emit(_record(message="x" * 20_000_000))
        assert snapshot_calls == 0
        assert not fallback_entered.is_set()
        started = time.monotonic()
        reporter.stop(timeout=0.02)
        assert time.monotonic() - started < 0.1
    finally:
        release.set()
        reporter.stop()


def test_capacity_reservation_prevents_snapshot_race(monkeypatch) -> None:
    reporter = error_reporter.ErrorReporter(lambda _: None, queue_size=1)
    original_snapshot = error_reporter.snapshot_from_record
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_snapshot(record: logging.LogRecord) -> error_reporter.ErrorSnapshot:
        nonlocal calls
        calls += 1
        entered.set()
        release.wait()
        return original_snapshot(record)

    monkeypatch.setattr(error_reporter, "snapshot_from_record", blocking_snapshot)
    producer = threading.Thread(target=reporter.handler.emit, args=(_record(),))
    try:
        producer.start()
        assert entered.wait(1)
        reporter.handler.emit(_record(message="must drop before snapshot"))
        assert calls == 1
    finally:
        release.set()
        producer.join(1)
        reporter.stop()


def test_capacity_token_released_after_writer_consumes() -> None:
    written: list[error_reporter.ErrorSnapshot] = []
    two_written = threading.Event()

    def writer(snapshot: error_reporter.ErrorSnapshot) -> None:
        written.append(snapshot)
        if len(written) == 2:
            two_written.set()

    reporter = error_reporter.ErrorReporter(writer, queue_size=1)
    try:
        reporter.handler.emit(_record(message="first"))
        deadline = time.monotonic() + 1
        while len(written) < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        reporter.handler.emit(_record(message="second"))
        assert two_written.wait(1)
    finally:
        reporter.stop()

    assert [snapshot.message for snapshot in written] == ["first", "second"]


def test_capacity_token_released_when_snapshot_fails(monkeypatch) -> None:
    written = threading.Event()
    reporter = error_reporter.ErrorReporter(lambda _: written.set(), queue_size=1)
    original_snapshot = error_reporter.snapshot_from_record
    calls = 0

    def fail_once(record: logging.LogRecord) -> error_reporter.ErrorSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("snapshot failed")
        return original_snapshot(record)

    monkeypatch.setattr(error_reporter, "snapshot_from_record", fail_once)
    try:
        reporter.handler.emit(_record(message="failed"))
        reporter.handler.emit(_record(message="accepted"))
        assert written.wait(1)
    finally:
        reporter.stop()

    assert calls == 2


def test_writer_failures_are_bounded_and_do_not_escape() -> None:
    attempts = 0
    fallback = threading.Event()

    def failing_writer(snapshot: error_reporter.ErrorSnapshot) -> None:
        nonlocal attempts
        del snapshot
        attempts += 1
        raise RuntimeError("database down")

    reporter = error_reporter.ErrorReporter(
        failing_writer,
        retries=2,
        retry_backoff=0,
        fallback=lambda _: fallback.set(),
    )
    try:
        reporter.handler.emit(_record())
        assert fallback.wait(1)
    finally:
        reporter.stop()

    assert attempts == 3


def test_global_error_handler_logs_exception_with_stable_event(monkeypatch) -> None:
    from ig_reel_downloader import app as app_module

    calls: list[tuple[str, dict[str, object]]] = []
    error = RuntimeError("handler failed")
    context = SimpleNamespace(error=error)
    monkeypatch.setattr(
        app_module.logger,
        "error",
        lambda message, **kwargs: calls.append((message, kwargs)),
    )
    instance = object.__new__(app_module.IgReelDownloaderApp)

    import asyncio

    asyncio.run(instance._unexpected_error_handler(object(), context))

    assert calls[0][1]["extra"] == {
        "event_code": "telegram.unexpected_handler",
        "redact_exception_message": True,
    }
    assert calls[0][1]["exc_info"][1] is error
