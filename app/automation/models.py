from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"  # accepted by the provider
    SIMULATED = "simulated"  # sandbox mode: recorded, not actually sent
    FAILED = "failed"  # gave up after the retry limit
    SKIPPED = "skipped"  # no phone number / customer opted out / rule disabled at send time


class CustomerNotification(Base, TimestampMixin):
    """Outbox of messages to customers. Rows are written in the same transaction as the
    event that caused them, then delivered by the worker (so nothing is lost on a crash)."""

    __tablename__ = "automation_notifications"

    notification_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    customer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("insurance_customers.customer_id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # whatsapp | sms
    to_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Ordered values for a WhatsApp template's {{1}}, {{2}}… placeholders (JSON list).
    template_params: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(
            NotificationStatus,
            native_enum=False,
            values_callable=lambda e: [m.value for m in e],
            name="notification_status",
        ),
        nullable=False,
        default=NotificationStatus.QUEUED,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Same key = same message; prevents a reminder being queued twice (e.g. two worker runs).
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    policy_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # employee name, or None = automatic


class AutomationRule(Base, TimestampMixin):
    """Manager overrides for a built-in automation rule (see app/automation/rules.py)."""

    __tablename__ = "automation_rules"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # None = built-in wording
    whatsapp_template: Mapped[str | None] = mapped_column(String(120), nullable=True)
    params: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, e.g. {"days": [60, 30, 7]}


class AutomationSetting(Base, TimestampMixin):
    """Single-row style key/value settings (channel mode etc.)."""

    __tablename__ = "automation_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class InboundStatus(StrEnum):
    UNMATCHED = "unmatched"  # no request number / insurer recognised — needs a person
    MATCHED = "matched"  # linked to a submission, waiting for extraction
    QUOTE_CREATED = "quote_created"  # AI extracted a quote (pending review)
    DECLINED = "declined"  # insurer said they won't quote
    NO_QUOTE = "no_quote"  # a reply without a price (question, acknowledgement…)
    FAILED = "failed"  # extraction error; can be retried
    SKIPPED = "skipped"  # AI extraction is switched off


class InboundEmail(Base, TimestampMixin):
    """An email received in the quotes mailbox."""

    __tablename__ = "automation_inbound_emails"

    inbound_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    message_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachments: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list of stored files
    status: Mapped[InboundStatus] = mapped_column(
        Enum(
            InboundStatus, native_enum=False, values_callable=lambda e: [m.value for m in e], name="inbound_status"
        ),
        nullable=False,
        default=InboundStatus.UNMATCHED,
        index=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quote_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    extraction: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON of what the AI returned
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
