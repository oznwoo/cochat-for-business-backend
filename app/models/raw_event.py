from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class RawEvent(Base):
    __tablename__ = "raw_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    provider = Column(String, nullable=False)
    integration_id = Column(
        BigInteger,
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_event_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = Column(String, nullable=False, default="pending", server_default="pending")
    error_message = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    integration = relationship("IntegrationAccount", backref="raw_events")
    notifications = relationship("Notification", back_populates="raw_event")
