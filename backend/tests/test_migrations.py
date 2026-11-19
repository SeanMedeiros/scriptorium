"""The production Alembic migrations and the ORM models describe the same schema (no drift)."""
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import get_engine
import app.models  # noqa: F401 — register tables
from app.db import Base

pytestmark = pytest.mark.integration


def test_migrations_match_models():
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
    assert diff == [], "\n".join(map(str, diff))
