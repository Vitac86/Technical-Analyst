"""Add engine column to instruments.

Revision ID: 20260518_0002
Revises: 20260518_0001
Create Date: 2026-05-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260518_0002"
down_revision: str | None = "20260518_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instruments",
        sa.Column("engine", sa.String(length=64), nullable=True),
    )
    # Default existing stock instruments to engine='stock'
    op.execute("UPDATE instruments SET engine = 'stock' WHERE engine IS NULL")


def downgrade() -> None:
    op.drop_column("instruments", "engine")
