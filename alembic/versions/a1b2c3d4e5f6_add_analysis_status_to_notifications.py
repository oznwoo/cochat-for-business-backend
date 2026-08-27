"""add analysis_status to notifications

Revision ID: a1b2c3d4e5f6
Revises: b16ffd58efef
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'b16ffd58efef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notifications',
        sa.Column(
            'analysis_status',
            sa.String(),
            nullable=False,
            server_default='completed',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('notifications', 'analysis_status')
