"""Add speaker_id FK to conversations for handoff lookup

Revision ID: z9a0b1c2d3e4
Revises: y8z9a0b1c2d3
Create Date: 2026-03-30
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "z9a0b1c2d3e4"
down_revision = "y8z9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("speaker_id", sa.Integer(), sa.ForeignKey("speakers.id"), nullable=True),
    )
    op.create_index("ix_conversations_speaker_id", "conversations", ["speaker_id"])


def downgrade() -> None:
    op.drop_index("ix_conversations_speaker_id", table_name="conversations")
    op.drop_column("conversations", "speaker_id")
