from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class Briefing(Base):
    __tablename__ = "briefings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        BigInteger,
        ForeignKey("focus_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    action_items = Column(JSONB, nullable=False, default=list, server_default="[]")
    generated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    session = relationship("FocusSession", back_populates="briefings")
    notifications = relationship(
        "Notification",
        secondary="briefing_notifications",
        back_populates="briefings",
    )
