from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

_RECORD_SQL = text("""
SELECT observability.record_error(
    CAST(:fingerprint AS varchar), CAST(:display_name AS varchar),
    CAST(:event_code AS varchar), CAST(:exception_type AS varchar),
    CAST(:occurred_at AS timestamptz), CAST(:severity AS varchar),
    CAST(:logger_name AS varchar), CAST(:component AS varchar),
    CAST(:message AS varchar), CAST(:traceback AS varchar),
    CAST(:provider AS varchar), CAST(:media_kind AS varchar),
    CAST(:release AS varchar), CAST(:request_ids AS bigint[])
)
""")


def _event(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "fingerprint": "a" * 64,
        "display_name": "Instagram download failed",
        "event_code": "instagram.download.failed",
        "exception_type": "RuntimeError",
        "occurred_at": datetime.now(UTC),
        "severity": "ERROR",
        "logger_name": "ig_reel_downloader.downloaders.instagram",
        "component": "download",
        "message": "sanitized failure",
        "traceback": "RuntimeError: sanitized failure",
        "provider": "instagram",
        "media_kind": "reel",
        "release": "test-release",
        "request_ids": [888888888],
    }
    values.update(overrides)
    return values


def _record(engine: Engine, **overrides: object) -> int:
    with engine.begin() as connection:
        occurrence_id = connection.scalar(_RECORD_SQL, _event(**overrides))
    assert isinstance(occurrence_id, int)
    return occurrence_id


def _denied(engine: Engine, statement: str) -> None:
    with pytest.raises(ProgrammingError), engine.begin() as connection:
        connection.execute(text(statement))


def test_bot_routine_is_atomic_and_preserves_workflow(
    app_engine: Engine, admin_engine: Engine
) -> None:
    first_at = datetime.now(UTC) - timedelta(minutes=1)
    first_id = _record(app_engine, occurred_at=first_at)

    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE observability.error_groups SET "
                "display_name='Manual name', status='monitoring', "
                "linked_change='PR-42', fixed_at=now() WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": "a" * 64},
        )

    second_id = _record(
        app_engine,
        display_name="Should not replace manual name",
        occurred_at=datetime.now(UTC),
        release="next-release",
    )
    assert second_id != first_id

    with admin_engine.connect() as connection:
        group = connection.execute(
            text(
                "SELECT display_name, status, linked_change, fixed_at, "
                "occurrence_count, first_release, last_release "
                "FROM observability.error_groups WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": "a" * 64},
        ).one()
        occurrence_count = connection.scalar(
            text(
                "SELECT count(*) FROM observability.error_occurrences "
                "WHERE error_group_id=(SELECT id FROM observability.error_groups "
                "WHERE fingerprint=:fingerprint)"
            ),
            {"fingerprint": "a" * 64},
        )
        link_count = connection.scalar(
            text(
                "SELECT count(*) FROM observability.error_request_links "
                "WHERE error_occurrence_id IN (:first_id, :second_id)"
            ),
            {"first_id": first_id, "second_id": second_id},
        )

    assert group.display_name == "Manual name"
    assert group.status == "monitoring"
    assert group.linked_change == "PR-42"
    assert group.fixed_at is not None
    assert group.occurrence_count == 2
    assert group.first_release == "test-release"
    assert group.last_release == "next-release"
    assert occurrence_count == 2
    assert link_count == 2


def test_out_of_order_events_preserve_seen_boundaries(
    app_engine: Engine, admin_engine: Engine
) -> None:
    fingerprint = "b" * 64
    later = datetime.now(UTC)
    earlier = later - timedelta(hours=1)
    _record(
        app_engine,
        fingerprint=fingerprint,
        occurred_at=later,
        release="later-release",
    )
    _record(
        app_engine,
        fingerprint=fingerprint,
        occurred_at=earlier,
        release="earlier-release",
    )

    with admin_engine.connect() as connection:
        group = connection.execute(
            text(
                "SELECT first_seen_at, last_seen_at, first_release, last_release "
                "FROM observability.error_groups WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": fingerprint},
        ).one()

    assert group.first_seen_at == earlier
    assert group.last_seen_at == later
    assert group.first_release == "earlier-release"
    assert group.last_release == "later-release"


