"""Add HNSW vector index on conversation_memories and composite cleanup index

Revision ID: y8z9a0b1c2d3
Revises: x7y8z9a0b1c2
Create Date: 2026-03-09
"""
from alembic import op
from sqlalchemy import text

# revision identifiers
revision = "y8z9a0b1c2d3"
down_revision = "x7y8z9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Check if pgvector extension is available
    result = conn.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    has_pgvector = result.scalar() is not None

    if has_pgvector:
        # C1: HNSW index on conversation_memories.embedding for similarity search
        result = conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_conversation_memories_embedding_hnsw'")
        )
        if result.scalar() is None:
            # Use halfvec cast for >2000-dim embeddings (pgvector HNSW limit)
            dim_result = conn.execute(text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'conversation_memories'::regclass "
                "AND attname = 'embedding'"
            ))
            dim_row = dim_result.first()
            dim = dim_row[0] if dim_row else 0
            if dim > 2000:
                op.execute(text(f"""
                    CREATE INDEX ix_conversation_memories_embedding_hnsw
                    ON conversation_memories
                    USING hnsw ((embedding::halfvec({dim})) halfvec_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """))
            else:
                op.execute(text("""
                    CREATE INDEX ix_conversation_memories_embedding_hnsw
                    ON conversation_memories
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """))

    # I3: Composite partial index for cleanup queries
    # (category, last_accessed_at) WHERE is_active = true
    result = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_conv_memories_cleanup'")
    )
    if result.scalar() is None:
        op.execute(text("""
            CREATE INDEX ix_conv_memories_cleanup
            ON conversation_memories (category, last_accessed_at)
            WHERE is_active = true
        """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS ix_conv_memories_cleanup"))
    op.execute(text("DROP INDEX IF EXISTS ix_conversation_memories_embedding_hnsw"))
