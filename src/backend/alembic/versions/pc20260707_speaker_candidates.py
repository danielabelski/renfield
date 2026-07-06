"""speaker_candidates review bucket (controlled recognition, Phase 3)

Revision ID: pc20260707_speaker_candidates
Revises: pc20260706_speaker_enrolled
Create Date: 2026-07-06

Under controlled recognition (`speaker_controlled_enrollment_enabled`) a passive
turn that matches no ENROLLED profile is NOT auto-enrolled (no more polluting
"Unbekannter Sprecher"); a quality-passing unknown embedding is dropped here for
admin review → promote-to-enrolled or dismiss. best_speaker_id SET NULL on the
nearest-profile delete (advisory only). See docs/design/speaker-enrollment-redesign.md.

Fully transactional. Chains off the real head ``pc20260706_speaker_enrolled``;
apply TARGETED (prod carries multiple alembic heads).
"""
import sqlalchemy as sa
from alembic import op

revision = "pc20260707_speaker_candidates"
down_revision = "pc20260706_speaker_enrolled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "speaker_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("best_score", sa.Float(), nullable=True),
        sa.Column(
            "best_speaker_id", sa.Integer(),
            sa.ForeignKey("speakers.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("audio_duration_s", sa.Float(), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_speaker_candidates_best_speaker_id", "speaker_candidates", ["best_speaker_id"])
    op.create_index("ix_speaker_candidates_created_at", "speaker_candidates", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_speaker_candidates_created_at", table_name="speaker_candidates")
    op.drop_index("ix_speaker_candidates_best_speaker_id", table_name="speaker_candidates")
    op.drop_table("speaker_candidates")
