"""add retry_count to raw_events

Revision ID: b7a1e9d4f2c8
Revises: e2c32c1832c5
Create Date: 2026-07-28 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7a1e9d4f2c8'
down_revision: Union[str, Sequence[str], None] = 'e2c32c1832c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'raw_events',
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('raw_events', 'retry_count')
