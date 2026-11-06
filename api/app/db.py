"""Persistence: orgs, workflows, runs, approvals and the audit log."""
from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20))  # "vultr" | "mock"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    team_key: Mapped[str] = mapped_column(String(80))
    role_key: Mapped[str] = mapped_column(String(80), default="")
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="draft")  # "hero" | "draft" | "skill"
    steps: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. recording observations, transcript
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"))
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"))
    status: Mapped[str] = mapped_column(String(30), default="running")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    team_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    decided_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    """Append-only audit log; also streamed to the UI."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


class AppSetting(Base):
    """Key/value settings connected from the app (AI provider keys, connector credentials)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class User(Base):
    """A person who uses OrgTwin. The first one is the admin; the admin invites everyone else.

    role: "admin" (everything) or "member". departments: team keys where this person may create and
    edit skills. Everyone signed in can see and run every skill. token_hash: sha256 of the personal
    sign-in token in their invite link (the token itself is never stored).
    """
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(20), default="member")
    departments: Mapped[list] = mapped_column(JSON, default=list)
    notify: Mapped[dict] = mapped_column(JSON, default=dict)  # {"slack": bool, "discord": bool, "slack_id", "discord_id"}
    token_hash: Mapped[str] = mapped_column(String(64), default="")
    joined: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Request(Base):
    """Someone asks for work ("I need an analysis of …"); OrgTwin runs the best skill and tells its owners."""
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    from_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_name: Mapped[str] = mapped_column(String(120), default="")
    from_team: Mapped[str] = mapped_column(String(80), default="")
    workflow_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_key: Mapped[str] = mapped_column(String(80), default="")   # the department that owns the skill
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="routing")  # routing|queued|running|done|failed|no_skill
    why: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    notified: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
