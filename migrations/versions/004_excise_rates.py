"""Add excise_rates table for excise tax management.

Stores excise rates by ТН ВЭД code prefix.
Based on НК КР, ст. 336 (Закон КР №78 от 03.04.2023).

Revision ID: 004
Revises: 003
Create Date: 2026-03-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "excise_rates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "code_pattern",
            sa.String(14),
            nullable=False,
            comment="TNVED code or prefix, e.g. '2203' or '271012'",
        ),
        sa.Column("product_name", sa.Text, nullable=False),
        sa.Column(
            "rate",
            sa.String(50),
            nullable=False,
            comment="Rate amount, e.g. '300', '10000'",
        ),
        sa.Column(
            "rate_unit",
            sa.String(50),
            nullable=False,
            comment="Unit: сом/литр, сом/тонну, сом/1000 шт, сом/кг, сом/шт",
        ),
        sa.Column("legal_ref", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_excise_rate_code_pattern",
        "excise_rates",
        ["code_pattern"],
    )


def downgrade() -> None:
    op.drop_index("idx_excise_rate_code_pattern", table_name="excise_rates")
    op.drop_table("excise_rates")
