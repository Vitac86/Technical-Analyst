from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Instrument(Base):
    """Tradable security or market instrument tracked by the application."""

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    market: Mapped[str | None] = mapped_column(String(64), nullable=True)
    board: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    candles: Mapped[list["Candle"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    indicator_values: Mapped[list["IndicatorValue"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
    analysis_signals: Mapped[list["AnalysisSignal"]] = relationship(
        back_populates="instrument",
        cascade="all, delete-orphan",
    )
