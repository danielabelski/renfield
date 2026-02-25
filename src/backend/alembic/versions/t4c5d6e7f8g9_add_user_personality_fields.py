"""add user personality fields

Revision ID: t4c5d6e7f8g9
Revises: s3b4c5d6e7f8
Create Date: 2026-02-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 't4c5d6e7f8g9'
down_revision: Union[str, None] = 's3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('personality_style', sa.String(20), nullable=False, server_default='freundlich'))
    op.add_column('users', sa.Column('personality_prompt', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'personality_prompt')
    op.drop_column('users', 'personality_style')
