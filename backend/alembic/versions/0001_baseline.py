"""baseline: extensions + 14 tables + special constraints/indexes

Revision ID: 0001
Revises:
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    Base.metadata.create_all(bind=op.get_bind())
    op.execute(
        "ALTER TABLE itinerary_items ADD CONSTRAINT ex_items_no_overlap "
        "EXCLUDE USING gist (itinerary_id WITH =, tstzrange(start_at, end_at) WITH &&)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw "
        "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
    op.execute("ALTER TABLE itinerary_items DROP CONSTRAINT IF EXISTS ex_items_no_overlap")
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
    op.execute("DROP EXTENSION IF EXISTS vector")