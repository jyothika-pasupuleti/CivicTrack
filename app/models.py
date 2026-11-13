from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.time_utils import UTCDateTime, utc_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    department: Mapped["Department | None"] = relationship(back_populates="users")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    service_radius_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    users: Mapped[list[User]] = relationship(back_populates="department")
    categories: Mapped[list["Category"]] = relationship(
        secondary="department_categories",
        back_populates="departments",
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_severity: Mapped[int] = mapped_column(Integer, default=2)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    departments: Mapped[list[Department]] = relationship(
        secondary="department_categories",
        back_populates="categories",
    )


class DepartmentCategory(Base):
    __tablename__ = "department_categories"
    __table_args__ = (UniqueConstraint("department_id", "category_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE")
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("category_id", "severity_min"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )
    severity_min: Mapped[int] = mapped_column(Integer, default=1)
    response_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    resolution_minutes: Mapped[int] = mapped_column(Integer, default=4320)


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        Index("ix_issues_status_created", "status", "created_at"),
        Index("ix_issues_location", "latitude", "longitude"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    complaint_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    assigned_department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text)
    location_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="submitted", index=True)
    severity: Mapped[int] = mapped_column(Integer, default=2)
    severity_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_source: Mapped[str] = mapped_column(String(20), default="citizen")
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    possible_duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("issues.id"), nullable=True
    )
    sla_response_due_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    sla_resolution_due_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now
    )

    category: Mapped[Category] = relationship()
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])
    assigned_department: Mapped[Department | None] = relationship()
    image: Mapped["IssueImage | None"] = relationship(
        back_populates="issue", uselist=False, cascade="all, delete-orphan"
    )
    events: Mapped[list["IssueEvent"]] = relationship(
        back_populates="issue",
        cascade="all, delete-orphan",
        order_by="IssueEvent.created_at",
    )


class IssueImage(Base):
    __tablename__ = "issue_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    media_type: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    perceptual_hash: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )

    issue: Mapped[Issue] = relationship(back_populates="image")


class IssueEvent(Base):
    __tablename__ = "issue_events"
    __table_args__ = (Index("ix_issue_events_issue_created", "issue_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(30))
    from_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)

    issue: Mapped[Issue] = relationship(back_populates="events")
    actor: Mapped[User] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    message: Mapped[str] = mapped_column(String(300))
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class VerifiedCategoryLabel(Base):
    __tablename__ = "verified_category_labels"
    __table_args__ = (Index("ix_verified_category_labels_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    verified_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title_snapshot: Mapped[str] = mapped_column(String(140))
    description_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