def test_failure_after_group_upsert_rolls_back_entire_call(
    app_engine: Engine, admin_engine: Engine
) -> None:
    fingerprint = "c" * 64
    _record(app_engine, fingerprint=fingerprint, request_ids=[])

    with pytest.raises(SQLAlchemyError), app_engine.begin() as connection:
        connection.execute(
            _RECORD_SQL,
            _event(fingerprint=fingerprint, request_ids=[-1]),
        )

    with admin_engine.connect() as connection:
        group_count = connection.scalar(
            text(
                "SELECT occurrence_count FROM observability.error_groups "
                "WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": fingerprint},
        )
        occurrence_count = connection.scalar(
            text(
                "SELECT count(*) FROM observability.error_occurrences o "
                "JOIN observability.error_groups g ON g.id=o.error_group_id "
                "WHERE g.fingerprint=:fingerprint"
            ),
            {"fingerprint": fingerprint},
        )
    assert group_count == 1
    assert occurrence_count == 1


def test_concurrent_events_share_one_group(
    app_engine: Engine, admin_engine: Engine
) -> None:
    fingerprint = "9" * 64
    event_count = 8
    barrier = Barrier(event_count)

    def record_event() -> int:
        with app_engine.begin() as connection:
            barrier.wait()
            occurrence_id = connection.scalar(
                _RECORD_SQL,
                _event(fingerprint=fingerprint, request_ids=[]),
            )
        assert isinstance(occurrence_id, int)
        return occurrence_id

    with ThreadPoolExecutor(max_workers=event_count) as executor:
        occurrence_ids = list(
            executor.map(lambda _: record_event(), range(event_count))
        )

    with admin_engine.connect() as connection:
        group = connection.execute(
            text(
                "SELECT id, occurrence_count FROM observability.error_groups "
                "WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": fingerprint},
        ).one()
        stored_occurrence_count = connection.scalar(
            text(
                "SELECT count(*) FROM observability.error_occurrences "
                "WHERE error_group_id=:group_id"
            ),
            {"group_id": group.id},
        )

    assert len(set(occurrence_ids)) == event_count
    assert group.occurrence_count == event_count
    assert stored_occurrence_count == event_count


def test_bot_role_has_only_record_routine(app_engine: Engine) -> None:
    _denied(app_engine, "SELECT * FROM observability.error_groups")
    _denied(app_engine, "SELECT * FROM observability.api_error_groups")
    _denied(app_engine, "UPDATE observability.error_groups SET status='resolved'")
    _denied(
        app_engine,
        "INSERT INTO observability.error_notes (error_group_id, note, actor) "
        "VALUES (1, 'x', 'bot')",
    )
    _denied(app_engine, "DELETE FROM observability.error_occurrences")
    _denied(app_engine, "SELECT * FROM observability.alembic_version_error_api")
    _denied(app_engine, "SELECT * FROM public.alembic_version")


