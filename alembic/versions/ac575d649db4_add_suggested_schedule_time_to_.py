"""add suggested schedule time to notifications

Revision ID: ac575d649db4
Revises: e7b4a1c9d0f6
Create Date: 2026-07-30 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac575d649db4'
down_revision: Union[str, Sequence[str], None] = 'e7b4a1c9d0f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notifications',
        sa.Column('suggested_start_time', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'notifications',
        sa.Column('suggested_duration_minutes', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('notifications', 'suggested_duration_minutes')
    op.drop_column('notifications', 'suggested_start_time')
