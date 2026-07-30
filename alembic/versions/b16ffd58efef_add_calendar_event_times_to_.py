"""add calendar event start/end times to notifications

Revision ID: b16ffd58efef
Revises: ac575d649db4
Create Date: 2026-07-30 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b16ffd58efef'
down_revision: Union[str, Sequence[str], None] = 'ac575d649db4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notifications',
        sa.Column('calendar_event_start_time', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'notifications',
        sa.Column('calendar_event_end_time', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('notifications', 'calendar_event_end_time')
    op.drop_column('notifications', 'calendar_event_start_time')