def test_api_role_can_use_only_curated_reads_and_workflow_writes(
    app_engine: Engine, api_engine: Engine, admin_engine: Engine
) -> None:
    target_fingerprint = "d" * 64
    other_fingerprint = "8" * 64
    _record(app_engine, fingerprint=target_fingerprint, request_ids=[])
    _record(app_engine, fingerprint=other_fingerprint, request_ids=[])

    with api_engine.begin() as connection:
        target_id = connection.scalar(
            text(
                "SELECT id FROM observability.api_error_groups "
                "WHERE fingerprint=:fingerprint"
            ),
            {"fingerprint": target_fingerprint},
        )
        assert isinstance(target_id, int)
        connection.execute(
            text(
                "UPDATE observability.api_error_groups "
                "SET status='investigating' WHERE id=:group_id"
            ),
            {"group_id": target_id},
        )
        connection.execute(
            text(
                "INSERT INTO observability.error_notes "
                "(error_group_id, note, actor) "
                "VALUES (:group_id, 'triage started', 'test-key')"
            ),
            {"group_id": target_id},
        )

    with admin_engine.connect() as connection:
        statuses = dict(
            connection.execute(
                text(
                    "SELECT fingerprint, status FROM observability.error_groups "
                    "WHERE fingerprint IN (:target, :other)"
                ),
                {"target": target_fingerprint, "other": other_fingerprint},
            )
            .tuples()
            .all()
        )
        note_count = connection.scalar(
            text(
                "SELECT count(*) FROM observability.error_notes "
                "WHERE error_group_id=:group_id"
            ),
            {"group_id": target_id},
        )

    assert statuses == {
        target_fingerprint: "investigating",
        other_fingerprint: "new",
    }
    assert note_count == 1

    _denied(api_engine, "SELECT * FROM observability.error_groups")
    _denied(api_engine, "SELECT * FROM public.telegram_users")
    _denied(api_engine, "SELECT * FROM public.media_requests")
    _denied(api_engine, "SELECT * FROM public.media_items")
    _denied(api_engine, "SELECT * FROM public.media_assets")
    _denied(api_engine, "SELECT * FROM public.judgmental_animations")
    _denied(api_engine, "SELECT * FROM observability.alembic_version_error_api")
    _denied(api_engine, "SELECT * FROM public.alembic_version")
    _denied(api_engine, "CREATE TABLE observability.forbidden (id int)")
    _denied(api_engine, "CREATE TABLE public.forbidden (id int)")
    _denied(
        api_engine,
        "UPDATE observability.error_groups SET status='resolved'",
    )
    _denied(
        api_engine,
        "UPDATE observability.api_error_groups SET event_code='forbidden'",
    )
    _denied(api_engine, "UPDATE observability.error_occurrences SET message='x'")
    _denied(api_engine, "DELETE FROM observability.error_occurrences")
    _denied(api_engine, "DELETE FROM observability.error_notes")


def test_api_reproduction_view_tolerates_missing_request(
    app_engine: Engine, api_engine: Engine
) -> None:
    _record(app_engine, fingerprint="e" * 64)
    with api_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT submitted_url, normalized_url, provider, media_kind, "
                "failure_stage, release "
                "FROM observability.api_reproduction_cases "
                "WHERE error_group_id=("
                "SELECT id FROM observability.api_error_groups "
                "WHERE fingerprint=:fingerprint)"
            ),
            {"fingerprint": "e" * 64},
        ).one()
    assert row.submitted_url is None
    assert row.normalized_url is None
    assert row.provider == "instagram"
    assert row.media_kind == "reel"


def test_api_reproduction_view_returns_linked_request_urls(
    app_engine: Engine, api_engine: Engine, admin_engine: Engine
) -> None:
    with admin_engine.begin() as connection:
        request_id = connection.scalar(
            text(
                "INSERT INTO public.media_requests "
                "(url, normalized_url, provider, media_kind, created_at) "
                "VALUES (:url, :normalized_url, 'instagram', 'reel', now()) "
                "RETURNING id"
            ),
            {
                "url": "https://www.instagram.com/reel/source",
                "normalized_url": "https://www.instagram.com/reel/normalized",
            },
        )
    assert isinstance(request_id, int)
    fingerprint = "f" * 64
    _record(app_engine, fingerprint=fingerprint, request_ids=[request_id])

    with api_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT submitted_url, normalized_url "
                "FROM observability.api_reproduction_cases "
                "WHERE error_group_id=("
                "SELECT id FROM observability.api_error_groups "
                "WHERE fingerprint=:fingerprint)"
            ),
            {"fingerprint": fingerprint},
        ).one()

    assert row.submitted_url == "https://www.instagram.com/reel/source"
    assert row.normalized_url == "https://www.instagram.com/reel/normalized"
