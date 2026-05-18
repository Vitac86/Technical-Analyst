"""Initial SQLAlchemy models.

Revision ID: 20260518_0001
Revises:
Create Date: 2026-05-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260518_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("market", sa.String(length=64), nullable=True),
        sa.Column("board", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_instruments_id", "instruments", ["id"], unique=False)
    op.create_index("ix_instruments_ticker", "instruments", ["ticker"], unique=True)

    op.create_table(
        "analysis_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("strength", sa.String(length=32), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_signals_direction",
        "analysis_signals",
        ["direction"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_signals_generated_at",
        "analysis_signals",
        ["generated_at"],
        unique=False,
    )
    op.create_index("ix_analysis_signals_id", "analysis_signals", ["id"], unique=False)
    op.create_index(
        "ix_analysis_signals_instrument_id",
        "analysis_signals",
        ["instrument_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_signals_signal_type",
        "analysis_signals",
        ["signal_type"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_signals_timeframe",
        "analysis_signals",
        ["timeframe"],
        unique=False,
    )

    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("volume", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "timeframe",
            "timestamp",
            name="uq_candles_instrument_timeframe_timestamp",
        ),
    )
    op.create_index("ix_candles_id", "candles", ["id"], unique=False)
    op.create_index(
        "ix_candles_instrument_id",
        "candles",
        ["instrument_id"],
        unique=False,
    )
    op.create_index("ix_candles_timeframe", "candles", ["timeframe"], unique=False)
    op.create_index("ix_candles_timestamp", "candles", ["timestamp"], unique=False)

    op.create_table(
        "indicator_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("indicator_name", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "indicator_name",
            "timeframe",
            "timestamp",
            name="uq_indicator_values_lookup",
        ),
    )
    op.create_index(
        "ix_indicator_values_category",
        "indicator_values",
        ["category"],
        unique=False,
    )
    op.create_index("ix_indicator_values_id", "indicator_values", ["id"], unique=False)
    op.create_index(
        "ix_indicator_values_indicator_name",
        "indicator_values",
        ["indicator_name"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_values_instrument_id",
        "indicator_values",
        ["instrument_id"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_values_timeframe",
        "indicator_values",
        ["timeframe"],
        unique=False,
    )
    op.create_index(
        "ix_indicator_values_timestamp",
        "indicator_values",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_indicator_values_timestamp", table_name="indicator_values")
    op.drop_index("ix_indicator_values_timeframe", table_name="indicator_values")
    op.drop_index("ix_indicator_values_instrument_id", table_name="indicator_values")
    op.drop_index("ix_indicator_values_indicator_name", table_name="indicator_values")
    op.drop_index("ix_indicator_values_id", table_name="indicator_values")
    op.drop_index("ix_indicator_values_category", table_name="indicator_values")
    op.drop_table("indicator_values")

    op.drop_index("ix_candles_timestamp", table_name="candles")
    op.drop_index("ix_candles_timeframe", table_name="candles")
    op.drop_index("ix_candles_instrument_id", table_name="candles")
    op.drop_index("ix_candles_id", table_name="candles")
    op.drop_table("candles")

    op.drop_index("ix_analysis_signals_timeframe", table_name="analysis_signals")
    op.drop_index("ix_analysis_signals_signal_type", table_name="analysis_signals")
    op.drop_index("ix_analysis_signals_instrument_id", table_name="analysis_signals")
    op.drop_index("ix_analysis_signals_id", table_name="analysis_signals")
    op.drop_index("ix_analysis_signals_generated_at", table_name="analysis_signals")
    op.drop_index("ix_analysis_signals_direction", table_name="analysis_signals")
    op.drop_table("analysis_signals")

    op.drop_index("ix_instruments_ticker", table_name="instruments")
    op.drop_index("ix_instruments_id", table_name="instruments")
    op.drop_table("instruments")
