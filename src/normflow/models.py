"""SQLModel domain models for NormFlow."""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class ExampleMapping(SQLModel, table=True):
    """A raw_text -> normalized_text mapping pair."""

    id: int | None = Field(default=None, primary_key=True)
    raw_text: str = Field(index=True)
    normalized_text: str


class Suggestion(SQLModel, table=True):
    """A system-generated candidate for a raw_text record."""

    id: int | None = Field(default=None, primary_key=True)
    raw_text: str = Field(index=True)
    suggested_text: str
    status: str = Field(default="pending")  # pending | accepted | rejected
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
