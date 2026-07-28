"""add unique index to raw_events (provider, integration_id, provider_event_id)

Revision ID: c4f2b6a19e3d
Revises: b7a1e9d4f2c8
Create Date: 2026-07-28 09:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f2b6a19e3d'
down_revision: Union[str, Sequence[str], None] = 'b7a1e9d4f2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNIQUE_INDEX_NAME = "uq_raw_events_provider_integration_event_id"


def upgrade() -> None:
    """Upgrade schema."""
    # 유니크 인덱스 생성 전, 기존에 SELECT-then-INSERT 레이스로 이미 쌓였을 수 있는
    # 중복 raw_event를 정리한다 (동일 키 그룹 중 가장 오래된 id만 남김) (#14).
    # notifications.raw_event_id는 ON DELETE SET NULL이라 연결된 Notification은 보존된다.
    op.execute(
        """
        DELETE FROM raw_events a
        USING raw_events b
        WHERE a.id > b.id
          AND a.provider = b.provider
          AND a.integration_id = b.integration_id
          AND a.provider_event_id = b.provider_event_id
        """
    )
    op.create_index(
        UNIQUE_INDEX_NAME,
        "raw_events",
        ["provider", "integration_id", "provider_event_id"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(UNIQUE_INDEX_NAME, table_name="raw_events")
