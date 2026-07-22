"""add rich_contents to notifications

Revision ID: f5cd6f4f01ef
Revises: a4625ae3c7da
Create Date: 2026-07-22 17:30:35.513693

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5cd6f4f01ef'
down_revision: Union[str, Sequence[str], None] = 'a4625ae3c7da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('notifications', sa.Column('rich_contents', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('notifications', 'rich_contents')
