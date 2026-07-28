"""add action_items to briefings

Revision ID: d8e3c7f1a5b2
Revises: c4f2b6a19e3d
Create Date: 2026-07-28 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd8e3c7f1a5b2'
down_revision: Union[str, Sequence[str], None] = 'c4f2b6a19e3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'briefings',
        sa.Column(
            'action_items',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('briefings', 'action_items')
