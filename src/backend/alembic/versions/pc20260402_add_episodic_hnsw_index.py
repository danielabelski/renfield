"""Add HNSW vector index on episodic_memories.embedding

The pc20260401 migration created the embedding column as sa.Text(),
but SQLAlchemy create_all() may have already created it as vector(768).
This migration:
1. Converts the column to vector(768) if it's still text (idempotent)
2. Creates the HNSW index for cosine similarity search

Revision ID: pc20260402a1
Revises: pc20260401a1
Create Date: 2026-04-02
"""
from alembic import op
from sqlalchemy import text

# revision identifiers
revision = "pc20260402a1"
down_revision = "pc20260401a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if pgvector extension is available
    result = conn.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    if result.scalar() is None:
        return  # pgvector not installed, skip

    # Check current column type
    result = conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = 'episodic_memories' AND column_name = 'embedding'"
    ))
    row = result.first()
    if row is None:
        return  # table or column doesn't exist

    # Convert from text to vector if needed
    if row[0] == "text":
        op.execute(text(
            "ALTER TABLE episodic_memories "
            "ALTER COLUMN embedding TYPE vector(768) "
            "USING embedding::vector(768)"
        ))

    # Create HNSW index if it doesn't exist
    result = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_episodic_embedding_hnsw'")
    )
    if result.scalar() is None:
        op.execute(text(
            "CREATE INDEX ix_episodic_embedding_hnsw "
            "ON episodic_memories "
            "USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        ))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_episodic_embedding_hnsw"))
