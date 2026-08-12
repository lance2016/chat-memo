"""Production-database checks that the fast SQLite suite cannot cover."""

import os

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.skipif(
    not os.getenv("POSTGRES_SMOKE_DATABASE_URL"),
    reason="requires the disposable PostgreSQL CI service",
)
async def test_migrated_postgres_schema_supports_runtime_features() -> None:
    database_url = os.environ["POSTGRES_SMOKE_DATABASE_URL"]
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            dialect = connection.dialect.name
            assert dialect == "postgresql"

            revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            assert revision == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()

            extensions = set(
                (await connection.execute(text("SELECT extname FROM pg_extension"))).scalars()
            )
            assert {"pg_trgm", "vector"} <= extensions

            indexes = set(
                (
                    await connection.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = 'public' AND indexname IN "
                            "('ix_messages_search_trgm', 'ix_memories_content_trgm')"
                        )
                    )
                ).scalars()
            )
            assert indexes == {"ix_messages_search_trgm", "ix_memories_content_trgm"}

            content_type = (
                await connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'messages' "
                        "AND column_name = 'content'"
                    )
                )
            ).scalar_one()
            assert content_type == "jsonb"

            conversation_id = (
                await connection.execute(
                    text("INSERT INTO conversations (title) VALUES ('CI smoke') RETURNING id")
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO messages (conversation_id, role, content, search_text) "
                    "VALUES (:conversation_id, 'user', CAST(:content AS jsonb), :search_text)"
                ),
                {
                    "conversation_id": conversation_id,
                    "content": '[{"type":"text","text":"迁移冒烟测试"}]',
                    "search_text": "迁移冒烟测试",
                },
            )
            matched = (
                await connection.execute(
                    text("SELECT count(*) FROM messages WHERE search_text ILIKE '%冒烟%'")
                )
            ).scalar_one()
            assert matched == 1
    finally:
        await engine.dispose()
