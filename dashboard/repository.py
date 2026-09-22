"""Bounded aggregate queries; no Telegram identities or request URLs leave the DB."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text


class DashboardRepository:
    def __init__(self, url: str) -> None:
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("DASHBOARD_DATABASE_URL must use postgresql+psycopg://")
        self.engine = create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": 5}
        )

    def snapshot(self, days: int) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        queries = {
            "totals": """
                SELECT count(*) AS requests,
                    count(DISTINCT telegram_user_id) AS users,
                    count(DISTINCT telegram_chat_id) AS chats,
                    count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered,
                    count(*) FILTER (WHERE failure_reason IS NOT NULL) AS download_failed,
                    count(*) FILTER (WHERE media_item_id IS NOT NULL AND delivered_at IS NULL) AS unconfirmed,
                    count(*) FILTER (WHERE completed_at IS NULL) AS pending
                FROM public.media_requests WHERE created_at >= :since
            """,
            "providers": """
                SELECT provider, count(*) AS requests,
                    count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered
                FROM public.media_requests WHERE created_at >= :since
                GROUP BY provider ORDER BY requests DESC
            """,
            "daily": """
                SELECT to_char(created_at, 'YYYY-MM-DD') AS day, count(*) AS requests,
                    count(*) FILTER (WHERE delivered_at IS NOT NULL) AS delivered,
                    count(*) FILTER (WHERE failure_reason IS NOT NULL) AS failed
                FROM public.media_requests WHERE created_at >= :since
                GROUP BY day ORDER BY day
            """,
            "failures": """
                SELECT failure_reason AS reason, count(*) AS count
                FROM public.media_requests WHERE created_at >= :since
                    AND failure_reason IS NOT NULL
                GROUP BY failure_reason ORDER BY count DESC
            """,
            "errors": """
                SELECT g.id, g.display_name, g.event_code, g.exception_type, g.status,
                    count(*) AS occurrences, max(o.occurred_at) AS last_seen
                FROM observability.error_groups g
                JOIN observability.error_occurrences o ON o.error_group_id = g.id
                WHERE o.occurred_at >= :since
                GROUP BY g.id ORDER BY last_seen DESC, g.id DESC LIMIT 501
            """,
        }
        with self.engine.connect() as connection, connection.begin():
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            connection.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            result: dict[str, Any] = {
                name: [
                    dict(row)
                    for row in connection.execute(
                        text(sql),
                        {
                            "since": since.replace(tzinfo=None)
                            if name != "errors"
                            else since
                        },
                    ).mappings()
                ]
                for name, sql in queries.items()
            }
        result["totals"] = result["totals"][0]
        result["errors_truncated"] = len(result["errors"]) > 500
        result["errors"] = result["errors"][:500]
        result["updated_at"] = datetime.now(UTC).isoformat()
        return result
