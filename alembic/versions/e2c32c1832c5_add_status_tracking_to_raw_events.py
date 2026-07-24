"""add status tracking to raw_events

Revision ID: e2c32c1832c5
Revises: f5cd6f4f01ef
Create Date: 2026-07-24 15:32:43.163058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2c32c1832c5'
down_revision: Union[str, Sequence[str], None] = 'f5cd6f4f01ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'raw_events',
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
    )
    op.add_column('raw_events', sa.Column('error_message', sa.Text(), nullable=True))
    op.add_column(
        'raw_events',
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('raw_events', 'processed_at')
    op.drop_column('raw_events', 'error_message')
    op.drop_column('raw_events', 'status')
