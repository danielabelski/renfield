"""speakers.enrolled flag (controlled enrollment, Phase 1)

Revision ID: pc20260706_speaker_enrolled
Revises: pc20260705_speaker_fk_ondelete
Create Date: 2026-07-05

Distinguishes a DELIBERATELY enrolled reference profile (guided, quality-gated,
multi-sample, user-linked — ``services/speaker_enrollment_service.py``) from an
ambient auto-enrolled "Unbekannter Sprecher". Phase 3 will identify passive turns
against enrolled=True profiles only; Phase 0's gates already stop the pollution
loop. Auto-enroll continues to create enrolled=False rows.

Fully transactional. Chains off the real head ``pc20260705_speaker_fk_ondelete``;
apply TARGETED (prod carries multiple alembic heads).
See docs/design/speaker-enrollment-redesign.md.
"""
import sqlalchemy as sa
from alembic import op

revision = "pc20260706_speaker_enrolled"
down_revision = "pc20260705_speaker_fk_ondelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "speakers",
        sa.Column("enrolled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("speakers", "enrolled")
