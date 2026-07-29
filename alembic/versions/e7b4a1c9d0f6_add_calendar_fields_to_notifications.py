"""add calendar fields to notifications

Revision ID: e7b4a1c9d0f6
Revises: d8e3c7f1a5b2
Create Date: 2026-07-29 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b4a1c9d0f6'
down_revision: Union[str, Sequence[str], None] = 'd8e3c7f1a5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notifications',
        sa.Column(
            'is_schedule_related',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        'notifications',
        sa.Column(
            'calendar_status',
            sa.String(),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        'notifications',
        sa.Column('calendar_event_id', sa.String(), nullable=True),
    )
    op.add_column(
        'notifications',
        sa.Column('calendar_event_url', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('notifications', 'calendar_event_url')
    op.drop_column('notifications', 'calendar_event_id')
    op.drop_column('notifications', 'calendar_status')
    op.drop_column('notifications', 'is_schedule_related')
