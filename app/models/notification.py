from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    integration_id = Column(
        BigInteger,
        ForeignKey("integration_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    raw_event_id = Column(
        BigInteger,
        ForeignKey("raw_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_type = Column(String, nullable=False)
    provider_object_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    original_text = Column(Text, nullable=True)
    rich_contents = Column(Text, nullable=True)
    content_preview = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    sender_name = Column(String, nullable=True)
    channel_name = Column(String, nullable=True)
    channel_id = Column(String, nullable=True, index=True)
    is_direct_target = Column(Boolean, nullable=False, default=False)
    is_broadcast = Column(Boolean, nullable=False, default=False)
    has_attachments = Column(Boolean, nullable=False, default=False)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    priority = Column(String, nullable=True)
    priority_score = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="unread")
    is_schedule_related = Column(Boolean, nullable=False, default=False)
    calendar_status = Column(String, nullable=False, default="none")
    calendar_event_id = Column(String, nullable=True)
    calendar_event_url = Column(String, nullable=True)
    suggested_start_time = Column(DateTime(timezone=True), nullable=True)
    suggested_duration_minutes = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    integration = relationship("IntegrationAccount", backref="notifications")
    raw_event = relationship("RawEvent", back_populates="notifications")
    feedback_reports = relationship("FeedbackReport", back_populates="notification")
    briefings = relationship(
        "Briefing",
        secondary="briefing_notifications",
        back_populates="notifications",
    )
