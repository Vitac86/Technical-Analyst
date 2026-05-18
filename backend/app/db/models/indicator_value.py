from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class IndicatorValue(Base):
    """Calculated indicator output for one instrument at a timestamp."""

    __tablename__ = "indicator_values"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "indicator_name",
            "timeframe",
            "timestamp",
            name="uq_indicator_values_lookup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    indicator_name: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSON)

    instrument: Mapped["Instrument"] = relationship(back_populates="indicator_values")
