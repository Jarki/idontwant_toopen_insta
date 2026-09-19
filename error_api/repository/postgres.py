"""Restricted PostgreSQL implementation for the private Error API."""

from __future__ import annotations

from typing import Any, TypeVar, cast

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import RowMapping

from .models import (
    ErrorFilters,
    ErrorGroup,
    ErrorGroupPage,
    ErrorNote,
    ErrorOccurrence,
    ErrorPatch,
    NotePage,
    OccurrencePage,
    PageRequest,
    ReproductionCase,
    ReproductionPage,
)

T = TypeVar("T")


class PostgreSQLErrorRepository:
    """Use only migration-owned views and the two narrowly granted writes."""

    def __init__(self, database_url: str, *, statement_timeout_ms: int = 3000) -> None:
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("ERROR_API_DATABASE_URL must use postgresql+psycopg://")
        if not 100 <= statement_timeout_ms <= 30_000:
            raise ValueError("statement timeout must be between 100 and 30000 ms")
        self._engine: Engine = create_engine(database_url, pool_pre_ping=True)
        self._timeout = f"{statement_timeout_ms}ms"

    def _timeout_connection(self) -> Any:
        connection = self._engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": self._timeout},
            )
        except BaseException:
            transaction.rollback()
            connection.close()
            raise
        return connection, transaction

    def health(self) -> None:
        connection, transaction = self._timeout_connection()
        try:
            connection.execute(text("SELECT 1")).scalar_one()
            transaction.commit()
        finally:
            connection.close()

    @staticmethod
    def _group(row: RowMapping) -> ErrorGroup:
        first = row.get("first_post_fix_occurrence_id")
        return ErrorGroup(
            id=row["id"],
            display_name=row["display_name"],
            event_code=row["event_code"],
            exception_type=row["exception_type"],
            status=row["status"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            occurrence_count=row["occurrence_count"],
            first_release=row["first_release"],
            last_release=row["last_release"],
            linked_change=row["linked_change"],
            fixed_at=row["fixed_at"],
            recurred_after_fix=row["recurred_after_fix"],
            first_post_fix_occurrence=f"ERR-{first}" if first is not None else None,
        )

    @staticmethod
    def _paged(items: list[T], page: PageRequest) -> tuple[list[T], str | None]:
        more = len(items) > page.limit
        return items[: page.limit], str(page.offset + page.limit) if more else None

    def list_groups(self, filters: ErrorFilters, page: PageRequest) -> ErrorGroupPage:
        statement = text("""
            SELECT groups.*,
                (SELECT occurrences.id
                 FROM observability.api_error_occurrences AS occurrences
                 WHERE occurrences.error_group_id = groups.id
                   AND groups.fixed_at IS NOT NULL
                   AND occurrences.occurred_at > groups.fixed_at
                 ORDER BY occurrences.occurred_at ASC, occurrences.id ASC LIMIT 1
                ) AS first_post_fix_occurrence_id
            FROM observability.api_error_groups AS groups
            WHERE (:status IS NULL OR groups.status = :status)
              AND (:event_code IS NULL OR groups.event_code = :event_code)
              AND (:seen_from IS NULL OR groups.last_seen_at >= :seen_from)
              AND (:seen_to IS NULL OR groups.first_seen_at <= :seen_to)
              AND NOT (:seen_from IS NOT NULL AND :seen_to IS NOT NULL
                       AND :seen_from > :seen_to)
              AND (CAST(:provider AS text) IS NULL OR EXISTS (
                    SELECT 1 FROM observability.api_error_occurrences AS occurrence
                    WHERE occurrence.error_group_id = groups.id
                      AND occurrence.provider = :provider))
              AND (CAST(:severity AS text) IS NULL OR EXISTS (
                    SELECT 1 FROM observability.api_error_occurrences AS occurrence
                    WHERE occurrence.error_group_id = groups.id
                      AND occurrence.severity = :severity))
              AND (CAST(:component AS text) IS NULL OR EXISTS (
                    SELECT 1 FROM observability.api_error_occurrences AS occurrence
                    WHERE occurrence.error_group_id = groups.id
                      AND occurrence.component = :component))
              AND (CAST(:release AS text) IS NULL OR EXISTS (
                    SELECT 1 FROM observability.api_error_occurrences AS occurrence
                    WHERE occurrence.error_group_id = groups.id
                      AND occurrence.release = :release))
            ORDER BY groups.last_seen_at DESC, groups.id DESC
            LIMIT :fetch_limit OFFSET :offset
        """)
        params = filters.model_dump()
        params.update(fetch_limit=page.limit + 1, offset=page.offset)
        connection, transaction = self._timeout_connection()
        try:
            rows = connection.execute(statement, params).mappings().all()
            transaction.commit()
        finally:
            connection.close()
        items, cursor = self._paged([self._group(row) for row in rows], page)
        return ErrorGroupPage(items=items, next_cursor=cursor)

    def _group_row(self, connection: Any, occurrence_id: int) -> RowMapping | None:
        return cast(
            RowMapping | None,
            connection.execute(
                text("""
                SELECT groups.*,
                    (SELECT candidate.id
                     FROM observability.api_error_occurrences AS candidate
                     WHERE candidate.error_group_id = groups.id
                       AND groups.fixed_at IS NOT NULL
                       AND candidate.occurred_at > groups.fixed_at
                     ORDER BY candidate.occurred_at ASC, candidate.id ASC LIMIT 1
                    ) AS first_post_fix_occurrence_id
                FROM observability.api_error_groups AS groups
                JOIN observability.api_error_occurrences AS anchor
                  ON anchor.error_group_id = groups.id
                WHERE anchor.id = :occurrence_id
            """),
                {"occurrence_id": occurrence_id},
            )
            .mappings()
            .one_or_none(),
        )

    def get_group_for_occurrence(self, occurrence_id: int) -> ErrorGroup | None:
        connection, transaction = self._timeout_connection()
        try:
            row = self._group_row(connection, occurrence_id)
            transaction.commit()
        finally:
            connection.close()
        return self._group(row) if row is not None else None

    def list_occurrences(
        self, occurrence_id: int, page: PageRequest
    ) -> OccurrencePage | None:
        statement = text("""
            SELECT occurrence.* FROM observability.api_error_occurrences AS occurrence
            WHERE occurrence.error_group_id = (
                SELECT anchor.error_group_id FROM observability.api_error_occurrences AS anchor
                WHERE anchor.id = :occurrence_id)
            ORDER BY occurrence.occurred_at DESC, occurrence.id DESC
            LIMIT :fetch_limit OFFSET :offset
        """)
        connection, transaction = self._timeout_connection()
        try:
            if self._group_row(connection, occurrence_id) is None:
                transaction.commit()
                return None
            rows = (
                connection.execute(
                    statement,
                    {
                        "occurrence_id": occurrence_id,
                        "fetch_limit": page.limit + 1,
                        "offset": page.offset,
                    },
                )
                .mappings()
                .all()
            )
            transaction.commit()
        finally:
            connection.close()
        values = [
            ErrorOccurrence(
                reference=f"ERR-{row['id']}",
                occurred_at=row["occurred_at"],
                severity=row["severity"],
                logger_name=row["logger_name"],
                component=row["component"],
                exception_type=row["exception_type"],
                message=row["message"],
                traceback=row["traceback"],
                provider=row["provider"],
                media_kind=row["media_kind"],
                release=row["release"],
            )
            for row in rows
        ]
        items, cursor = self._paged(values, page)
        return OccurrencePage(items=items, next_cursor=cursor)

    def list_reproduction_cases(
        self, occurrence_id: int, page: PageRequest
    ) -> ReproductionPage | None:
        statement = text("""
            SELECT reproduction.* FROM observability.api_reproduction_cases AS reproduction
            WHERE reproduction.error_group_id = (
                SELECT anchor.error_group_id FROM observability.api_error_occurrences AS anchor
                WHERE anchor.id = :occurrence_id)
            ORDER BY reproduction.occurred_at DESC, reproduction.occurrence_id DESC,
                     reproduction.url ASC NULLS LAST, reproduction.normalized_url ASC NULLS LAST
            LIMIT :fetch_limit OFFSET :offset
        """)
        connection, transaction = self._timeout_connection()
        try:
            if self._group_row(connection, occurrence_id) is None:
                transaction.commit()
                return None
            rows = (
                connection.execute(
                    statement,
                    {
                        "occurrence_id": occurrence_id,
                        "fetch_limit": page.limit + 1,
                        "offset": page.offset,
                    },
                )
                .mappings()
                .all()
            )
            transaction.commit()
        finally:
            connection.close()
        values = [
            ReproductionCase(
                reference=f"ERR-{row['occurrence_id']}",
                display_name=row["display_name"],
                occurred_at=row["occurred_at"],
                submitted_url=row["url"],
                normalized_url=row["normalized_url"],
                provider=row["provider"],
                media_kind=row["media_kind"],
                component=row["component"],
                release=row["release"],
            )
            for row in rows
        ]
        items, cursor = self._paged(values, page)
        return ReproductionPage(items=items, next_cursor=cursor)

    def list_notes(self, occurrence_id: int, page: PageRequest) -> NotePage | None:
        statement = text("""
            SELECT note.* FROM observability.api_error_notes AS note
            WHERE note.error_group_id = (
                SELECT anchor.error_group_id FROM observability.api_error_occurrences AS anchor
                WHERE anchor.id = :occurrence_id)
            ORDER BY note.created_at DESC, note.id DESC
            LIMIT :fetch_limit OFFSET :offset
        """)
        connection, transaction = self._timeout_connection()
        try:
            if self._group_row(connection, occurrence_id) is None:
                transaction.commit()
                return None
            rows = (
                connection.execute(
                    statement,
                    {
                        "occurrence_id": occurrence_id,
                        "fetch_limit": page.limit + 1,
                        "offset": page.offset,
                    },
                )
                .mappings()
                .all()
            )
            transaction.commit()
        finally:
            connection.close()
        values = [ErrorNote.model_validate(row) for row in rows]
        items, cursor = self._paged(values, page)
        return NotePage(items=items, next_cursor=cursor)

    def update_group(self, occurrence_id: int, patch: ErrorPatch) -> ErrorGroup | None:
        supplied = patch.model_fields_set
        statement = text("""
            UPDATE observability.error_groups SET
                display_name = CASE WHEN :set_display_name THEN :display_name ELSE display_name END,
                status = CASE WHEN :set_status THEN :status ELSE status END,
                linked_change = CASE WHEN :set_linked_change THEN :linked_change ELSE linked_change END,
                fixed_at = CASE WHEN :set_fixed_at THEN :fixed_at ELSE fixed_at END
            WHERE id = (SELECT anchor.error_group_id
                        FROM observability.api_error_occurrences AS anchor
                        WHERE anchor.id = :occurrence_id)
            RETURNING id
        """)
        params = patch.model_dump()
        params.update(
            occurrence_id=occurrence_id,
            set_display_name="display_name" in supplied,
            set_status="status" in supplied,
            set_linked_change="linked_change" in supplied,
            set_fixed_at="fixed_at" in supplied,
        )
        connection, transaction = self._timeout_connection()
        try:
            changed = connection.execute(statement, params).scalar_one_or_none()
            row = (
                self._group_row(connection, occurrence_id)
                if changed is not None
                else None
            )
            transaction.commit()
        finally:
            connection.close()
        return self._group(row) if row is not None else None

    def add_note(self, occurrence_id: int, note: str, actor: str) -> ErrorNote | None:
        statement = text("""
            INSERT INTO observability.error_notes (error_group_id, note, actor, created_at)
            SELECT anchor.error_group_id, :note, :actor, CURRENT_TIMESTAMP
            FROM observability.api_error_occurrences AS anchor WHERE anchor.id = :occurrence_id
            RETURNING id, note, actor, created_at
        """)
        connection, transaction = self._timeout_connection()
        try:
            row = (
                connection.execute(
                    statement,
                    {"occurrence_id": occurrence_id, "note": note, "actor": actor},
                )
                .mappings()
                .one_or_none()
            )
            transaction.commit()
        finally:
            connection.close()
        return ErrorNote.model_validate(row) if row is not None else None
